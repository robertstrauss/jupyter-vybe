# Notebook data filtering rules

Applied by src/notebook/snapshot.ts when serialising the in-memory notebook for the agent.
This file is the spec; a test asserts the table below matches the constants in snapshot.ts.

The source is always `notebookPanel.content.model.toJSON()` — the live, unsaved model, never the
file on disk.

| Field | Rule |
|---|---|
| `nbformat`, `nbformat_minor` | kept |
| `metadata.kernelspec`, `metadata.language_info` | kept |
| all other notebook metadata | dropped |
| cell `id`, `cell_type`, `source` | kept, always, in full |
| cell `execution_count` | kept |
| cell `metadata` | dropped except `tags` |
| `attachments` | dropped, replaced by `"<attachment: name>"` |
| output `stream` | kept, truncated to maxOutputChars (default 2000), middle-elided |
| output `text/plain` | kept, same truncation |
| output `text/html`, `text/markdown`, `text/latex` | dropped if `text/plain` exists, else truncated to 500 chars |
| output `image/*`, `application/pdf`, base64 bundles | dropped, replaced by `"<image WxH type>"` |
| `application/vnd.*` | dropped, replaced by `"<rich output: mimetype>"` |
| output `error` | kept: ename, evalue, last 20 traceback lines |

Errors are preserved aggressively because they are the highest-signal-per-token content in a
notebook and are exactly what a "fix this" prompt is about. Images are annotated rather than
described because they are pure token cost for a text model.

Serialised as compact JSON (no indentation): ~15% cheaper in tokens, no loss of comprehension.
