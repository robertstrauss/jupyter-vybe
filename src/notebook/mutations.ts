import { NotebookActions, type NotebookPanel } from '@jupyterlab/notebook';
import type { ICellModel } from '@jupyterlab/cells';

/**
 * The ONLY module permitted to write to a notebook model (docs/02).
 *
 * Everything the agent can cause to happen passes through these four functions,
 * which is what keeps it structurally unable to corrupt a notebook. Direct
 * `model.cells` manipulation is banned elsewhere and enforced by lint.
 *
 * Writes run inside `sharedModel.transact` so the ydoc undo manager sees them
 * as units. Note that `model.cells` does not observe writes made inside the
 * transaction it is in: any index or cell model needed after a write has to be
 * read once that write has committed. Getting this wrong is silent -- the write
 * lands on a detached model -- and has cost this project two bugs.
 */

export type ICellSpec = { type: 'code' | 'markdown'; source: string };

/** One reversible step, recorded so a round can be replayed backwards. */
export type IOperation =
  | { kind: 'insert'; cellIds: string[] }
  | { kind: 'edit'; cellId: string; before: string; after: string }
  | {
      kind: 'convert';
      cellId: string;
      beforeType: string;
      beforeSource: string;
    };

export class OperationLog {
  private _ops: IOperation[] = [];
  private _sealed = false;

  record(op: IOperation): void {
    if (!this._sealed) {
      this._ops.push(op);
    }
  }

  /** After a non-vybe edit the log stops being authoritative (docs/02, R5). */
  seal(): void {
    this._sealed = true;
  }

  get sealed(): boolean {
    return this._sealed;
  }

  get operations(): readonly IOperation[] {
    return this._ops;
  }

  pop(): IOperation | undefined {
    return this._ops.pop();
  }
}

