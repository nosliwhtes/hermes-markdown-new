# Markdown.new for Hermes

**Search the web. Read the useful parts. Bring back Markdown.**

A community plugin that connects Hermes to [Markdown.new](https://markdown.new) for public web research. Search with source links, read pages as Markdown, crawl documentation sites, and convert public documents. No API key required.

Use Hermes's familiar `web_search` and `web_extract` tools for everyday research. Reach for `markdown_new` when you need locale-specific search, conversion options, crawl job controls, or an explicitly approved local-file upload.

## Get started

Install the companion CLI, then install and enable the native plugin:

```bash
uv tool install git+https://github.com/nosliwhtes/hermes-markdown-new@v1.0.0
hermes plugins install "nosliwhtes/hermes-markdown-new#plugin/markdown-new" --no-enable
hermes plugins enable markdown-new
hermes plugins doctor --ci markdown-new
```

This direct repository install is available independently of the Hermes catalog review. Add `--ref` with the release's full commit SHA if you want an immutable plugin install. The catalog listing is pending maintainer review.

**[Full setup, examples, privacy details, and operation reference](plugin/markdown-new/README.md)**

Requires a Hermes version with native web-provider registration, Python 3.10+ for the plugin, and the included CLI on your `PATH`. Plugin version: 1.0.0. Companion CLI version: 0.2.0.

## A few things to try

> Find recent articles about Cloudflare Markdown for Agents and include the source links.

> Read this public documentation page and summarize the installation steps.

> Crawl this public documentation site with markdown_new, up to 20 pages, then download its Markdown.

> Convert this public PDF URL to Markdown.

## Before you use it

Markdown.new is an external service. Search queries, submitted URLs, and approved uploads leave your machine. Use it for public sources, not credentials, authenticated pages, internal URLs, or confidential documents. Local uploads and crawl cancellation require approval on every invocation. Filename checks cannot establish that a file's contents are safe to upload.

Installing does not change your active provider defaults or toolsets automatically. Other providers remain available; Hermes owns fallback behavior. Service limits and availability are controlled by Markdown.new.

## Verification

The offline suite covers client behavior and native host integration, including URL policy enforcement, approved upload snapshots, and bounded response reads. See the [development instructions](plugin/markdown-new/README.md#verification-and-development) to run client tests and native Hermes contract tests together.

## Contribute

Bug reports and focused pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

MIT licensed. Community integration by [nosliwhtes](https://github.com/nosliwhtes), not affiliated with or endorsed by Markdown.new, Cloudflare, or Nous Research.
