import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from markdown_new.client import APIError
from markdown_new.cli import build_parser, main
from markdown_new.safety import MAX_FILE_SIZE


class FakeClient:
    def __init__(self) -> None:
        self.calls = []
        self.url_result = {"content": "# URL", "markdown": "wrong"}
        self.search_result = {
            "results": [],
            "extracted": [{"markdown": "# Extracted"}],
            "combined": "# Combined",
        }
        self.upload_result = {"data": {"content": "# File"}}
        self.crawl_result = None
        self.start_result = {"jobId": "job-start", "cached": False}
        self.status_result = {
            "result": {"status": "running", "records": [{"status": "completed"}]}
        }
        self.download_result = "# Partial crawl"
        self.cancel_result = {"success": True, "status": "cancelled"}
        self.error = None

    def convert_url(self, url, **kwargs):
        self.calls.append(("convert_url", url, kwargs))
        if self.error:
            raise self.error
        return self.url_result

    def search(self, query, **kwargs):
        self.calls.append(("search", query, kwargs))
        if self.error:
            raise self.error
        return self.search_result

    def upload_file(self, path):
        self.calls.append(("upload_file", Path(path), {}))
        if self.error:
            raise self.error
        return self.upload_result

    def crawl(self, url, **kwargs):
        self.calls.append(("crawl", url, kwargs))
        if self.error:
            raise self.error
        if kwargs["output_format"] == "json":
            return {
                "result": {
                    "status": "completed",
                    "records": [{"status": "completed"}],
                }
            }
        return "# Crawl"

    def start_crawl(self, url, **kwargs):
        self.calls.append(("start_crawl", url, kwargs))
        return self.start_result

    def get_crawl_status(self, job_id):
        self.calls.append(("get_crawl_status", job_id, {}))
        return self.status_result

    def download_crawl_markdown(self, job_id, **kwargs):
        self.calls.append(("download_crawl_markdown", job_id, kwargs))
        return self.download_result

    def cancel_crawl(self, job_id):
        self.calls.append(("cancel_crawl", job_id, {}))
        return self.cancel_result


