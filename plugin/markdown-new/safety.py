"""Shared trust-boundary checks for additive Markdown.new URL and file operations."""

import ipaddress
import os
import socket
import stat
from pathlib import Path
from typing import Any, Dict, Tuple
from urllib.parse import unquote, urlsplit

from agent.redact import _PREFIX_RE
from tools.url_safety import (
    async_is_safe_url,
    normalize_url_for_request,
    sensitive_query_param_name,
)
from tools.website_policy import check_website_access


MAX_FILE_SIZE = 10 * 1024 * 1024
_SENSITIVE_NAMES = frozenset(
    {
        ".env",
        ".netrc",
        "credentials",
        "credentials.json",
        "id_dsa",
        "id_ed25519",
        "id_rsa",
        "known_hosts",
        "work-hermes-secrets.md",
    }
)
_SENSITIVE_SUFFIXES = frozenset((".key", ".kdbx", ".p12", ".pem", ".pfx"))
_SENSITIVE_PARTS = frozenset((".aws", ".gnupg", ".ssh"))


class SafetyError(ValueError):
    def __init__(self, message: str, *, details: Dict[str, Any] = None) -> None:
        super().__init__(message)
        self.details = details or {}


async def guard_public_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SafetyError("URL must be a non-empty public http or https URL")
    raw = value
    normalized = normalize_url_for_request(raw)
    candidates = (raw, unquote(raw), normalized, unquote(normalized))
    if any(_PREFIX_RE.search(candidate) for candidate in candidates):
        raise SafetyError("Blocked: URL contains what appears to be an API key or token")
    if any(sensitive_query_param_name(candidate) for candidate in candidates):
        raise SafetyError("Blocked: URL contains a credential-bearing query parameter")
    _validate_public_http_syntax(normalized)
    if not await async_is_safe_url(normalized):
        raise SafetyError("Blocked: URL targets a private or internal network address")
    policy = check_website_access(normalized)
    if policy is not None:
        raise SafetyError(str(policy.get("message") or "Blocked by website policy"), details=policy)
    return normalized


def is_remote_source(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return urlsplit(value).scheme.lower() in ("http", "https")
    except ValueError:
        return False


def validate_upload_path(value: str) -> Path:
    return inspect_upload_path(value)[0]


def inspect_upload_path(value: str) -> Tuple[Path, Tuple[int, int, int, int, int]]:
    requested = Path(value).expanduser()
    _reject_sensitive_path(requested)
    try:
        path = requested.resolve(strict=True)
    except OSError as error:
        raise SafetyError("cannot access local file '{}': {}".format(requested, error)) from error
    _reject_sensitive_path(path)
    try:
        metadata = path.stat()
    except OSError as error:
        raise SafetyError("cannot access local file '{}': {}".format(path, error)) from error
    if not stat.S_ISREG(metadata.st_mode):
        raise SafetyError("local path is not a regular file: {}".format(path))
    if metadata.st_size > MAX_FILE_SIZE:
        raise SafetyError("local file exceeds the 10 MiB upload limit: {}".format(path))
    try:
        with path.open("rb"):
            pass
    except OSError as error:
        raise SafetyError("local file is not readable '{}': {}".format(path, error)) from error
    identity = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )
    return path, identity


def _reject_sensitive_path(path: Path) -> None:
    lowered_name = path.name.lower()
    lowered_parts = {part.lower() for part in path.parts}
    if (
        lowered_name in _SENSITIVE_NAMES
        or path.suffix.lower() in _SENSITIVE_SUFFIXES
        or lowered_parts & _SENSITIVE_PARTS
        or any(marker in lowered_name for marker in ("password", "secret", "token", "credential"))
    ):
        raise SafetyError("sensitive local files may not be uploaded: {}".format(path))


def _validate_public_http_syntax(value: str) -> None:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise SafetyError("invalid URL: {}".format(error)) from error
    if parsed.scheme.lower() not in ("http", "https"):
        raise SafetyError("URL must use http or https")
    if not parsed.netloc or not hostname:
        raise SafetyError("URL must include a public hostname")
    if parsed.username is not None or parsed.password is not None:
        raise SafetyError("credentials are not allowed in URLs")
    if port is not None and not 1 <= port <= 65535:
        raise SafetyError("URL port must be between 1 and 65535")
    host = hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise SafetyError("URL hostname must be public")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if _is_legacy_ipv4_address(host):
            raise SafetyError("URL IP address must use canonical public notation")
        if "." not in host:
            raise SafetyError("URL hostname must be a public DNS name")
    else:
        if not address.is_global:
            raise SafetyError("URL IP address must be public")


def _is_legacy_ipv4_address(host: str) -> bool:
    try:
        socket.inet_aton(host)
    except OSError:
        return False
    return True


def snapshot_upload(upload: Tuple[Path, Tuple[int, int, int, int, int]]) -> bytes:
    """Capture bounded bytes from the inspected inode before asking for approval."""
    path, identity = upload
    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if nofollow is None or nonblock is None:
        raise SafetyError("secure local uploads are unavailable on this platform")
    try:
        descriptor = os.open(str(path), os.O_RDONLY | nofollow | nonblock)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            def matches(metadata):
                return stat.S_ISREG(metadata.st_mode) and (
                    metadata.st_dev, metadata.st_ino, metadata.st_size,
                    metadata.st_mtime_ns, metadata.st_ctime_ns,
                ) == identity
            if not matches(before):
                raise SafetyError("local upload identity changed before approval")
            content = stream.read(MAX_FILE_SIZE + 1)
            if len(content) > MAX_FILE_SIZE or not matches(os.fstat(stream.fileno())):
                raise SafetyError("local upload identity changed before approval")
            return content
    except OSError as error:
        raise SafetyError("cannot snapshot local upload") from error
