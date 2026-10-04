# Markdown.new for Hermes

**Search the web. Read the useful parts. Bring back Markdown.**

Give Hermes access to [Markdown.new](https://markdown.new) for public web research: search results with source links, readable page content, site crawls, and document conversion. No API key is required.

It works through Hermes's existing `web_search` and `web_extract` tools, so ordinary research stays familiar. A separate `markdown_new` tool gives you more control when you need it.

## What you can do

- **Search and follow the sources.** Get search results with usable links, or request extracted content alongside results. Choose a country and language for locale-specific searches.
- **Read pages as Markdown.** Convert a public URL with `auto`, `ai`, or `browser` mode, with an option to retain images.
- **Crawl a public site.** Start a bounded crawl, check progress, retrieve its Markdown, or reconnect using its job ID. Cancelling a job requires approval.
- **Convert documents.** Convert a public file URL, including PDFs, or explicitly approve an upload of a non-sensitive local file. Format support depends on Markdown.new.
- **Keep your existing setup.** Select Markdown.new as your web provider without removing other providers. Hermes still owns fallback behavior.

## Try asking Hermes

> Find recent articles about Cloudflare Markdown for Agents and include the source links.

> Read this public documentation page and summarize the installation steps.

> Use markdown_new to crawl this public documentation site, with a maximum of 20 pages, then download the Markdown.

> Convert this public PDF URL to Markdown.

These are example prompts, not demonstrations of a completed crawl or conversion.

## Install

Requires a Hermes version with `register_web_search_provider` and async provider extraction, Python 3.10+ for the native plugin, and the companion `markdown-new` CLI on your `PATH`. The CLI is included in this repository and has no runtime Python dependencies.

### 1. Install the companion CLI

With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/nosliwhtes/hermes-markdown-new@v1.0.0
markdown-new --version
```

The plugin release is **1.0.0**; its companion CLI reports **0.2.0**. These are separate components with separate version numbers.

### 2. Install and enable the plugin

```bash
hermes plugins install "nosliwhtes/hermes-markdown-new#plugin/markdown-new" --no-enable
hermes plugins enable markdown-new
hermes plugins doctor --ci markdown-new
```

This is a direct repository install, not a claim of approval by the Hermes catalog. Review the installer's source and capability disclosures. For an immutable install, add `--ref` with the full 40-character commit SHA shown on the GitHub release.

The Hermes catalog submission is pending maintainer review. If it is admitted, `hermes plugins install markdown-new` will install the catalog's reviewed, SHA-pinned version.

### 3. Choose it for public web tasks

To use it for ordinary search and extraction, set these existing keys in your active Hermes `config.yaml`:

```yaml
web:
  search_backend: markdown-new
  extract_backend: markdown-new
```

For the advanced tool, add `markdown_new` to your existing `toolsets` list. Keep your other toolsets. For example, if you currently use `hermes-cli`:

```yaml
toolsets:
  - hermes-cli
  - markdown_new
```

Start a new Hermes session after changing the provider or toolsets. Installing the plugin does not change these settings for you.

## The advanced tool

`markdown_new` supports eight operations:

| Operation | Purpose |
|---|---|
| `url_convert` | Convert a public web page, with method and image options |
| `search_extract` | Search with optional country, language, and extracted page content |
| `crawl_run` | Start a bounded crawl and poll it in one call |
| `crawl_start` | Start a bounded public-site crawl |
| `crawl_status` | Check a job's progress and page outcomes |
| `crawl_download` | Retrieve a job's available Markdown |
| `crawl_cancel` | Cancel a job after native approval |
| `file_convert` | Convert a public file URL or an approved local upload |

You can also edit Markdown in [Markdown.new's web editor](https://markdown.new/markdown-editor). It remains a website, not a tool operation or an embedded Hermes editor.

## Privacy and limits

**This is a public web service, not a local-only converter.** Search queries, submitted URLs, and approved file uploads go to Markdown.new. Crawls and file conversion may involve its upstream processing services. Read its [privacy policy](https://markdown.new/privacy) and [terms](https://markdown.new/terms).

Local uploads and crawl cancellation require approval for each invocation. The plugin blocks private-network URLs, sensitive-looking local paths, and file-substitution attempts. These checks are safeguards, not a content classifier: they cannot prove that a file is safe to upload. Do not submit credentials, confidential documents, authenticated pages, or internal URLs.

Native extraction and advanced URL operations share the host URL and website-policy guard. Local uploads capture at most 10 MiB before approval; the CLI receives a private temporary copy of those exact bytes, removed after conversion. The original path is rechecked after approval, but subsequent file changes cannot replace the approved bytes. HTTP response bodies are limited to 64 MiB while reading, including responses without Content-Length.

No API key is required, but service limits, availability, conversion quality, and supported formats are controlled by Markdown.new. A crawl may return a cached job; that does not establish that every page was fetched again. Existing Hermes providers remain available, but fallback depends on your host configuration.

## Verification and development

Coverage includes native async provider dispatch, source-link normalization, all eight advanced operations, approval denial and replay, URL safety, symlinks, immutable upload snapshots, and response read budgets.

To exercise native host contracts as well as the standalone client tests, point to your Hermes source checkout and use its Python environment:

```bash
export HERMES_ACTIVE_SOURCE=/path/to/hermes-agent
PYTHONPATH="$HERMES_ACTIVE_SOURCE:src" "$HERMES_ACTIVE_SOURCE/venv/bin/python" -m unittest discover -s tests -v
hermes plugins validate plugin/markdown-new --json
```

Without `HERMES_ACTIVE_SOURCE`, the native host contract tests are skipped. Do not report that run as the full release suite.

Bug reports and pull requests are welcome at [nosliwhtes/hermes-markdown-new](https://github.com/nosliwhtes/hermes-markdown-new).

## License and affiliation

MIT licensed. Community integration by [nosliwhtes](https://github.com/nosliwhtes), not affiliated with or endorsed by Markdown.new, Cloudflare, or Nous Research. The MIT license covers this repository's code, not the third-party service or content you retrieve.
