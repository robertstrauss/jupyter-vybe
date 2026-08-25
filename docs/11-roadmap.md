# 11 — Roadmap

Estimates assume one experienced developer working with AI assistance. Milestones are ordered so
that the riskiest unknown (execution interception) is settled first and the most user-visible
polish (diff UI) has a working agent behind it before it is built.

## M0 — Scaffold and environment · ~2 days

- `pip install jupyterlab==4.6.3 copier jupyter-packaging`; the machine currently has Node 24.9,
  npm 11.6, Python 3.14, `ollama`, but **no `jupyter`** — provision a venv first.
- `copier copy --trust https://github.com/jupyterlab/extension-template .` with
  `has_server_extension: true`, `has_settings: true`, `test: true`.
- Wire `disabledExtensions`, ESLint custom rules, prettier, pytest, jest, Galata, GitHub Actions.
- `pip install -ve . && jupyter labextension develop --overwrite . && jlpm watch` loop verified.

**Exit:** a no-op extension loads in JupyterLab 4.6.3 and its server extension answers `/health`.

## M1 — Prompt cell interception · ~4 days

The load-bearing milestone. Everything else is ordinary UI work.

- `jupyter-vybe:cell-executor` providing `INotebookCellExecutor`, with the core-fallback
  try/catch and the bootstrap-safety test.
- Prefix detection, validator, `[*]` via `executionState`, read-only-while-running.
- `mutations.ts` with all four operations and the round operation log.
- Prompt-cell → markdown transform and `reopen-prompt`.
- Both undo layers; `Ctrl+C` cancel.
- Gates T-KERNEL-1/3/4, T-BOOT, T-UNDO, T-MUTATE green — with a **stub agent** that returns two
  hardcoded cells and one hardcoded proposal.

**Exit:** running a `>` cell with no kernel inserts stub cells and settles correctly.

## M2 — Agent transport · ~4 days

- Server extension: handlers, `SessionManager`, `AgentSession`, WebSocket protocol.
- `OllamaAdapter` including `ollama serve` auto-start ported from `code-llm/run.sh`.
- Frontend `agentClient.ts`: reconnect, cancellation, backpressure.
- `MockAdapter` and the fixture scenario suite.

**Exit:** a real `> ` prompt reaches `qwen2.5-coder:7b` and prose streams into the bubble.

## M3 — Context assembly · ~3 days

- The five prompt files; `PromptAssembler`.
- `snapshot.ts` + filtering rules + the doc/code consistency test.
- `diffSummary.ts`, `snapshotSeq` resync, forced periodic full snapshots.
- History store writer/reader; conversation context assembled from the store, not from cells.
- Pinned kernel info, exempt from truncation ([12](12-decisions.md) D13).
- Token estimator calibrated against Qwen; context meter.

**Exit:** assembled prompts are inspectable, correct, and within budget on a 60-cell notebook.

## M4 — Tool protocol and apply pipeline · ~4 days

- Grammar, both parsers, shared golden files.
- Validation and apply pipeline into `mutations.ts`.
- `introspect.ts` with its guards and the tool-turn loop.
- First real end-to-end generation into a notebook.

**Exit:** scenario fixtures all pass; a real model reliably produces valid `add_cell` calls.

## M5 — Proposal and diff UI · ~5 days

- Proposal store, staleness detection.
- `DiffOverlay` with CM6 decorations; theme variables for both themes.
- `ReviewControls`, `StatusBubble`, all keybindings, smooth navigation, reject-all.
- Inserted-cell group selection and fade accent.
- Galata flows 1–5.

**Exit:** the full keyboard-only round from PROMPT §2 works end to end.

## M6 — Settings and history · ~3 days

- `schema/plugin.json`, custom field renderers, agent discovery, prefix validation UI, meters.
- History pane with virtualised CM6 entries, grouping, cell-clipboard copy.
- The durable history store ([12](12-decisions.md) D12): append-only JSONL, rename tracking via
  `pathChanged`, orphan adoption, `history.location`. **Moved forward from M6 into M3** in part —
  the store is the source of the agent's conversation context, so persistent sessions depend on
  it. M3 builds the writer and reader; M6 builds the pane on top.
- Galata flows 6–9.

**Exit:** every control in [04](04-settings.md) and [05](05-history-pane.md) is live.

## M7 — Remaining adapters and packaging · ~4 days

- `TurboFieldfareAdapter` with the vendored client; `CommandAdapter` (one-shot + persistent,
  validated against `claude -p`); `OpenAICompatAdapter`. No native-tools path
  ([12](12-decisions.md) D3).
- Document the reverse-tunnel configuration for split laptop/cluster setups, and add the
  health probe that reports *which machine* answered ([06](06-agent-integration.md)).
- Rewrite `README.md`: fill the `[TODO]` markers (installation, table of contents, settings
  location), add the screenshots the README marks `[HUMAN TODO]`.
- `pip install jupyter-vybe` from a built wheel on a clean venv.

**Exit:** all four adapters selectable and working; wheel installs clean.

## M8 — Hardening and release · ~3 days

- Full Galata matrix; the 20-prompt quality bench across all three models; prompt tuning against
  the results.
- Performance: 200-cell notebook, 50-entry history, snapshot under 100ms.
- Accessibility: focus order, ARIA on bubble and review controls, `prefers-reduced-motion`,
  contrast on diff colours in both themes.
- Docs, CHANGELOG, v1.0.0 tag.

**Total: ~32 working days (~6.5 weeks).**

## Deferred to v1.1

**The `jupyter-vybe-local` sidecar** ([12](12-decisions.md) D2) — `python -m jupyter_vybe.local`,
a ~150-line transport around the identical adapter package, letting the browser reach a model and
a `claude -p` login on the user's own machine while Jupyter runs elsewhere. ~3 days. Moves into
v1.0 if the split laptop/cluster setup turns out to be the primary workflow rather than a
secondary one.

**Advanced inline autocompletion** ([00](00-overview.md), [12](12-decisions.md) D5) as an
`ICompletionProviderManager` provider using FIM prompting — `README_turbofieldfare.md` documents
Gemma's `<|fim_prefix|>` / `<|fim_suffix|>` / `<|fim_middle|>` tokens, and Qwen2.5-Coder has its
own FIM format, so both backends can serve it. Estimated ~8 days: a provider, a debounce/cancel
policy, a ghost-text renderer, and a separate quality bench.
