"""Wenqu-owned, one-shot multi-engine candidate discovery.

The module deliberately implements only search. Fetching and browser rendering
live in :mod:`wenqu.crawler`, so no Node daemon or open-websearch executable is
part of Wenqu's runtime contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import os
from typing import Callable, Iterable
from urllib.parse import parse_qs, quote_plus, unquote, urlparse, urlunparse

from bs4 import BeautifulSoup
import httpx

from .models import EngineFailure, EngineSkip, SearchEnvelope, SearchResult


ENGINE_NAMES = (
    "baidu", "bing", "linuxdo", "csdn", "duckduckgo", "exa", "brave", "juejin", "sogou",
)
RECOVERABLE_ENGINES = frozenset({"baidu", "bing", "brave", "sogou"})
ENGINE_ALIASES = {"sougou": "sogou", "搜狗": "sogou"}
USER_AGENT = "Mozilla/5.0 (compatible; WenquLibrary/0.1; +https://github.com/gogoingai/wenqu-cli)"
ACCESS_CHALLENGE_MARKERS = {
    "baidu": ("百度安全验证", "安全验证"),
}


class EngineError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RequestOptions:
    proxy_url: str | None = None
    exa_api_key: str | None = None
    timeout_seconds: float = 15.0


def normalise_engines(raw: str | Iterable[str] | None) -> tuple[str, ...]:
    if raw is None:
        return ENGINE_NAMES
    values = raw.split(",") if isinstance(raw, str) else raw
    selected: list[str] = []
    for value in values:
        engine = ENGINE_ALIASES.get(value.strip().lower(), value.strip().lower())
        if engine not in ENGINE_NAMES:
            raise ValueError(f"Unknown search engine: {value}. Supported engines: {', '.join(ENGINE_NAMES)}.")
        if engine not in selected:
            selected.append(engine)
    if not selected:
        raise ValueError("At least one search engine is required.")
    return tuple(selected)


def search(
    query: str,
    *,
    engines: str | Iterable[str] | None = None,
    limit: int = 10,
    options: RequestOptions | None = None,
    browser_fallback: bool = True,
    browser_fetcher: Callable[[str], str] | None = None,
) -> SearchEnvelope:
    if not query.strip():
        raise ValueError("Search query must not be empty.")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    selected = normalise_engines(engines)
    supplied = options or RequestOptions()
    request_options = RequestOptions(
        proxy_url=supplied.proxy_url,
        exa_api_key=supplied.exa_api_key or os.getenv("EXA_API_KEY"),
        timeout_seconds=supplied.timeout_seconds,
    )
    runnable, skipped = _preflight_engines(selected, request_options)
    if not runnable:
        return SearchEnvelope(query=query, engines=selected, results=(), partial_failures=(), skipped_engines=tuple(skipped))
    per_engine = max(1, (limit + len(runnable) - 1) // len(runnable))
    results: list[SearchResult] = []
    failures: list[EngineFailure] = []
    client_args: dict[str, object] = {"follow_redirects": True, "timeout": request_options.timeout_seconds, "headers": {"User-Agent": USER_AGENT}}
    if request_options.proxy_url:
        client_args["proxy"] = request_options.proxy_url
    with httpx.Client(**client_args) as client:
        for engine in runnable:
            try:
                found = _search_engine(engine, query, per_engine, client, request_options)
            except (httpx.HTTPError, EngineError, ValueError) as error:
                code = error.code if isinstance(error, EngineError) else "REQUEST_FAILED"
                message = str(error)
                found = []
                if browser_fallback and engine in RECOVERABLE_ENGINES:
                    try:
                        found = _search_with_browser(engine, query, per_engine, browser_fetcher)
                    except Exception as browser_error:  # browser setup is external and may fail independently
                        if isinstance(browser_error, EngineError) and browser_error.code == "ACCESS_CHALLENGE":
                            message = f"{message}; Crawl4AI browser fallback received the same verification page."
                        else:
                            message = f"{message}; browser recovery failed: {browser_error}"
                            code = "BROWSER_RECOVERY_FAILED"
                if not found:
                    failures.append(EngineFailure(engine, code, message))
            else:
                if not found and browser_fallback and engine in RECOVERABLE_ENGINES:
                    try:
                        found = _search_with_browser(engine, query, per_engine, browser_fetcher)
                    except Exception as error:  # browser recovery never blocks remaining engines
                        code = error.code if isinstance(error, EngineError) else "BROWSER_RECOVERY_FAILED"
                        failures.append(EngineFailure(engine, code, str(error)))
            results.extend(found)
    return SearchEnvelope(
        query=query,
        engines=selected,
        results=tuple(_dedupe(results)[:limit]),
        partial_failures=tuple(failures),
        skipped_engines=tuple(skipped),
    )


def _preflight_engines(engines: tuple[str, ...], options: RequestOptions) -> tuple[tuple[str, ...], list[EngineSkip]]:
    runnable: list[str] = []
    skipped: list[EngineSkip] = []
    for engine in engines:
        if engine == "exa" and not options.exa_api_key:
            skipped.append(
                EngineSkip(
                    engine="exa",
                    reason="EXA_API_KEY is not configured.",
                    configure="Run `wenqu config init`, then set EXA_API_KEY in the credentials file; or set it in the command environment.",
                )
            )
            continue
        runnable.append(engine)
    return tuple(runnable), skipped


def _search_engine(engine: str, query: str, limit: int, client: httpx.Client, options: RequestOptions) -> list[SearchResult]:
    if engine == "juejin":
        response = client.post(
            "https://api.juejin.cn/search_api/v1/search",
            params={"aid": "2608", "uuid": ""},
            json={"key_word": query, "search_type": 0, "cursor": "0", "limit": limit},
        )
        response.raise_for_status()
        return _parse_juejin(response.json(), limit)
    if engine == "linuxdo":
        return _search_linuxdo(query, limit, client, options)
    if engine == "csdn":
        return _search_csdn(query, limit, client, options)
    if engine == "exa":
        if not options.exa_api_key:
            raise EngineError("CREDENTIAL_REQUIRED", "EXA_API_KEY is required for the exa engine.")
        response = client.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": options.exa_api_key},
            json={"query": query, "numResults": limit, "contents": {"highlights": {"maxCharacters": 500}}},
        )
        response.raise_for_status()
        return _parse_exa(response.json(), limit)

    url, params = _web_request(engine, query)
    response = client.get(url, params=params)
    response.raise_for_status()
    _raise_if_access_challenge(engine, response.text, str(response.url))
    return _parse_html(engine, response.text, limit, redirect_resolver=lambda url: _resolve_redirect(client, url))


def _search_linuxdo(query: str, limit: int, client: httpx.Client, options: RequestOptions) -> list[SearchResult]:
    """Discover public LinuxDo topics through supported web indexes.

    LinuxDo's own unauthenticated search API currently returns a Cloudflare
    challenge. Its documented Wenqu path is therefore delegated, not retried.
    """
    return _search_site_delegated("linuxdo", "site:linux.do", query, limit, client, options, ("duckduckgo", "bing", "brave"))


def _search_csdn(query: str, limit: int, client: httpx.Client, options: RequestOptions) -> list[SearchResult]:
    """Discover public CSDN material through web indexes, not CSDN's protected search endpoint."""
    attempts: list[str] = []
    for delegate in ("duckduckgo", "baidu", "bing", "sogou", "brave"):
        try:
            found = _search_engine(delegate, f"{query} CSDN", limit, client, options)
        except (httpx.HTTPError, EngineError) as error:
            attempts.append(f"{delegate}: {error}")
            continue
        candidates = [item for item in found if urlparse(item.url).netloc.endswith("csdn.net")]
        if candidates:
            return [SearchResult(item.title, item.url, item.description, "csdn", f"delegate:{delegate}") for item in candidates]
    detail = "; ".join(attempts) if attempts else "delegated engines returned no CSDN candidates"
    raise EngineError("DELEGATE_FAILED", f"csdn search delegation failed: {detail}")


