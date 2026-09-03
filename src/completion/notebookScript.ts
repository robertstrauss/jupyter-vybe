import type { NotebookPanel } from '@jupyterlab/notebook';

/**
 * The notebook as a Python script, the way nbconvert writes one.
 *
 * The completion prompt used to be fill-in-the-middle, with a different token
 * vocabulary per model family and a per-adapter raw-completion route to send
 * it down. It predicted the current cell from the current cell, which is the
 * wrong context for a notebook: what a cell should say next is mostly decided
 * by what the cells above it defined.
 *
 * A script is the format the model has read most of, and `# In[n]:` markers
 * are part of that format rather than a convention we invented -- so the
 * notebook arrives as something the model already knows how to continue, and
 * the answer needs no special decoding.
 */

export interface INotebookScript {
  /** Every cell above the one being written, plus that cell's text so far. */
  script: string;
  /** 1-based, for the instruction that names which cell to continue. */
  cellNumber: number;
}

/** Comment out a markdown cell rather than dropping it: it is often the spec. */
function asComment(source: string): string {
  return source
    .split('\n')
    .map(line => (line ? `# ${line}` : '#'))
    .join('\n');
}

export function notebookAsScript(
  panel: NotebookPanel,
  activeCellId: string,
  textSoFar: string
): INotebookScript | null {
  const cells = panel.content.model?.cells;
  if (!cells) {
    return null;
  }
  const parts: string[] = [];
  let cellNumber = 0;
  for (let i = 0; i < cells.length; i++) {
    const cell = cells.get(i);
    const current = cell.id === activeCellId;
    // The cell being written is sent as far as the cursor and no further:
    // text after the cursor is what the model is being asked to predict.
    const source = current ? textSoFar : cell.sharedModel.getSource();
    if (current) {
      cellNumber = i + 1;
    }
    if (cell.type === 'markdown') {
      parts.push(`# In[${i + 1}]:\n${asComment(source)}`);
    } else if (cell.type === 'code') {
      parts.push(`# In[${i + 1}]:\n${source}`);
    }
    if (current) {
      break; // nothing below the cursor is context for continuing it
    }
  }
  if (!cellNumber) {
    return null;
  }
  return { script: parts.join('\n\n\n'), cellNumber };
}

/**
 * Whether a suggestion is still mid-line, so the next one continues it rather
 * than starting a line of its own.
 *
 * The second press of the trigger means "and then what?", and the answer is
 * almost always a new line -- the server stops on a complete statement. When
 * it did not, because the line-budget cap cut a runaway short, gluing the next
 * answer onto a fresh line would break the statement in half. So the join is
 * decided by whether what is there stands on its own.
 *
 * A colon counts as finished for this purpose even though the block is not:
 * what a `for` header needs next is a body, on its own line.
 */
export function continuesTheLine(text: string): boolean {
  const last = text.split('\n').pop() ?? '';
  if (!last.trim()) {
    return false;
  }
  if (/[:\\]\s*$/.test(last)) {
    return false; // a block header wants a body beneath it, not more of itself
  }
  // An operator, a comma, or an unclosed bracket: the statement is unfinished.
  return /[([{,+\-*/%=<>&|^]\s*$/.test(last) || openBrackets(text) > 0;
}

function openBrackets(text: string): number {
  let depth = 0;
  for (const ch of text) {
    if ('([{'.includes(ch)) {
      depth++;
    } else if (')]}'.includes(ch)) {
      depth = Math.max(0, depth - 1);
    }
  }
  return depth;
}
