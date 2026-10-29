"""Deterministic research scope and provenance checks; no model or network calls.

Lexical relevance and potential conflicts are review aids, not verification of
publisher claims. Source text is untrusted data and never supplies instructions.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .contracts import ToolError


_STOP = set("a an and are as at be been by can could do does for from had has have how i in into is it its may of on or our should than that the their these they this to us was we were what when where which who why will with would your about available evidence research report overview summary findings conclusion customer context requested request small new use using compare comparison include provide assess cover discuss check estimate evaluate calculate explain identify list address account consider ensure must only please state".split())
_GROUPS = (
    "price pricing cost costs fee fees budget subscription paid payment",
    "implementation implement implementing setup integration integrate deployment deploy installation install",
    "compliance compliant gdpr privacy security legal regulation regulatory consent",
    "benefit benefits efficiency efficient productivity savings saving advantage advantages",
    "risk risks limitation limitations disadvantage disadvantages drawback drawbacks",
    "market markets demand adoption sector survey trend trends growth",
    "appointment appointments booking bookings scheduling schedule scheduler",
    "dental dentist dentists dentistry", "veterinary veterinarian veterinarians vet vets",
    "bicycle bicycles bike bikes cycling", "solar photovoltaic photovoltaics pv",
)
_ALIASES = {word: group.split()[0] for group in _GROUPS for word in group.split()}
_NUMBERS = re.compile(r"(?<!\w)(?:[$€£]\s*)?\d+(?:[,.]\d+)*(?:\s*(?:%|percent|million|billion|thousand|USD|EUR|GBP))?", re.I)
_NEGATIVE = re.compile(r"\b(?:no|not|never|cannot|unavailable|prohibited|forbidden|unsupported|disallow(?:ed|s)?)\b|\bcan['’]t\b", re.I)
_POSITIVE = re.compile(r"\b(?:available|allowed|permitted|supported|supports|enabled|eligible|can|does|is|are)\b", re.I)
MAX_CONFLICTS = 24


def _error(message):
    raise ToolError("invalid_input", message)


def _text(value, name, maximum, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum:
        _error(f"{name} must be bounded text.")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        _error(f"{name} must contain valid Unicode.")
    if not empty and not value.strip():
        _error(f"{name} must be nonempty text.")
    return value.strip()


def _integer(value, name, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        _error(f"{name} must be an integer from {lower} to {upper}.")
    return value


def _date(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _error(f"{name} must be an ISO calendar date (YYYY-MM-DD).")
    try:
        return date.fromisoformat(value)
    except ValueError:
        _error(f"{name} must be a valid ISO calendar date.")


def _tokens(value):
    words = re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold())
    result = set()
    for word in words:
        if word in _STOP or word.isdecimal() or len(word) < 2:
            continue
        if word in _ALIASES:
            word = _ALIASES[word]
        elif len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us")):
            word = word[:-1]
        result.add(word)
    if re.search(r"[$€£]|\b(?:USD|EUR|GBP)\b", value):
        result.add("price")
    return result


def make_plan(arguments, settings):
    """Freeze explicit customer scope without a hidden paid planning request."""
    if not isinstance(arguments, dict) or not isinstance(settings, dict):
        _error("Research arguments and settings must be objects.")
    if set(arguments) - {"topic", "sections", "min_sources", "context", "requirements", "as_of", "max_age_days"}:
        _error("Research scope contains unsupported fields.")
    topic = _text(arguments.get("topic"), "topic", 500)
    context = _text(arguments.get("context", ""), "context", 2000, empty=True)
    headings = arguments.get("sections")
    if not isinstance(headings, list) or not 1 <= len(headings) <= 8:
        _error("Research requires 1–8 section headings.")
    headings = [_text(item, "section heading", 100) for item in headings]
    if len({item.casefold() for item in headings}) != len(headings):
        _error("Research section headings must be distinct.")
    maximum = _integer(settings.get("max_sources", 12), "max_sources", 1, 12)
    minimum = _integer(arguments.get("min_sources", 2), "min_sources", 1, maximum)
    as_of = _date(arguments.get("as_of", settings.get("as_of", datetime.now(timezone.utc).date().isoformat())), "as_of").isoformat()
    max_age = arguments.get("max_age_days", settings.get("max_age_days"))
    if max_age is not None:
        max_age = _integer(max_age, "max_age_days", 1, 3650)
    sections = []
    for index, heading in enumerate(headings, 1):
        question = f"What does the evidence show about {heading} for {topic}?"
        if context:
            question += f" Apply the customer's context: {context}"
        sections.append({"id": f"q{index}", "heading": heading, "question": question})
    requested = arguments.get("requirements", [])
    if not isinstance(requested, list) or len(requested) > 20:
        _error("requirements must be a list of at most 20 customer instructions.")
    requested = [_text(item, "requirement", 1000) for item in requested]
    requirements = []
    for index, instruction in enumerate(requested, 1):
        terms = _tokens(instruction)
        linked = [item["id"] for item in sections if terms & _tokens(item["heading"])]
        requirements.append({"id": f"r{index}", "text": instruction,
                             "section_ids": linked or [item["id"] for item in sections]})
    if not requirements:
        for index, section in enumerate(sections, 1):
            instruction = f"Address {section['heading']} for {topic}."
            if context:
                instruction += f" Customer context: {context}"
            requirements.append({"id": f"r{index}", "text": instruction, "section_ids": [section["id"]]})
    queries = []
    for section in sections:
        # Search accepts at most 400 printable characters. Keep room for the
        # section and customer context even when the topic reaches its limit;
        # multiline intake text must not become invalid HTTP search input.
        def fragment(value, maximum):
            return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", value).split())[:maximum]
        query = " ".join(filter(None, [fragment(topic, 200), fragment(section["heading"], 100),
                                       fragment(context, 98)]))
        if query not in queries:
            queries.append(query)
    return {"version": 1, "topic": topic, "context": context, "as_of": as_of,
            "min_sources": minimum, "max_age_days": max_age, "sections": sections,
            "requirements": requirements, "queries": queries}


def _canonical_url(value):
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\x00-\x20\x7f]", value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            return None
        host = parsed.hostname.casefold().encode("idna").decode("ascii")
        port = parsed.port
        if ":" in host:
            host = "[" + host + "]"
        if port and (parsed.scheme.lower(), port) not in (("https", 443), ("http", 80)):
            host += ":" + str(port)
        path = parsed.path or "/"
        path = path.rstrip("/") or "/"
        query = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=100)
                 if not key.casefold().startswith("utm_") and key.casefold() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}]
        return urlunsplit((parsed.scheme.lower(), host, path, urlencode(sorted(query)), ""))
    except (ValueError, UnicodeError):
        return None


def _fingerprint(text):
    normalized = " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold()))
    words = normalized.split()
    # Critical factual changes must survive deduplication for conflict review.
    numbers = tuple(match.group().casefold().replace(" ", "") for match in _NUMBERS.finditer(text))
    polarity = bool(_NEGATIVE.search(text))
    shingles = {tuple(words[index:index + 4]) for index in range(max(0, len(words) - 3))}
    return normalized, set(words), shingles, numbers, polarity


def _near_duplicate(first, second):
    if first[3:] != second[3:]:
        return False
    if first[0] == second[0]:
        return True
    words_a, words_b = first[1], second[1]
    if min(len(words_a), len(words_b)) < 8:
        return False
    word_ratio = len(words_a & words_b) / max(1, len(words_a | words_b))
    shingles_a, shingles_b = first[2], second[2]
    shingle_ratio = len(shingles_a & shingles_b) / max(1, len(shingles_a | shingles_b))
    return word_ratio >= .90 or shingle_ratio >= .78


def _pieces(text):
    """Yield contiguous, trimmed source spans, retaining literal source offsets."""
    previous = 0
    boundaries = [match.end() for match in re.finditer(r"\n+|[.!?](?=\s|$)", text)]
    if not boundaries or boundaries[-1] != len(text):
        boundaries.append(len(text))
    for end in boundaries:
        start, previous = previous, end
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        while start < end:
            stop = min(end, start + 700)
            if stop < end:
                boundary = text.rfind(" ", start + 350, stop)
                if boundary > start:
                    stop = boundary
            trimmed = stop
            while trimmed > start and text[trimmed - 1].isspace():
                trimmed -= 1
            if trimmed > start:
                yield start, trimmed, text[start:trimmed]
            start = stop
            while start < end and text[start].isspace():
                start += 1


def _date_status(record, as_of, max_age):
    supplied = record.get("published_date")
    if supplied is None or supplied == "":
        return None, "unknown"
    try:
        published = _date(supplied, "published_date")
    except ToolError:
        return None, "unknown"
    if published > as_of:
        return published.isoformat(), "future"
    if max_age is not None and (as_of - published).days > max_age:
        return published.isoformat(), "stale"
    return published.isoformat(), "current"


def _conflicts(spans):
    conflicts = []
    # Only compare different publishers' short, similarly worded claims about
    # the same question. Flag potential discrepancies; never infer a winner.
    for index, first in enumerate(spans):
        for second in spans[index + 1:]:
            if first["source_id"] == second["source_id"] or not set(first["question_ids"]) & set(second["question_ids"]):
                continue
            tokens_a, tokens_b = _tokens(first["text"]), _tokens(second["text"])
            similarity = len(tokens_a & tokens_b) / max(1, min(len(tokens_a), len(tokens_b)))
            if len(tokens_a & tokens_b) < 3 or similarity < .65:
                continue
            numbers_a = [re.sub(r"\s+", "", item.group().casefold()) for item in _NUMBERS.finditer(first["text"])]
            numbers_b = [re.sub(r"\s+", "", item.group().casefold()) for item in _NUMBERS.finditer(second["text"])]
            numerical = bool(numbers_a and numbers_b and numbers_a != numbers_b and len(numbers_a) == len(numbers_b))
            polarity = bool(_NEGATIVE.search(first["text"])) != bool(_NEGATIVE.search(second["text"]))
            polarity = polarity and bool(_POSITIVE.search(first["text"]) or _POSITIVE.search(second["text"]))
            if numerical or polarity:
                kind = "numerical values" if numerical else "polarity"
                conflicts.append({"id": f"C{len(conflicts) + 1}", "evidence_ids": [first["id"], second["id"]],
                                  "description": f"Potential {kind} discrepancy in similarly scoped claims; compare dates, units and qualifications before drawing a conclusion."})
                if len(conflicts) >= MAX_CONFLICTS:
                    return conflicts
    return conflicts


def evaluate_sources(plan, fetched, settings):
    """Return bounded literal evidence and explicit insufficiency, never synthesis."""
    if not isinstance(plan, dict) or not isinstance(settings, dict) or not isinstance(fetched, list) or len(fetched) > 100:
        _error("Supply a frozen research plan, settings and at most 100 fetched records.")
    # Validate the scope without modifying the caller's frozen plan.
    try:
        checked = make_plan({"topic": plan["topic"], "context": plan.get("context", ""), "as_of": plan["as_of"],
                             "min_sources": plan["min_sources"], "max_age_days": plan.get("max_age_days"),
                             "sections": [item["heading"] for item in plan["sections"]],
                             "requirements": []}, settings)
    except (KeyError, TypeError):
        _error("Frozen research plan is malformed.")
    if not isinstance(plan["sections"], list) or [item.get("id") for item in plan["sections"]] != [item["id"] for item in checked["sections"]]:
        _error("Frozen research question IDs must be ordered q1, q2, and so on.")
    maximum = _integer(settings.get("max_sources", 12), "max_sources", 1, 12)
    max_chars = _integer(settings.get("max_source_chars", 12_000), "max_source_chars", 1, 60_000)
    as_of = _date(checked["as_of"], "as_of")
    topic_tokens = _tokens(checked["topic"])
    if not topic_tokens:
        _error("Topic must contain meaningful searchable terms.")
    topic_required = 1 if len(topic_tokens) <= 2 else 2
    questions = {item["id"]: _tokens(item["heading"]) for item in checked["sections"]}
    sources, spans, rejected, issues = [], [], [], []
    coverage = {question: [] for question in questions}
    seen_urls, fingerprints = set(), []
    for record in fetched:
        if not isinstance(record, dict):
            _error("Each fetched source must be an object.")
        url = record.get("url")
        canonical = _canonical_url(url)
        def reject(reason):
            rejected.append({"url": url[:2048] if isinstance(url, str) else "", "reason": reason})
        if canonical is None:
            reject("invalid_url")
            continue
        if canonical in seen_urls:
            reject("duplicate_url")
            continue
        seen_urls.add(canonical)
        raw = record.get("text")
        if not isinstance(raw, str) or not raw.strip():
            reject("empty_text")
            continue
        # Only this literal prefix is retained or scanned; offsets refer to it.
        content = raw[:max_chars]
        try:
            encoded = content.encode("utf-8", errors="strict")
        except UnicodeError:
            reject("invalid_text")
            continue
        title = record.get("title") or canonical
        title = title[:500] if isinstance(title, str) else canonical
        fingerprint = _fingerprint(content)
        duplicate = next((old for old in fingerprints if old[0] == fingerprint[0]), None)
        if duplicate is not None:
            reject("duplicate_content")
            continue
        if any(_near_duplicate(fingerprint, old) for old in fingerprints):
            reject("near_duplicate")
            continue
        body_terms = _tokens(content)
        terms = _tokens(title) | body_terms
        topic_matches = sorted(topic_tokens & terms)
        if len(topic_matches) < topic_required or len(topic_tokens & body_terms) < topic_required:
            reject("unrelated_topic")
            continue
        source_questions = [key for key, wanted in questions.items() if not wanted or wanted & terms]
        if not source_questions:
            reject("unrelated_questions")
            continue
        candidates = []
        for start, end, piece in _pieces(content):
            piece_tokens = _tokens(piece)
            linked = [key for key, wanted in questions.items() if (wanted & piece_tokens if wanted else topic_tokens & piece_tokens)]
            if linked:
                score = sum(len(questions[key] & piece_tokens) for key in linked) * 3 + len(topic_tokens & piece_tokens)
                candidates.append((score, start, end, piece, linked))
        if not candidates:
            reject("no_relevant_evidence_spans")
            continue
        if len(sources) >= maximum:
            reject("source_limit")
            continue
        fingerprints.append(fingerprint)
        selected = sorted(sorted(candidates, key=lambda item: (-item[0], item[1]))[:6], key=lambda item: item[1])
        source_id = f"S{len(sources) + 1}"
        published, status = _date_status(record, as_of, checked["max_age_days"])
        retrieved = record.get("retrieved_at")
        if retrieved is not None:
            try:
                if not isinstance(retrieved, str) or len(retrieved) > 64:
                    raise ValueError()
                datetime.fromisoformat(retrieved.replace("Z", "+00:00"))
            except ValueError:
                retrieved = None
        # Hash the retained actual text. A caller's hash may refer to HTML or
        # another representation and cannot prove identity of these spans.
        digest = hashlib.sha256(encoded).hexdigest()
        source = {"id": source_id, "url": canonical, "title": title, "excerpt": selected[0][3], "text": content,
                  "published_date": published, "date_status": status, "retrieved_at": retrieved,
                  "content_sha256": digest.lower(),
                  "relevance": {"topic_matches": topic_matches, "question_ids": source_questions,
                                "topic_score": round(len(topic_matches) / len(topic_tokens), 3)}}
        if isinstance(record.get("date_basis"), str):
            source["date_basis"] = record["date_basis"][:300]
        sources.append(source)
        if len(raw) > max_chars:
            issues.append(f"{source_id}: source text truncated to {max_chars} characters; evidence offsets refer to the retained text.")
        eligible = status not in {"future", "stale"} and not (checked["max_age_days"] is not None and status == "unknown")
        if status != "current":
            issues.append(f"{source_id}: publication date is {status}; " + ("excluded from current evidence coverage." if not eligible else "publication freshness cannot be verified."))
        for _, start, end, piece, linked in selected:
            evidence_id = f"E{len(spans) + 1}"
            spans.append({"id": evidence_id, "source_id": source_id, "text": piece, "start": start, "end": end, "question_ids": linked})
            if eligible:
                for question in linked:
                    coverage[question].append(evidence_id)
    conflicts = _conflicts(spans)
    eligible_ids = {span["source_id"] for span in spans if any(span["id"] in values for values in coverage.values())}
    if len(eligible_ids) < checked["min_sources"]:
        issues.append(f"Insufficient distinct relevant current sources: {len(eligible_ids)} of {checked['min_sources']} required.")
    missing = [key for key, value in coverage.items() if not value]
    if missing:
        issues.append("No eligible evidence covers customer questions: " + ", ".join(missing) + ".")
    requirements = plan.get("requirements", [])
    if not isinstance(requirements, list) or len(requirements) > 20:
        _error("Frozen plan requirements must be a bounded list.")
    unmet_requirements = []
    for index, requirement in enumerate(requirements, 1):
        if not isinstance(requirement, dict) or requirement.get("id") != f"r{index}":
            _error("Frozen requirement IDs must be ordered r1, r2, and so on.")
        instruction = _text(requirement.get("text"), "frozen requirement", 4000)
        linked = requirement.get("section_ids")
        if not isinstance(linked, list) or not linked or len(linked) > 8 or any(key not in questions for key in linked):
            _error("Frozen requirements must link to valid research questions.")
        scope_terms = _tokens(instruction) - topic_tokens - _tokens(checked["context"])
        for question in linked:
            scope_terms -= questions[question]
        eligible_spans = [span for span in spans if any(span["id"] in coverage[key] for key in linked)]
        observed = set().union(*(_tokens(span["text"]) for span in eligible_spans)) if eligible_spans else set()
        required_matches = min(2, len(scope_terms))
        if not eligible_spans or len(scope_terms & observed) < required_matches:
            unmet_requirements.append(requirement["id"])
    if unmet_requirements:
        issues.append("Customer checklist lacks specific eligible evidence for requirements: " + ", ".join(unmet_requirements) + ".")
    if conflicts:
        issues.append("Potential conflicting source claims require explicit discussion of uncertainty in the synthesis; do not silently choose a winner.")
    return {"sources": sources, "spans": spans, "conflicts": conflicts, "rejected": rejected,
            "coverage": coverage, "sufficient": len(eligible_ids) >= checked["min_sources"] and not missing and not unmet_requirements,
            "issues": issues}
