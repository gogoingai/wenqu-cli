from __future__ import annotations

import unittest
from unittest.mock import patch

from wenqu.crawler import CrawledPage, _brief_crawl_error, crawl_wechat_article


class CrawlerTests(unittest.TestCase):
    def test_direct_wechat_fetch_requires_a_public_article_body(self) -> None:
        async def fake_crawl(*args, **kwargs):
            return CrawledPage("https://mp.weixin.qq.com/s?id=1", "content", "<div id='js_content'>body</div>")

        with patch("wenqu.crawler._crawl_one", new=fake_crawl):
            page = crawl_wechat_article("https://mp.weixin.qq.com/s?id=1")

        self.assertEqual(page.url, "https://mp.weixin.qq.com/s?id=1")

    def test_crawler_network_failure_is_concise(self) -> None:
        message = _brief_crawl_error(
            "https://example.invalid",
            RuntimeError("Failed on navigating ACS-GOTO:\nPage.goto: net::ERR_CONNECTION_CLOSED at https://example.invalid/\nCode context:\n  778 → raise RuntimeError(...)"),
        )

        self.assertEqual(message, "Crawl4AI could not fetch https://example.invalid: net::ERR_CONNECTION_CLOSED.")
        self.assertNotIn("Code context", message)

if __name__ == "__main__":
    unittest.main()
