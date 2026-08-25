# Vendored from code-llm

Copied 2026-08-25 from `/Users/rrs/Desktop/thoughtcode/code-llm/`, per PROMPT.md §5.

| File | Source | Notes |
|---|---|---|
| `turbofieldfare_client.py` | `code-llm/turbofieldfare_client.py` | Reference copy, unmodified. The live adapter is `../turbofieldfare.py`, which reuses this file's binary/model probe order but streams asynchronously instead of printing to stdout. |
| `setup_turbofieldfare.sh` | `code-llm/setup_turbofieldfare.sh` | Unmodified. Builds the Swift binaries and repacks the Gemma weights. |

`make sync-vendor` diffs these against the originals so upstream fixes are
visible rather than silently divergent (docs/13 R8).