class CLITests(unittest.TestCase):
    def run_cli(self, args, client=None, factory=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        selected = client or FakeClient()
        client_factory = factory or (lambda **_: selected)
        code = main(
            args,
            stdout=stdout,
            stderr=stderr,
            client_factory=client_factory,
        )
        return code, stdout.getvalue(), stderr.getvalue(), selected

    def test_url_markdown_uses_top_level_content(self) -> None:
        code, stdout, stderr, client = self.run_cli(
            ["url", "https://example.com", "--method", "ai", "--retain-images"]
        )

        self.assertEqual((code, stdout, stderr), (0, "# URL\n", ""))
        self.assertEqual(
            client.calls,
            [
                (
                    "convert_url",
                    "https://example.com",
                    {"method": "ai", "retain_images": True},
                )
            ],
        )

    def test_json_format_emits_full_service_shape(self) -> None:
        code, stdout, stderr, _ = self.run_cli(
            ["url", "https://example.com", "--format", "json"]
        )

        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["content"], "# URL")

    def test_search_markdown_uses_combined(self) -> None:
        code, stdout, stderr, client = self.run_cli(
            [
                "search",
                "frozen query",
                "--n",
                "2",
                "--gl",
                "gb",
                "--hl",
                "en",
                "--retain-images",
            ]
        )

        self.assertEqual((code, stdout, stderr), (0, "# Combined\n", ""))
        self.assertEqual(
            client.calls,
            [
                (
                    "search",
                    "frozen query",
                    {
                        "n": 2,
                        "gl": "gb",
                        "hl": "en",
                        "retain_images": True,
                    },
                )
            ],
        )

    def test_search_default_count_matches_service_default(self) -> None:
        code, stdout, stderr, client = self.run_cli(["search", "frozen query"])

        self.assertEqual((code, stdout, stderr), (0, "# Combined\n", ""))
        self.assertEqual(client.calls[0][2]["n"], 3)

    def test_remote_file_uses_url_conversion_without_cloud_gate(self) -> None:
        code, stdout, stderr, client = self.run_cli(
            ["file", "https://example.com/public.pdf"]
        )

        self.assertEqual((code, stdout, stderr), (0, "# URL\n", ""))
        self.assertEqual(
            client.calls,
            [
                (
                    "convert_url",
                    "https://example.com/public.pdf",
                    {"method": None, "retain_images": False},
                )
            ],
        )

    def test_local_file_requires_cloud_ok_before_validation_or_client_creation(self) -> None:
        def forbidden_factory(**kwargs):
            raise AssertionError("client must not be created")

        with mock.patch(
            "markdown_new.cli.validate_local_file",
            side_effect=AssertionError("file validation must not run"),
        ):
            code, stdout, stderr, _ = self.run_cli(
                ["file", "/definitely/missing/private.txt"],
                factory=forbidden_factory,
            )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("--cloud-ok", stderr)

    def test_cloud_approved_local_file_upload_reads_nested_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "approved.txt"
            path.write_text("approved synthetic body", encoding="utf-8")
            code, stdout, stderr, client = self.run_cli(
                ["file", str(path), "--cloud-ok"]
            )

        self.assertEqual((code, stdout, stderr), (0, "# File\n", ""))
        self.assertEqual(client.calls, [("upload_file", path, {})])

    def test_oversized_local_file_fails_before_client_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oversized.bin"
            with path.open("wb") as stream:
                stream.truncate(MAX_FILE_SIZE + 1)

            def forbidden_factory(**kwargs):
                raise AssertionError("client must not be created")

            code, stdout, stderr, _ = self.run_cli(
                ["file", str(path), "--cloud-ok"], factory=forbidden_factory
            )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("10 MiB", stderr)

    def test_crawl_forwards_bounded_polling_and_documented_options(self) -> None:
        args = [
            "crawl",
            "https://example.com/docs",
            "--format",
            "json",
            "--limit",
            "3",
            "--depth",
            "2",
            "--render",
            "--source",
            "sitemaps",
            "--max-age",
            "0",
            "--modified-since",
            "1700000000",
            "--include-external-links",
            "--include-subdomains",
            "--include-pattern",
            "/docs/*",
            "--exclude-pattern",
            "/private/*",
            "--poll-interval",
            "0.5",
            "--poll-timeout",
            "10",
            "--retain-images",
        ]
        code, stdout, stderr, client = self.run_cli(args)

        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["result"]["status"], "completed")
        call = client.calls[0]
        self.assertEqual((call[0], call[1]), ("crawl", "https://example.com/docs"))
        self.assertEqual(
            call[2],
            {
                "output_format": "json",
                "retain_images": True,
                "poll_interval": 0.5,
                "poll_timeout": 10.0,
                "limit": 3,
                "depth": 2,
                "render": True,
                "source": "sitemaps",
                "max_age": 0,
                "modified_since": 1700000000,
                "include_external_links": True,
                "include_subdomains": True,
                "include_patterns": ["/docs/*"],
                "exclude_patterns": ["/private/*"],
            },
        )

    def test_crawl_parser_rejects_out_of_contract_options(self) -> None:
        invalid_arguments = (
            ["crawl", "https://example.com", "--source", "sitemap"],
            ["crawl", "https://example.com", "--depth", "0"],
            ["crawl", "https://example.com", "--depth", "11"],
            ["crawl", "https://example.com", "--max-age", "604801"],
            ["crawl", "https://example.com", "--modified-since", "2026-08-01"],
        )
        parser = build_parser()
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments), mock.patch(
                "sys.stderr", new=io.StringIO()
            ):
                with self.assertRaises(SystemExit) as caught:
                    parser.parse_args(arguments)
                self.assertEqual(caught.exception.code, 2)

    def test_resumable_crawl_commands_route_to_exact_client_methods(self) -> None:
        cases = (
            (
                ["crawl-start", "https://example.com/docs", "--limit", "2", "--format", "json"],
                "start_crawl",
                "job-start",
            ),
            (["crawl-status", "job-1", "--format", "json"], "get_crawl_status", "running"),
            (
                ["crawl-download", "job-1", "--retain-images", "--format", "json"],
                "download_crawl_markdown",
                "# Partial crawl",
            ),
            (["crawl-cancel", "job-1", "--format", "json"], "cancel_crawl", "cancelled"),
        )
        for arguments, method, expected in cases:
            with self.subTest(arguments=arguments):
                code, stdout, stderr, client = self.run_cli(arguments)
                self.assertEqual((code, stderr), (0, ""))
                self.assertEqual(client.calls[0][0], method)
                payload = json.loads(stdout)
                self.assertIn(expected, json.dumps(payload))

    def test_resumable_crawl_commands_reject_irrelevant_options_before_client(self) -> None:
        cases = (
            ["crawl-status", "job-1", "--retain-images"],
            ["crawl-download", "job-1", "--limit", "2"],
            ["crawl-cancel", "job-1", "--poll-timeout", "1"],
            ["crawl-start", "https://example.com", "--retain-images"],
        )

        def forbidden_factory(**kwargs):
            raise AssertionError("client must not be created")

        parser = build_parser()
        for arguments in cases:
            with self.subTest(arguments=arguments), mock.patch(
                "sys.stderr", new=io.StringIO()
            ):
                with self.assertRaises(SystemExit) as caught:
                    parser.parse_args(arguments)
                self.assertEqual(caught.exception.code, 2)

    def test_output_write_uses_atomic_sibling_replace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.md"
            output.write_text("old", encoding="utf-8")
            real_replace = os.replace
            with mock.patch(
                "markdown_new.cli.os.replace", wraps=real_replace
            ) as replace:
                code, stdout, stderr, _ = self.run_cli(
                    [
                        "url",
                        "https://example.com",
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual((code, stdout, stderr), (0, "", ""))
            self.assertEqual(output.read_text(encoding="utf-8"), "# URL\n")
            replace.assert_called_once()
            source, destination = replace.call_args.args
            self.assertEqual(Path(destination), output)
            self.assertEqual(Path(source).parent, output.parent)
            self.assertFalse(Path(source).exists())

    def test_api_or_network_failure_exits_one(self) -> None:
        client = FakeClient()
        client.error = APIError("network failure: connection refused")
        code, stdout, stderr, _ = self.run_cli(
            ["url", "https://example.com"], client=client
        )

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("connection refused", stderr)

    def test_invalid_url_is_local_validation_exit_two_without_client(self) -> None:
        def forbidden_factory(**kwargs):
            raise AssertionError("client must not be created")

        code, stdout, stderr, _ = self.run_cli(
            ["url", "http://127.0.0.1/private"], factory=forbidden_factory
        )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("public", stderr.lower())

    def test_non_finite_timeouts_are_usage_exit_two_before_client_creation(self) -> None:
        cases = (
            ["url", "https://example.com", "--timeout", "nan"],
            ["crawl", "https://example.com", "--poll-timeout", "inf"],
            ["crawl", "https://example.com", "--poll-interval", "nan"],
        )

        def forbidden_factory(**kwargs):
            raise AssertionError("client must not be created")

        for arguments in cases:
            with self.subTest(arguments=arguments):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        main(arguments, client_factory=forbidden_factory)
                self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
