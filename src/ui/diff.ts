import { diffLines as jsdiffLines } from 'diff';

/**
 * Line-level diff, coalesced into hunks with context (docs/03).
 *
 * Hand-rolled rather than @codemirror/merge: its chrome (gutters, collapse
 * widgets, per-chunk revert arrows) fights the GitHub-style full-line
 * backgrounds and the per-cell accept/reject model (decision D7).
 */

export type LineKind = 'context' | 'add' | 'del';

export interface IDiffLine {
  kind: LineKind;
  text: string;
}

export interface IHunk {
  lines: IDiffLine[];
}

const CONTEXT = 3;

export function diffLines(before: string, after: string): IHunk[] {
  const parts = jsdiffLines(before, after);
  const lines: IDiffLine[] = [];
  for (const part of parts) {
    const kind: LineKind = part.added
      ? 'add'
      : part.removed
        ? 'del'
        : 'context';
    const text = part.value.endsWith('\n')
      ? part.value.slice(0, -1)
      : part.value;
    for (const line of text.split('\n')) {
      lines.push({ kind, text: line });
    }
  }
  return coalesce(lines);
}

/** Group changed lines with CONTEXT lines either side; drop the rest. */
function coalesce(lines: IDiffLine[]): IHunk[] {
  const interesting = lines
    .map((l, i) => (l.kind === 'context' ? -1 : i))
    .filter(i => i !== -1);
  if (!interesting.length) {
    return [];
  }
  const hunks: IHunk[] = [];
  let start = Math.max(0, interesting[0] - CONTEXT);
  let end = Math.min(lines.length - 1, interesting[0] + CONTEXT);
  for (const i of interesting.slice(1)) {
    if (i - CONTEXT <= end + 1) {
      end = Math.min(lines.length - 1, i + CONTEXT);
    } else {
      hunks.push({ lines: lines.slice(start, end + 1) });
      start = Math.max(0, i - CONTEXT);
      end = Math.min(lines.length - 1, i + CONTEXT);
    }
  }
  hunks.push({ lines: lines.slice(start, end + 1) });
  return hunks;
}

/** Counts for the status bubble and history entries. */
export function diffStat(hunks: IHunk[]): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const h of hunks) {
    for (const l of h.lines) {
      if (l.kind === 'add') {
        added++;
      } else if (l.kind === 'del') {
        removed++;
      }
    }
  }
  return { added, removed };
}

/**
 * The `Enter` merge: accepted lines live, replaced lines retained beneath as
 * comments. The cell stays valid syntax at every moment, so an interrupted
 * review never leaves a notebook that will not parse (docs/03, decision D6).
 */
export function mergedSource(
  hunks: IHunk[],
  after: string,
  comment: string
): string {
  const removed: string[] = [];
  for (const h of hunks) {
    for (const l of h.lines) {
      if (l.kind === 'del') {
        removed.push(l.text);
      }
    }
  }
  if (!removed.length || comment === '') {
    return after;
  }
  const block = [
    `${comment} -- replaced --`,
    ...removed.map(l => `${comment} ${l}`)
  ].join('\n');
  return `${after}\n${block}\n`;
}

/** Comment syntax from the kernel's language_info (docs/03). */
export function commentFor(language: string, cellType: string): string {
  if (cellType === 'markdown') {
    return '<!--';
  }
  const lang = language.toLowerCase();
  if (
    [
      'javascript',
      'typescript',
      'c',
      'cpp',
      'c++',
      'java',
      'rust',
      'go',
      'scala'
    ].includes(lang)
  ) {
    return '//';
  }
  if (['matlab', 'octave'].includes(lang)) {
    return '%';
  }
  return '#';
}
