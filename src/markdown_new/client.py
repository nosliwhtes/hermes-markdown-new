"""HTTP client for the frozen Markdown.new API contract."""

import json
import math
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Union
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import OpenerDirector, Request, build_opener

from .safety import MAX_FILE_SIZE, ValidationError, validate_local_file, validate_public_url

DEFAULT_BASE_URL = "https://markdown.new"
DEFAULT_TIMEOUT = 30.0
ERROR_BODY_LIMIT = 4096
RESPONSE_BODY_LIMIT = 64 * 1024 * 1024
_RETRY_DELAY = 1.0
_ACTIVE_CRAWL_STATUSES = frozenset(
    ("queued", "pending", "running", "processing", "starting", "in_progress")
)
_FAILED_CRAWL_STATUSES = frozenset(("failed", "error", "cancelled", "canceled"))

JSONObject = Dict[str, Any]


class APIError(RuntimeError):
    """A network, HTTP, service-contract, or crawl lifecycle failure."""

    def __init__(
        self, message: str, *, status: Optional[int] = None, body: str = ""
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class MarkdownNewClient:
    """Small synchronous client with one bounded retry for retryable HTTP errors."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        *,
        opener: Optional[OpenerDirector] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValidationError("request timeout must be finite and greater than zero")
        self.base_url = base_url.rstrip("/")
        if not self.base_url:
            raise ValidationError("API base URL must not be empty")
        self.timeout = float(timeout)
        self._opener = opener or build_opener()
        self._sleep = sleep
        self._clock = clock

    def convert_url(
        self,
        url: str,
        *,
        method: Optional[str] = None,
        retain_images: bool = False,
    ) -> JSONObject:
        validate_public_url(url)
        if method is not None and method not in ("auto", "ai", "browser"):
            raise ValidationError("method must be auto, ai, or browser")

        payload: JSONObject = {"url": url}
        if method is not None:
            payload["method"] = method
        if retain_images:
            payload["retain_images"] = True

        result = self._request_json("POST", "/", payload)
        self._require_string(result, "content")
        return result

    def search(
        self,
        query: str,
        *,
        n: int = 3,
        gl: str = "us",
        hl: str = "en",
        retain_images: bool = False,
    ) -> JSONObject:
        if not isinstance(query, str) or not query.strip():
            raise ValidationError("search query must not be empty")
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 5:
            raise ValidationError("search n must be between 1 and 5")
        if not gl or not hl:
            raise ValidationError("search gl and hl must not be empty")

        payload: JSONObject = {
            "q": query,
            "n": n,
            "gl": gl,
            "hl": hl,
            "retain_images": bool(retain_images),
            "format": "json",
        }
        result = self._request_json("POST", "/search", payload)
        self._require_string(result, "combined")
        return result

    def upload_file(self, value: Union[str, Path]) -> JSONObject:
        path = validate_local_file(value)
        try:
            with path.open("rb") as stream:
                content = stream.read(MAX_FILE_SIZE + 1)
        except OSError as error:
            raise ValidationError("cannot read local file '{}': {}".format(path, error)) from error
        if len(content) > MAX_FILE_SIZE:
            raise ValidationError("local file exceeds the 10 MiB upload limit: {}".format(path))

        boundary = "----markdown-new-{}".format(uuid.uuid4().hex)
        filename = _safe_multipart_filename(path.name)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        prefix = (
            "--{boundary}\r\n"
            "Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            "Content-Type: {content_type}\r\n"
            "\r\n"
        ).format(
            boundary=boundary,
            filename=filename,
            content_type=content_type,
        ).encode("utf-8")
        body = prefix + content + "\r\n--{}--\r\n".format(boundary).encode("ascii")
        headers = {
            "Accept": "application/json",
            "Content-Type": "multipart/form-data; boundary={}".format(boundary),
        }

        raw = self._request("POST", "/convert", body=body, headers=headers)
        result = self._decode_json_object(raw)
        data = result.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("content"), str):
            raise APIError("service response is missing string field data.content")
        return result

    def start_crawl(
        self,
        url: str,
        *,
        limit: int = 10,
        depth: int = 1,
        render: bool = False,
        source: Optional[str] = None,
        max_age: Optional[int] = None,
        modified_since: Optional[int] = None,
        include_external_links: bool = False,
        include_subdomains: bool = False,
        include_patterns: Optional[Sequence[str]] = None,
        exclude_patterns: Optional[Sequence[str]] = None,
    ) -> JSONObject:
        validate_public_url(url)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValidationError("crawl limit must be between 1 and 500")
        if (
            isinstance(depth, bool)
            or not isinstance(depth, int)
            or not 1 <= depth <= 10
        ):
            raise ValidationError("crawl depth must be between 1 and 10")
        if max_age is not None and (
            isinstance(max_age, bool)
            or not isinstance(max_age, int)
            or not 0 <= max_age <= 604800
        ):
            raise ValidationError("crawl max age must be between 0 and 604800")
        if source is not None and source not in ("all", "sitemaps", "links"):
            raise ValidationError("crawl source must be all, sitemaps, or links")
        if modified_since is not None and (
            isinstance(modified_since, bool)
            or not isinstance(modified_since, int)
            or modified_since < 0
        ):
            raise ValidationError("crawl modified since must be a non-negative Unix timestamp")
        _validate_patterns(include_patterns, "include")
        _validate_patterns(exclude_patterns, "exclude")

        payload: JSONObject = {
            "url": url,
            "limit": limit,
            "depth": depth,
            "render": bool(render),
            "includeExternalLinks": bool(include_external_links),
            "includeSubdomains": bool(include_subdomains),
        }
        if source is not None:
            payload["source"] = source
        if max_age is not None:
            payload["maxAge"] = max_age
        if modified_since is not None:
            payload["modifiedSince"] = modified_since
        if include_patterns is not None:
            payload["includePatterns"] = list(include_patterns)
        if exclude_patterns is not None:
            payload["excludePatterns"] = list(exclude_patterns)

        result = self._request_json("POST", "/crawl", payload)
        self._require_string(result, "jobId")
        return result

    def get_crawl_status(self, job_id: str) -> JSONObject:
        path = "/crawl/status/{}?format=json".format(_encoded_job_id(job_id))
        result = self._request_json("GET", path)
        status_result = result.get("result")
        if not isinstance(status_result, dict) or not isinstance(
            status_result.get("status"), str
        ):
            raise APIError(
                "crawl job {} status response is missing result.status".format(job_id)
            )
        return result

    def download_crawl_markdown(
        self, job_id: str, *, retain_images: bool = False
    ) -> str:
        path = "/crawl/status/{}".format(_encoded_job_id(job_id))
        if retain_images:
            path += "?retain_images=true"
        raw = self._request("GET", path)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise APIError(
                "crawl job {} returned non-UTF-8 Markdown".format(job_id)
            ) from error

    def cancel_crawl(self, job_id: str) -> JSONObject:
        path = "/crawl/status/{}".format(_encoded_job_id(job_id))
        return self._request_json("DELETE", path)

    def crawl(
        self,
        url: str,
        *,
        output_format: str = "markdown",
        retain_images: bool = False,
        poll_interval: float = 2.0,
        poll_timeout: float = 300.0,
        **start_options: Any,
    ) -> Union[JSONObject, str]:
        if output_format not in ("markdown", "json"):
            raise ValidationError("crawl output format must be markdown or json")
        if not math.isfinite(poll_interval) or poll_interval < 0:
            raise ValidationError(
                "crawl poll interval must be finite and zero or greater"
            )
        if not math.isfinite(poll_timeout) or poll_timeout <= 0:
            raise ValidationError(
                "crawl poll timeout must be finite and greater than zero"
            )

        started = self.start_crawl(url, **start_options)
        job_id = self._require_string(started, "jobId")
        deadline = self._clock() + poll_timeout

        while True:
            try:
                status_response = self.get_crawl_status(job_id)
            except APIError as error:
                raise _crawl_context_error(job_id, "status check failed", error) from error

            result = status_response["result"]
            status = result["status"]
            normalized_status = status.lower()
            if normalized_status == "completed":
                records = result.get("records")
                if not isinstance(records, list):
                    raise APIError(
                        "crawl job {} completed without result.records".format(job_id)
                    )
                if output_format == "json":
                    return status_response
                try:
                    return self.download_crawl_markdown(
                        job_id, retain_images=retain_images
                    )
                except APIError as error:
                    raise _crawl_context_error(
                        job_id, "Markdown download failed", error
                    ) from error

            if normalized_status in _FAILED_CRAWL_STATUSES:
                detail = _bounded_json(result)
                raise APIError(
                    "crawl job {} failed with status {}: {}".format(
                        job_id, status, detail
                    )
                )
            if normalized_status not in _ACTIVE_CRAWL_STATUSES:
                detail = _bounded_json(result)
                raise APIError(
                    "crawl job {} returned unknown status {}: {}".format(
                        job_id, status, detail
                    )
                )

            remaining = deadline - self._clock()
            if remaining <= 0:
                raise APIError("crawl job {} timed out while {}".format(job_id, status))
            self._sleep(min(float(poll_interval), remaining))

    def _request_json(
        self, method: str, path: str, payload: Optional[Mapping[str, Any]] = None
    ) -> JSONObject:
        body = None
        headers: Dict[str, str] = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            headers["Content-Type"] = "application/json"
        raw = self._request(method, path, body=body, headers=headers)
        return self._decode_json_object(raw)

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[bytes] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> bytes:
        request_headers = {
            "Accept": "*/*",
            "User-Agent": "markdown-new/0.1.0",
        }
        if headers:
            request_headers.update(headers)
        request = Request(
            self.base_url + path,
            data=body,
            headers=request_headers,
            method=method,
        )

        for attempt in range(2):
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    length = response.headers.get("Content-Length")
                    if length and length.isascii() and length.isdecimal():
                        # Bound conversion before int(), including on Python versions
                        # without an integer-string digit limit. Zero padding is valid.
                        significant_length = length.lstrip("0") or "0"
                        if (len(significant_length) > len(str(RESPONSE_BODY_LIMIT))
                                or int(significant_length) > RESPONSE_BODY_LIMIT):
                            raise APIError("service response exceeds the 64 MiB limit")
                    raw = response.read(RESPONSE_BODY_LIMIT + 1)
                    if len(raw) > RESPONSE_BODY_LIMIT:
                        raise APIError("service response exceeds the 64 MiB limit")
                    return raw
            except HTTPError as error:
                error_body = _read_bounded_error(error)
                retryable = error.code == 429 or 500 <= error.code <= 599
                if retryable and attempt == 0:
                    self._sleep(_RETRY_DELAY)
                    continue
                reason = "HTTP {} {}".format(error.code, error.reason or "error")
                if error_body:
                    reason += ": {}".format(error_body)
                raise APIError(reason, status=error.code, body=error_body) from error
            except (URLError, TimeoutError, OSError) as error:
                detail = getattr(error, "reason", error)
                raise APIError("network failure: {}".format(detail)) from error

        raise AssertionError("unreachable retry loop")

    def _decode_json_object(self, raw: bytes) -> JSONObject:
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            detail = _bounded_bytes(raw)
            raise APIError("service returned invalid JSON: {}".format(detail)) from error
        if not isinstance(value, dict):
            raise APIError("service returned JSON that is not an object")
        if value.get("success") is False:
            detail = value.get("error") or value.get("message") or _bounded_json(value)
            raise APIError("service reported failure: {}".format(detail))
        return value

    @staticmethod
    def _require_string(value: Mapping[str, Any], field: str) -> str:
        selected = value.get(field)
        if not isinstance(selected, str) or not selected:
            raise APIError("service response is missing string field {}".format(field))
        return selected


def _safe_multipart_filename(value: str) -> str:
    return value.replace("\\", "_").replace('"', "_").replace("\r", "_").replace("\n", "_")


def _validate_patterns(value: Optional[Sequence[str]], label: str) -> None:
    if value is None:
        return
    if isinstance(value, (str, bytes)) or not all(
        isinstance(pattern, str) and pattern for pattern in value
    ):
        raise ValidationError("crawl {} patterns must be non-empty strings".format(label))


def _encoded_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not job_id:
        raise ValidationError("crawl job ID must not be empty")
    return quote(job_id, safe="")


def _read_bounded_error(error: HTTPError) -> str:
    try:
        raw = error.read(ERROR_BODY_LIMIT + 1)
    finally:
        error.close()
    return _bounded_bytes(raw)


def _bounded_bytes(raw: bytes) -> str:
    truncated = len(raw) > ERROR_BODY_LIMIT
    selected = raw[:ERROR_BODY_LIMIT]
    text = selected.decode("utf-8", errors="replace")
    if truncated:
        text += "\n...[truncated]"
    return text


def _bounded_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _bounded_bytes(raw)


def _crawl_context_error(job_id: str, action: str, error: APIError) -> APIError:
    return APIError(
        "crawl job {} {}: {}".format(job_id, action, error),
        status=error.status,
        body=error.body,
    )
