import {
  OperationLog,
  applyProposal,
  insertCellsBelow,
  findCellModel,
  revertRound
} from '../notebook/mutations';

/**
 * mutations.ts is the only module allowed to write to a notebook, which makes
 * it the one place a bug can corrupt the user's work. The invariant under test
 * throughout: every write is recorded, and replaying the log backwards leaves
 * the notebook exactly as it was.
 */

type Fake = { id: string; cell_type: string; source: string };

function fakePanel(initial: Fake[]): any {
  const cells: any[] = initial.map(c => wrap(c));
  let nextId = 0;

  function wrap(c: Fake) {
    return {
      id: c.id,
      type: c.cell_type,
      sharedModel: {
        getSource: () => c.source,
        setSource: (s: string) => {
          c.source = s;
        }
      },
      raw: c
    };
  }

  const shared = {
    transact: (fn: () => void) => fn(),
    insertCell: (index: number, spec: any) => {
      const created: Fake = {
        id: `new-${nextId++}`,
        cell_type: spec.cell_type,
        source: spec.source
      };
      cells.splice(index, 0, wrap(created));
    },
    deleteCell: (index: number) => {
      cells.splice(index, 1);
    }
  };

  return {
    id: 'panel-1',
    content: {
      activeCellIndex: 0,
      mode: 'command',
      deselectAll: () => undefined,
      widgets: [],
      model: {
        cells: {
          get length() {
            return cells.length;
          },
          get: (i: number) => cells[i]
        },
        sharedModel: shared
      }
    },
    sources: () => cells.map(c => c.sharedModel.getSource()),
    ids: () => cells.map(c => c.id),
    /** Stands in for the user deleting a cell themselves. */
    userDeletes: (index: number) => shared.deleteCell(index)
  };
}

const base = () => [
  { id: 'a', cell_type: 'code', source: 'import numpy as np' },
  { id: 'b', cell_type: 'code', source: 'x = 1' }
];

describe('insertCellsBelow', () => {
  it('inserts in order directly below the anchor', () => {
    const panel = fakePanel(base());
    const log = new OperationLog();

    const ids = insertCellsBelow(
      panel,
      'a',
      [
        { type: 'code', source: 'first' },
        { type: 'markdown', source: 'second' }
      ],
      log
    );

    expect(ids).toHaveLength(2);
    expect(panel.sources()).toEqual([
      'import numpy as np',
      'first',
      'second',
      'x = 1'
    ]);
    expect(log.operations).toEqual([{ kind: 'insert', cellIds: ids }]);
  });

  it('does nothing when the anchor is gone', () => {
    const panel = fakePanel(base());
    const log = new OperationLog();

    // The user deleted the prompt cell while the model was still answering.
    const ids = insertCellsBelow(
      panel,
      'no-such-cell',
      [{ type: 'code', source: 'orphan' }],
      log
    );

    expect(ids).toEqual([]);
    expect(panel.sources()).toEqual(['import numpy as np', 'x = 1']);
    expect(log.operations).toEqual([]);
  });

  it('records nothing for an empty batch', () => {
    const panel = fakePanel(base());
    const log = new OperationLog();
    expect(insertCellsBelow(panel, 'a', [], log)).toEqual([]);
    expect(log.operations).toEqual([]);
  });
});

describe('applyProposal', () => {
  it('writes the new source and records what it replaced', () => {
    const panel = fakePanel(base());
    const log = new OperationLog();

    expect(applyProposal(panel, 'b', 'x = 2', log)).toBe(true);

    expect(panel.sources()[1]).toBe('x = 2');
    expect(log.operations).toEqual([
      { kind: 'edit', cellId: 'b', before: 'x = 1', after: 'x = 2' }
    ]);
  });

  it('is a no-op when the cell is gone', () => {
    const panel = fakePanel(base());
    const log = new OperationLog();
    expect(applyProposal(panel, 'vanished', 'x = 2', log)).toBe(false);
    expect(log.operations).toEqual([]);
  });

  it('is a no-op when the source is unchanged', () => {
    const panel = fakePanel(base());
    const log = new OperationLog();
    expect(applyProposal(panel, 'b', 'x = 1', log)).toBe(false);
    expect(log.operations).toEqual([]);
  });
});

describe('the operation log', () => {
  it('stops recording once sealed', () => {
    const log = new OperationLog();
    log.record({ kind: 'insert', cellIds: ['x'] });
    log.seal();
    log.record({ kind: 'insert', cellIds: ['y'] });
    expect(log.operations).toHaveLength(1);
    expect(log.sealed).toBe(true);
  });
});

describe('revertRound', () => {
  it('removes inserted cells and restores edited ones', () => {
    const panel = fakePanel(base());
    const log = new OperationLog();
    const before = panel.sources();

    insertCellsBelow(panel, 'a', [{ type: 'code', source: 'generated' }], log);
    applyProposal(panel, 'b', 'x = 999', log);
    expect(panel.sources()).not.toEqual(before);

    revertRound(panel, log);

    expect(panel.sources()).toEqual(before);
    expect(panel.ids()).toEqual(['a', 'b']);
  });

  it('unwinds in reverse, so two edits to one cell land back at the start', () => {
    const panel = fakePanel(base());
    const log = new OperationLog();

    applyProposal(panel, 'b', 'x = 2', log);
    applyProposal(panel, 'b', 'x = 3', log);

    revertRound(panel, log);

    expect(panel.sources()[1]).toBe('x = 1');
  });

  it('leaves the log empty, so reverting twice is not destructive', () => {
    const panel = fakePanel(base());
    const log = new OperationLog();
    insertCellsBelow(panel, 'a', [{ type: 'code', source: 'generated' }], log);

    revertRound(panel, log);
    revertRound(panel, log);

    expect(panel.sources()).toEqual(['import numpy as np', 'x = 1']);
  });

  it('survives a cell the user deleted before the revert', () => {
    const panel = fakePanel(base());
    const log = new OperationLog();
    const ids = insertCellsBelow(
      panel,
      'a',
      [{ type: 'code', source: 'generated' }],
      log
    );
    // The user deleted the generated cell themselves.
    panel.userDeletes(1);

    expect(() => revertRound(panel, log)).not.toThrow();
    expect(findCellModel(panel, ids[0])).toBeNull();
    expect(panel.sources()).toEqual(['import numpy as np', 'x = 1']);
  });
});
