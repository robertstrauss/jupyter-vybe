// The verdict is posted to the sidecar record; the network is not the
// subject here (docs/05).
jest.mock('../history', () => ({ appendHistory: jest.fn() }));

import { Proposals, type IRoundHandle } from '../plugins/proposals';
import { OperationLog } from '../notebook/mutations';

/**
 * A round that produces proposals is not over when the agent stops streaming;
 * it is over when the last verdict lands. Until then it stays registered, and
 * a registered round refuses the next prompt in that notebook -- which the
 * user sees as shift-enter doing nothing at all (docs/14).
 */

type FakeCell = { id: string; type: string; source: string };

function fakePanel(cells: FakeCell[]): any {
  const models = cells.map(c => ({
    id: c.id,
    type: c.type,
    sharedModel: {
      getSource: () => c.source,
      setSource: (s: string) => {
        c.source = s;
      }
    }
  }));
  return {
    id: 'panel-1',
    context: { path: 'nb.ipynb' },
    node: { classList: { add: () => undefined, remove: () => undefined } },
    content: {
      // The round marker lives on the content node -- the element carrying
      // .jp-Notebook -- because that is what the keybinding selectors match.
      node: {
        classList: { add: () => undefined, remove: () => undefined },
        focus: () => undefined
      },
      activeCellIndex: 0,
      mode: 'command',
      widgets: models.map(() => ({
        model: { type: 'code' },
        node: { scrollIntoView: () => undefined }
      })),
      model: {
        cells: { length: models.length, get: (i: number) => models[i] },
        sharedModel: { transact: (fn: () => void) => fn() }
      }
    }
  };
}

function handle(onEnd: () => void): IRoundHandle {
  return {
    id: 'round-1',
    log: new OperationLog(),
    promptCellId: 'p1',
    endReview: onEnd
  };
}

describe('Proposals review lifecycle', () => {
  const stageTwo = (proposals: Proposals, panel: any) => {
    expect(
      proposals.stage(panel, 'round-1', {
        cellId: 'c1',
        after: 'a = 2\n',
        prefix: '#%'
      })
    ).toBe(true);
    expect(
      proposals.stage(panel, 'round-1', {
        cellId: 'c2',
        after: 'b = 2\n',
        prefix: '#%'
      })
    ).toBe(true);
  };

  const panelWithTwo = () =>
    fakePanel([
      { id: 'p1', type: 'markdown', source: '#% do a thing' },
      { id: 'c1', type: 'code', source: 'a = 1\n' },
      { id: 'c2', type: 'code', source: 'b = 1\n' }
    ]);

  it('ends the round exactly once, after the last verdict', () => {
    const proposals = new Proposals();
    const panel = panelWithTwo();
    let ended = 0;
    stageTwo(proposals, panel);
    proposals.beginReview(panel, handle(() => ended++));

    proposals.accept(panel, proposals.list(panel)[0].id);
    expect(ended).toBe(0); // one still pending
    proposals.reject(panel, proposals.list(panel)[1].id);
    expect(ended).toBe(1);
    expect(proposals.pendingCount(panel)).toBe(0);
  });

  it('ends the round when every proposal is rejected at once', () => {
    const proposals = new Proposals();
    const panel = panelWithTwo();
    let ended = 0;
    stageTwo(proposals, panel);
    proposals.beginReview(panel, handle(() => ended++));

    proposals.rejectAll(panel);
    expect(ended).toBe(1);
  });

  it('does not end the round twice when a settled proposal is re-settled', () => {
    const proposals = new Proposals();
    const panel = panelWithTwo();
    let ended = 0;
    stageTwo(proposals, panel);
    proposals.beginReview(panel, handle(() => ended++));

    proposals.rejectAll(panel);
    const first = proposals.list(panel)[0].id;
    proposals.accept(panel, first);
    proposals.reject(panel, first);
    expect(ended).toBe(1);
  });

  it('drops a proposal that differs only in its trailing newline', () => {
    const proposals = new Proposals();
    const panel = panelWithTwo();
    expect(
      proposals.stage(panel, 'round-1', {
        cellId: 'c1',
        after: 'a = 1',
        prefix: '#%'
      })
    ).toBe(false);
  });
});

describe('round-scoped undo (docs/02 layer 2)', () => {
  const panelWith = (sources: string[]) => {
    const cells = [
      { id: 'p1', type: 'markdown', source: '#% do a thing' },
      ...sources.map((s, i) => ({ id: `c${i + 1}`, type: 'code', source: s }))
    ];
    const models = cells.map(c => ({
      id: c.id,
      type: c.type,
      sharedModel: {
        getSource: () => c.source,
        setSource: (s: string) => {
          c.source = s;
        }
      }
    }));
    return {
      id: 'panel-1',
      context: { path: 'nb.ipynb' },
      node: { classList: { add: () => undefined, remove: () => undefined } },
      content: {
        node: {
          classList: { add: () => undefined, remove: () => undefined },
          focus: () => undefined
        },
        activeCellIndex: 0,
        mode: 'command',
        widgets: models.map(() => ({
          model: { type: 'code' },
          node: { scrollIntoView: () => undefined }
        })),
        model: {
          cells: { length: models.length, get: (i: number) => models[i] },
          sharedModel: { transact: (fn: () => void) => fn() }
        }
      },
      _cells: cells
    } as any;
  };

  const setup = () => {
    const proposals = new Proposals();
    const panel = panelWith(['a = 1\n', 'b = 1\n']);
    proposals.stage(panel, 'round-1', {
      cellId: 'c1',
      after: 'a = 2\n',
      prefix: '#%'
    });
    proposals.stage(panel, 'round-1', {
      cellId: 'c2',
      after: 'b = 2\n',
      prefix: '#%'
    });
    proposals.beginReview(panel, handle(() => undefined));
    return { proposals, panel };
  };

  it('un-accepts the last verdict and restores the cell', () => {
    const { proposals, panel } = setup();
    const [first] = proposals.list(panel);
    proposals.accept(panel, first.id);
    expect(panel._cells[1].source).toBe('a = 2\n');

    expect(proposals.undoLast(panel)).toBe(true);

    expect(panel._cells[1].source).toBe('a = 1\n');
    expect(proposals.list(panel)[0].state).toBe('pending');
  });

  it('un-rejects without touching the cell', () => {
    const { proposals, panel } = setup();
    const [, second] = proposals.list(panel);
    proposals.reject(panel, second.id);

    expect(proposals.undoLast(panel)).toBe(true);

    expect(panel._cells[2].source).toBe('b = 1\n');
    expect(proposals.pendingCount(panel)).toBe(2);
  });

  it('takes verdicts back in reverse order, then reports nothing left', () => {
    const { proposals, panel } = setup();
    const [first, second] = proposals.list(panel);
    proposals.accept(panel, first.id);
    proposals.reject(panel, second.id);

    expect(proposals.undoLast(panel)).toBe(true);
    expect(proposals.list(panel)[1].state).toBe('pending');
    expect(proposals.undoLast(panel)).toBe(true);
    expect(proposals.list(panel)[0].state).toBe('pending');
    // Nothing left to take back: the caller reverts the whole round instead.
    expect(proposals.undoLast(panel)).toBe(false);
  });

  it('recovers a reject-all one verdict at a time', () => {
    const { proposals, panel } = setup();
    proposals.rejectAll(panel);
    expect(proposals.pendingCount(panel)).toBe(0);

    expect(proposals.undoLast(panel)).toBe(true);

    expect(proposals.pendingCount(panel)).toBe(1);
  });
});