def _search_site_delegated(
    source: str,
    site_filter: str,
    query: str,
    limit: int,
    client: httpx.Client,
    options: RequestOptions,
    delegates: tuple[str, ...],
) -> list[SearchResult]:
    attempts: list[str] = []
    for delegate in delegates:
        try:
            found = _search_engine(delegate, f"{site_filter} {query}", limit, client, options)
        except (httpx.HTTPError, EngineError) as error:
            attempts.append(f"{delegate}: {error}")
            continue
        if found:
            return [SearchResult(item.title, item.url, item.description, source, f"delegate:{delegate}") for item in found]
    detail = "; ".join(attempts) if attempts else "delegated engines returned no candidates"
    raise EngineError("DELEGATE_FAILED", f"{source} search delegation failed: {detail}")


def _web_request(engine: str, query: str) -> tuple[str, dict[str, str]]:
    requests = {
        "baidu": ("https://www.baidu.com/s", {"wd": query}),
        "bing": ("https://www.bing.com/search", {"q": query}),
        "duckduckgo": ("https://html.duckduckgo.com/html/", {"q": query}),
        "brave": ("https://search.brave.com/search", {"q": query}),
        "sogou": ("https://www.sogou.com/web", {"query": query}),
    }
    try:
        return requests[engine]
    except KeyError as error:
        raise EngineError("NOT_IMPLEMENTED", f"No direct request adapter for {engine}") from error


