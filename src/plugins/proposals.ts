import type { JupyterFrontEndPlugin } from '@jupyterlab/application';
import { INotebookTracker, type NotebookPanel } from '@jupyterlab/notebook';
import type { Cell, MarkdownCell } from '@jupyterlab/cells';
import { Token, UUID } from '@lumino/coreutils';
import { Signal, type ISignal } from '@lumino/signaling';

import {
  applyProposal,
  findCellModel,
  OperationLog
} from '../notebook/mutations';
import { isSettledPromptCell } from '../notebook/promptCell';
import { diffLines, type IHunk } from '../ui/diff';

export interface IProposal {
  id: string;
  roundId: string;
  cellId: string;
  before: string;
  after: string;
  note?: string;
  targetLabel?: string;
  hunks: IHunk[];
  wasRendered: boolean;
  state: 'pending' | 'accepted' | 'rejected' | 'stale';
}

export interface IStageRequest {
  cellId: string;
  after: string;
  note?: string;
  prefix: string;
}

export interface IRoundHandle {
  id: string;
  log: OperationLog;
  promptCellId: string;
}

export interface IVybeProposals {
  stage(panel: NotebookPanel, roundId: string, req: IStageRequest): boolean;
  pendingCount(panel: NotebookPanel, roundId?: string): number;
  list(panel: NotebookPanel): IProposal[];
  beginReview(panel: NotebookPanel, round: IRoundHandle): void;
  accept(panel: NotebookPanel, proposalId: string): void;
  reject(panel: NotebookPanel, proposalId: string): void;
  rejectAll(panel: NotebookPanel): void;
  activeProposal(panel: NotebookPanel): IProposal | null;
  clear(panel: NotebookPanel, roundId?: string): void;
  logFor(panel: NotebookPanel): OperationLog;
  readonly changed: ISignal<IVybeProposals, NotebookPanel>;
}
export const IVybeProposals = new Token<IVybeProposals>(
  'jupyter-vybe:IVybeProposals'
);

export class Proposals implements IVybeProposals {
  private _byPanel = new Map<string, IProposal[]>();
  private _rounds = new Map<string, IRoundHandle>();
  private _changed = new Signal<this, NotebookPanel>(this);

  get changed() {
    return this._changed;
  }

  stage(panel: NotebookPanel, roundId: string, req: IStageRequest): boolean {
    const model = findCellModel(panel, req.cellId);
    if (!model) {
      return false;
    }
    // A settled prompt cell is the user's own prompt history, not the agent's
    // to rewrite (docs/03). Enforced here, not merely discouraged in the prompt.
    if (isSettledPromptCell(model, req.prefix)) {
      return false;
    }
    const before = model.sharedModel.getSource();
    if (before === req.after) {
      // Models frequently "edit" a cell into itself; drop silently.
      return false;
    }
    const proposal: IProposal = {
      id: UUID.uuid4(),
      roundId,
      cellId: req.cellId,
      before,
      after: req.after,
      note: req.note,
      targetLabel: before.split('\n')[0].slice(0, 60),
      hunks: diffLines(before, req.after),
      wasRendered:
        model.type === 'markdown' ? this._isRendered(panel, req.cellId) : false,
      state: 'pending'
    };
    const list = this._byPanel.get(panel.id) ?? [];
    list.push(proposal);
    this._byPanel.set(panel.id, list);
    this._changed.emit(panel);
    return true;
  }

  logFor(panel: NotebookPanel): OperationLog {
    return this._rounds.get(panel.id)?.log ?? new OperationLog();
  }

  pendingCount(panel: NotebookPanel, roundId?: string): number {
    return this.list(panel).filter(
      p =>
        p.state === 'pending' &&
        (roundId === undefined || p.roundId === roundId)
    ).length;
  }

  list(panel: NotebookPanel): IProposal[] {
    return this._byPanel.get(panel.id) ?? [];
  }

  activeProposal(panel: NotebookPanel): IProposal | null {
    return this.list(panel).find(p => p.state === 'pending') ?? null;
  }

