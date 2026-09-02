import { NotebookActions, type NotebookPanel } from '@jupyterlab/notebook';
import type { ICellModel } from '@jupyterlab/cells';

/**
 * The ONLY module permitted to write to a notebook model (docs/02).
 *
 * Everything the agent can cause to happen passes through these four functions,
 * which is what keeps it structurally unable to corrupt a notebook. Direct
 * `model.cells` manipulation is banned elsewhere and enforced by lint.
 *
 * Every mutation of a round runs inside one `sharedModel.transact`, so the ydoc
 * undo manager sees the whole round as a single unit and a plain Cmd+Z reverts
 * it atomically.
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

/** Replay a round's operations backwards. Used by cancel and reject-all. */
export function revertRound(panel: NotebookPanel, log: OperationLog): void {
  const model = panel.content.model;
  if (!model) {
    return;
  }
  model.sharedModel.transact(() => {
    let op = log.pop();
    while (op) {
      if (op.kind === 'insert') {
        for (const id of [...op.cellIds].reverse()) {
          const idx = sharedIndexOfCell(panel, id);
          if (idx !== -1) {
            model.sharedModel.deleteCell(idx);
          }
        }
      } else if (op.kind === 'edit') {
        const cell = findCellModel(panel, op.cellId);
        cell?.sharedModel.setSource(op.before);
      } else {
        const idx = indexOfCell(panel, op.cellId);
        if (idx !== -1) {
          const priorActive = panel.content.activeCellIndex;
          panel.content.activeCellIndex = idx;
          panel.content.deselectAll();
          NotebookActions.changeCellType(
            panel.content,
            op.beforeType as 'code' | 'markdown' | 'raw'
          );
          model.cells.get(idx).sharedModel.setSource(op.beforeSource);
          panel.content.activeCellIndex = priorActive;
        }
      }
      op = log.pop();
    }
  });
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
