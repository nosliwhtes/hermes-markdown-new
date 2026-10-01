import json
import math
import tempfile
import unittest
from pathlib import Path
from markdown_new.client import APIError, ERROR_BODY_LIMIT, MarkdownNewClient
from markdown_new.safety import ValidationError
from support import scripted_http_server


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, delay: float) -> None:
        self.value += delay


class MarkdownNewClientContractTests(unittest.TestCase):
    def client(self, server, **kwargs) -> MarkdownNewClient:
        return MarkdownNewClient(base_url=server.url, timeout=1, **kwargs)

    def test_url_conversion_posts_root_and_reads_top_level_content(self) -> None:
        response = {"content": "# Converted", "markdown": "wrong-field"}
        with scripted_http_server([(200, {}, response)]) as server:
            result = self.client(server).convert_url(
                "https://example.com/page", method="browser", retain_images=True
            )

        self.assertEqual(result["content"], "# Converted")
        self.assertEqual(len(server.requests), 1)
        request = server.requests[0]
        self.assertEqual((request.method, request.path), ("POST", "/"))
        self.assertEqual(request.headers["Accept"], "application/json")
        self.assertEqual(
            json.loads(request.body),
            {
                "url": "https://example.com/page",
                "method": "browser",
                "retain_images": True,
            },
        )

    def test_url_conversion_rejects_invented_markdown_response_field(self) -> None:
        with scripted_http_server([(200, {}, {"markdown": "not the contract"})]) as server:
            with self.assertRaisesRegex(APIError, "content"):
                self.client(server).convert_url("https://example.com")

    def test_search_uses_q_and_reads_combined_and_extracted_markdown(self) -> None:
        response = {
            "results": [{"title": "One", "url": "https://example.com/one"}],
            "extracted": [
                {"url": "https://example.com/one", "markdown": "# One"}
            ],
            "combined": "# Search bundle",
        }
        with scripted_http_server([(200, {}, response)]) as server:
            result = self.client(server).search(
                "frozen contract", n=2, gl="us", hl="en", retain_images=True
            )

        self.assertEqual(result["combined"], "# Search bundle")
        self.assertEqual(result["extracted"][0]["markdown"], "# One")
        request = server.requests[0]
        self.assertEqual((request.method, request.path), ("POST", "/search"))
        self.assertEqual(
            json.loads(request.body),
            {
                "q": "frozen contract",
                "n": 2,
                "gl": "us",
                "hl": "en",
                "retain_images": True,
                "format": "json",
            },
        )
        self.assertNotIn("query", json.loads(request.body))

    def test_search_rejects_n_outside_one_through_five_without_network(self) -> None:
        with scripted_http_server([]) as server:
            client = self.client(server)
            for count in (0, 6):
                with self.subTest(count=count):
                    with self.assertRaises(ValidationError):
                        client.search("query", n=count)
        self.assertEqual(server.requests, [])

    def test_search_rejects_missing_combined_field(self) -> None:
        response = {"results": [], "extracted": [], "markdown": "wrong"}
        with scripted_http_server([(200, {}, response)]) as server:
            with self.assertRaisesRegex(APIError, "combined"):
                self.client(server).search("query", n=1)

    def test_local_upload_uses_convert_file_part_and_tolerates_missing_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.txt"
            path.write_text("local body", encoding="utf-8")
            response = {"data": {"content": "# Uploaded"}}
            with scripted_http_server([(200, {}, response)]) as server:
                result = self.client(server).upload_file(path)

        self.assertEqual(result["data"]["content"], "# Uploaded")
        self.assertNotIn("tokens", result["data"])
        request = server.requests[0]
        self.assertEqual((request.method, request.path), ("POST", "/convert"))
        content_type = request.headers["Content-Type"]
        self.assertEqual(request.headers["Accept"], "application/json")
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertIn(b'Content-Disposition: form-data; name="file"; filename="sample.txt"', request.body)
        self.assertIn(b"local body", request.body)

    def test_local_upload_rejects_top_level_content_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.txt"
            path.write_text("body", encoding="utf-8")
            with scripted_http_server([(200, {}, {"content": "wrong"})]) as server:
                with self.assertRaisesRegex(APIError, "data.content"):
                    self.client(server).upload_file(path)

    def test_crawl_start_uses_documented_camel_case_body_and_accepts_cached_job(self) -> None:
        with scripted_http_server(
            [(200, {}, {"jobId": "job-123", "cached": True})]
        ) as server:
            result = self.client(server).start_crawl(
                "https://example.com/docs",
                limit=25,
                depth=2,
                render=True,
                source="sitemaps",
                max_age=0,
                modified_since=1700000000,
                include_external_links=True,
                include_subdomains=True,
                include_patterns=["/docs/*"],
                exclude_patterns=["/private/*"],
            )

        self.assertEqual(result["jobId"], "job-123")
        self.assertIs(result["cached"], True)
        request = server.requests[0]
        self.assertEqual((request.method, request.path), ("POST", "/crawl"))
        self.assertEqual(
            json.loads(request.body),
            {
                "url": "https://example.com/docs",
                "limit": 25,
                "depth": 2,
                "render": True,
                "source": "sitemaps",
                "maxAge": 0,
                "modifiedSince": 1700000000,
                "includeExternalLinks": True,
                "includeSubdomains": True,
                "includePatterns": ["/docs/*"],
                "excludePatterns": ["/private/*"],
            },
        )

    def test_crawl_rejects_invalid_documented_options_without_network(self) -> None:
        invalid_options = (
            {"source": "sitemap"},
            {"source": "other"},
            {"depth": 0},
            {"depth": 11},
            {"max_age": -1},
            {"max_age": 604801},
            {"modified_since": "2026-08-01"},
            {"modified_since": True},
        )
        with scripted_http_server([]) as server:
            client = self.client(server)
            for options in invalid_options:
                with self.subTest(options=options):
                    with self.assertRaises(ValidationError):
                        client.start_crawl("https://example.com", **options)
        self.assertEqual(server.requests, [])

    def test_explicit_service_failure_envelope_is_an_api_error(self) -> None:
        response = {"success": False, "error": "conversion failed"}
        with scripted_http_server([(200, {}, response)]) as server:
            with self.assertRaisesRegex(APIError, "conversion failed"):
                self.client(server).convert_url("https://example.com")

    def test_crawl_polls_status_path_and_returns_result_records_as_json(self) -> None:
        completed = {
            "success": True,
            "result": {
                "status": "completed",
                "records": [
                    {"url": "https://example.com/a", "status": "completed"},
                    {"url": "https://example.com/b", "status": "skipped"},
                ],
            },
        }
        responses = [
            (200, {}, {"jobId": "cached-job", "cached": True}),
            (200, {}, {"result": {"status": "running", "records": []}}),
            (200, {}, completed),
        ]
        with scripted_http_server(responses) as server:
            result = self.client(server, sleep=lambda _: None).crawl(
                "https://example.com",
                output_format="json",
                poll_interval=0,
                poll_timeout=5,
                limit=2,
            )

        self.assertEqual(result, completed)
        self.assertEqual(
            [(item.method, item.path) for item in server.requests],
            [
                ("POST", "/crawl"),
                ("GET", "/crawl/status/cached-job?format=json"),
                ("GET", "/crawl/status/cached-job?format=json"),
            ],
        )
        self.assertEqual(result["result"]["records"][1]["status"], "skipped")

    def test_crawl_markdown_download_uses_status_path_and_retain_images_query(self) -> None:
        responses = [
            (200, {}, {"jobId": "job-md"}),
            (200, {}, {"result": {"status": "completed", "records": []}}),
            (200, {"Content-Type": "text/markdown"}, "# Crawl\n"),
        ]
        with scripted_http_server(responses) as server:
            result = self.client(server).crawl(
                "https://example.com",
                output_format="markdown",
                retain_images=True,
                poll_interval=0,
                poll_timeout=5,
            )

        self.assertEqual(result, "# Crawl\n")
        self.assertEqual(
            [(item.method, item.path) for item in server.requests],
            [
                ("POST", "/crawl"),
                ("GET", "/crawl/status/job-md?format=json"),
                ("GET", "/crawl/status/job-md?retain_images=true"),
            ],
        )
        paths = [item.path for item in server.requests]
        self.assertNotIn("/crawl/job-md", paths)
        self.assertNotIn("/crawl/job-md/markdown", paths)
        self.assertEqual(server.requests[-1].headers["Accept"], "*/*")

    def test_crawl_cancel_deletes_exact_encoded_status_path(self) -> None:
        response = {"success": True, "jobId": "job/one", "status": "cancelled"}
        with scripted_http_server([(200, {}, response)]) as server:
            result = self.client(server).cancel_crawl("job/one")

        self.assertEqual(result, response)
        self.assertEqual(
            [(item.method, item.path) for item in server.requests],
            [("DELETE", "/crawl/status/job%2Fone")],
        )

    def test_crawl_cancel_rejects_empty_job_id_without_network(self) -> None:
        with scripted_http_server([]) as server:
            with self.assertRaisesRegex(ValidationError, "job ID"):
                self.client(server).cancel_crawl("")
        self.assertEqual(server.requests, [])

    def test_crawl_cancel_rejects_malformed_and_explicit_failure_responses(self) -> None:
        cases = (
            ("not-json", "invalid JSON"),
            ({"success": False, "error": "already completed"}, "already completed"),
        )
        for response, message in cases:
            with self.subTest(response=response):
                with scripted_http_server([(200, {}, response)]) as server:
                    with self.assertRaisesRegex(APIError, message):
                        self.client(server).cancel_crawl("job-1")

    def test_crawl_cancel_does_not_retry_404(self) -> None:
        responses = [(404, {}, "missing"), (200, {}, {"success": True})]
        with scripted_http_server(responses) as server:
            with self.assertRaises(APIError) as caught:
                self.client(server).cancel_crawl("job-404")
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(len(server.requests), 1)

    def test_crawl_cancel_retries_retryable_error_exactly_once(self) -> None:
        responses = [
            (503, {}, "retry"),
            (200, {}, {"success": True, "status": "cancelled"}),
        ]
        with scripted_http_server(responses) as server:
            result = self.client(server, sleep=lambda _: None).cancel_crawl("job-retry")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(len(server.requests), 2)

    def test_crawl_cancel_timeout_is_not_retried(self) -> None:
        class TimeoutOpener:
            def __init__(self) -> None:
                self.calls = 0

            def open(self, request, timeout):
                self.calls += 1
                raise TimeoutError("timed out")

        opener = TimeoutOpener()
        client = MarkdownNewClient(opener=opener, timeout=1)
        with self.assertRaisesRegex(APIError, "network failure"):
            client.cancel_crawl("job-timeout")
        self.assertEqual(opener.calls, 1)

    def test_crawl_failure_and_unknown_status_include_job_id(self) -> None:
        for status in ("failed", "unexpected-terminal"):
            with self.subTest(status=status):
                responses = [
                    (200, {}, {"jobId": "recover-me"}),
                    (200, {}, {"result": {"status": status, "error": "reason"}}),
                ]
                with scripted_http_server(responses) as server:
                    with self.assertRaisesRegex(APIError, "recover-me"):
                        self.client(server).crawl(
                            "https://example.com",
                            output_format="json",
                            poll_interval=0,
                            poll_timeout=5,
                        )

    def test_crawl_timeout_is_bounded_and_includes_job_id(self) -> None:
        clock = FakeClock()
        responses = [
            (200, {}, {"jobId": "slow-job"}),
            (200, {}, {"result": {"status": "running"}}),
            (200, {}, {"result": {"status": "running"}}),
        ]
        with scripted_http_server(responses) as server:
            with self.assertRaisesRegex(APIError, "slow-job") as error:
                self.client(
                    server, sleep=clock.sleep, clock=clock.monotonic
                ).crawl(
                    "https://example.com",
                    output_format="json",
                    poll_interval=1,
                    poll_timeout=1,
                )

        self.assertIn("timed out", str(error.exception).lower())

    def test_retries_exactly_once_for_429_and_5xx(self) -> None:
        for status in (429, 500, 503):
            with self.subTest(status=status):
                responses = [
                    (status, {}, "retry me"),
                    (200, {}, {"content": "ok"}),
                ]
                with scripted_http_server(responses) as server:
                    result = self.client(server, sleep=lambda _: None).convert_url(
                        "https://example.com"
                    )
                self.assertEqual(result["content"], "ok")
                self.assertEqual(len(server.requests), 2)

    def test_stops_after_one_retry(self) -> None:
        responses = [
            (500, {}, "first"),
            (500, {}, "second"),
            (200, {}, {"content": "must not be reached"}),
        ]
        with scripted_http_server(responses) as server:
            with self.assertRaises(APIError):
                self.client(server, sleep=lambda _: None).convert_url(
                    "https://example.com"
                )
        self.assertEqual(len(server.requests), 2)

    def test_other_4xx_is_not_retried_and_error_body_is_bounded(self) -> None:
        oversized_body = "service-detail-" + ("x" * (ERROR_BODY_LIMIT * 2))
        responses = [
            (400, {}, oversized_body),
            (200, {}, {"content": "must not be reached"}),
        ]
        with scripted_http_server(responses) as server:
            with self.assertRaises(APIError) as error:
                self.client(server).convert_url("https://example.com")

        self.assertEqual(len(server.requests), 1)
        self.assertEqual(error.exception.status, 400)
        self.assertIn("service-detail-", error.exception.body)
        self.assertIn("truncated", error.exception.body)
        self.assertLess(len(error.exception.body), ERROR_BODY_LIMIT + 100)

    def test_rejects_non_finite_request_and_polling_bounds_before_network(self) -> None:
        with scripted_http_server([]) as server:
            for timeout in (math.nan, math.inf):
                with self.subTest(bound="request timeout", value=timeout):
                    with self.assertRaises(ValidationError):
                        MarkdownNewClient(base_url=server.url, timeout=timeout)

            client = self.client(server, sleep=lambda _: None)
            with self.assertRaises(ValidationError):
                client.crawl(
                    "https://example.com",
                    output_format="json",
                    poll_interval=math.nan,
                    poll_timeout=1.0,
                )
            with self.assertRaises(ValidationError):
                client.crawl(
                    "https://example.com",
                    output_format="json",
                    poll_interval=0.0,
                    poll_timeout=math.inf,
                )

        self.assertEqual(server.requests, [])


if __name__ == "__main__":
    unittest.main()
