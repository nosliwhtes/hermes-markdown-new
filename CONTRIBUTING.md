# Contributing

Keep changes focused. Describe the behavior you are changing and how you tested it. Explain trust-boundary changes and include offline regressions when a change affects approval, privacy, provider routing, or service endpoints.

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

## Noninteractive checks and machine use

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m pip wheel --no-deps --no-build-isolation --wheel-dir /tmp/markdown-new-wheels .
```

CI runs the standalone suite on Python 3.9 and 3.14; host tests require the separate Hermes environment above and must be reported separately. Do not call the live conversion service in tests.

For machine use, request `--format json` and check the exit status before parsing stdout. Successful CLI JSON preserves the service object; errors are text on stderr, with status 1 for API/network errors and 2 for usage, validation, or filesystem errors. `--output` writes atomically instead of printing the result. The plugin returns a JSON `success` boolean and `operation`, plus `data` on success or `error` on failure. Native extraction preserves input order and returns an `error` per failed URL. Large plugin results include `truncated` and `full_result_path`.

There are no runtime dependencies or dependency lockfiles. The setuptools build requirement retains support for Python 3.9; use an up-to-date compatible build environment rather than adding runtime dependencies.
