"""Command-line interface for Markdown.new conversions."""

import argparse
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, TextIO, Union
from urllib.parse import urlsplit

from . import __version__
from .client import APIError, DEFAULT_TIMEOUT, MarkdownNewClient
from .safety import ValidationError, validate_local_file, validate_public_url

DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_POLL_TIMEOUT = 300.0
DEFAULT_CRAWL_LIMIT = 10
DEFAULT_CRAWL_DEPTH = 1

ClientFactory = Callable[..., MarkdownNewClient]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="markdown-new",
        description="Convert public web content with Markdown.new.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s {}".format(__version__))
    commands = parser.add_subparsers(dest="command", required=True)

    url_parser = commands.add_parser("url", help="convert one public URL")
    url_parser.add_argument("url")
    _add_conversion_options(url_parser)
    _add_common_output_options(url_parser)

    search_parser = commands.add_parser("search", help="search and extract 1-5 public pages")
    search_parser.add_argument("query")
    search_parser.add_argument("-n", "--n", type=_search_count, default=3, metavar="COUNT")
    search_parser.add_argument("--gl", default="us", help="search country code")
    search_parser.add_argument("--hl", default="en", help="search language code")
    search_parser.add_argument("--retain-images", action="store_true")
    _add_common_output_options(search_parser)

    crawl_parser = commands.add_parser("crawl", help="start and poll a bounded public crawl")
    crawl_parser.add_argument("url")
    _add_crawl_start_options(crawl_parser)
    crawl_parser.add_argument("--retain-images", action="store_true")
    crawl_parser.add_argument(
        "--poll-interval", type=_nonnegative_float, default=DEFAULT_POLL_INTERVAL
    )
    crawl_parser.add_argument(
        "--poll-timeout", type=_positive_float, default=DEFAULT_POLL_TIMEOUT
    )
    _add_common_output_options(crawl_parser)

    crawl_start_parser = commands.add_parser(
        "crawl-start", help="start a bounded crawl and return its resumable job ID"
    )
    crawl_start_parser.add_argument("url")
    _add_crawl_start_options(crawl_start_parser)
    _add_common_output_options(crawl_start_parser, formats=("json",), default_format="json")

    crawl_status_parser = commands.add_parser(
        "crawl-status", help="return structured status for a crawl job"
    )
    crawl_status_parser.add_argument("job_id")
    _add_common_output_options(crawl_status_parser, formats=("json",), default_format="json")

    crawl_download_parser = commands.add_parser(
        "crawl-download", help="download Markdown currently available for a crawl job"
    )
    crawl_download_parser.add_argument("job_id")
    crawl_download_parser.add_argument("--retain-images", action="store_true")
    _add_common_output_options(crawl_download_parser)

    crawl_cancel_parser = commands.add_parser(
        "crawl-cancel", help="cancel a crawl job (caller owns human approval)"
    )
    crawl_cancel_parser.add_argument("job_id")
    _add_common_output_options(crawl_cancel_parser, formats=("json",), default_format="json")

    file_parser = commands.add_parser(
        "file", help="convert a public remote file or explicitly approved local file"
    )
    file_parser.add_argument("source")
    file_parser.add_argument(
        "--cloud-ok",
        action="store_true",
        help="confirm that a local file may be uploaded to Markdown.new",
    )
    _add_conversion_options(file_parser)
    _add_common_output_options(file_parser)

    return parser


def _add_conversion_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--method", choices=("auto", "ai", "browser"))
    parser.add_argument("--retain-images", action="store_true")


def _add_crawl_start_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=_crawl_limit, default=DEFAULT_CRAWL_LIMIT)
    parser.add_argument("--depth", type=_crawl_depth, default=DEFAULT_CRAWL_DEPTH)
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--source", choices=("all", "sitemaps", "links"))
    parser.add_argument("--max-age", type=_max_age)
    parser.add_argument("--modified-since", type=_nonnegative_int)
    parser.add_argument("--include-external-links", action="store_true")
    parser.add_argument("--include-subdomains", action="store_true")
    parser.add_argument("--include-pattern", action="append", dest="include_patterns")
    parser.add_argument("--exclude-pattern", action="append", dest="exclude_patterns")


