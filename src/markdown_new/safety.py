"""Validation at the local-file and public-URL trust boundaries."""

import ipaddress
import os
import socket
import stat
from pathlib import Path
from typing import Union
from urllib.parse import urlsplit

MAX_FILE_SIZE = 10 * 1024 * 1024


class ValidationError(ValueError):
    """A local validation failure that must be reported as CLI exit 2."""


def validate_public_url(value: str) -> str:
    """Return *value* when it is an uncredentialed public HTTP(S) URL."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValidationError("URL must be a non-empty public http or https URL")
    if any(ord(character) < 32 for character in value):
        raise ValidationError("URL must not contain control characters")

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValidationError("invalid URL: {}".format(error)) from error

    if parsed.scheme.lower() not in ("http", "https"):
        raise ValidationError("URL must use http or https")
    if not parsed.netloc or not hostname:
        raise ValidationError("URL must include a public hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValidationError("credentials are not allowed in URLs")
    if port is not None and not 1 <= port <= 65535:
        raise ValidationError("URL port must be between 1 and 65535")

    host = hostname.rstrip(".").lower()
    if not host:
        raise ValidationError("URL must include a public hostname")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValidationError("URL hostname must be public")
    if "%" in host:
        raise ValidationError("scoped IP addresses are not public URLs")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if _is_legacy_ipv4_address(host):
            raise ValidationError("URL IP address must use canonical public notation")
        _validate_public_dns_name(host)
    else:
        if not address.is_global:
            raise ValidationError("URL IP address must be public")

    return value


def _is_legacy_ipv4_address(host: str) -> bool:
    try:
        socket.inet_aton(host)
    except OSError:
        return False
    return True


def _validate_public_dns_name(host: str) -> None:
    if "." not in host:
        raise ValidationError("URL hostname must be a public DNS name")
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValidationError("URL hostname is invalid") from error
    if len(ascii_host) > 253:
        raise ValidationError("URL hostname is too long")

    labels = ascii_host.split(".")
    for label in labels:
        if not label or len(label) > 63:
            raise ValidationError("URL hostname is invalid")
        if label[0] == "-" or label[-1] == "-":
            raise ValidationError("URL hostname is invalid")
        if not all(character.isalnum() or character == "-" for character in label):
            raise ValidationError("URL hostname is invalid")


def validate_local_file(value: Union[str, os.PathLike]) -> Path:
    """Return a readable regular file no larger than the 10 MiB service limit."""
    path = Path(value).expanduser()
    try:
        metadata = path.stat()
    except OSError as error:
        raise ValidationError("cannot access local file '{}': {}".format(path, error)) from error

    if not stat.S_ISREG(metadata.st_mode):
        raise ValidationError("local path is not a regular file: {}".format(path))
    if metadata.st_size > MAX_FILE_SIZE:
        raise ValidationError("local file exceeds the 10 MiB upload limit: {}".format(path))

    try:
        with path.open("rb"):
            pass
    except OSError as error:
        raise ValidationError("local file is not readable '{}': {}".format(path, error)) from error

    return path
