"""Owner-configured SearXNG/Brave HTTP search, with durable request receipts."""
from __future__ import annotations

import base64
from dataclasses import asdict
import hashlib
from html.parser import HTMLParser
import json
from typing import Any, Mapping
from urllib.parse import urlencode, urlsplit

from .contracts import Quote, Reconciliation, Secrets, ToolContext, ToolError, ToolResult, ToolSpec, Unavailable, Uncertain, Usage
from .research import URLPolicy, _integer, _origin, _page_date, source_reference


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _settings(config: Mapping[str, Any]) -> dict:
    provider = config.get("provider")
    if not isinstance(provider, str) or provider not in {"searxng", "brave"}:
        raise Unavailable("Configure research.search provider as searxng or brave.")
    endpoint = config.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        raise Unavailable("Configure the owner-hosted SearXNG /search endpoint or Brave /res/v1/web/search endpoint.")
    try:
        original = urlsplit(endpoint)
        if original.query or original.fragment:
            raise ValueError
        endpoint = URLPolicy(config).validate(endpoint)
        parts = urlsplit(endpoint)
        if parts.query or parts.fragment:
            raise ValueError
        if parts.scheme != "https" and not (
            config.get("allow_local_http") is True
            and _origin(endpoint) in URLPolicy(config).fixture_origins
        ):
            raise ValueError
    except (ToolError, ValueError):
        raise Unavailable("Search endpoint must be credential-free HTTPS; controlled HTTP fixtures require owner allow_local_http and exact trusted_fixture_origins.") from None
    allocation = config.get("allocation")
    if not isinstance(allocation, str) or not allocation.strip() or len(allocation) > 1000:
        raise Unavailable("Configure allocation to disclose owner compute/network subsidy or the fixed per-request tariff basis.")
    charge = config.get("request_microusd")
    if type(charge) is not int or not 0 <= charge <= 1_000_000_000:
        raise Unavailable("Configure an integer request_microusd maximum for each search request.")
    if provider == "searxng" and charge != 0:
        raise Unavailable("Owner-hosted SearXNG requires request_microusd:0 and an explicit subsidy allocation.")
    if provider == "brave" and charge <= 0:
        raise Unavailable("Brave requires a positive owner-certified fixed request_microusd tariff bound.")
    secret_ref = config.get("secret_ref")
    if (provider == "brave" or secret_ref is not None) and (
        not isinstance(secret_ref, str) or not secret_ref or len(secret_ref) > 128
    ):
        raise Unavailable("Configure a valid dedicated secret_ref in the owner secret store.")
    return {"provider": provider, "endpoint": endpoint, "allocation": allocation,
            "request_microusd": charge, "secret_ref": secret_ref,
            "max_results": _integer(config.get("max_results"), "configured max_results", 10, 1, 20),
            "max_response_bytes": _integer(config.get("max_response_bytes"), "max_response_bytes", 256_000, 1024, 512_000),
            "max_output_bytes": _integer(config.get("max_output_bytes"), "max_output_bytes", 128_000, 1024, 1_000_000),
            "request_timeout_seconds": _integer(config.get("request_timeout_seconds"), "request_timeout_seconds", 15, 1, 30),
            "trusted_fixture_origins": sorted(URLPolicy(config).fixture_origins) if config.get("allow_local_http") is True else []}


