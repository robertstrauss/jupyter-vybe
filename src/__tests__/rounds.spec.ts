// The notebook mutations themselves are covered by their own module and drive
// real JupyterLab widgets; here only the round's lifetime is under test.
// The real mutations drive JupyterLab widgets, so they are mocked -- but they
// still record to the operation log, because that is what undo replays and a
// fake that skipped it would make undo look broken here and work in the app.
jest.mock('../notebook/mutations', () => ({
  ...jest.requireActual('../notebook/mutations'),
  convertPromptToMarkdown: jest.fn(
    (_panel: any, cellId: string, _source: string, log: any) =>
      log.record({
        kind: 'convert',
        cellId,
        beforeType: 'code',
        beforeSource: ''
      })
  ),
  insertCellsBelow: jest.fn(
    (_panel: any, _anchor: any, _specs: any, log: any) => {
      log.record({ kind: 'insert', cellIds: ['inserted-1'] });
      return ['inserted-1'];
    }
  ),
  revertRound: jest.fn()
}));

import { convertPromptToMarkdown, revertRound } from '../notebook/mutations';
import { Rounds } from '../plugins/rounds';
import type { IRoundHandle } from '../plugins/proposals';

/**
 * The bug this covers: after one prompt had run, a second did nothing at all
 * -- the cell stayed `[ ]` with no `[*]`. Rounds refuses to start while a
 * round is registered for the panel, and a round that produced proposals was
 * never unregistered (docs/14 N5).
 */

const kernelSpec = { name: 'python3' };

function fakePanel(): any {
  const classes = new Set<string>();
  const cells = [
    { id: 'p1', type: 'code', source: '#% do a thing', metadata: {} },
    {
      id: 'load',
      type: 'code',
      source: "open('sales_2024.csv')",
      metadata: {},
      sharedModel: { getSource: () => "open('sales_2024.csv')" }
    }
  ];
  return {
    id: 'panel-1',
    node: { classList: { add: () => undefined, remove: () => undefined } },
    context: { path: 'nb.ipynb' },
    content: {
      // The round marker lives on the content node -- the element carrying
      // .jp-Notebook -- because that is what the keybinding selectors match.
      // Real enough to assert on: `jv-can-revert` is what makes Cmd+Z reach
      // the round when focus has wandered off the cell.
      node: {
        classList: {
          add: (c: string) => classes.add(c),
          remove: (c: string) => classes.delete(c),
          contains: (c: string) => classes.has(c),
          toggle: (c: string, on: boolean) =>
            on ? classes.add(c) : classes.delete(c)
        },
        focus: () => undefined
      },
      activeCellIndex: 0,
      mode: 'command',
      widgets: [{ node: {} }],
      extendContiguousSelectionTo: () => undefined,
      deselectAll: () => undefined,
      model: {
        cells: { length: cells.length, get: (i: number) => cells[i] },
        sharedModel: { transact: (fn: () => void) => fn() },
        toJSON: () => ({
          nbformat: 4,
          nbformat_minor: 5,
          metadata: {
            kernelspec: kernelSpec,
            language_info: { name: 'python' }
          },
          cells: [
            {
              id: 'p1',
              cell_type: 'code',
              source: '#% do a thing',
              outputs: []
            }
          ]
        })
      }
    },
    classes
  };
}

function fakeCell(panel: any): any {
  return {
    model: {
      id: 'p1',
      type: 'code',
      executionState: 'idle',
      sharedModel: { getSource: () => '#% do a thing' }
    },
    parent: panel.content
  };
}

const settings: any = {
  enabled: true,
  windowTokens: 32768,
  cellWindow: 0,
  prefix: '#%',
  includeOutputs: false,
  maxOutputChars: 100,
  globalPrompt: '',
  maxInsertions: 4,
  maxProposals: 4,
  maxToolTurns: 2,
  kernelIntrospection: false
};

function options(cell: any) {
  return {
    cell,
    notebook: {} as any,
    onCellExecuted: () => undefined,
    onCellExecutionScheduled: () => undefined
  } as any;
}

describe('Rounds', () => {
  const build = (
    frames: any[],
    proposalCount: number
  ): { rounds: Rounds; endReview: () => void } => {
    let handle: IRoundHandle | null = null;
    const proposals: any = {
      stage: () => true,
      pendingCount: () => proposalCount,
      beginReview: (_p: any, r: IRoundHandle) => {
        handle = r;
      },
      clear: () => undefined,
      list: () => []
    };
    const agent: any = {
      send: () =>
        (async function* () {
          for (const f of frames) {
            yield f;
          }
        })(),
      cancel: () => undefined,
      toolResult: () => undefined
    };
    const tracker: any = { find: () => null, currentWidget: null };
    const rounds = new Rounds(tracker, agent, settings, proposals);
    return { rounds, endReview: () => handle?.endReview() };
  };

  it('accepts a second prompt once a proposal-free round settles', async () => {
    const panel = fakePanel();
    const { rounds } = build([{ op: 'prose', delta: 'done' }], 0);
    (rounds as any)._panelFor = () => panel;

    expect(await rounds.begin(options(fakeCell(panel)))).toBe(true);
    expect(rounds.hasActiveRound(panel)).toBe(false);
    expect(await rounds.begin(options(fakeCell(panel)))).toBe(true);
  });

  it('holds the round open through review, then releases it', async () => {
    const panel = fakePanel();
    const { rounds, endReview } = build(
      [{ op: 'tool', name: 'propose_edit', args: { cell: '1', body: 'x' } }],
      1
    );
    (rounds as any)._panelFor = () => panel;

    await rounds.begin(options(fakeCell(panel)));
    // Still under review: a second prompt is refused, deliberately.
    expect(rounds.hasActiveRound(panel)).toBe(true);
    expect(await rounds.begin(options(fakeCell(panel)))).toBe(false);

    endReview();
    expect(rounds.hasActiveRound(panel)).toBe(false);
    expect(await rounds.begin(options(fakeCell(panel)))).toBe(true);
  });

  it('releases the round when the agent stream throws', async () => {
    const panel = fakePanel();
    const { rounds } = build([], 0);
    (rounds as any)._panelFor = () => panel;
    (rounds as any).agent = undefined;
    const agent: any = {
      send: () =>
        (async function* () {
          throw new Error('adapter died');
        })(),
      cancel: () => undefined
    };
    (rounds as any).agent = agent;

    await expect(rounds.begin(options(fakeCell(panel)))).rejects.toThrow(
      'adapter died'
    );
    expect(rounds.hasActiveRound(panel)).toBe(false);
  });

  // The context meter's data path (docs/09).
  it('reports elapsed time and the measured context while generating', async () => {
    const panel = fakePanel();
    const { rounds } = build([{ op: 'prose', delta: 'ok' }], 0);
    (rounds as any)._panelFor = () => panel;
    const seen: any[] = [];
    rounds.statusChanged.connect((_r, status) => seen.push(status));

    await rounds.begin(options(fakeCell(panel)));

    expect(seen.length).toBeGreaterThanOrEqual(2);
    expect(seen[0].phase).toBe('generating');
    expect(seen[seen.length - 1].phase).toBe('ended');
    // Measured from the snapshot the round actually sent, not guessed.
    expect(seen[seen.length - 1].contextTokens).toBeGreaterThan(0);
    expect(seen[seen.length - 1].budgetTokens).toBeGreaterThan(0);
  });

  it('stops ticking when the round ends', async () => {
    jest.useFakeTimers();
    try {
      const panel = fakePanel();
      const { rounds } = build([{ op: 'prose', delta: 'ok' }], 0);
      (rounds as any)._panelFor = () => panel;
      await rounds.begin(options(fakeCell(panel)));
      const after = jest.getTimerCount();
      expect(after).toBe(0);
    } finally {
      jest.useRealTimers();
    }
  });

  // The prompt cell is where the review happens now (docs/03).
  it('leaves the prompt cell as code while verdicts are outstanding', async () => {
    const panel = fakePanel();
    const { rounds } = build(
      [{ op: 'tool', name: 'propose_edit', args: { cell: '1', body: 'x' } }],
      1
    );
    (rounds as any)._panelFor = () => panel;
    (convertPromptToMarkdown as jest.Mock).mockClear();

    await rounds.begin(options(fakeCell(panel)));

    // Converting now would destroy the cell showing the count the user is
    // being asked to act on.
    expect(convertPromptToMarkdown).not.toHaveBeenCalled();
  });

  it('converts the prompt cell once the last verdict is in', async () => {
    const panel = fakePanel();
    const { rounds, endReview } = build(
      [{ op: 'tool', name: 'propose_edit', args: { cell: '1', body: 'x' } }],
      1
    );
    (rounds as any)._panelFor = () => panel;
    (convertPromptToMarkdown as jest.Mock).mockClear();

    await rounds.begin(options(fakeCell(panel)));
    endReview();

    expect(convertPromptToMarkdown).toHaveBeenCalledTimes(1);
  });

  it('converts immediately when nothing was proposed', async () => {
    const panel = fakePanel();
    const { rounds } = build([{ op: 'prose', delta: 'done' }], 0);
    (rounds as any)._panelFor = () => panel;
    (convertPromptToMarkdown as jest.Mock).mockClear();

    await rounds.begin(options(fakeCell(panel)));

    expect(convertPromptToMarkdown).toHaveBeenCalledTimes(1);
  });

  it('says so when the agent proposes a cell unchanged', async () => {
    // It used to stream a diff preview, then drop it in silence: the edit
    // seemed to vanish before the user could accept or reject it.
    const panel = fakePanel();
    const { rounds } = build(
      [
        {
          op: 'tool',
          name: 'propose_edit',
          args: {
            cell: 'load',
            body: "open('sales_2024.csv')\n",
            note: 'fixed'
          }
        }
      ],
      0
    );
    (rounds as any)._panelFor = () => panel;
    (convertPromptToMarkdown as jest.Mock).mockClear();

    await rounds.begin(options(fakeCell(panel)));

    const settled = (convertPromptToMarkdown as jest.Mock).mock.calls[0][2];
    expect(settled).toContain('proposed no change');
    expect(settled).toContain('fixed');
  });

  it('leaves the user on the prompt cell in command mode for review', async () => {
    const panel = fakePanel();
    const { rounds } = build(
      [{ op: 'tool', name: 'propose_edit', args: { cell: '1', body: 'x' } }],
      1
    );
    (rounds as any)._panelFor = () => panel;
    // Shift+Enter has already moved on by the time a round settles.
    panel.content.activeCellIndex = 5;

    await rounds.begin(options(fakeCell(panel)));

    expect(panel.content.activeCellIndex).toBe(0); // the prompt cell
    expect(panel.content.mode).toBe('command');
  });

  describe('undoing a settled round (docs/02)', () => {
    const settled = async () => {
      const panel = fakePanel();
      const { rounds } = build([{ op: 'prose', delta: 'done' }], 0);
      (rounds as any)._panelFor = () => panel;
      await rounds.begin(options(fakeCell(panel)));
      return { panel, rounds };
    };

    it('has nothing to revert before a round has run', () => {
      const { rounds } = build([], 0);
      expect(rounds.canRevertLast(fakePanel())).toBe(false);
    });

    it('reverts the round and hands the prompt back for editing', async () => {
      const { panel, rounds } = await settled();
      (revertRound as jest.Mock).mockClear();

      expect(rounds.canRevertLast(panel)).toBe(true);
      expect(rounds.revertLast(panel)).toBe(true);

      // Everything the round did, undone in one step...
      expect(revertRound).toHaveBeenCalledTimes(1);
      // ...and the cursor put back in the prompt, because undoing a generation
      // is almost always the prelude to rewording it.
      expect(panel.content.mode).toBe('edit');
    });

    /**
     * The user's report: Cmd+Z did nothing after a round settled. Settling
     * replaces the prompt cell's widget and focus goes with it, so a binding
     * that required a focused cell never fired. This class is the binding's
     * other target, and it has to appear and vanish with the undo window.
     */
    it('marks the notebook while the round can still be taken back', async () => {
      const { panel, rounds } = await settled();
      expect(panel.classes.has('jv-can-revert')).toBe(true);

      rounds.revertLast(panel);

      expect(panel.classes.has('jv-can-revert')).toBe(false);
    });

    it('only reaches back one round', async () => {
      const { panel, rounds } = await settled();
      expect(rounds.revertLast(panel)).toBe(true);
      // A second press is not ours: core undo should get it.
      expect(rounds.revertLast(panel)).toBe(false);
      expect(rounds.canRevertLast(panel)).toBe(false);
    });

    it("a new round supersedes the previous round's undo", async () => {
      const { panel, rounds } = await settled();
      expect(rounds.canRevertLast(panel)).toBe(true);

      await rounds.begin(options(fakeCell(panel)));

      // Reaching further back would delete cells the user has since worked on.
      expect(rounds.canRevertLast(panel)).toBe(true);
      rounds.revertLast(panel);
      expect(rounds.canRevertLast(panel)).toBe(false);
    });
  });
});
