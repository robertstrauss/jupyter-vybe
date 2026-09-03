import {
  continuesTheLine,
  notebookAsScript
} from '../completion/notebookScript';

/**
 * The completion prompt is the notebook as a script. What matters is where it
 * stops: everything below the cursor is what the model is being asked for, so
 * sending any of it turns the question into a quiz it can copy the answer to.
 */

type Cell = { id: string; type: string; source: string };

const panel = (cells: Cell[]): any => ({
  content: {
    model: {
      cells: {
        length: cells.length,
        get: (i: number) => ({
          id: cells[i].id,
          type: cells[i].type,
          sharedModel: { getSource: () => cells[i].source }
        })
      }
    }
  }
});

const notebook = [
  { id: 'a', type: 'code', source: 'import pandas as pd' },
  { id: 'b', type: 'markdown', source: 'Load the data' },
  { id: 'c', type: 'code', source: 'df = ' },
  { id: 'd', type: 'code', source: 'df.describe()' }
];

describe('the notebook as a script', () => {
  it('stops at the cursor, sending nothing from below it', () => {
    const result = notebookAsScript(panel(notebook), 'c', 'df = ');

    expect(result?.cellNumber).toBe(3);
    expect(result?.script).toContain('import pandas as pd');
    // The cell below holds a plausible next line. Sending it would be
    // handing the model the answer to the question being asked.
    expect(result?.script).not.toContain('df.describe()');
  });

  it('sends the current cell only as far as the cursor', () => {
    const result = notebookAsScript(panel(notebook), 'c', 'df = pd.read');

    expect(result?.script.endsWith('df = pd.read')).toBe(true);
  });

  it('keeps markdown as comments, because it is often the specification', () => {
    const result = notebookAsScript(panel(notebook), 'c', 'df = ');

    expect(result?.script).toContain('# Load the data');
  });

  it('marks the cells the way nbconvert does', () => {
    const result = notebookAsScript(panel(notebook), 'c', 'df = ');

    expect(result?.script).toContain('# In[1]:');
    expect(result?.script).toContain('# In[3]:');
  });

  it('declines when the cell is not in this notebook', () => {
    // The user switched notebooks while the request was being built.
    expect(notebookAsScript(panel(notebook), 'gone', 'x')).toBeNull();
  });
});

/**
 * The reported bug: pressing the trigger again produced a second suggestion
 * glued onto the end of the first line. Code does not begin with a newline,
 * and nothing supplied one -- so the join has to be decided here, from whether
 * what is already suggested stands on its own.
 */
describe('joining one suggestion to the next', () => {
  it('starts a new line after a finished statement', () => {
    expect(continuesTheLine("pd.read_csv('data.csv')")).toBe(false);
  });

  it('starts a new line after a block header, which wants a body', () => {
    expect(continuesTheLine('for row in rows:')).toBe(false);
    expect(continuesTheLine('def f(x):')).toBe(false);
  });

  it('stays on the line while the statement is unfinished', () => {
    // The line-budget cap can cut a runaway short mid-expression; gluing the
    // next answer onto a fresh line would break the statement in half.
    expect(continuesTheLine('m = df.groupby(')).toBe(true);
    expect(continuesTheLine('total = a +')).toBe(true);
    expect(continuesTheLine('items = [1,')).toBe(true);
  });

  it('reads the whole suggestion, not only its last line', () => {
    // The bracket opened two lines up is still open.
    expect(continuesTheLine('m = df.groupby(\n    "month"')).toBe(true);
    expect(continuesTheLine('m = df.groupby(\n    "month"\n).sum()')).toBe(
      false
    );
  });
});
