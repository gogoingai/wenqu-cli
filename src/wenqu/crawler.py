"""Crawl4AI adapter kept behind Wenqu's own library interface."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
from urllib.parse import urljoin, urlparse


WECHAT_USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.43 NetType/WIFI Language/zh_CN"


class CrawlerUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class CrawledPage:
    url: str
    markdown: str
    html: str


async def _crawl_one(
    url: str,
    *,
    wait_for: str | None = None,
    delay_seconds: float | None = None,
    javascript: str | None = None,
    wechat_headers: bool = False,
) -> CrawledPage:
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    except ImportError as error:  # pragma: no cover - depends on runtime environment
        raise CrawlerUnavailable("Crawl4AI is not installed. Reinstall wenqu-cli, then run `wenqu setup library`.") from error
    browser_options: dict[str, object] = {"headless": True}
    if wechat_headers:
        browser_options.update({"user_agent": WECHAT_USER_AGENT, "headers": {"Referer": "https://weixin.sogou.com/"}})
    run_options: dict[str, object] = {"cache_mode": CacheMode.BYPASS}
    if wait_for:
        run_options["wait_for"] = wait_for
    if delay_seconds is not None:
        run_options["delay_before_return_html"] = delay_seconds
    if javascript:
        run_options["js_code"] = javascript
    browser = BrowserConfig(**browser_options)
    run = CrawlerRunConfig(**run_options)
    try:
        async with AsyncWebCrawler(config=browser) as crawler:
            result = await crawler.arun(url=url, config=run)
    except Exception as error:
        raise RuntimeError(_brief_crawl_error(url, error)) from error
    if not result.success:
        raise RuntimeError(_brief_crawl_error(url, result.error_message or "unknown crawler error"))
    markdown_value = result.markdown
    markdown = getattr(markdown_value, "fit_markdown", None) or getattr(markdown_value, "raw_markdown", None) or str(markdown_value)
    return CrawledPage(url=result.url or url, markdown=markdown, html=result.html or "")


def _brief_crawl_error(url: str, error: BaseException | str) -> str:
    """Keep third-party crawler failures actionable without leaking a stack dump."""
    message = str(error)
    network = re.search(r"net::([A-Z_]+)", message)
    if network:
        detail = f"net::{network.group(1)}"
    else:
        lines = [line.strip() for line in message.splitlines() if line.strip()]
        detail = next((line.removeprefix("Error: ").strip() for line in lines if not line.startswith(("Code context:", "Call log:", "["))), "crawler request failed")
        detail = detail[:240]
    return f"Crawl4AI could not fetch {url}: {detail}."


def crawl_page(url: str) -> CrawledPage:
    return asyncio.run(_crawl_one(url))


def crawl_wechat_article(url: str) -> CrawledPage:
    """Fetch an already-confirmed public WeChat article without bypassing access controls."""
    page = asyncio.run(_crawl_one(url, wait_for="css:#js_content", delay_seconds=2.0, wechat_headers=True))
    if urlparse(page.url).netloc != "mp.weixin.qq.com" or not _has_wechat_body(page.html):
        raise RuntimeError("The WeChat page did not expose a confirmed public article body.")
    return page


def crawl_site(url: str, max_pages: int) -> list[CrawledPage]:
    """Small, bounded same-origin deep crawl for Wenqu material collection."""
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    origin = urlparse(url)
    pending = [url]
    seen: set[str] = set()
    pages: list[CrawledPage] = []
    while pending and len(pages) < max_pages:
        current = pending.pop(0)
        if current in seen:
            continue
        seen.add(current)
        page = crawl_page(current)
        pages.append(page)
        for candidate in _same_origin_links(page.html, page.url, origin.netloc):
            if candidate not in seen and candidate not in pending:
                pending.append(candidate)
    return pages


def _same_origin_links(html: str, base_url: str, origin: str) -> list[str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for anchor in soup.select("a[href]"):
        candidate = urljoin(base_url, str(anchor["href"]))
        parsed = urlparse(candidate)
        if parsed.scheme in {"http", "https"} and parsed.netloc == origin:
            links.append(candidate.split("#", 1)[0])
    return links


def _has_wechat_body(html: str) -> bool:
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser").find(id="js_content") is not None
