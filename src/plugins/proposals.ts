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
import { IVybeSettings } from '../tokens';
import { appendHistory } from '../history';
import { diffLines, normaliseSource, type IHunk } from '../ui/diff';

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
  /**
   * Called once, when the last pending proposal of the round is settled.
   * The round is not over when the agent stops streaming -- it is over when
   * review ends -- and until this fires the round is still registered, which
   * blocks the next prompt in that notebook.
   */
  endReview(): void;
}

export interface IVybeProposals {
  stage(panel: NotebookPanel, roundId: string, req: IStageRequest): boolean;
  pendingCount(panel: NotebookPanel, roundId?: string): number;
  list(panel: NotebookPanel): IProposal[];
  beginReview(panel: NotebookPanel, round: IRoundHandle): void;
  accept(panel: NotebookPanel, proposalId: string): void;
  reject(panel: NotebookPanel, proposalId: string): void;
  rejectAll(panel: NotebookPanel): void;
  undoLast(panel: NotebookPanel): boolean;
  canUndo(panel: NotebookPanel): boolean;
  undoRejectAll(panel: NotebookPanel): boolean;
  undoableRejection(panel: NotebookPanel): number;
  activeProposal(panel: NotebookPanel): IProposal | null;
  /** The prompt cell of the round under review, for showing its summary. */
  roundPromptCellId(panel: NotebookPanel): string | null;
  clear(panel: NotebookPanel, roundId?: string): void;
  logFor(panel: NotebookPanel): OperationLog;
  readonly changed: ISignal<IVybeProposals, NotebookPanel>;
}
export const IVybeProposals = new Token<IVybeProposals>(
  'jupyter-vybe:IVybeProposals'
);

export class Proposals implements IVybeProposals {
  /** Where the record lives; only used when posting a verdict (D12). */
  constructor(private readonly _location: 'user' | 'adjacent' = 'user') {}