def _request(arguments: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[dict, str, dict]:
    settings = _settings(config)
    if not isinstance(arguments, Mapping) or set(arguments) - {"query", "max_results"}:
        raise ToolError("invalid_arguments", "Search accepts only query and max_results; credentials, endpoints and prices are owner configuration.")
    query = arguments.get("query")
    if (not isinstance(query, str) or not query.strip() or len(query) > 400
            or any(ord(c) < 32 or ord(c) == 127 for c in query)):
        raise ToolError("invalid_arguments", "query must contain 1–400 characters without control characters.")
    try:
        query.encode("utf-8")
    except UnicodeError:
        raise ToolError("invalid_arguments", "query must be valid Unicode.") from None
    count = _integer(arguments.get("max_results"), "max_results", min(5, settings["max_results"]), 1, settings["max_results"])
    params = {"q": query}
    if settings["provider"] == "searxng":
        params.update(format="json", categories="general", pageno=1)
    else:
        params.update(count=count, result_filter="web", text_decorations="false")
    url = settings["endpoint"] + "?" + urlencode(params)
    intent = {"provider": settings["provider"], "operation": "web_search", "endpoint": settings["endpoint"],
              "request_sha256": hashlib.sha256(url.encode()).hexdigest(), "max_results": count,
              "config_sha256": hashlib.sha256(_json(settings)).hexdigest(),
              "maximum_microusd": settings["request_microusd"]}
    return settings, url, intent


def _headers(settings: Mapping[str, Any], secrets: Secrets) -> dict:
    headers = {"accept": "application/json", "accept-encoding": "identity", "user-agent": "WideMind-Search/1.0"}
    if settings["secret_ref"] is not None:
        key = secrets.get(settings["secret_ref"])
        if not isinstance(key, str) or not key or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise Unavailable("Set the dedicated search credential named by secret_ref; live search is unavailable until configured.")
        if settings["provider"] == "brave":
            headers["x-subscription-token"] = key
        else:
            headers["authorization"] = "Bearer " + key
    return headers


class _Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.skip:
            self.skip -= 1
        if not self.skip:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _plain(value: Any, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    parser = _Plain()
    parser.feed(value[:12_000])
    parser.close()
    return " ".join("".join(parser.parts).split())[:maximum]


def _usage(settings: Mapping[str, Any], *, size: int = 0, request_id: str | None = None, unknown: bool = False) -> Usage:
    paid = settings["provider"] == "brave"
    return Usage(None if paid and unknown else settings["request_microusd"],
                 {"search_requests": 1, "response_bytes": size}, request_id, paid,
                 {"cost_basis": "owner_fixed_request_allocation" if paid else "owner_subsidy",
                  "allocation": settings["allocation"], "invoice_verified": False,
                  "dispatch_outcome": "unknown" if unknown else "response_received"})


class SearchAdapter:
    spec = ToolSpec("research.search", "Search through an owner-configured SearXNG or Brave endpoint; results remain untrusted.", "external")

    def quote(self, arguments: Mapping[str, Any], config: Mapping[str, Any]) -> Quote:
        settings, _, intent = _request(arguments, config)
        return Quote(settings["request_microusd"], settings["provider"], settings["provider"] == "brave",
                     {"max_requests": 1, "max_results": intent["max_results"],
                      "max_response_bytes": settings["max_response_bytes"], "max_output_bytes": settings["max_output_bytes"],
                      "cost_basis": "owner_fixed_request_allocation" if settings["provider"] == "brave" else "owner_subsidy"})

    def availability(self, config: Mapping[str, Any], secrets: Secrets) -> dict:
        try:
            settings = _settings(config)
            _headers(settings, secrets)
            return {"available": True, "provider": settings["provider"], "live_verified": False}
        except ToolError as exc:
            return {"available": False, "reason": exc.message}

    def _result(self, arguments: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        settings, url, intent = _request(arguments, ctx.config)
        state = ctx.load_state()
        if state.get("search_intent") != intent:
            raise ToolError("intent_mismatch", "Search arguments or owner configuration differ from the durable request intent.")
        receipt = state.get("search_response")
        usage = Usage(**state["search_usage"])
        if not isinstance(receipt, dict):
            raise Uncertain("No complete saved search response; reconcile the original request without retrying.", usage=usage)
        try:
            raw = base64.b64decode(receipt["body_base64"], validate=True)
            if len(raw) > settings["max_response_bytes"]:
                raise ValueError
            if receipt["url"] != url or not 200 <= receipt["status"] < 300:
                raise ValueError
            if receipt["content_type"].partition(";")[0].strip().lower() not in {"application/json", "application/ld+json"}:
                raise ValueError
            payload = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
            if not isinstance(payload, dict):
                raise ValueError
            candidates = payload.get("results") if settings["provider"] == "searxng" else payload.get("web", {}).get("results")
            if not isinstance(candidates, list):
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            raise Uncertain("Search response was saved but is unsuccessful or malformed; known request allocation remains visible.", usage=usage) from None
        policy = URLPolicy({"trusted_fixture_origins": settings["trusted_fixture_origins"]})
        evidence = source_reference(url, hashlib.sha256(raw).hexdigest(), kind="search_response")
        evidence["retrieved_at"] = state["search_response_meta"]["retrieved_at"]
        references = [evidence]
        results, seen = [], set()
        for candidate in candidates[:200]:
            ctx.check()
            if not isinstance(candidate, dict):
                continue
            try:
                result_url = policy.validate(candidate.get("url"))
            except ToolError:
                continue
            if result_url in seen:
                continue
            result = {"url": result_url, "title": _plain(candidate.get("title"), 512),
                      "snippet": _plain(candidate.get("content") if settings["provider"] == "searxng" else candidate.get("description"), 2000)}
            if not result["title"] and not result["snippet"]:
                continue
            date_value = candidate.get("publishedDate") if settings["provider"] == "searxng" else candidate.get("page_age")
            published = _page_date(date_value)
            if published:
                result.update(published_date=published, date_basis="searxng:publishedDate" if settings["provider"] == "searxng" else "brave:page_age_unverified")
            results.append(result)
            seen.add(result_url)
            reference = source_reference(result_url, hashlib.sha256(_json(result)).hexdigest(), kind="search_result")
            reference.update(retrieved_at=evidence["retrieved_at"], derived_from=evidence["source_id"])
            references.append(reference)
            if len(results) >= intent["max_results"]:
                break
        data = {"query": arguments["query"], "provider": settings["provider"], "results": results,
                "source_id": evidence["source_id"], "retrieved_at": evidence["retrieved_at"],
                "result_basis": "search_index_only; pages require separate authorized fetches"}
        if len(_json({"data": data, "references": references})) > settings["max_output_bytes"]:
            raise Uncertain("Saved search result exceeds the authorized output limit; known request allocation remains recorded.", usage=usage)
        ctx.check()
        return ToolResult(data=data, usage=usage, references=references)

    def execute(self, arguments: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        ctx.check()
        settings, url, intent = _request(arguments, ctx.config)
        if ctx.load_state().get("search_intent"):
            recovered = self.reconcile(arguments, ctx)
            if recovered.outcome == "completed" and recovered.result:
                return recovered.result
            raise Uncertain(recovered.detail, usage=recovered.result.usage if recovered.result else _usage(settings, unknown=True))
        headers = _headers(settings, ctx.secrets)
        unknown_usage = _usage(settings, unknown=True)
        ctx.save_state({"search_intent": intent, "search_usage": asdict(unknown_usage)})
        ctx.mark_dispatched(intent)
        try:
            ctx.check()
            response = ctx.transport.request("GET", url, headers=headers,
                                             timeout=ctx.remaining(settings["request_timeout_seconds"]),
                                             max_bytes=settings["max_response_bytes"], cancel=ctx.check, public_only=True)
        except Exception as exc:
            raise Uncertain("Search was dispatched without a complete response; the same action must not send another HTTP request.", usage=unknown_usage) from exc
        request_id = response.headers.get("x-request-id") or response.headers.get("x-brave-request-id")
        request_id = request_id[:256] if isinstance(request_id, str) else None
        usage = _usage(settings, size=len(response.body), request_id=request_id)
        metadata = source_reference(url, hashlib.sha256(response.body).hexdigest(), kind="search_response")
        try:
            # Known billing identity/usage precedes raw receipt storage. Neither
            # parse errors nor a failed journal write prove a request was free.
            ctx.save_state({"search_usage": asdict(usage), "search_response_meta": {
                "status": response.status, "request_id": request_id, "sha256": metadata["sha256"],
                "retrieved_at": metadata["retrieved_at"]}})
            if len(response.body) > settings["max_response_bytes"]:
                raise ToolError("output_limit", "Search response exceeded its byte allowance.")
            ctx.save_state({"search_response": {"status": response.status, "url": response.url,
                            "content_type": response.headers.get("content-type", ""),
                            "body_base64": base64.b64encode(response.body).decode("ascii")}})
            ctx.check()
            return self._result(arguments, ctx)
        except Exception as exc:
            raise Uncertain("Search response or output needs reconciliation from its saved receipt; no automatic retry is permitted.", usage=usage) from exc

    def reconcile(self, arguments: Mapping[str, Any], ctx: ToolContext) -> Reconciliation:
        ctx.check()
        state = ctx.load_state()
        if not state.get("search_intent"):
            return Reconciliation("uncertain", detail="No saved search request proves its outcome.")
        try:
            return Reconciliation("completed", self._result(arguments, ctx))
        except Exception as exc:
            saved = state.get("search_usage")
            usage = Usage(**saved) if isinstance(saved, dict) else getattr(exc, "usage", None)
            result = ToolResult({"receipt_status": "unresolved"}, usage) if usage is not None else None
            return Reconciliation("uncertain", result, detail="Saved search receipt cannot establish a usable completed result; retain original usage/reservation and never repeat the request.")
