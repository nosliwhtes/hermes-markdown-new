# Contributing

Keep changes focused. Describe the behavior you are changing and how you tested it. Open an issue first if a change affects approval, privacy, provider routing, or service endpoints.

The runtime code lives in two places:

- `src/markdown_new/`: the dependency-free client and CLI.
- `plugin/markdown-new/`: the native Hermes provider, advanced tool, and approval/safety boundary.

Reuse the client rather than adding a second HTTP implementation. Never weaken per-invocation approval, private-network guards, sensitive-path checks, or file-identity checks to make a test pass.

## Run the full suite

Use a Hermes checkout that supports `register_web_search_provider` and its installed Python environment:

```bash
export HERMES_ACTIVE_SOURCE=/path/to/hermes-agent
PYTHONPATH="$HERMES_ACTIVE_SOURCE:src" "$HERMES_ACTIVE_SOURCE/venv/bin/python" -m unittest discover -s tests -v
hermes plugins validate plugin/markdown-new --json
```

Without the host checkout, native contract tests are skipped. State any skips explicitly in your pull request.

Do not upload secrets or customer data to issues, test fixtures, or conversion services. Report security concerns through GitHub's private vulnerability-reporting feature if it is available; otherwise open an issue requesting a private contact without disclosing exploit details.