def _parse_html(
    engine: str,
    html: str,
    limit: int,
    *,
    channel: str = "direct",
    redirect_resolver: Callable[[str], str | None] | None = None,
) -> list[SearchResult]:
    soup = BeautifulSoup(html, "html.parser")
    selectors: dict[str, tuple[str, str]] = {
        "baidu": ("h3 a", "div"),
        "bing": ("li.b_algo h2 a", "li.b_algo"),
        "duckduckgo": (".result__a", ".result"),
        "brave": ("#results a[href], .snippet a[href]", ".snippet, .result"),
        "sogou": (".vr-title a, h3 a", ".vrwrap, .results"),
    }
    link_selector, container_selector = selectors[engine]
    results: list[SearchResult] = []
    for link in soup.select(link_selector):
        title = link.get_text(" ", strip=True)
        href = str(link.get("href") or "")
        if not title or not href:
            continue
        url = _clean_result_url(engine, href)
        if not url and engine == "baidu" and redirect_resolver:
            try:
                url = redirect_resolver(href)
            except httpx.HTTPError:
                continue
        if not url or (engine == "baidu" and _is_baidu_wrapper(url)):
            continue
        container = link.select_one(container_selector) or link.parent
        description = container.get_text(" ", strip=True) if container else ""
        results.append(SearchResult(title=title, url=url, description=description, engine=engine, channel=channel))
        if len(results) >= limit:
            break
    return results


def _raise_if_access_challenge(engine: str, html: str, response_url: str = "") -> None:
    lowered = f"{html}\n{response_url}".lower()
    markers = ACCESS_CHALLENGE_MARKERS.get(engine, ())
    if any(marker.lower() in lowered for marker in markers):
        raise EngineError("ACCESS_CHALLENGE", f"{engine} returned an access-verification page; no candidates were parsed.")


def _clean_result_url(engine: str, href: str) -> str | None:
    if href.startswith("//"):
        href = f"https:{href}"
    if not href.startswith(("http://", "https://")):
        return None
    parsed = urlparse(href)
    if engine == "duckduckgo" and parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
        target = parse_qs(parsed.query).get("uddg", [None])[0]
        if target and target.startswith(("http://", "https://")):
            return target
        return None
    if engine == "bing" and "bing.com" in parsed.netloc and "u=" in parsed.query:
        for pair in parsed.query.split("&"):
            if pair.startswith("u=a1"):
                try:
                    return base64.b64decode(unquote(pair[2:])[2:] + "===").decode("utf-8")
                except Exception:
                    return None
    if engine == "sogou":
        for key in ("pcurl=", "url="):
            if key in parsed.query:
                value = parsed.query.split(key, 1)[1].split("&", 1)[0]
                candidate = unquote(value)
                if candidate.startswith(("http://", "https://")):
                    return candidate
    if engine == "baidu" and _is_baidu_wrapper(href):
        return None
    return href