  private _byPanel = new Map<string, IProposal[]>();
  private _rounds = new Map<string, IRoundHandle>();
  /** The last reject-all, kept so a mis-click is recoverable (docs/14 N3). */
  private _undo = new Map<string, { ids: string[]; round: IRoundHandle }>();
  /** Verdicts in the order they were given, for round-scoped undo (docs/02). */
  private _resolved = new Map<string, string[]>();
  /**
   * The round that just ended, kept so its log survives long enough to undo
   * the verdict that ended it. Without this the *last* accept of a round could
   * be re-staged but not un-applied: the diff came back while the cell stayed
   * changed, which is worse than not undoing at all.
   */
  private _lastRound = new Map<string, IRoundHandle>();
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
    if (normaliseSource(before) === normaliseSource(req.after)) {
      // Models frequently "edit" a cell into itself; drop silently. A reply
      // that differs only in its trailing newline counts as itself.
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
    this._undo.delete(panel.id);
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

  roundPromptCellId(panel: NotebookPanel): string | null {
    return (
      this._rounds.get(panel.id)?.promptCellId ??
      this._undo.get(panel.id)?.round.promptCellId ??
      null
    );
  }

  beginReview(panel: NotebookPanel, round: IRoundHandle): void {
    this._rounds.set(panel.id, round);
    for (const p of this.list(panel)) {
      if (p.state === 'pending') {
        this._setRendered(panel, p.cellId, false);
      }
    }
    // Focus deliberately stays where Rounds put it -- on the prompt cell,
    // showing how many edits are waiting. Review starts when the user presses
    // Enter, rather than dropping them into the first diff unasked.
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
    this._pushResolved(panel, p);
    this._recordVerdict(panel, p);
    this._advance(panel);
  }

  reject(panel: NotebookPanel, proposalId: string): void {
    const p = this.list(panel).find(x => x.id === proposalId);
    if (!p || p.state !== 'pending') {
      return;
    }
    p.state = 'rejected';
    this._setRendered(panel, p.cellId, p.wasRendered);
    this._pushResolved(panel, p);
    this._recordVerdict(panel, p);
    this._advance(panel);
  }

  rejectAll(panel: NotebookPanel): void {
    const round = this._rounds.get(panel.id);
    const ids: string[] = [];
    for (const p of this.list(panel)) {
      if (p.state === 'pending') {
        p.state = 'rejected';
        this._setRendered(panel, p.cellId, p.wasRendered);
        this._pushResolved(panel, p);
        this._recordVerdict(panel, p);
        ids.push(p.id);
      }
    }
    // One click can discard a whole round's review, so remember what it was.
    if (ids.length && round) {
      this._undo.set(panel.id, { ids, round });
    }
    this._advance(panel);
  }

  /**
   * Layer 2 of undo (docs/02): take back the most recent verdict.
   *
   * An accepted proposal is restored to its previous source and re-staged as
   * pending, so the cell shows its diff again and the review resumes there.
   * Returns false when there is no verdict left to take back -- the caller
   * then reverts the whole round, which is the last row of the undo table.
   */
  undoLast(panel: NotebookPanel): boolean {
    const order = this._resolved.get(panel.id) ?? [];
    const id = order.pop();
    if (!id) {
      return false;
    }
    const p = this.list(panel).find(x => x.id === id);
    if (!p || p.state === 'pending') {
      return false;
    }
    const round =
      this._undo.get(panel.id)?.round ??
      this._rounds.get(panel.id) ??
      this._lastRound.get(panel.id);
    if (p.state === 'accepted' && round) {
      // Put the cell back the way it was. Recorded as an edit of its own, so
      // a later revertRound still unwinds cleanly.
      applyProposal(panel, p.cellId, p.before, round.log);
    }
    p.state = 'pending';
    this._setRendered(panel, p.cellId, false);
    if (round) {
      this._rounds.set(panel.id, round);
    }
    this._undo.delete(panel.id);
    panel.content.node.classList.add('jv-round-active');
    this._changed.emit(panel);
    const idx = this._indexOf(panel, p.cellId);
    if (idx !== -1) {
      panel.content.activeCellIndex = idx;
      panel.content.mode = 'command';
      panel.content.widgets[idx]?.node.scrollIntoView({
        behavior: 'smooth',
        block: 'center'
      });
    }
    return true;
  }

  /**
   * Whether a verdict is still takeable back. Used to decide whether Cmd+Z
   * belongs to us or to the notebook: the binding is registered for any
   * notebook in command mode, and when this returns false Lumino skips the
   * disabled command and core undo runs instead.
   */
  canUndo(panel: NotebookPanel): boolean {
    const order = this._resolved.get(panel.id) ?? [];
    if (!order.length) {
      return false;
    }
    const last = this.list(panel).find(p => p.id === order[order.length - 1]);
    return !!last && last.state !== 'pending';
  }

  private _pushResolved(panel: NotebookPanel, p: IProposal): void {
    if (p.state === 'stale') {
      // Nothing to take back: the user's own edit is what settled it.
      return;
    }
    const order = this._resolved.get(panel.id) ?? [];
    order.push(p.id);
    this._resolved.set(panel.id, order);
  }

  /** Put the last reject-all back up for review. */
  undoRejectAll(panel: NotebookPanel): boolean {
    const undo = this._undo.get(panel.id);
    if (!undo) {
      return false;
    }
    this._undo.delete(panel.id);
    const list = this.list(panel);
    let restored = 0;
    for (const p of list) {
      if (undo.ids.includes(p.id) && p.state === 'rejected') {
        p.state = 'pending';
        restored++;
      }
    }
    if (!restored) {
      return false;
    }
    // Re-register the round: accept() needs its log to record the edit, and
    // endReview must fire again when the restored review finishes.
    this._rounds.set(panel.id, undo.round);
    this.beginReview(panel, undo.round);
    return true;
  }

  undoableRejection(panel: NotebookPanel): number {
    return this._undo.get(panel.id)?.ids.length ?? 0;
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
    this._undo.delete(panel.id);
    this._resolved.delete(panel.id);
    this._lastRound.delete(panel.id);
    const round = this._rounds.get(panel.id);
    if (round && (roundId === undefined || round.id === roundId)) {
      // Cleared, not reviewed: the caller (a cancel) finishes the round
      // itself, so drop the handle without calling endReview.
      this._rounds.delete(panel.id);
    }
    this._changed.emit(panel);
  }

  /**
   * The verdict is the half of a proposal the server cannot see, so it is
   * posted rather than inferred (docs/05). A stale proposal is recorded too:
   * "the user edited this cell before deciding" is history worth keeping.
   */
  private _recordVerdict(panel: NotebookPanel, p: IProposal): void {
    void appendHistory(
      panel.context.path,
      {
        kind: 'verdict',
        roundId: p.roundId,
        cellId: p.cellId,
        outcome: p.state,
        cellLabel: p.targetLabel
      },
      this._location
    );
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
      // Focused so the next verdict's keystroke lands on the next cell.
      widget?.node.focus();
      widget?.node.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    if (!next) {
      const ending = this._rounds.get(panel.id);
      if (ending) {
        this._lastRound.set(panel.id, ending);
      }
      this._rounds.delete(panel.id);
      // Ownership of the round -- the marker class included -- belongs to
      // Rounds, not here.
      round?.endReview();
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
  optional: [IVybeSettings],
  activate: (
    _app,
    _tracker: INotebookTracker,
    settings: IVybeSettings | null
  ): IVybeProposals => new Proposals(settings?.historyLocation ?? 'user')
};

export default proposalsPlugin;