function indexOfCell(panel: NotebookPanel, cellId: string): number {
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

/**
 * The same lookup against the *shared* model.
 *
 * `model.cells` is a view that syncs from the ydoc through observers, and
 * those do not run until the enclosing `transact` commits. Inside a
 * transaction it is therefore stale: revertRound deleted a cell by an index
 * computed before the conversion above it had landed, and quietly deleted
 * nothing. The shared model is always current.
 */
function sharedIndexOfCell(panel: NotebookPanel, cellId: string): number {
  const shared = panel.content.model?.sharedModel;
  if (!shared) {
    return -1;
  }
  const cells = shared.cells;
  for (let i = 0; i < cells.length; i++) {
    if (cells[i].getId() === cellId) {
      return i;
    }
  }
  return -1;
}

export function findCellModel(
  panel: NotebookPanel,
  cellId: string
): ICellModel | null {
  const idx = indexOfCell(panel, cellId);
  return idx === -1 ? null : (panel.content.model!.cells.get(idx) ?? null);
}

/**
 * Insert cells directly below `anchorId`. The anchor index is recomputed from
 * the cell id at apply time, never cached, so concurrent edits above cannot
 * misplace insertions (docs/02, R9).
 */
export function insertCellsBelow(
  panel: NotebookPanel,
  anchorId: string,
  specs: ICellSpec[],
  log: OperationLog
): string[] {
  const model = panel.content.model;
  if (!model || specs.length === 0) {
    return [];
  }
  const anchor = indexOfCell(panel, anchorId);
  if (anchor === -1) {
    return [];
  }
  // Insert inside the transaction, but read the ids back *after* it commits.
  //
  // Both views of the notebook lag inside a transact: `model.cells` syncs
  // through observers that have not run yet, and `sharedModel.cells` is no
  // better. Reading either one mid-transaction returns whichever cell used to
  // occupy that index -- so the round recorded the id of a cell it had not
  // created, undo deleted the wrong cell, and the post-round highlight
  // selected the wrong one. Waiting until the transaction commits is the only
  // reading that is true.
  const indices: number[] = [];
  model.sharedModel.transact(() => {
    specs.forEach((spec, offset) => {
      const at = anchor + 1 + offset;
      model.sharedModel.insertCell(at, {
        cell_type: spec.type,
        source: spec.source,
        metadata: {}
      });
      indices.push(at);
    });
  });
  const ids = indices
    .map(index => model.cells.get(index)?.id)
    .filter((id): id is string => !!id);
  log.record({ kind: 'insert', cellIds: ids });
  return ids;
}

/**
 * Write a cell the round is still receiving.
 *
 * Deliberately *not* logged: the operation log is for things a user might undo,
 * and a half-written cell is not one of them. The `insert` op that created the
 * cell already covers removing it, so undo stays correct however far the
 * streaming got.
 *
 * Only ever called on a cell this round created, never on the user's own.
 */
export function streamIntoCell(
  panel: NotebookPanel,
  cellId: string,
  source: string
): void {
  const cell = findCellModel(panel, cellId);
  if (!cell || cell.sharedModel.getSource() === source) {
    return;
  }
  panel.content.model!.sharedModel.transact(() => {
    cell.sharedModel.setSource(source);
  });
}

/** Apply an accepted proposal. The model is untouched until this runs. */
export function applyProposal(
  panel: NotebookPanel,
  cellId: string,
  after: string,
  log: OperationLog
): boolean {
  const cell = findCellModel(panel, cellId);
  if (!cell) {
    return false;
  }
  const before = cell.sharedModel.getSource();
  if (before === after) {
    return false;
  }
  panel.content.model!.sharedModel.transact(() => {
    cell.sharedModel.setSource(after);
  });
  log.record({ kind: 'edit', cellId, before, after });
  return true;
}

/**
 * Settle a prompt cell into markdown. Goes through NotebookActions so cell ids,
 * ydoc history, and RTC stay coherent -- never by hand-editing the model.
 */
export function convertPromptToMarkdown(
  panel: NotebookPanel,
  cellId: string,
  markdownSource: string,
  log: OperationLog
): void {
  const model = panel.content.model;
  const idx = indexOfCell(panel, cellId);
  if (!model || idx === -1) {
    return;
  }
  const cell = model.cells.get(idx);
  const beforeSource = cell.sharedModel.getSource();
  const beforeType = cell.type;

  const priorActive = panel.content.activeCellIndex;
  panel.content.activeCellIndex = idx;
  panel.content.deselectAll();
  model.sharedModel.transact(() => {
    cell.sharedModel.setSource(markdownSource);
  });
  NotebookActions.changeCellType(panel.content, 'markdown');
  panel.content.activeCellIndex = priorActive;

  log.record({ kind: 'convert', cellId, beforeType, beforeSource });
}

/**
 * Replay a round's operations backwards. Used by cancel, reject-all and undo.
 *
 * One transaction per operation, not one around the lot.
 *
 * A single enclosing transaction reads better and would group the whole revert
 * for the ydoc undo manager, but it is wrong twice over. `model.cells` does not
 * see writes made inside the transaction it is in, so (a) after deleting the
 * cells an insert added, the index lookups for the remaining operations are
 * computed against a notebook that still contains them -- aiming an edit or a
 * cell-type change at whatever now sits at that index -- and (b)
 * `changeCellType` replaces the cell, so the model read back to restore the
 * source is the detached one, and the write lands nowhere. That second bug is
 * why undoing a round removed the inserted cells but left the prompt as
 * settled markdown.
 */
export function revertRound(panel: NotebookPanel, log: OperationLog): void {
  const model = panel.content.model;
  if (!model) {
    return;
  }
  let op = log.pop();
  while (op) {
    if (op.kind === 'insert') {
      const ids = [...op.cellIds].reverse();
      model.sharedModel.transact(() => {
        for (const id of ids) {
          const idx = sharedIndexOfCell(panel, id);
          if (idx !== -1) {
            model.sharedModel.deleteCell(idx);
          }
        }
      });
    } else if (op.kind === 'edit') {
      const cell = findCellModel(panel, op.cellId);
      const before = op.before;
      if (cell) {
        model.sharedModel.transact(() => {
          cell.sharedModel.setSource(before);
        });
      }
    } else {
      revertConversion(panel, op);
    }
    op = log.pop();
  }
}

/**
 * Put a converted cell back to the type and source it had.
 *
 * Deliberately outside any transaction: `changeCellType` swaps the cell for a
 * new one, and only once the change has committed does `activeCell` name the
 * cell that now exists. Reading it any earlier hands back the cell that was
 * just thrown away.
 */
function revertConversion(
  panel: NotebookPanel,
  op: Extract<IOperation, { kind: 'convert' }>
): void {
  const model = panel.content.model;
  const idx = model ? indexOfCell(panel, op.cellId) : -1;
  if (!model || idx === -1) {
    return;
  }
  const priorActive = panel.content.activeCellIndex;
  panel.content.activeCellIndex = idx;
  panel.content.deselectAll();
  NotebookActions.changeCellType(
    panel.content,
    op.beforeType as 'code' | 'markdown' | 'raw'
  );
  const restored = model.cells.get(idx);
  if (restored) {
    const beforeSource = op.beforeSource;
    model.sharedModel.transact(() => {
      restored.sharedModel.setSource(beforeSource);
    });
  }
  panel.content.activeCellIndex = priorActive;
}

/**
 * Materialise the `Enter` merge into a cell: accepted lines live, replaced
 * lines commented beneath (docs/03, decision D6). On demand only -- pending
 * proposals never touch the model.
 */
export function materialiseMerge(
  panel: NotebookPanel,
  cellId: string,
  merged: string,
  log: OperationLog
): void {
  const cell = findCellModel(panel, cellId);
  if (!cell) {
    return;
  }
  const before = cell.sharedModel.getSource();
  panel.content.model!.sharedModel.transact(() => {
    cell.sharedModel.setSource(merged);
  });
  log.record({ kind: 'edit', cellId, before, after: merged });
}

/**
 * Turn a settled markdown prompt cell back into an editable code prompt cell.
 * Available forever, not just inside the undo window (docs/02).
 */
export function reopenPromptCell(panel: NotebookPanel, restored: string): void {
  const model = panel.content.model;
  const cell = panel.content.activeCell;
  if (!model || !cell) {
    return;
  }
  NotebookActions.changeCellType(panel.content, 'code');
  const active = panel.content.activeCell;
  if (active) {
    model.sharedModel.transact(() => {
      active.model.sharedModel.setSource(restored);
    });
  }
  panel.content.mode = 'edit';
}

/**
 * Turn a settled prompt cell back into the code cell it was, by id and in
 * command mode -- for undo putting a round back under review, where the cell
 * has to be a code cell again to carry the review summary and answer `Enter`.
 *
 * Distinct from reopenPromptCell, which acts on the active cell and leaves the
 * user typing: that one ends a round, this one resumes it.
 */
export function restorePromptForReview(
  panel: NotebookPanel,
  cellId: string,
  source: string
): boolean {
  const model = panel.content.model;
  const index = indexOfCell(panel, cellId);
  if (!model || index === -1) {
    return false;
  }
  if (model.cells.get(index).type === 'code') {
    return true; // already a code cell; nothing to undo here
  }
  const priorActive = panel.content.activeCellIndex;
  panel.content.activeCellIndex = index;
  panel.content.deselectAll();
  NotebookActions.changeCellType(panel.content, 'code');
  const restored = panel.content.activeCell;
  if (restored) {
    model.sharedModel.transact(() => {
      restored.model.sharedModel.setSource(source);
    });
  }
  panel.content.activeCellIndex = priorActive;
  return true;
}