def _add_common_output_options(
    parser: argparse.ArgumentParser,
    *,
    formats: Sequence[str] = ("markdown", "json"),
    default_format: str = "markdown",
) -> None:
    parser.add_argument("--format", choices=formats, default=default_format)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=DEFAULT_TIMEOUT,
        help="per-request timeout in seconds",
    )


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
    client_factory: ClientFactory = MarkdownNewClient,
) -> int:
    output_stream = stdout if stdout is not None else sys.stdout
    error_stream = stderr if stderr is not None else sys.stderr
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        prepared = _preflight(args)
        client = client_factory(timeout=args.timeout)
        result = _execute(args, prepared, client)
        rendered = _render(args, prepared, result)
        if args.output is not None:
            atomic_write(args.output, rendered)
        else:
            output_stream.write(rendered)
        return 0
    except APIError as error:
        error_stream.write("error: {}\n".format(error))
        return 1
    except (ValidationError, OSError) as error:
        error_stream.write("error: {}\n".format(error))
        return 2


def _preflight(args: argparse.Namespace) -> Dict[str, Any]:
    prepared: Dict[str, Any] = {}
    source_path: Optional[Path] = None

    if args.command in ("url", "crawl", "crawl-start"):
        prepared["url"] = validate_public_url(args.url)
    elif args.command == "search":
        if not args.query.strip():
            raise ValidationError("search query must not be empty")
    elif args.command == "file":
        kind = _file_source_kind(args.source)
        if kind == "remote":
            prepared["url"] = validate_public_url(args.source)
        elif kind == "invalid-url":
            validate_public_url(args.source)
            raise AssertionError("unreachable URL validation")
        else:
            if not args.cloud_ok:
                raise ValidationError(
                    "local file upload requires --cloud-ok before validation or upload"
                )
            source_path = validate_local_file(args.source)
            prepared["path"] = source_path
    elif args.command in ("crawl-status", "crawl-download", "crawl-cancel"):
        if not isinstance(args.job_id, str) or not args.job_id:
            raise ValidationError("crawl job ID must not be empty")
        prepared["job_id"] = args.job_id

    if args.output is not None:
        prepared["output"] = _validate_output_path(args.output, source_path)
        args.output = prepared["output"]
    return prepared


def _file_source_kind(value: str) -> str:
    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError:
        return "invalid-url" if "://" in value else "local"
    if scheme in ("http", "https"):
        return "remote"
    if "://" in value:
        return "invalid-url"
    return "local"


def _validate_output_path(value: Path, source_path: Optional[Path]) -> Path:
    path = value.expanduser()
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        raise ValidationError("output parent directory does not exist: {}".format(parent))
    if path.exists() and path.is_dir():
        raise ValidationError("output path is a directory: {}".format(path))
    if source_path is not None:
        try:
            same_source = path.resolve(strict=False) == source_path.resolve(strict=True)
        except OSError as error:
            raise ValidationError("cannot validate output path '{}': {}".format(path, error)) from error
        if same_source:
            raise ValidationError("output path must not replace the local source file")
    return path


