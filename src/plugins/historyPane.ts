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
import {
  adoptHistory,
  readHistory,
  renameHistory,
  type IHistoryEntry
} from '../history';
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
  private _panel: NotebookPanel | null = null;

  constructor(
    private readonly settings: IVybeSettings,
    private readonly _openSettings: () => void = () => undefined
  ) {
    super();
    this.addClass('jv-history-pane');
    this.title.label = 'Prompt history';
    this.title.caption =
      'Prompts, replies, and every proposal with its verdict';
    this.title.iconClass = 'jp-MaterialIcon jp-TextEditorIcon';

    const header = document.createElement('div');
    header.className = 'jv-history-header';
    const heading = document.createElement('span');
    heading.textContent = 'Prompt history';
    // The settings pane is three menus deep otherwise, and this pane is the
    // one piece of vybe chrome that is always on screen.
    const gear = document.createElement('button');
    gear.className = 'jv-history-gear';
    gear.textContent = '⚙';
    gear.title = 'Jupyter Vybe settings';
    gear.setAttribute('aria-label', 'Open Jupyter Vybe settings');
    gear.onclick = () => this._openSettings();
    header.append(heading, gear);
    this._status = document.createElement('div');
    this._status.className = 'jv-history-status';
    this._status.setAttribute('role', 'status');
    this._status.setAttribute('aria-live', 'polite');
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
    this._panel = panel;
    try {
      const payload = await readHistory(
        this._path,
        this.settings.historyMaxEntries,
        this.settings.historyLocation
      );
      if (!payload.entries.length) {
        if (payload.orphan) {
          this._offerAdoption(payload.orphan);
        } else {
          this._render(
            [],
            `Nothing yet. Run a cell starting with ${this.settings.prefix}.`
          );
        }
        return;
      }
      this._render(payload.entries, '');
    } catch (err) {
      this._render([], `Could not read the record: ${err}`);
    }
  }

  /**
   * A record exists for a notebook of this name but not this path — a rename
   * done outside JupyterLab. Offered rather than done, because the match is by
   * file name: a good guess, not a certainty (docs/05).
   */
  private _offerAdoption(orphan: string): void {
    this._render(
      [],
      'No record for this notebook, but one exists for a notebook of this ' +
        'name — it was probably renamed outside JupyterLab.'
    );
    const button = document.createElement('button');
    button.className = 'jv-history-adopt';
    button.textContent = 'Use that record for this notebook';
    button.title = orphan;
    button.onclick = async () => {
      button.disabled = true;
      const ok = await adoptHistory(
        orphan,
        this._path,
        this.settings.historyLocation
      );
      if (ok) {
        void this.showFor(this._panel);
      } else {
        this._status.textContent = 'Could not adopt that record.';
      }
    };
    this._list.appendChild(button);
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
      // Clickable, therefore reachable and operable from the keyboard.
      el.tabIndex = 0;
      el.setAttribute('role', 'button');
      el.setAttribute('aria-label', 'Copy this inserted cell to the clipboard');
      el.onkeydown = event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          this._copyCell(String(entry.cellType ?? 'code'), source, el);
        }
      };
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
      el.tabIndex = 0;
      el.setAttribute('role', 'button');
      el.setAttribute(
        'aria-label',
        `Copy the new source of ${String(entry.cellLabel || entry.cellId)}`
      );
      el.onkeydown = event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          this._copyCell('code', after, el);
        }
      };
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
    const pane = new HistoryPane(settings, () => {
      void app.commands.execute('settingeditor:open', {
        query: 'Jupyter Vybe'
      });
    });
    pane.id = 'jupyter-vybe-history';
    app.shell.add(pane, 'right', { rank: 900 });
    restorer?.add(pane, 'jupyter-vybe-history');

    const refresh = () => void pane.showFor(tracker.currentWidget);

    // Follow renames: the record is keyed by path, so without this a rename
    // orphans it and the pane goes blank on a notebook with real history.
    const watched = new WeakSet<NotebookPanel>();
    const watchRenames = (panel: NotebookPanel | null) => {
      if (!panel || watched.has(panel)) {
        return;
      }
      watched.add(panel);
      let previous = panel.context.path;
      panel.context.pathChanged.connect((_ctx, next: string) => {
        const from = previous;
        previous = next;
        void renameHistory(from, next, settings.historyLocation).then(refresh);
      });
    };

    tracker.widgetAdded.connect((_t, panel) => watchRenames(panel));
    tracker.forEach(watchRenames);
    watchRenames(tracker.currentWidget);
    tracker.currentChanged.connect((_t, panel) => {
      watchRenames(panel);
      refresh();
    });
    // A verdict is the last thing written for a round, so refreshing on the
    // proposals signal keeps the pane current without polling.
    proposals.changed.connect(refresh);
    refresh();
  }
};

export default historyPanePlugin;
