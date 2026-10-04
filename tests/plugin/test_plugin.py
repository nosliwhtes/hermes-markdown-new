import asyncio
import importlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / "plugin" / "markdown-new"
ACTIVE_SOURCE = os.environ.get("HERMES_ACTIVE_SOURCE", "")
HOST_READY = bool(ACTIVE_SOURCE) and sys.version_info >= (3, 10)


def load_plugin_package():
    name = "markdown_new_plugin_test_{}".format(uuid.uuid4().hex)
    init_path = PLUGIN_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        name, str(init_path), submodule_search_locations=[str(PLUGIN_DIR)]
    )
    if spec is None or spec.loader is None:
        raise AssertionError("could not create plugin package spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module, name


class FakeAdapter:
    def __init__(self):
        self.calls = []
        self.convert_results = {}
        self.search_result = {
            "results": [
                {
                    "title": "One",
                    "url": "https://example.com/one",
                    "description": "First",
                    "position": 1,
                }
            ],
            "extracted": [
                {"url": "https://example.com/one", "markdown": "# One"}
            ],
            "combined": "# Combined",
            "query": "example",
        }
        self.start_result = {"jobId": "job-1", "cached": False}
        self.status_results = [
            {
                "result": {
                    "status": "completed",
                    "records": [
                        {"status": "completed"},
                        {"status": "skipped"},
                        {"status": "failed", "error": "fixture failure"},
                    ],
                }
            }
        ]
        self.download_result = "# Crawl result"
        self.cancel_result = {"success": True, "status": "cancelled"}
        self.file_result = {"data": {"content": "# File"}}

    def is_available(self):
        return True

    def convert_url(self, url, **kwargs):
        self.calls.append(("convert_url", url, kwargs))
        selected = self.convert_results.get(url)
        if isinstance(selected, Exception):
            raise selected
        return selected or {
            "content": "# {}".format(url.rsplit("/", 1)[-1] or "page"),
            "title": "Converted",
            "metadata": {"source": "fixture"},
        }

    def search(self, query, **kwargs):
        self.calls.append(("search", query, kwargs))
        return self.search_result

    def start_crawl(self, url, **kwargs):
        self.calls.append(("start_crawl", url, kwargs))
        return self.start_result

    def crawl_status(self, job_id, **kwargs):
        self.calls.append(("crawl_status", job_id, kwargs))
        if len(self.status_results) > 1:
            return self.status_results.pop(0)
        return self.status_results[0]

    def crawl_download(self, job_id, **kwargs):
        self.calls.append(("crawl_download", job_id, kwargs))
        return {"jobId": job_id, "content": self.download_result}

    def crawl_cancel(self, job_id, **kwargs):
        self.calls.append(("crawl_cancel", job_id, kwargs))
        return self.cancel_result

    def file_convert(self, source, **kwargs):
        self.calls.append(("file_convert", source, kwargs))
        return self.file_result


async def allow_url(url):
    return url


@unittest.skipUnless(HOST_READY, "active Hermes host source/runtime required")
class ProviderAndToolContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package, cls.package_name = load_plugin_package()
        cls.provider_module = importlib.import_module(cls.package_name + ".provider")
        cls.tool_module = importlib.import_module(cls.package_name + ".tool")
        cls.safety_module = importlib.import_module(cls.package_name + ".safety")

    def run_tool(self, tool, args):
        return json.loads(asyncio.run(tool.handle(dict(args))))

    def make_tool(self, adapter=None, **kwargs):
        return self.tool_module.MarkdownNewTool(
            adapter or FakeAdapter(), safety_gate=allow_url, **kwargs
        )

    def test_native_extract_enforces_guard_and_host_async_dispatch(self):
        from tools.web_tools_extract import _dispatch_extract
        adapter = FakeAdapter()
        provider = self.provider_module.MarkdownNewWebProvider(adapter)
        urls = ["https://example.com/?token=opaque-fixture", "https://example.com/blocked",
                "https://example.com/good"]
        with mock.patch.object(self.safety_module, "async_is_safe_url", new=mock.AsyncMock(return_value=True)), mock.patch.object(
            self.safety_module, "check_website_access",
            side_effect=lambda url: {"message": "fixture policy"} if url.endswith("blocked") else None,
        ), mock.patch("tools.web_result_cache.extract_cache_put"), mock.patch(
            "tools.web_tools_extract._rescue_eligible", return_value=False
        ):
            rows = asyncio.run(_dispatch_extract(provider, urls, None))
        self.assertEqual([row["url"] for row in rows], urls)
        self.assertIn("credential", rows[0]["error"])
        self.assertIn("fixture policy", rows[1]["error"])
        self.assertEqual(rows[2]["content"], "# good")
        self.assertEqual([call[1] for call in adapter.calls], [urls[2]])

    def test_native_guard_refusals_are_not_rescued_by_host(self):
        from tools.web_tools_extract import _dispatch_extract
        adapter = FakeAdapter()
        provider = self.provider_module.MarkdownNewWebProvider(adapter)
        urls = ["https://example.com/?token=opaque-fixture", "https://example.com/blocked"]
        with mock.patch.object(self.safety_module, "async_is_safe_url", new=mock.AsyncMock(return_value=True)), mock.patch.object(
            self.safety_module, "check_website_access", return_value={"message": "fixture refusal"}
        ), mock.patch("tools.web_tools_extract._rescue_eligible", return_value=True), mock.patch(
            "plugins.web.keyless_mcp.extract_with_failover", side_effect=AssertionError("must not fetch refused URLs")
        ) as fallback:
            rows = asyncio.run(_dispatch_extract(provider, urls, None))
        self.assertTrue(all(row["blocked_by_policy"] for row in rows))
        self.assertEqual(adapter.calls, [])
        fallback.assert_not_called()

    def test_snapshot_survives_file_and_symlink_swap_at_adapter_boundary(self):
        adapter_module = importlib.import_module(self.package_name + ".adapter")
        for symlink in (False, True):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "note.txt"
                source.write_bytes(b"approved fixture")
                replacement = Path(directory) / "other.txt"
                replacement.write_bytes(b"different fixture")
                adapter = adapter_module.MarkdownNewAdapter()
                original = adapter.file_convert
                captured = []
                def swap_then_convert(path, **kwargs):
                    source.unlink()
                    if symlink:
                        source.symlink_to(replacement)
                    else:
                        source.write_bytes(b"different fixture")
                    # Restore the bound implementation for its snapshot-only recursion.
                    adapter.file_convert = original
                    return original(path, **kwargs)
                def capture(args, timeout):
                    captured.append(Path(args[1]))
                    self.assertEqual(captured[-1].read_bytes(), b"approved fixture")
                    return {"data": {"content": "fixture"}}
                adapter.file_convert = swap_then_convert
                with mock.patch.object(adapter, "_json", side_effect=capture):
                    result = self.run_tool(self.make_tool(adapter, approval_requester=lambda *a: {"approved": True}),
                                           {"operation": "file_convert", "source": str(source)})
                self.assertTrue(result["success"], result)
                self.assertFalse(captured[0].exists())

    def test_snapshot_rejects_symlink_swap_between_inspection_and_open(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "note.txt"
            source.write_bytes(b"fixture")
            upload = self.safety_module.inspect_upload_path(str(source))
            other = Path(directory) / "other.txt"
            other.write_bytes(b"other")
            source.unlink()
            source.symlink_to(other)
            with self.assertRaisesRegex(self.safety_module.SafetyError, "snapshot"):
                self.safety_module.snapshot_upload(upload)

    def test_adapter_version_check_ignores_parent_pythonpath(self):
        adapter_module = importlib.import_module(self.package_name + ".adapter")
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "markdown-new"
            binary.write_text(
                "#!/bin/sh\n"
                "if [ -n \"$PYTHONPATH\" ]; then echo 'markdown-new 0.2.0'; exit 0; fi\n"
                "echo 'markdown-new 0.1.0'\n",
                encoding="utf-8",
            )
            binary.chmod(0o700)
            adapter = adapter_module.MarkdownNewAdapter(binary=str(binary))
            with self.assertRaisesRegex(adapter_module.AdapterError, "too old"):
                adapter.search("must-not-reach-network", n=1)

    def test_provider_native_search_shape_limit_clamp_and_extract_order(self):
        adapter = FakeAdapter()
        adapter.convert_results["https://example.com/bad"] = RuntimeError("fixture outage")
        provider = self.provider_module.MarkdownNewWebProvider(adapter)

        search = provider.search("example", limit=99)
        self.assertEqual(
            search,
            {
                "success": True,
                "data": {
                    "web": [
                        {
                            "title": "One",
                            "url": "https://example.com/one",
                            "description": "First",
                            "position": 1,
                        }
                    ]
                },
            },
        )
        self.assertEqual(adapter.calls[0], ("search", "example", {"n": 5}))

        with mock.patch.object(self.provider_module, "guard_public_url", side_effect=allow_url):
            extracted = asyncio.run(provider.extract(
                ["https://example.com/good", "https://example.com/bad"]
            ))
        self.assertEqual([row["url"] for row in extracted], [
            "https://example.com/good", "https://example.com/bad"
        ])
        self.assertEqual(extracted[0]["content"], "# good")
        self.assertEqual(extracted[0]["raw_content"], "# good")
        self.assertIn("fixture outage", extracted[1]["error"])
        self.assertFalse(extracted[1]["content"])

    def test_provider_native_search_accepts_vendor_link_and_prefers_nonempty_url(self):
        adapter = FakeAdapter()
        adapter.search_result["results"] = [
            {
                "title": "Vendor Link",
                "link": "https://example.com/vendor-link",
                "snippet": "Vendor-style organic result",
                "position": 1,
            },
            {
                "title": "Canonical URL",
                "url": "https://example.com/canonical-url",
                "link": "https://example.com/fallback-link",
                "snippet": "Both fields supplied",
                "position": 2,
            },
        ]
        provider = self.provider_module.MarkdownNewWebProvider(adapter)

        search = provider.search("example", limit=2)

        self.assertEqual(
            search["data"]["web"],
            [
                {
                    "title": "Vendor Link",
                    "url": "https://example.com/vendor-link",
                    "description": "Vendor-style organic result",
                    "position": 1,
                },
                {
                    "title": "Canonical URL",
                    "url": "https://example.com/canonical-url",
                    "description": "Both fields supplied",
                    "position": 2,
                },
            ],
        )

    def test_tool_exposes_exactly_eight_operations_and_rejects_irrelevant_fields(self):
        expected = {
            "url_convert", "search_extract", "crawl_run", "crawl_start",
            "crawl_status", "crawl_download", "crawl_cancel", "file_convert",
        }
        self.assertEqual(set(self.tool_module.OPERATIONS), expected)
        schema_operations = set(
            self.tool_module.TOOL_SCHEMA["parameters"]["properties"]["operation"]["enum"]
        )
        self.assertEqual(schema_operations, expected)

        adapter = FakeAdapter()
        tool = self.make_tool(adapter)
        for args in (
            {},
            {"operation": "unknown"},
            {"operation": "url_convert", "url": "https://example.com", "query": "wrong"},
            {"operation": "crawl_status", "job_id": "job-1", "retain_images": True},
            {"operation": "search_extract", "query": "q", "cloud_ok": True},
        ):
            with self.subTest(args=args):
                result = self.run_tool(tool, args)
                self.assertFalse(result["success"])
        self.assertEqual(adapter.calls, [])

    def test_rich_url_and_search_forward_all_published_controls_without_losing_content(self):
        adapter = FakeAdapter()
        tool = self.make_tool(adapter)

        converted = self.run_tool(tool, {
            "operation": "url_convert", "url": "https://example.com/page",
            "method": "browser", "retain_images": True, "timeout": 9,
        })
        self.assertTrue(converted["success"])
        self.assertEqual(adapter.calls[0], (
            "convert_url", "https://example.com/page",
            {"method": "browser", "retain_images": True, "timeout": 9.0},
        ))

        searched = self.run_tool(tool, {
            "operation": "search_extract", "query": "example", "n": 1,
            "gl": "gb", "hl": "en", "retain_images": True, "timeout": 8,
        })
        self.assertTrue(searched["success"])
        self.assertEqual(adapter.calls[1], (
            "search", "example",
            {"n": 1, "gl": "gb", "hl": "en", "retain_images": True, "timeout": 8.0},
        ))
        self.assertEqual(searched["data"]["extracted"][0]["markdown"], "# One")
        self.assertEqual(searched["data"]["combined"], "# Combined")
        self.assertEqual(searched["data"]["results"][0]["url"], "https://example.com/one")

    def test_resumable_crawl_operations_and_run_preserve_job_and_partial_counts(self):
        adapter = FakeAdapter()
        tool = self.make_tool(adapter)

        started = self.run_tool(tool, {
            "operation": "crawl_start", "url": "https://example.com/docs",
            "limit": 2, "depth": 1,
        })
        self.assertEqual(started["job_id"], "job-1")

        status = self.run_tool(tool, {"operation": "crawl_status", "job_id": "job-1"})
        self.assertEqual(status["job_id"], "job-1")
        self.assertEqual(status["record_counts"], {"completed": 1, "skipped": 1, "failed": 1})
        self.assertEqual(status["data"]["result"]["records"][2]["error"], "fixture failure")

        downloaded = self.run_tool(tool, {
            "operation": "crawl_download", "job_id": "job-1", "retain_images": True,
        })
        self.assertEqual(downloaded["data"]["content"], "# Crawl result")

        adapter.status_results = [
            {"result": {"status": "running", "records": []}},
            {"result": {"status": "completed", "records": []}},
        ]
        run = self.run_tool(tool, {
            "operation": "crawl_run", "url": "https://example.com/docs",
            "poll_interval": 0, "poll_timeout": 2,
        })
        self.assertTrue(run["success"])
        self.assertEqual(run["job_id"], "job-1")
        self.assertEqual(run["data"]["download"]["content"], "# Crawl result")

    def test_sensitive_calls_require_native_approval_and_model_boolean_is_not_consent(self):
        adapter = FakeAdapter()
        approvals = []

        def deny(tool_name, reason, rule_key):
            approvals.append((tool_name, reason, rule_key))
            return {"approved": False, "message": "fixture denied"}

        tool = self.make_tool(adapter, approval_requester=deny)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "harmless.txt"
            path.write_text("harmless fixture", encoding="utf-8")
            denied = self.run_tool(tool, {"operation": "file_convert", "source": str(path)})
            forged = self.run_tool(tool, {
                "operation": "file_convert", "source": str(path), "approved": True,
            })

        self.assertFalse(denied["success"])
        self.assertIn("denied", denied["error"])
        self.assertFalse(forged["success"])
        self.assertEqual(adapter.calls, [])
        self.assertEqual(len(approvals), 1)

    def test_hook_cannot_authorize_handler_after_native_denial(self):
        adapter = FakeAdapter()
        approvals = []

        def deny(tool_name, reason, rule_key):
            approvals.append((tool_name, reason, rule_key))
            return {"approved": False, "message": "fixture denied"}

        tool = self.make_tool(adapter, approval_requester=deny)
        args = {"operation": "crawl_cancel", "job_id": "job-denied"}
        directive = tool.pre_tool_call("markdown_new", args, tool_call_id="call-denied")
        self.assertNotEqual((directive or {}).get("action"), "block")

        replayed = self.run_tool(tool, args)

        self.assertFalse(replayed["success"])
        self.assertIn("denied", replayed["error"])
        self.assertEqual(len(approvals), 1)
        self.assertEqual(adapter.calls, [])

    def test_native_approval_requires_exact_true(self):
        adapter = FakeAdapter()
        tool = self.make_tool(
            adapter,
            approval_requester=lambda *unused: {"approved": 1, "message": "not boolean consent"},
        )

        result = self.run_tool(
            tool, {"operation": "crawl_cancel", "job_id": "job-truthy"}
        )

        self.assertFalse(result["success"])
        self.assertEqual(adapter.calls, [])

    def test_every_sensitive_handler_call_requests_new_native_approval(self):
        adapter = FakeAdapter()
        approvals = []

        def approve(tool_name, reason, rule_key):
            approvals.append((tool_name, reason, rule_key))
            return {"approved": True}

        tool = self.make_tool(adapter, approval_requester=approve)
        args = {"operation": "crawl_cancel", "job_id": "job-repeat"}

        first = self.run_tool(tool, args)
        second = self.run_tool(tool, args)

        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertEqual(len(approvals), 2)
        self.assertNotEqual(approvals[0][2], approvals[1][2])
        self.assertEqual([call[0] for call in adapter.calls], ["crawl_cancel", "crawl_cancel"])

    def test_approval_failures_and_post_approval_revalidation_never_call_adapter(self):
        outcomes = (
            {"approved": False, "message": "timeout"},
            {"approved": False, "message": "no bridge"},
            {"approved": False, "message": "cron deny"},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "harmless.txt"
            path.write_text("harmless fixture", encoding="utf-8")
            for outcome in outcomes:
                adapter = FakeAdapter()
                tool = self.make_tool(
                    adapter, approval_requester=lambda *unused, result=outcome: result
                )
                result = self.run_tool(
                    tool, {"operation": "file_convert", "source": str(path)}
                )
                self.assertFalse(result["success"])
                self.assertEqual(adapter.calls, [])

            adapter = FakeAdapter()

            def broken_gate(*unused):
                raise RuntimeError("approval gate failed")

            result = self.run_tool(
                self.make_tool(adapter, approval_requester=broken_gate),
                {"operation": "file_convert", "source": str(path)},
            )
            self.assertFalse(result["success"])
            self.assertEqual(adapter.calls, [])

            adapter = FakeAdapter()

            def approve_then_remove(*unused):
                path.unlink()
                return {"approved": True}

            result = self.run_tool(
                self.make_tool(adapter, approval_requester=approve_then_remove),
                {"operation": "file_convert", "source": str(path)},
            )
            self.assertFalse(result["success"])
            self.assertEqual(adapter.calls, [])

    def test_pre_tool_hook_only_prevalidates_and_handler_owns_approval(self):
        adapter = FakeAdapter()
        fallback_approvals = []

        def fallback(tool_name, reason, rule_key):
            fallback_approvals.append(rule_key)
            return {"approved": False, "message": "no bridge"}

        tool = self.make_tool(adapter, approval_requester=fallback)
        first_args = {"operation": "crawl_cancel", "job_id": "job-1"}
        second_args = {"operation": "crawl_cancel", "job_id": "job-1"}
        first = tool.pre_tool_call(
            "markdown_new", first_args, tool_call_id="call-1", session_id="session-1"
        )
        second = tool.pre_tool_call(
            "markdown_new", second_args, tool_call_id="call-2", session_id="session-1"
        )
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(first_args, {"operation": "crawl_cancel", "job_id": "job-1"})
        self.assertEqual(second_args, {"operation": "crawl_cancel", "job_id": "job-1"})
        self.assertEqual(fallback_approvals, [])

        denied = self.run_tool(tool, first_args)
        self.assertFalse(denied["success"])
        self.assertEqual(adapter.calls, [])
        self.assertEqual(len(fallback_approvals), 1)

    def test_approved_harmless_local_fixture_revalidates_then_sets_cli_cloud_gate(self):
        adapter = FakeAdapter()
        keys = []

        def approve(tool_name, reason, rule_key):
            keys.append(rule_key)
            return {"approved": True}

        tool = self.make_tool(adapter, approval_requester=approve)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "harmless.txt"
            path.write_text("markdown-new plugin smoke test", encoding="utf-8")
            result = self.run_tool(tool, {"operation": "file_convert", "source": str(path)})

        self.assertTrue(result["success"])
        self.assertEqual(len(keys), 1)
        self.assertEqual(adapter.calls[0][0:2], ("file_convert", str(path.resolve())))
        self.assertIs(adapter.calls[0][2]["cloud_ok"], True)

    def test_upload_rejects_benign_named_symlink_to_credential_named_target(self):
        adapter = FakeAdapter()
        tool = self.make_tool(
            adapter, approval_requester=lambda *unused: {"approved": True}
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "id_rsa"
            target.write_text("fake credential-shaped fixture only", encoding="utf-8")
            link = root / "nice-notes.txt"
            link.symlink_to(target)

            result = self.run_tool(
                tool, {"operation": "file_convert", "source": str(link)}
            )

        self.assertFalse(result["success"])
        self.assertIn("sensitive", result["error"])
        self.assertEqual(adapter.calls, [])

    def test_upload_approval_shows_and_forwards_canonical_target(self):
        adapter = FakeAdapter()
        approvals = []

        def approve(tool_name, reason, rule_key):
            approvals.append((tool_name, reason, rule_key))
            return {"approved": True}

        tool = self.make_tool(adapter, approval_requester=approve)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "generated-note.txt"
            target.write_text("harmless generated fixture", encoding="utf-8")
            link = root / "nice-notes.txt"
            link.symlink_to(target)

            result = self.run_tool(
                tool, {"operation": "file_convert", "source": str(link)}
            )

            self.assertTrue(result["success"])
            self.assertIn(str(target.resolve()), approvals[0][1])
            self.assertEqual(adapter.calls[0][1], str(target.resolve()))

    def test_upload_rejects_symlink_target_substitution_after_approval(self):
        adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("first harmless fixture", encoding="utf-8")
            second.write_text("second harmless fixture", encoding="utf-8")
            link = root / "nice-notes.txt"
            link.symlink_to(first)

            def approve_then_retarget(*unused):
                link.unlink()
                link.symlink_to(second)
                return {"approved": True}

            result = self.run_tool(
                self.make_tool(adapter, approval_requester=approve_then_retarget),
                {"operation": "file_convert", "source": str(link)},
            )

        self.assertFalse(result["success"])
        self.assertIn("changed after approval", result["error"])
        self.assertEqual(adapter.calls, [])

    def test_upload_rejects_same_path_identity_substitution_after_approval(self):
        adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "harmless.txt"
            path.write_text("original harmless fixture", encoding="utf-8")

            def approve_then_replace(*unused):
                replacement = path.with_suffix(".replacement")
                replacement.write_text("replacement harmless fixture", encoding="utf-8")
                os.replace(str(replacement), str(path))
                return {"approved": True}

            result = self.run_tool(
                self.make_tool(adapter, approval_requester=approve_then_replace),
                {"operation": "file_convert", "source": str(path)},
            )

        self.assertFalse(result["success"])
        self.assertIn("changed after approval", result["error"])
        self.assertEqual(adapter.calls, [])

    def test_upload_post_approval_safety_changes_never_call_adapter(self):
        cases = ("unreadable", "over-limit", "forbidden-target")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                adapter = FakeAdapter()
                root = Path(directory)
                path = root / "harmless.txt"
                path.write_text("harmless generated fixture", encoding="utf-8")
                source = path
                if case == "forbidden-target":
                    source = root / "nice-notes.txt"
                    source.symlink_to(path)

                def approve_then_mutate(*unused):
                    if case == "unreadable":
                        path.chmod(0)
                    elif case == "over-limit":
                        path.write_bytes(b"x" * (self.safety_module.MAX_FILE_SIZE + 1))
                    else:
                        forbidden = root / "id_rsa"
                        forbidden.write_text("fake credential-shaped fixture only", encoding="utf-8")
                        source.unlink()
                        source.symlink_to(forbidden)
                    return {"approved": True}

                try:
                    result = self.run_tool(
                        self.make_tool(adapter, approval_requester=approve_then_mutate),
                        {"operation": "file_convert", "source": str(source)},
                    )
                finally:
                    path.chmod(0o600)

                self.assertFalse(result["success"])
                self.assertEqual(adapter.calls, [])

    def test_large_result_is_bounded_and_complete_artifact_is_byte_exact(self):
        adapter = FakeAdapter()
        adapter.search_result["combined"] = "x" * 4000
        with tempfile.TemporaryDirectory() as directory:
            tool = self.make_tool(
                adapter, inline_limit=300, scratch_root=Path(directory)
            )
            result = self.run_tool(tool, {
                "operation": "search_extract", "query": "large", "n": 1,
            })
            self.assertTrue(result["success"])
            self.assertTrue(result["truncated"])
            self.assertIn("preview", result["data"])
            self.assertNotIn("full", result["data"])
            artifact = Path(result["full_result_path"])
            raw = artifact.read_bytes()
            self.assertEqual(len(raw), result["original_bytes"])
            complete = json.loads(raw.decode("utf-8"))
            self.assertEqual(complete["data"]["combined"], "x" * 4000)
            self.assertFalse(complete.get("truncated", False))

    def test_shared_url_gate_rejects_secrets_private_dns_and_policy_before_adapter(self):
        guard = self.safety_module.guard_public_url
        cases = (
            "not-a-url",
            "https://user:pass@example.com/",
            "http://127.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://2130706433/",
            "https://example.com/?token=secret-value",
            "https://example.com/?%74oken=secret-value",
        )
        for url in cases:
            with self.subTest(url=url):
                with self.assertRaises(Exception):
                    asyncio.run(guard(url))

        with mock.patch.object(
            self.safety_module, "async_is_safe_url", new=mock.AsyncMock(return_value=False)
        ):
            with self.assertRaisesRegex(Exception, "private|internal"):
                asyncio.run(guard("https://private-dns.example/"))

        with mock.patch.object(
            self.safety_module, "async_is_safe_url", new=mock.AsyncMock(return_value=True)
        ), mock.patch.object(
            self.safety_module,
            "check_website_access",
            return_value={"message": "blocked fixture", "rule": "example.com"},
        ):
            with self.assertRaisesRegex(Exception, "blocked fixture"):
                asyncio.run(guard("https://example.com/"))

    def test_url_gate_rejects_legacy_ip_syntax_before_async_host_gate(self):
        guard = self.safety_module.guard_public_url
        with mock.patch.object(
            self.safety_module, "async_is_safe_url", new=mock.AsyncMock(return_value=True)
        ) as async_gate, mock.patch.object(
            self.safety_module, "check_website_access", return_value=None
        ):
            for url in ("http://0x7f.0.0.1/", "http://0177.0.0.1/"):
                with self.subTest(url=url):
                    with self.assertRaisesRegex(Exception, "canonical public notation"):
                        asyncio.run(guard(url))
        async_gate.assert_not_awaited()


@unittest.skipUnless(HOST_READY, "active Hermes host source/runtime required")
class RealHostContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "hermes-home"
        plugin_target = self.home / "plugins" / "markdown-new"
        plugin_target.parent.mkdir(parents=True)
        shutil.copytree(PLUGIN_DIR, plugin_target)
        (self.home / "config.yaml").write_text(
            "plugins:\n  enabled:\n    - markdown-new\n", encoding="utf-8"
        )
        self.env = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home)}, clear=False)
        self.env.start()

        from hermes_cli import plugins as plugins_module
        from tools.registry import registry

        self.plugins_module = plugins_module
        self.registry = registry
        self.old_manager = plugins_module._plugin_manager
        self.manager = plugins_module.PluginManager(scope_key=str(self.home))
        plugins_module._plugin_manager = self.manager
        self.manager.discover_and_load()

    def tearDown(self):
        try:
            self.manager.unload()
        finally:
            self.plugins_module._plugin_manager = self.old_manager
            try:
                import model_tools
                loop = model_tools._tool_loop
                if loop is not None and not loop.is_closed():
                    loop.close()
                model_tools._tool_loop = None
            except Exception:
                pass
            self.env.stop()
            self.temp.cleanup()

    def test_real_manifest_manager_registers_one_provider_one_tool_one_hook_offline(self):
        loaded = self.manager._plugins["markdown-new"]
        self.assertTrue(loaded.enabled)
        self.assertIsNone(loaded.error)
        entry = self.registry.get_entry("markdown_new", scope=self.manager.scope_key)
        self.assertIsNotNone(entry)
        self.assertTrue(entry.is_async)
        self.assertEqual(len(self.manager._hooks.get("pre_tool_call", [])), 1)

        from agent.web_search_registry import get_provider
        provider = get_provider("markdown-new")
        self.assertIsNotNone(provider)
        self.assertTrue(provider.supports_extract())

    def test_real_dispatch_denies_without_bridge_and_approved_fixture_runs_once(self):
        entry = self.registry.get_entry("markdown_new", scope=self.manager.scope_key)
        tool = entry.handler.__self__
        adapter = FakeAdapter()
        tool.adapter = adapter

        with mock.patch(
            "tools.approval.request_tool_approval",
            return_value={"approved": False, "message": "no bridge"},
        ):
            direct = json.loads(self.registry.dispatch(
                "markdown_new", {"operation": "crawl_cancel", "job_id": "fixture-job"},
                scope=self.manager.scope_key, session_id="session-direct",
            ))
        self.assertFalse(direct["success"])
        self.assertEqual(adapter.calls, [])

        args = {"operation": "crawl_cancel", "job_id": "fixture-job"}
        with mock.patch(
            "tools.approval.request_tool_approval", return_value={"approved": True}
        ) as approval:
            blocked, modified = self.plugins_module._dispatch_pre_tool_call_hooks(
                "markdown_new", args, session_id="session-1", tool_call_id="call-1"
            )
            self.assertIsNone(blocked)
            self.assertIsNone(modified)
            approved = json.loads(self.registry.dispatch(
                "markdown_new", args, scope=self.manager.scope_key, session_id="session-1"
            ))
        self.assertTrue(approved["success"])
        self.assertEqual([call[0] for call in adapter.calls], ["crawl_cancel"])
        approval.assert_called_once()

    def test_skip_pre_tool_call_cannot_authorize_sensitive_handler(self):
        entry = self.registry.get_entry("markdown_new", scope=self.manager.scope_key)
        tool = entry.handler.__self__
        adapter = FakeAdapter()
        tool.adapter = adapter
        with mock.patch(
            "tools.approval.request_tool_approval",
            return_value={"approved": False, "message": "no bridge"},
        ):
            result = json.loads(self.registry.dispatch(
                "markdown_new",
                {"operation": "crawl_cancel", "job_id": "fixture-job", "approved": True},
                scope=self.manager.scope_key, session_id="session-1",
            ))
        self.assertFalse(result["success"])
        self.assertEqual(adapter.calls, [])


if __name__ == "__main__":
    unittest.main()
