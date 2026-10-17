"""Offline tests of the real plugin snapshot boundary, without a Hermes host."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Snapshotting does not call host URL/redaction helpers. Stub only those
        # imports so this security boundary is exercised in standalone CI too.
        dependencies = {name: types.ModuleType(name) for name in (
            "agent", "agent.redact", "tools", "tools.url_safety", "tools.website_policy"
        )}
        dependencies["agent.redact"]._PREFIX_RE = mock.Mock()
        for name in ("async_is_safe_url", "normalize_url_for_request", "sensitive_query_param_name"):
            setattr(dependencies["tools.url_safety"], name, mock.Mock(side_effect=AssertionError("unexpected URL helper")))
        dependencies["tools.website_policy"].check_website_access = mock.Mock(side_effect=AssertionError("unexpected policy helper"))
        path = Path(__file__).resolve().parents[1] / "plugin" / "markdown-new" / "safety.py"
        spec = importlib.util.spec_from_file_location("offline_upload_safety", path)
        cls.safety = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, dependencies):
            spec.loader.exec_module(cls.safety)

    def test_missing_secure_open_flags_fail_before_open(self):
        for missing in ("O_NOFOLLOW", "O_NONBLOCK"):
            with self.subTest(flag=missing):
                flags = {"O_RDONLY": os.O_RDONLY, "O_NOFOLLOW": 1, "O_NONBLOCK": 2, "open": mock.Mock()}
                del flags[missing]
                platform_os = types.SimpleNamespace(**flags)
                with mock.patch.object(self.safety, "os", platform_os):
                    with self.assertRaisesRegex(self.safety.SafetyError, "secure local uploads.*platform"):
                        self.safety.snapshot_upload((Path("fixture.txt"), (0, 0, 0, 0, 0)))
                platform_os.open.assert_not_called()

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_NONBLOCK"), "secure snapshot flags unavailable")
    def test_supported_platform_preserves_exact_bytes_and_identity_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.txt"
            path.write_bytes(b"fixture\r\n\x00bytes")
            metadata = path.stat()
            identity = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)
            self.assertEqual(self.safety.snapshot_upload((path, identity)), b"fixture\r\n\x00bytes")
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(self.safety.SafetyError, "identity changed"):
                self.safety.snapshot_upload((path, identity))

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_NONBLOCK"), "secure snapshot flags unavailable")
    def test_symlink_substitution_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.txt"
            path.write_bytes(b"fixture")
            metadata = path.stat()
            identity = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)
            target = Path(folder) / "other.txt"
            target.write_bytes(b"other")
            path.unlink()
            path.symlink_to(target)
            with self.assertRaisesRegex(self.safety.SafetyError, "cannot snapshot"):
                self.safety.snapshot_upload((path, identity))
