import { expect, galata, test } from '@jupyterlab/galata';

/**
 * End-to-end cover for the one behaviour nothing else can prove: a prompt cell
 * runs without a kernel, and what comes back lands in the notebook.
 *
 * The agent is the `command` adapter pointed at a script that prints fixed tool
 * tags. That makes the whole pipeline deterministic -- no model, no network,
 * no flakiness -- while still exercising the real parser, the real round, and
 * the real mutations.
 */

// Must match PLUGIN_ID in src/plugins/settings.ts.
const PLUGIN = 'jupyter-vybe:settings';

const SCRIPTED_AGENT = [
  'python3',
  '-c',
  [
    'import sys',
    'print("Here is the helper.")',
    'print(\'<vybe:add_cell type="code">\')',
    'print("def double(x):")',
    'print("    return x * 2")',
    'print("</vybe:add_cell>")'
  ].join('; ')
].join(' ');

test.use({
  autoGoto: false,
  mockSettings: {
    ...galata.DEFAULT_SETTINGS,
    [PLUGIN]: {
      agent: { id: 'command', command: SCRIPTED_AGENT },
      // Off: an e2e run should not write to the developer's home directory.
      history: { persist: false }
    }
  }
});

test('a prompt cell runs with no kernel and inserts what the agent returned', async ({
  page
}) => {
  await page.goto();
  await page.notebook.createNew();
  // Deliberately no kernel: the whole point is that this path never needs one.
  await page.notebook.setCell(0, 'code', '#% add a doubling function');

  await page.notebook.runCell(0);

  // The prompt cell settles into markdown holding the prompt as a blockquote.
  await expect
    .poll(async () => await page.notebook.getCellCount(), { timeout: 30000 })
    .toBeGreaterThan(1);
  const inserted = await page.notebook.getCellTextInput(1);
  expect(inserted).toContain('def double(x):');
  expect(inserted).toContain('return x * 2');
});

test('a plain code cell is untouched by the executor', async ({ page }) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', 'x = 1  # not a prompt');

  await page.notebook.runCell(0);

  // No insertion, no conversion: the cell is exactly as it was.
  expect(await page.notebook.getCellCount()).toBe(1);
  expect(await page.notebook.getCellTextInput(0)).toBe('x = 1  # not a prompt');
});
