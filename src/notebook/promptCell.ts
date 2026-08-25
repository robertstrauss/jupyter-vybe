import type { Cell } from '@jupyterlab/cells';
import type { ICellModel } from '@jupyterlab/cells';

/**
 * A cell is a prompt cell iff it is a code cell whose source begins with the
 * trigger prefix (docs/02).
 *
 * Leading blank lines are tolerated; leading spaces are not. The prefix is
 * matched against the first non-newline character exactly, with no trimming, so
 * a line of code that merely contains the prefix later is unaffected.
 */
export function isPromptSource(source: string, prefix: string): boolean {
  if (!prefix) {
    return false;
  }
  let i = 0;
  while (i < source.length && (source[i] === '\n' || source[i] === '\r')) {
    i++;
  }
  return source.startsWith(prefix, i);
}

export function isPromptCell(cell: Cell | ICellModel, prefix: string): boolean {
  const model = 'model' in cell ? cell.model : cell;
  if (model.type !== 'code') {
    return false;
  }
  return isPromptSource(model.sharedModel.getSource(), prefix);
}

/** Strip the prefix and one following space, preserving everything else. */
export function promptText(source: string, prefix: string): string {
  let i = 0;
  while (i < source.length && (source[i] === '\n' || source[i] === '\r')) {
    i++;
  }
  let rest = source.slice(i + prefix.length);
  if (rest.startsWith(' ')) {
    rest = rest.slice(1);
  }
  return rest;
}

/**
 * A settled prompt cell: a markdown cell whose source is the original prompt as
 * a blockquote. These are the user's prompt history and are never valid targets
 * for a proposed edit (docs/03).
 */
export function isSettledPromptCell(
  model: ICellModel,
  prefix: string
): boolean {
  if (model.type !== 'markdown') {
    return false;
  }
  return isPromptSource(model.sharedModel.getSource(), prefix);
}

/** Render a prompt + agent prose as the markdown a settled prompt cell holds. */
export function toMarkdownSource(
  prompt: string,
  prose: string,
  prefix: string
): string {
  const quoted = prompt
    .split('\n')
    .map(line => (line.length ? `${prefix} ${line}` : prefix))
    .join('\n');
  const tail = prose.trim();
  return tail ? `${quoted}\n\n${tail}\n` : `${quoted}\n`;
}

/** Recover the original prompt source from a settled markdown cell. */
export function fromMarkdownSource(source: string, prefix: string): string {
  const lines = source.split('\n');
  const out: string[] = [];
  for (const line of lines) {
    if (!line.startsWith(prefix)) {
      break;
    }
    out.push(line.slice(prefix.length).replace(/^ /, ''));
  }
  return `${prefix} ${out.join('\n')}`.replace(`${prefix} `, `${prefix} `);
}
