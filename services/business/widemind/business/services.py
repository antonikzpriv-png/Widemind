"""An extensible service registry. Research is one service, not a business constraint."""
from datetime import date
import hashlib
import re
from urllib.parse import urlsplit
from .contracts import Plan
from .policy import integer


def text(value, name, limit=10000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"invalid {name}")
    return value.strip()


def single_line(value, name, limit=300):
    # Validate the original string: stripping first would hide trailing CR/LF
    # while callers still retain the original provider ID or attachment fields.
    if isinstance(value, str) and any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"invalid {name}: control characters")
    return text(value, name, limit)


def web_url(value):
    value = single_line(value, "source URL", 2048)
    if any(ch.isspace() for ch in value):
        raise ValueError("URL whitespace must be encoded")
    parsed = urlsplit(value)
    _ = parsed.port  # Reject invalid and out-of-range ports.
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("source must be an HTTPS URL")
    return value


class ResearchBrief:
    name = "research-brief"
    capability = "research"

    def plan(self, scope, allowance):
        topic = text(scope.get("topic"), "topic", 500)
        headings = scope.get("sections")
        if not isinstance(headings, list) or not 1 <= len(headings) <= 8:
            raise ValueError("scope requires 1–8 sections")
        if any(text(h, "section", 100) != h or "\n" in h for h in headings) or len(set(headings)) != len(headings):
            raise ValueError("section headings must be unique")
        minimum = integer(scope.get("min_sources", 2), "minimum sources", 1, 12)
        integer(allowance, "research maximum cost", 1)
        arguments = {"topic": topic, "sections": headings, "min_sources": minimum}
        if "context" in scope:
            if not isinstance(scope["context"], str) or len(scope["context"]) > 2000:
                raise ValueError("invalid customer context")
            arguments["context"] = scope["context"].strip()
        if "requirements" in scope:
            requirements = scope["requirements"]
            if not isinstance(requirements, list) or len(requirements) > 20:
                raise ValueError("invalid customer requirements")
            arguments["requirements"] = [text(item, "customer requirement", 1000) for item in requirements]
        if "as_of" in scope:
            value = single_line(scope["as_of"], "as_of", 10)
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("as_of must be an ISO date")
            arguments["as_of"] = date.fromisoformat(value).isoformat()
        if "max_age_days" in scope:
            arguments["max_age_days"] = integer(scope["max_age_days"], "source maximum age", 1, 3650)
        return Plan("research.collect", arguments, allowance)

    def deliverable(self, scope, result):
        # The gateway supplies reviewed synthesis and its actual Markdown file.
        # Customer-facing output never includes the private evidence manifest.
        self.plan(scope, 1)
        if not isinstance(result, dict) or type(result.get("synthesis_version")) is not int or result["synthesis_version"] != 2:
            raise ValueError("research requires version 2 reviewed synthesis")
        quality = result.get("quality")
        checks = result.get("checks")
        if not isinstance(quality, dict) or quality.get("passed") is not True:
            raise ValueError("research quality review did not pass")
        if not isinstance(checks, dict) or not checks or len(checks) > 100 or any(
            value is not True and not (type(value) is int and 0 < value <= 1_000_000) for value in checks.values()
        ):
            raise ValueError("research synthesis checks failed")
        sources = result.get("sources")
        if not isinstance(sources, list) or not scope.get("min_sources", 2) <= len(sources) <= 12:
            raise ValueError("insufficient sources")
        urls = set()
        bibliography = []
        for source in sources:
            url = web_url(source["url"])
            if url in urls:
                raise ValueError("duplicate source")
            urls.add(url)
            title = text(source["title"], "source title", 500)
            excerpt = text(source["excerpt"], "source excerpt", 8000)
            bibliography.append({"url": url, "title": title})
        sections = result.get("sections")
        if not isinstance(sections, list) or any(not isinstance(section, dict) for section in sections) or [s.get("heading") for s in sections] != scope["sections"]:
            raise ValueError("sections do not match agreed scope")
        document = result.get("document")
        if not isinstance(document, dict) or document.get("media_type") != "text/markdown":
            raise ValueError("research requires a downloadable Markdown document")
        filename = single_line(document.get("filename"), "research filename", 200)
        if not filename.endswith(".md") or "/" in filename or "\\" in filename or filename.startswith("."):
            raise ValueError("invalid research document filename")
        content = text(document.get("content"), "research document", 1_000_000)
        # Preserve the exact bytes written by the tools service, including any
        # trailing newline. Validation must not regenerate or append excerpts.
        content = document["content"]
        record = document.get("artifact")
        if not isinstance(record, dict) or record.get("sha256") != hashlib.sha256(content.encode()).hexdigest():
            raise ValueError("research document artifact hash mismatch")
        if type(record.get("size_bytes")) is not int or record["size_bytes"] != len(content.encode()):
            raise ValueError("research document artifact size mismatch")
        if self._normalized(scope["topic"]) not in self._normalized(content):
            raise ValueError("document omits the customer topic")
        blocks = re.split(r"(?m)^##[ \t]+", content)
        scoped_blocks = {}
        for block in blocks[1:]:
            heading, _, body = block.partition("\n")
            if heading.strip() in scope["sections"]:
                if heading.strip() in scoped_blocks:
                    raise ValueError("duplicate research document section")
                scoped_blocks[heading.strip()] = body
        if list(scoped_blocks) != scope["sections"]:
            raise ValueError("document sections do not match customer scope")
        summaries = []
        for section in sections:
            summary = text(section.get("text"), "section text", 20000)
            normalized = self._normalized(summary)
            words = normalized.split()
            if len(words) < 12 or self._filler(summary):
                raise ValueError("section is filler or lacks meaningful synthesis")
            for previous in summaries:
                first, second = set(previous.split()), set(words)
                if normalized == previous or len(first & second) / max(1, len(first | second)) > .90:
                    raise ValueError("repeated generic research sections")
            summaries.append(normalized)
            terms = self._heading_terms(section["heading"])
            if terms and not terms & self._heading_terms(summary):
                raise ValueError("synthesis does not address its scoped section")
            copied = 0
            for source in sources:
                excerpt = self._normalized(source["excerpt"])
                if len(excerpt.split()) >= 8 and excerpt in normalized:
                    copied += len(excerpt)
                if len(excerpt) >= 100 and excerpt in self._normalized(content):
                    raise ValueError("full source excerpts are not a synthesized deliverable")
            if copied >= max(60, len(normalized) * .6):
                raise ValueError("source excerpt dump is not research synthesis")
            citations = section.get("source_urls")
            if not isinstance(citations, list) or not citations or len(citations) > 12 or any(url not in urls for url in citations):
                raise ValueError("every section needs known source citations")
            if any(url not in content for url in citations):
                raise ValueError("document omits cited source URLs")
            body = self._normalized(scoped_blocks[section["heading"]])
            # Citation markers and fact/inference labels can interrupt a flat
            # summary. Require its actual meaningful text in the matching block.
            snippets = []
            for key in ("facts", "inferences"):
                values = section.get(key, [])
                if not isinstance(values, list) or len(values) > 40:
                    raise ValueError("invalid structured synthesis")
                for value in values:
                    snippets.append(text(value.get("text") if isinstance(value, dict) else value, "synthesis statement", 10000))
            if snippets:
                if any(self._normalized(snippet) not in body for snippet in snippets):
                    raise ValueError("document omits section-specific synthesis statements")
            elif len(set(words) & set(body.split())) / max(1, len(set(words))) < .90:
                raise ValueError("document omits the scoped synthesis")
        return {"filename": filename, "media_type": "text/markdown", "content": content,
                "checks": {"scope_sections": True, "citations": True, "reviewed_synthesis": True,
                           "document_integrity": True, "source_count": len(urls)},
                "sources": bibliography, "artifact": {key: record[key] for key in
                    ("path", "sha256", "size_bytes", "media_type") if key in record}}

    @staticmethod
    def _normalized(value):
        value = re.sub(r"\[(?:E|S)\d+\]", " ", value)
        return " ".join(re.findall(r"[^\W_]+", value.casefold()))

    @staticmethod
    def _heading_terms(value):
        aliases = {"costs": "cost", "pricing": "cost", "price": "cost", "fees": "cost",
                   "risks": "risk", "limitations": "risk", "limitation": "risk", "caution": "risk",
                   "eligibility": "eligible", "grants": "grant", "competitors": "competition"}
        ignored = {"overview", "summary", "findings", "conclusion", "and", "the", "for", "of"}
        return {aliases.get(word, word) for word in ResearchBrief._normalized(value).split() if word not in ignored}

    @staticmethod
    def _filler(value):
        return bool(re.search(r"\b(?:lorem ipsum|placeholder|todo|tbd|summary goes here|insert (?:text|summary)|not an inferred synthesis|source excerpts)\b", value, re.I))
