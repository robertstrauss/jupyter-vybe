import { ILayoutRestorer } from '@jupyterlab/application';
import type {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { INotebookTracker, type NotebookPanel } from '@jupyterlab/notebook';
import { Widget } from '@lumino/widgets';
import { Clipboard } from '@jupyterlab/apputils';

import { IVybeProposals } from './proposals';
import { IVybeSettings } from '../tokens';
import { readHistory, type IHistoryEntry } from '../history';
import { diffLines } from '../ui/diff';
import { DiffOverlay } from '../ui/DiffOverlay';

const JUPYTER_CELL_MIME = 'application/vnd.jupyter.cells';

/**
 * The prompt history pane (docs/05).
 *
 * Reads the sidecar store, never the notebook: the store is what makes a
 * prompt survive deleting the cell that rendered it, and reading cells back
 * would quietly reintroduce the thing D12 removed.
 */
class HistoryPane extends Widget {
  private _list: HTMLElement;
  private _status: HTMLElement;
  private _path = '';

  constructor(private readonly settings: IVybeSettings) {
    super();
    this.addClass('jv-history-pane');
    this.title.label = 'Prompt history';
    this.title.caption =
      'Prompts, replies, and every proposal with its verdict';
    this.title.iconClass = 'jp-MaterialIcon jp-TextEditorIcon';

    const header = document.createElement('div');
    header.className = 'jv-history-header';
    header.textContent = 'Prompt history';
    this._status = document.createElement('div');
    this._status.className = 'jv-history-status';
    this._list = document.createElement('div');
    this._list.className = 'jv-history-list';
    this.node.append(header, this._status, this._list);
  }

  /** Not `show`: Widget already has one, and it means something else. */
  async showFor(panel: NotebookPanel | null): Promise<void> {
    if (!panel) {
      this._path = '';
      this._render([], 'No notebook open.');
      return;
    }
    this._path = panel.context.path;
    try {
      const payload = await readHistory(
        this._path,
        this.settings.historyMaxEntries
      );
      if (!payload.entries.length) {
        this._render(
          [],
          payload.orphan
            ? `No record for this path. A record naming a notebook of this ` +
                `name exists at ${payload.orphan} — it was probably renamed ` +
                `outside JupyterLab.`
            : `Nothing yet. Run a cell starting with ${this.settings.prefix}.`
        );
        return;
      }
      this._render(payload.entries, '');
    } catch (err) {
      this._render([], `Could not read the record: ${err}`);
    }
  }

  private _render(entries: IHistoryEntry[], status: string): void {
    this._status.textContent = status;
    this._status.style.display = status ? '' : 'none';
    this._list.textContent = '';
    // Verdicts arrive as their own entries; fold them onto the proposal they
    // settle so a reader sees one row per proposal, with its outcome.
    const outcomes = new Map<string, string>();
    for (const e of entries) {
      if (e.kind === 'verdict') {
        outcomes.set(`${e.roundId}:${e.cellId}`, String(e.outcome ?? ''));
      }
    }
    let round = '';
    for (const entry of entries) {
      if (entry.kind === 'verdict') {
        continue;
      }
      if (entry.roundId && entry.roundId !== round) {
        round = entry.roundId;
        this._list.appendChild(this._roundHeader(entry));
      }
      const node = this._entry(entry, outcomes);
      if (node) {
        this._list.appendChild(node);
      }
    }
    this._list.scrollTop = this._list.scrollHeight;
  }

  private _roundHeader(entry: IHistoryEntry): HTMLElement {
    const el = document.createElement('div');
    el.className = 'jv-history-round';
    const when = entry.ts ? new Date((entry.ts as number) * 1000) : null;
    el.textContent = when ? when.toLocaleTimeString() : '';
    return el;
  }

  private _entry(
    entry: IHistoryEntry,
    outcomes: Map<string, string>
  ): HTMLElement | null {
    const el = document.createElement('div');
    el.className = `jv-history-entry jv-history-${entry.kind}`;

    if (entry.kind === 'prompt') {
      el.textContent = `${this.settings.prefix} ${String(entry.text ?? '')}`;
      return el;
    }
    if (entry.kind === 'prose' || entry.kind === 'error') {
      el.textContent = String(entry.text ?? entry.message ?? '');
      return el;
    }
    if (entry.kind === 'insertion') {
      const source = String(entry.source ?? '');
      el.appendChild(this._label(`inserted ${entry.cellType} cell`));
      el.appendChild(this._code(source));
      // Clicking copies to the *cell* clipboard, so `v` in command mode
      // pastes it as a cell -- the same MIME core copy/paste uses (docs/05).
      el.onclick = () =>
        this._copyCell(String(entry.cellType ?? 'code'), source, el);
      return el;
    }
    if (entry.kind === 'proposal') {
      const before = String(entry.before ?? '');
      const after = String(entry.after ?? '');
      const outcome =
        outcomes.get(`${entry.roundId}:${entry.cellId}`) ?? 'pending';
      el.appendChild(
        this._label(
          `proposal → ${String(entry.cellLabel || entry.cellId)}`,
          outcome
        )
      );
      el.appendChild(DiffOverlay.build(diffLines(before, after)));
      // The net "after" source is what a reader wants to reuse, whether or
      // not they took it at the time -- rejected proposals especially.
      el.onclick = () => this._copyCell('code', after, el);
      return el;
    }
    return null;
  }

  private _label(text: string, outcome?: string): HTMLElement {
    const el = document.createElement('div');
    el.className = 'jv-history-label';
    el.textContent = text;
    if (outcome) {
      const badge = document.createElement('span');
      badge.className = `jv-history-badge jv-history-${outcome}`;
      badge.textContent = outcome;
      el.appendChild(badge);
    }
    return el;
  }

  private _code(source: string): HTMLElement {
    const el = document.createElement('pre');
    el.className = 'jv-history-code';
    el.textContent = source;
    return el;
  }

  private _copyCell(cellType: string, source: string, el: HTMLElement): void {
    Clipboard.getInstance().setData(JUPYTER_CELL_MIME, [
      { cell_type: cellType, source, metadata: {} }
    ]);
    this._status.style.display = '';
    this._status.textContent = 'copied — press v in a notebook to paste';
    el.classList.add('jv-history-flash');
    window.setTimeout(() => el.classList.remove('jv-history-flash'), 600);
  }
}

export const historyPanePlugin: JupyterFrontEndPlugin<void> = {
  id: 'jupyter-vybe:history-pane',
  description: 'The prompt record, rendered beside the notebook.',
  autoStart: true,
  requires: [INotebookTracker, IVybeSettings, IVybeProposals],
  optional: [ILayoutRestorer],
  activate: (
    app: JupyterFrontEnd,
    tracker: INotebookTracker,
    settings: IVybeSettings,
    proposals: IVybeProposals,
    restorer: ILayoutRestorer | null
  ): void => {
    const pane = new HistoryPane(settings);
    pane.id = 'jupyter-vybe-history';
    app.shell.add(pane, 'right', { rank: 900 });
    restorer?.add(pane, 'jupyter-vybe-history');

    const refresh = () => void pane.showFor(tracker.currentWidget);
    tracker.currentChanged.connect(refresh);
    // A verdict is the last thing written for a round, so refreshing on the
    // proposals signal keeps the pane current without polling.
    proposals.changed.connect(refresh);
    refresh();
  }
};

export default historyPanePlugin;
