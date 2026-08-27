import { truncate, estimateTokens } from '../notebook/snapshot';

/**
 * The ladder from docs/09. Order matters: it drops the cheapest signal first,
 * and never touches the prompt cell, its neighbours, or an import.
 */

const opts = (over: Record<string, unknown> = {}) => ({
  includeOutputs: true,
  maxOutputChars: 200,
  budgetTokens: 100,
  cellWindow: 0,
  promptCellId: 'prompt',
  ...over
});

const code = (id: string, source: string, outputs?: unknown[]) => ({
  id,
  cell_type: 'code',
  source,
  ...(outputs ? { outputs } : {})
});

const filler = (n: number) => `y${n} = ${'9'.repeat(400)}\n`;

describe('truncate', () => {
  it('does nothing when the notebook already fits', () => {
    const cells = [code('a', 'x = 1'), code('prompt', '#% go')];
    expect(truncate(cells, opts())).toEqual([]);
    expect(cells[0].source).toBe('x = 1');
  });

  it('is disabled when no budget is given', () => {
    const cells = [code('a', filler(1)), code('prompt', '#% go')];
    expect(truncate(cells, opts({ budgetTokens: 0 }))).toEqual([]);
    expect(cells[0].source).toBe(filler(1));
  });

  it('drops ordinary outputs before it touches any source', () => {
    const cells = [
      code('a', 'x = 1', [
        { output_type: 'stream', name: 'stdout', text: 'z'.repeat(2000) }
      ]),
      code('b', 'y = 2'),
      code('c', 'z = 3'),
      code('d', 'w = 4'),
      code('prompt', '#% go')
    ];
    const dropped = truncate(cells, opts());
    expect(dropped[0]).toContain('outputs');
    expect(cells[0].outputs).toBeUndefined();
    expect(cells[0].source).toBe('x = 1'); // source survives
  });

  it('keeps error outputs when it drops the rest', () => {
    const cells = [
      code('a', 'x = 1', [
        { output_type: 'stream', name: 'stdout', text: 'z'.repeat(3000) },
        { output_type: 'error', ename: 'ValueError', evalue: 'bad' }
      ]),
      code('b', 'y = 2'),
      code('c', 'z = 3'),
      code('d', 'w = 4'),
      code('prompt', '#% go')
    ];
    truncate(cells, opts());
    expect(cells[0].outputs).toHaveLength(1);
    expect((cells[0].outputs as any[])[0].output_type).toBe('error');
  });

  it('replaces a distant code body with its signatures', () => {
    const cells = [
      code('far', `def one(a, b):\n    ${filler(1)}    ${filler(2)}`),
      code('b', 'y = 2'),
      code('c', 'z = 3'),
      code('d', 'w = 4'),
      code('prompt', '#% go')
    ];
    const dropped = truncate(cells, opts());
    expect(dropped.some(d => d.includes('signatures kept'))).toBe(true);
    expect(cells[0].source).toContain('def one(a, b): …');
    expect(cells[0].source).not.toContain('999');
  });

  it('never touches the prompt cell or its immediate neighbours', () => {
    const neighbour = `neighbour = ${'8'.repeat(3000)}`;
    const cells = [
      code('far', filler(1)),
      code('b', 'b = 1'),
      code('c', 'c = 1'),
      code('near', neighbour),
      code('prompt', '#% go')
    ];
    truncate(cells, opts());
    expect(cells[4].source).toBe('#% go');
    expect(cells[3].source).toBe(neighbour);
  });

  it('never touches an import-bearing cell, however far away', () => {
    const imports = `import numpy as np\n${filler(1)}`;
    const cells = [
      code('imports', imports),
      code('b', filler(2)),
      code('c', 'c = 1'),
      code('d', 'd = 1'),
      code('prompt', '#% go')
    ];
    truncate(cells, opts());
    expect(cells[0].source).toBe(imports);
  });

  it('omits distant markdown before it summarises code', () => {
    const cells = [
      { id: 'md', cell_type: 'markdown', source: 'prose '.repeat(600) },
      code('b', 'b = 1'),
      code('c', 'c = 1'),
      code('d', 'd = 1'),
      code('prompt', '#% go')
    ];
    const dropped = truncate(cells, opts());
    expect(dropped.some(d => d.includes('markdown'))).toBe(true);
    expect(cells[0].source).toContain('omitted');
  });
});

describe('estimateTokens', () => {
  it('is within about 10% of chars/4 either way', () => {
    const text = 'x'.repeat(360);
    expect(estimateTokens(text, 'code')).toBe(100);
    expect(estimateTokens(text, 'prose')).toBeLessThan(100);
  });
});