  beginReview(panel: NotebookPanel, round: IRoundHandle): void {
    this._rounds.set(panel.id, round);
    for (const p of this.list(panel)) {
      if (p.state === 'pending') {
        this._setRendered(panel, p.cellId, false);
      }
    }
    this._changed.emit(panel);
  }

  accept(panel: NotebookPanel, proposalId: string): void {
    const p = this.list(panel).find(x => x.id === proposalId);
    if (!p || p.state !== 'pending') {
      return;
    }
    const model = findCellModel(panel, p.cellId);
    if (!model) {
      p.state = 'stale';
    } else if (model.sharedModel.getSource() !== p.before) {
      // The user edited the cell while the model was thinking. Accepting now
      // would clobber their work (docs/03, R4).
      p.state = 'stale';
    } else {
      const round = this._rounds.get(panel.id);
      if (round) {
        applyProposal(panel, p.cellId, p.after, round.log);
      }
      p.state = 'accepted';
      this._setRendered(panel, p.cellId, p.wasRendered);
    }
    this._advance(panel);
  }

  reject(panel: NotebookPanel, proposalId: string): void {
    const p = this.list(panel).find(x => x.id === proposalId);
    if (!p || p.state !== 'pending') {
      return;
    }
    p.state = 'rejected';
    this._setRendered(panel, p.cellId, p.wasRendered);
    this._advance(panel);
  }

  rejectAll(panel: NotebookPanel): void {
    for (const p of this.list(panel)) {
      if (p.state === 'pending') {
        p.state = 'rejected';
        this._setRendered(panel, p.cellId, p.wasRendered);
      }
    }
    this._advance(panel);
  }

  clear(panel: NotebookPanel, roundId?: string): void {
    const keep = roundId
      ? this.list(panel).filter(p => p.roundId !== roundId)
      : [];
    for (const p of this.list(panel)) {
      if (roundId === undefined || p.roundId === roundId) {
        this._setRendered(panel, p.cellId, p.wasRendered);
      }
    }
    this._byPanel.set(panel.id, keep);
    this._changed.emit(panel);
  }

  private _advance(panel: NotebookPanel): void {
    this._changed.emit(panel);
    const next = this.activeProposal(panel);
    const round = this._rounds.get(panel.id);
    const targetId = next ? next.cellId : round?.promptCellId;
    if (!targetId) {
      return;
    }
    const idx = this._indexOf(panel, targetId);
    if (idx !== -1) {
      panel.content.activeCellIndex = idx;
      panel.content.mode = 'command';
      const widget = panel.content.widgets[idx];
      widget?.node.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    if (!next) {
      panel.node.classList.remove('jv-round-active');
      this._rounds.delete(panel.id);
    }
  }

  private _cellWidget(panel: NotebookPanel, cellId: string): Cell | null {
    const idx = this._indexOf(panel, cellId);
    return idx === -1 ? null : (panel.content.widgets[idx] ?? null);
  }

  private _isRendered(panel: NotebookPanel, cellId: string): boolean {
    const w = this._cellWidget(panel, cellId);
    return w && w.model.type === 'markdown'
      ? (w as MarkdownCell).rendered
      : false;
  }

  /** Markdown cells must be unrendered to show a source diff (docs/03). */
  private _setRendered(
    panel: NotebookPanel,
    cellId: string,
    rendered: boolean
  ): void {
    const w = this._cellWidget(panel, cellId);
    if (w && w.model.type === 'markdown') {
      (w as MarkdownCell).rendered = rendered;
    }
  }

  private _indexOf(panel: NotebookPanel, cellId: string): number {
    const cells = panel.content.model?.cells;
    if (!cells) {
      return -1;
    }
    for (let i = 0; i < cells.length; i++) {
      if (cells.get(i).id === cellId) {
        return i;
      }
    }
    return -1;
  }
}

export const proposalsPlugin: JupyterFrontEndPlugin<IVybeProposals> = {
  id: 'jupyter-vybe:proposals',
  description: 'Stages and reviews proposed cell edits.',
  autoStart: true,
  provides: IVybeProposals,
  requires: [INotebookTracker],
  activate: (_app, _tracker: INotebookTracker): IVybeProposals =>
    new Proposals()
};

export default proposalsPlugin;
