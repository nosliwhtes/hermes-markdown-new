import tempfile
import unittest
from pathlib import Path

from markdown_new.safety import MAX_FILE_SIZE, ValidationError, validate_local_file, validate_public_url


class PublicURLValidationTests(unittest.TestCase):
    def test_accepts_public_http_and_https_urls(self) -> None:
        accepted = (
            "https://example.com/path?q=1#fragment",
            "http://8.8.8.8/resource",
            "https://[2606:4700:4700::1111]/",
        )
        for value in accepted:
            with self.subTest(value=value):
                self.assertEqual(validate_public_url(value), value)

    def test_rejects_non_public_urls_and_credentials(self) -> None:
        rejected = (
            "",
            "example.com/no-scheme",
            "ftp://example.com/file",
            "file:///tmp/private.txt",
            "https://user:secret@example.com/",
            "http://localhost/",
            "http://api.localhost/",
            "http://printer.local/",
            "http://intranet/",
            "http://127.0.0.1/",
            "http://127.1/",
            "http://0177.0.0.1/",
            "http://0x7f.0.0.1/",
            "http://10.0.0.1/",
            "http://169.254.10.20/",
            "http://192.168.1.5/",
            "http://192.0.2.1/",
            "http://[::1]/",
            "http://[fe80::1]/",
        )
        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    validate_public_url(value)


class LocalFileValidationTests(unittest.TestCase):
    def test_accepts_regular_file_at_exact_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "maximum.bin"
            with path.open("wb") as stream:
                stream.truncate(MAX_FILE_SIZE)

            self.assertEqual(validate_local_file(path), path)

    def test_rejects_file_larger_than_ten_mib(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "too-large.bin"
            with path.open("wb") as stream:
                stream.truncate(MAX_FILE_SIZE + 1)

            with self.assertRaisesRegex(ValidationError, "10 MiB"):
                validate_local_file(path)

    def test_rejects_missing_path_and_non_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path in (root / "missing.txt", root):
                with self.subTest(path=path):
                    with self.assertRaises(ValidationError):
                        validate_local_file(path)


if __name__ == "__main__":
    unittest.main()
