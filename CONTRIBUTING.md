# Contributing

Use Python 3.13+ and install requirements-dev.txt. Run:

```text
python -m unittest discover -s tests -p test_hive_virtual.py
node --check custom_components/hive_virtual/frontend/panel.js
```

The offline suite uses Home Assistant stubs. It does not replace the isolated runtime test in tools/check_virtual_runtime.py or physical acceptance tests.

The implementation, documentation and tests were created with OpenAI Codex; the logo was generated with OpenAI image generation. Review heating-control behaviour before merging. Do not commit credentials, private configurations, device/account identifiers, raw logs or tokens. Use sanitized issue reports.

For Codex-assisted commits, include the co-author trailer documented in [AGENTS.md](AGENTS.md). This adds commit-level credit; prose acknowledgements alone do not populate GitHub's author information. Only credit contributors who actually participated.

## Releases

Update custom_components/hive_virtual/manifest.json and CHANGELOG.md, run checks, then publish a GitHub release with a matching version tag such as v0.4.1. HACS installs the integration folder from that release; no hand-built ZIP is required. CI checks release tags against the manifest. Draft releases do not notify HACS users. Do not describe simulated Hive tests as hardware validation.
