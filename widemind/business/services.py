"""An extensible service registry. Research is one service, not a business constraint."""
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
        minimum = integer(scope.get("min_sources", 2), "minimum sources", 1, 10)
        if allowance < 1:
            raise ValueError("research requires a positive maximum cost")
        arguments = {"topic": topic, "sections": headings, "min_sources": minimum}
        if "source_urls" in scope:
            urls = scope["source_urls"]
            if not isinstance(urls, list) or not minimum <= len(urls) <= 10:
                raise ValueError("source_urls must meet the agreed minimum source count (up to 10)")
            arguments["source_urls"] = [web_url(url) for url in urls]
            if len(set(arguments["source_urls"])) != len(urls):
                raise ValueError("source URLs must be unique")
        return Plan("research.collect", arguments, allowance)

    def deliverable(self, scope, result):
        # A gateway result supplies sourced statements; business does not invent research.
        self.plan(scope, 1)
        sources = result.get("sources")
        if not isinstance(sources, list) or not scope.get("min_sources", 2) <= len(sources) <= 30:
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
            bibliography.append({"url": url, "title": title, "excerpt": excerpt})
        sections = result.get("sections")
        if not isinstance(sections, list) or [s.get("heading") for s in sections] != scope["sections"]:
            raise ValueError("sections do not match agreed scope")
        lines = [f"# {scope['topic']}", "", "Sourced research brief. Source summaries require human judgment.", ""]
        for section in sections:
            summary = text(section.get("text"), "section text", 20000)
            citations = section.get("source_urls")
            if not isinstance(citations, list) or not citations or any(url not in urls for url in citations):
                raise ValueError("every section needs known source citations")
            lines.extend([f"## {section['heading']}", "", summary, "", *[f"Source: {url}" for url in citations], ""])
        lines.extend(["## Sources", ""])
        for source in bibliography:
            lines.extend([source["title"], source["url"], f"Excerpt: {source['excerpt']}", ""])
        return {"filename": "research-brief.md", "media_type": "text/markdown",
                "content": "\n".join(lines), "checks": {"scope_sections": True, "citations": True,
                "source_count": len(urls)}, "sources": bibliography}
