# Repository instructions

## Attribution

For commits containing work performed by Codex, include this trailer:

```text
Co-authored-by: Codex <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
```

This identifies OpenAI's verified chatgpt-codex-connector bot account for GitHub attribution. It does not claim that commits were submitted through the GitHub App. Do not add the trailer to work Codex did not contribute to, impersonate a human maintainer, or rewrite published releases just to change attribution.

## Validation

Run `python -m unittest discover -s tests -p test_hive_virtual.py` for control/configuration changes and `node --check custom_components/hive_virtual/frontend/panel.js` for frontend changes. Documentation-only edits do not require rerunning simulated heating tests. Keep HACS and hassfest checks enabled. Physical Hive validation must not be inferred from simulated services.

## Publication

Keep private Home Assistant configuration, authentication data and raw device logs out of this repository. New HACS versions require a matching manifest version and published GitHub release. This integration is experimental; retain the documented limitations and supervised acceptance tests.
