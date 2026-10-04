"""Bounded subprocess adapter for the reviewed markdown-new CLI."""

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


MINIMUM_VERSION = (0, 2, 0)
STDOUT_LIMIT = 64 * 1024 * 1024
STDERR_LIMIT = 8192
_VERSION_RE = re.compile(r"^markdown-new (\d+)\.(\d+)\.(\d+)$")


class AdapterError(RuntimeError):
    """The CLI is absent, incompatible, failed, timed out, or returned invalid output."""


class MarkdownNewAdapter:
    def __init__(
        self,
        binary: Optional[str] = None,
        *,
        stdout_limit: int = STDOUT_LIMIT,
        stderr_limit: int = STDERR_LIMIT,
        process_grace: float = 5.0,
    ) -> None:
        self.binary = binary or shutil.which("markdown-new")
        self.stdout_limit = int(stdout_limit)
        self.stderr_limit = int(stderr_limit)
        self.process_grace = float(process_grace)
        self._version_checked = False
        self._version_lock = threading.Lock()

    def is_available(self) -> bool:
        return bool(self.binary and Path(self.binary).is_file())

    def convert_url(
        self,
        url: str,
        *,
        method: Optional[str] = None,
        retain_images: bool = False,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        args = ["url", url]
        _option(args, "--method", method)
        _flag(args, "--retain-images", retain_images)
        return self._json(args, timeout)

    def search(
        self,
        query: str,
        *,
        n: int = 3,
        gl: str = "us",
        hl: str = "en",
        retain_images: bool = False,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        args = ["search", query, "--n", str(n), "--gl", gl, "--hl", hl]
        _flag(args, "--retain-images", retain_images)
        return self._json(args, timeout)

    def start_crawl(self, url: str, *, timeout: float = 30.0, **options: Any) -> Dict[str, Any]:
        args = ["crawl-start", url]
        _crawl_options(args, options)
        return self._json(args, timeout)

    def crawl_status(self, job_id: str, *, timeout: float = 30.0) -> Dict[str, Any]:
        return self._json(["crawl-status", job_id], timeout)

    def crawl_download(
        self, job_id: str, *, retain_images: bool = False, timeout: float = 30.0
    ) -> Dict[str, Any]:
        args = ["crawl-download", job_id]
        _flag(args, "--retain-images", retain_images)
        return self._json(args, timeout)

    def crawl_cancel(self, job_id: str, *, timeout: float = 30.0) -> Dict[str, Any]:
        return self._json(["crawl-cancel", job_id], timeout)

    def file_convert(
        self,
        source: str,
        *,
        method: Optional[str] = None,
        retain_images: bool = False,
        cloud_ok: bool = False,
        content: Optional[bytes] = None,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        if content is not None:
            # Only the approved immutable snapshot crosses the subprocess boundary.
            with tempfile.TemporaryDirectory(prefix="markdown-new-upload-") as directory:
                snapshot = Path(directory) / Path(source).name
                snapshot.write_bytes(content)
                snapshot.chmod(0o400)
                return self.file_convert(
                    str(snapshot), method=method, retain_images=retain_images,
                    cloud_ok=cloud_ok, timeout=timeout,
                )
        args = ["file", source]
        _option(args, "--method", method)
        _flag(args, "--retain-images", retain_images)
        _flag(args, "--cloud-ok", cloud_ok)
        return self._json(args, timeout)

    def _json(self, args: Sequence[str], timeout: float) -> Dict[str, Any]:
        _finite_positive(timeout, "timeout")
        self._ensure_compatible()
        raw = self._run(list(args) + ["--format", "json", "--timeout", str(float(timeout))], timeout + self.process_grace)
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise AdapterError("markdown-new CLI returned invalid JSON") from error
        if not isinstance(value, dict):
            raise AdapterError("markdown-new CLI returned JSON that is not an object")
        return value

    def _ensure_compatible(self) -> None:
        if self._version_checked:
            return
        with self._version_lock:
            if self._version_checked:
                return
            raw = self._run(["--version"], 5.0, check_version=False).strip()
            match = _VERSION_RE.fullmatch(raw)
            if match is None:
                raise AdapterError("cannot determine markdown-new CLI version from {!r}".format(raw[:200]))
            version = tuple(int(part) for part in match.groups())
            if version < MINIMUM_VERSION:
                raise AdapterError(
                    "markdown-new CLI {} is too old; install reviewed version 0.2.0 or newer before enabling the plugin".format(
                        ".".join(str(part) for part in version)
                    )
                )
            self._version_checked = True

    def _run(
        self, args: Sequence[str], wall_timeout: float, *, check_version: bool = True
    ) -> str:
        del check_version
        if not self.is_available():
            raise AdapterError("markdown-new CLI was not found on PATH")
        _finite_positive(wall_timeout, "subprocess timeout")
        argv = [str(self.binary)] + [str(item) for item in args]
        environment = os.environ.copy()
        for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
            environment.pop(name, None)
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(
                argv,
                shell=False,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
            )
            try:
                returncode = process.wait(timeout=wall_timeout)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.wait()
                raise AdapterError(
                    "markdown-new CLI timed out after {:.1f} seconds".format(wall_timeout)
                ) from error
            stdout.seek(0, 2)
            stdout_size = stdout.tell()
            stderr.seek(0, 2)
            stderr_size = stderr.tell()
            if stdout_size > self.stdout_limit:
                raise AdapterError(
                    "markdown-new CLI output exceeded {} bytes".format(self.stdout_limit)
                )
            stdout.seek(0)
            stderr.seek(0)
            output = stdout.read().decode("utf-8", errors="replace")
            error_text = stderr.read(self.stderr_limit + 1).decode("utf-8", errors="replace")
            if stderr_size > self.stderr_limit:
                error_text = error_text[: self.stderr_limit] + "\n...[truncated]"
            if returncode != 0:
                detail = error_text.strip() or output.strip() or "no error detail"
                raise AdapterError(
                    "markdown-new CLI exited {}: {}".format(returncode, detail)
                )
            return output


def _finite_positive(value: float, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterError("{} must be a finite positive number".format(label))
    if not math.isfinite(float(value)) or float(value) <= 0:
        raise AdapterError("{} must be a finite positive number".format(label))


def _option(args: List[str], name: str, value: Any) -> None:
    if value is not None:
        args.extend((name, str(value)))


def _flag(args: List[str], name: str, enabled: Any) -> None:
    if enabled:
        args.append(name)


def _crawl_options(args: List[str], options: Dict[str, Any]) -> None:
    names = {
        "limit": "--limit",
        "depth": "--depth",
        "source": "--source",
        "max_age": "--max-age",
        "modified_since": "--modified-since",
    }
    for key, option in names.items():
        _option(args, option, options.get(key))
    flags = {
        "render": "--render",
        "include_external_links": "--include-external-links",
        "include_subdomains": "--include-subdomains",
    }
    for key, option in flags.items():
        _flag(args, option, options.get(key, False))
    for key, option in (
        ("include_patterns", "--include-pattern"),
        ("exclude_patterns", "--exclude-pattern"),
    ):
        for pattern in options.get(key) or ():
            args.extend((option, pattern))