def _is_baidu_wrapper(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.endswith("baidu.com") and (parsed.path.startswith("/link") or parsed.path == "/baidu.php")


def _resolve_redirect(client: httpx.Client, url: str) -> str | None:
    """Resolve search-engine wrappers before candidates enter Wenqu's index."""
    response = client.get(url)
    response.raise_for_status()
    final_url = str(response.url)
    parsed = urlparse(final_url)
    return final_url if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _parse_juejin(payload: object, limit: int) -> list[SearchResult]:
    if not isinstance(payload, dict) or payload.get("err_no") not in (0, None):
        raise EngineError("INVALID_RESPONSE", "Juejin returned an error response.")
    rows = payload.get("data", [])
    results: list[SearchResult] = []
    if not isinstance(rows, list):
        return results
    for row in rows:
        article = row.get("result_model", {}).get("article_info", {}) if isinstance(row, dict) else {}
        if not isinstance(article, dict):
            continue
        article_id = article.get("article_id")
        title = article.get("title")
        if not isinstance(article_id, str) or not isinstance(title, str):
            continue
        results.append(SearchResult(title, f"https://juejin.cn/post/{article_id}", str(article.get("brief_content") or ""), "juejin"))
        if len(results) >= limit:
            break
    return results


def _parse_linuxdo(payload: object, limit: int) -> list[SearchResult]:
    if not isinstance(payload, dict):
        raise EngineError("INVALID_RESPONSE", "LinuxDo returned an invalid response.")
    topics = payload.get("topics", [])
    if not isinstance(topics, list):
        return []
    results: list[SearchResult] = []
    for topic in topics:
        if not isinstance(topic, dict) or not isinstance(topic.get("id"), int) or not isinstance(topic.get("title"), str):
            continue
        results.append(SearchResult(topic["title"], f"https://linux.do/t/{topic['id']}", str(topic.get("excerpt") or ""), "linuxdo"))
        if len(results) >= limit:
            break
    return results


def _parse_exa(payload: object, limit: int) -> list[SearchResult]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise EngineError("INVALID_RESPONSE", "Exa returned an invalid response.")
    results: list[SearchResult] = []
    for row in payload["results"]:
        if not isinstance(row, dict) or not isinstance(row.get("url"), str):
            continue
        highlights = row.get("highlights", [])
        description = " ".join(value for value in highlights if isinstance(value, str)) if isinstance(highlights, list) else ""
        results.append(SearchResult(str(row.get("title") or row["url"]), row["url"], description, "exa"))
        if len(results) >= limit:
            break
    return results


def _search_with_browser(engine: str, query: str, limit: int, fetcher: Callable[[str], str] | None) -> list[SearchResult]:
    if fetcher is None:
        from .crawler import crawl_page

        fetcher = lambda url: crawl_page(url).html
    url, params = _web_request(engine, query)
    suffix = "&".join(f"{quote_plus(key)}={quote_plus(value)}" for key, value in params.items())
    html = fetcher(f"{url}?{suffix}")
    _raise_if_access_challenge(engine, html)
    def resolve(url: str) -> str | None:
        with httpx.Client(follow_redirects=True, timeout=15.0, headers={"User-Agent": USER_AGENT}) as client:
            return _resolve_redirect(client, url)

    return _parse_html(engine, html, limit, channel="browser", redirect_resolver=resolve)


def _dedupe(results: Iterable[SearchResult]) -> list[SearchResult]:
    deduped: list[SearchResult] = []
    seen: set[str] = set()
    for result in results:
        parsed = urlparse(result.url)
        key = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped
