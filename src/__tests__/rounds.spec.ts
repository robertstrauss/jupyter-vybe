// The notebook mutations themselves are covered by their own module and drive
// real JupyterLab widgets; here only the round's lifetime is under test.
jest.mock('../notebook/mutations', () => ({
  ...jest.requireActual('../notebook/mutations'),
  convertPromptToMarkdown: jest.fn(),
  insertCellsBelow: jest.fn(() => ['inserted-1']),
  revertRound: jest.fn()
}));

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
  const cells = [
    { id: 'p1', type: 'code', source: '#% do a thing', metadata: {} }
  ];
  return {
    id: 'panel-1',
    node: { classList: { add: () => undefined, remove: () => undefined } },
    context: { path: 'nb.ipynb' },
    content: {
      activeCellIndex: 0,
      mode: 'command',
      widgets: [{ node: {} }],
      extendContiguousSelectionTo: () => undefined,
      model: {
        cells: { length: cells.length, get: (i: number) => cells[i] },
        sharedModel: { transact: (fn: () => void) => fn() },
        toJSON: () => ({
          nbformat: 4,
          nbformat_minor: 5,
          metadata: { kernelspec: kernelSpec, language_info: { name: 'python' } },
          cells: [
            { id: 'p1', cell_type: 'code', source: '#% do a thing', outputs: [] }
          ]
        })
      }
    },
    sessionContext: { session: null }
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
    sessionContext: undefined,
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
      send: () => (async function* () {
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
      send: () => (async function* () {
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
});
