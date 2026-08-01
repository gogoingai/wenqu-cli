from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx
from wenqu.models import SearchResult
from wenqu.search import ENGINE_NAMES, RECOVERABLE_ENGINES, EngineError, RequestOptions, _parse_exa, _parse_juejin, _parse_linuxdo, _parse_html, _raise_if_access_challenge, _search_engine, _web_request, normalise_engines, search


class SearchTests(unittest.TestCase):
    def test_all_documented_engines_are_available(self) -> None:
        self.assertEqual(
            normalise_engines("baidu,bing,linuxdo,csdn,duckduckgo,exa,brave,juejin,sogou"),
            ("baidu", "bing", "linuxdo", "csdn", "duckduckgo", "exa", "brave", "juejin", "sogou"),
        )
        self.assertEqual(normalise_engines("搜狗,sougou"), ("sogou",))
        self.assertEqual(RECOVERABLE_ENGINES, {"baidu", "bing", "brave", "sogou"})

    def test_every_engine_has_a_direct_adapter_or_a_documented_service_protocol(self) -> None:
        web_engines = {"baidu", "bing", "duckduckgo", "brave", "sogou"}
        for engine in web_engines:
            self.assertTrue(_web_request(engine, "query")[0].startswith("https://"))
        self.assertEqual(set(ENGINE_NAMES) - web_engines, {"linuxdo", "csdn", "juejin", "exa"})

    def test_html_adapter_preserves_title_url_and_channel(self) -> None:
        html = """<h2><a href='https://maps.google.com/'>Navigation item</a></h2><li class='b_algo'><h2><a href='https://example.com/a'>Example title</a></h2><p>Useful description</p></li>"""

        results = _parse_html("bing", html, 3, channel="browser")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Example title")
        self.assertEqual(results[0].url, "https://example.com/a")
        self.assertEqual(results[0].channel, "browser")

    def test_baidu_wrappers_are_resolved_before_becoming_candidates(self) -> None:
        for wrapper in ("https://www.baidu.com/link?url=wrapped", "https://www.baidu.com/baidu.php?url=wrapped"):
            with self.subTest(wrapper=wrapper):
                html = f"<h3><a href='{wrapper}'>Example</a></h3>"

                results = _parse_html("baidu", html, 3, redirect_resolver=lambda value: "https://example.com/final")

                self.assertEqual(results[0].url, "https://example.com/final")

    def test_unresolved_baidu_wrapper_is_discarded(self) -> None:
        wrapper = "https://www.baidu.com/baidu.php?url=wrapped"

        results = _parse_html("baidu", f"<h3><a href='{wrapper}'>Example</a></h3>", 3, redirect_resolver=lambda value: wrapper)

        self.assertEqual(results, [])

    def test_duckduckgo_wrapper_is_unwrapped_before_becoming_a_candidate(self) -> None:
        html = "<a class='result__a' href='https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle'>Example</a>"

        results = _parse_html("duckduckgo", html, 3)

        self.assertEqual(results[0].url, "https://example.com/article")

    def test_failed_engine_does_not_block_other_engines(self) -> None:
        def fake_engine(engine, query, limit, client, options):
            if engine == "baidu":
                raise EngineError("REQUEST_FAILED", "Baidu unavailable")
            return [SearchResult("One", "https://example.com/one", "", engine)]

        with patch("wenqu.search._search_engine", side_effect=fake_engine):
            result = search("test", engines="baidu,juejin", limit=4, browser_fallback=False)

        self.assertEqual([item.engine for item in result.results], ["juejin"])
        self.assertEqual(result.partial_failures[0].engine, "baidu")
        self.assertEqual(result.partial_failures[0].code, "REQUEST_FAILED")

    def test_results_are_deduplicated_without_removing_query_parameters(self) -> None:
        def fake_engine(engine, query, limit, client, options):
            return [SearchResult(engine, "https://example.com/post?signature=preserved#section", "", engine)]

        with patch("wenqu.search._search_engine", side_effect=fake_engine):
            result = search("test", engines="baidu,bing", limit=4, browser_fallback=False)

        self.assertEqual(len(result.results), 1)
        self.assertEqual(result.results[0].url, "https://example.com/post?signature=preserved#section")

    def test_service_adapters_normalise_their_distinct_json_protocols(self) -> None:
        juejin = _parse_juejin({"err_no": 0, "data": [{"result_model": {"article_info": {"article_id": "123", "title": "Juejin", "brief_content": "Brief"}}}]}, 3)
        linuxdo = _parse_linuxdo({"topics": [{"id": 99, "title": "LinuxDo", "excerpt": "Discussion"}]}, 3)
        exa = _parse_exa({"results": [{"url": "https://example.com", "title": "Exa", "highlights": ["One", "Two"]}]}, 3)

        self.assertEqual((juejin[0].url, linuxdo[0].url, exa[0].description), ("https://juejin.cn/post/123", "https://linux.do/t/99", "One Two"))

    def test_missing_exa_key_skips_before_any_network_call(self) -> None:
        with patch("wenqu.search._search_engine") as engine:
            result = search("test", engines="exa", browser_fallback=False)

        engine.assert_not_called()
        self.assertEqual(result.partial_failures, ())
        self.assertEqual(result.skipped_engines[0].engine, "exa")
        self.assertIn("EXA_API_KEY", result.skipped_engines[0].configure)

    def test_access_challenges_are_not_silent_empty_results(self) -> None:
        with self.assertRaisesRegex(EngineError, "access-verification") as caught:
            _raise_if_access_challenge("baidu", "<div>百度安全验证</div>")

        self.assertEqual(caught.exception.code, "ACCESS_CHALLENGE")

    def test_linuxdo_delegates_to_web_search_without_calling_its_403_api(self) -> None:
        seen_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_urls.append(str(request.url))
            return httpx.Response(
                200,
                text="<a class='result__a' href='https://linux.do/t/42'>A LinuxDo topic</a>",
                request=request,
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            results = _search_engine("linuxdo", "asyncio", 2, client, RequestOptions())

        self.assertEqual(results[0].engine, "linuxdo")
        self.assertEqual(results[0].channel, "delegate:duckduckgo")
        self.assertIn("site%3Alinux.do", seen_urls[0])
        self.assertNotIn("linux.do/search.json", seen_urls[0])

    def test_csdn_delegates_to_web_search_and_keeps_only_csdn_domains(self) -> None:
        seen_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_urls.append(str(request.url))
            return httpx.Response(200, text="<a class='result__a' href='https://blog.csdn.net/example/article/details/42'>A CSDN article</a>", request=request)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            results = _search_engine("csdn", "asyncio", 2, client, RequestOptions())

        self.assertEqual(results[0].engine, "csdn")
        self.assertEqual(results[0].channel, "delegate:duckduckgo")
        self.assertIn("q=asyncio+CSDN", seen_urls[0])
        self.assertNotIn("so.csdn.net/so/search", seen_urls[0])


if __name__ == "__main__":
    unittest.main()
