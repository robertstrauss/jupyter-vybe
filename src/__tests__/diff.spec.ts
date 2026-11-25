import { diffLines, diffStat, mergedSource, normaliseSource } from '../ui/diff';
import { toMarkdownSource } from '../notebook/promptCell';

const kinds = (before: string, after: string) =>
  diffLines(before, after).flatMap(h => h.lines.map(l => l.kind));

describe('diffLines', () => {
  it('marks only the line that changed', () => {
    const before = 'a = 1\nb = 2\nc = 3\n';
    const after = 'a = 1\nb = 22\nc = 3\n';
    const hunks = diffLines(before, after);
    expect(hunks).toHaveLength(1);
    expect(kinds(before, after)).toEqual(['context', 'del', 'add', 'context']);
    expect(diffStat(hunks)).toEqual({ added: 1, removed: 1 });
  });

  it('is empty when only the trailing newline differs', () => {
    expect(diffLines('a = 1\nb = 2\n', 'a = 1\nb = 2')).toEqual([]);
  });

  it('ignores a change of line endings', () => {
    expect(diffLines('a = 1\nb = 2\n', 'a = 1\r\nb = 2\r\n')).toEqual([]);
  });

  it('does not treat a trailing-newline mismatch as a changed last line', () => {
    // The agent's body arrives without its trailing newline; the cell it
    // replaces keeps one. Only the real edit should show (docs/14 N1).
    const before = 'x = 1\ny = 2\n';
    const after = 'x = 1\ny = 3';
    expect(kinds(before, after)).toEqual(['context', 'del', 'add']);
  });

  it('keeps distant changes in separate hunks with context between', () => {
    const before = Array.from({ length: 20 }, (_, i) => `l${i}`).join('\n');
    const after = before.replace('l1\n', 'CHANGED\n').replace('l18', 'ALSO');
    const hunks = diffLines(before, after);
    expect(hunks.length).toBe(2);
    for (const h of hunks) {
      expect(h.lines.some(l => l.kind === 'context')).toBe(true);
    }
  });

  it('reports every line changed only when every line really changed', () => {
    // The whole-cell reindentation that N1 was actually caused by: if it ever
    // reaches the differ again, this is the shape it takes.
    const before = 'def f():\n    return 1\n';
    const after = '    def f():\n        return 1';
    expect(kinds(before, after)).toEqual(['del', 'del', 'add', 'add']);
  });
});

describe('normaliseSource', () => {
  it('terminates the final line without adding a blank one', () => {
    expect(normaliseSource('a')).toBe('a\n');
    expect(normaliseSource('a\n')).toBe('a\n');
    expect(normaliseSource('a\n\n')).toBe('a\n\n');
    expect(normaliseSource('')).toBe('');
  });
});

describe('mergedSource', () => {
  it('retains removed lines as comments beneath the new source', () => {
    const before = 'a = 1\nb = 2\n';
    const after = 'a = 1\nb = 3\n';
    const merged = mergedSource(diffLines(before, after), after, '#');
    expect(merged).toContain('b = 3');
    expect(merged).toContain('# -- replaced --');
    expect(merged).toContain('# b = 2');
  });

  it('returns the new source unchanged when nothing was removed', () => {
    const hunks = diffLines('a = 1\n', 'a = 1\nb = 2\n');
    expect(mergedSource(hunks, 'a = 1\nb = 2\n', '#')).toBe('a = 1\nb = 2\n');
  });
});

describe('the settled prompt cell', () => {
  const toMarkdown = (prose: string) =>
    toMarkdownSource('do a thing', prose, '>');

  it('keeps prose that says something', () => {
    expect(toMarkdown('Reused the existing import.')).toContain(
      'Reused the existing import.'
    );
  });

  it('drops a stray fence the model left outside its tags', () => {
    // What the user actually saw: a settled cell whose whole content was
    // "```python" and a protocol complaint.
    expect(toMarkdown('```python\n\n')).toBe('> do a thing');
  });

  it('adds no trailing newline, even after a prompt typed with one', () => {
    expect(toMarkdownSource('do a thing\n\n', '', '>')).toBe('> do a thing');
    expect(toMarkdown('Done.\n')).toBe('> do a thing\n\nDone.');
  });

  it('keeps the code inside a fence but not the delimiters', () => {
    const out = toMarkdown('```python\nx = 1\n```');
    expect(out).toContain('x = 1');
    expect(out).not.toContain('```');
  });
});