def _execute(
    args: argparse.Namespace,
    prepared: Dict[str, Any],
    client: MarkdownNewClient,
) -> Union[Dict[str, Any], str]:
    if args.command == "url":
        return client.convert_url(
            prepared["url"], method=args.method, retain_images=args.retain_images
        )
    if args.command == "search":
        return client.search(
            args.query,
            n=args.n,
            gl=args.gl,
            hl=args.hl,
            retain_images=args.retain_images,
        )
    if args.command == "crawl":
        return client.crawl(
            prepared["url"],
            output_format=args.format,
            retain_images=args.retain_images,
            poll_interval=args.poll_interval,
            poll_timeout=args.poll_timeout,
            limit=args.limit,
            depth=args.depth,
            render=args.render,
            source=args.source,
            max_age=args.max_age,
            modified_since=args.modified_since,
            include_external_links=args.include_external_links,
            include_subdomains=args.include_subdomains,
            include_patterns=args.include_patterns,
            exclude_patterns=args.exclude_patterns,
        )
    if args.command == "crawl-start":
        return client.start_crawl(
            prepared["url"],
            limit=args.limit,
            depth=args.depth,
            render=args.render,
            source=args.source,
            max_age=args.max_age,
            modified_since=args.modified_since,
            include_external_links=args.include_external_links,
            include_subdomains=args.include_subdomains,
            include_patterns=args.include_patterns,
            exclude_patterns=args.exclude_patterns,
        )
    if args.command == "crawl-status":
        return client.get_crawl_status(prepared["job_id"])
    if args.command == "crawl-download":
        content = client.download_crawl_markdown(
            prepared["job_id"], retain_images=args.retain_images
        )
        if args.format == "json":
            return {"jobId": prepared["job_id"], "content": content}
        return content
    if args.command == "crawl-cancel":
        return client.cancel_crawl(prepared["job_id"])
    if args.command == "file":
        if "url" in prepared:
            return client.convert_url(
                prepared["url"], method=args.method, retain_images=args.retain_images
            )
        return client.upload_file(prepared["path"])
    raise AssertionError("unknown command {}".format(args.command))


def _render(
    args: argparse.Namespace,
    prepared: Dict[str, Any],
    result: Union[Dict[str, Any], str],
) -> str:
    if args.format == "json":
        if not isinstance(result, dict):
            raise APIError("service returned Markdown when JSON was requested")
        return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    if args.command in ("crawl", "crawl-download"):
        if not isinstance(result, str):
            raise APIError("service returned JSON when Markdown was requested")
        return _with_final_newline(result)
    if not isinstance(result, dict):
        raise APIError("service response is not a JSON object")

    if args.command == "search":
        content = result.get("combined")
        field = "combined"
    elif args.command == "file" and "path" in prepared:
        data = result.get("data")
        content = data.get("content") if isinstance(data, dict) else None
        field = "data.content"
    else:
        content = result.get("content")
        field = "content"
    if not isinstance(content, str):
        raise APIError("service response is missing string field {}".format(field))
    return _with_final_newline(content)


def _with_final_newline(value: str) -> str:
    return value if value.endswith("\n") else value + "\n"


def atomic_write(value: Path, content: str) -> None:
    path = Path(value).expanduser()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".{}-".format(path.name), suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary_path), str(path))
    except BaseException:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _search_count(value: str) -> int:
    selected = int(value)
    if not 1 <= selected <= 5:
        raise argparse.ArgumentTypeError("must be between 1 and 5")
    return selected


def _crawl_limit(value: str) -> int:
    selected = int(value)
    if not 1 <= selected <= 500:
        raise argparse.ArgumentTypeError("must be between 1 and 500")
    return selected


def _crawl_depth(value: str) -> int:
    selected = int(value)
    if not 1 <= selected <= 10:
        raise argparse.ArgumentTypeError("must be between 1 and 10")
    return selected


def _max_age(value: str) -> int:
    selected = int(value)
    if not 0 <= selected <= 604800:
        raise argparse.ArgumentTypeError("must be between 0 and 604800")
    return selected


def _nonnegative_int(value: str) -> int:
    selected = int(value)
    if selected < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return selected


def _positive_float(value: str) -> float:
    selected = float(value)
    if not math.isfinite(selected) or selected <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return selected


def _nonnegative_float(value: str) -> float:
    selected = float(value)
    if not math.isfinite(selected) or selected < 0:
        raise argparse.ArgumentTypeError("must be finite and zero or greater")
    return selected


def entrypoint() -> None:
    raise SystemExit(main())
