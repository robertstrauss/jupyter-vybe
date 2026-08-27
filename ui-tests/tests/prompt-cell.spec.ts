import { expect, galata, test } from '@jupyterlab/galata';
import * as path from 'path';

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

/**
 * The agent under test is the real CommandAdapter running a real subprocess;
 * only the model is replaced. See fixtures/scripted-agent.py -- and note it is
 * a file, not a `python3 -c` one-liner, because the command setting is split
 * with shlex and a one-liner comes apart at the spaces.
 */
const SCRIPTED_AGENT = `python3 ${path.join(
  __dirname,
  '..',
  'fixtures',
  'scripted-agent.py'
)}`;

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


/**
 * Read the notebook *model* rather than the rendered DOM. Cell text scraped
 * from a windowed notebook comes back empty for anything off-screen, which
 * makes a passing assertion a matter of scroll position.
 */
async function cells(page: any): Promise<{ type: string; source: string }[]> {
  return await page.evaluate(() => {
    const app = (window as any).jupyterapp;
    const panel: any = app.shell.currentWidget;
    const model = panel?.content?.model;
    if (!model) {
      return [];
    }
    const out: { type: string; source: string }[] = [];
    for (let i = 0; i < model.cells.length; i++) {
      const cell = model.cells.get(i);
      out.push({ type: cell.type, source: cell.sharedModel.getSource() });
    }
    return out;
  });
}

test('a prompt cell runs with no kernel and inserts what the agent returned', async ({
  page
}) => {
  await page.goto();
  await page.notebook.createNew();
  // Deliberately no kernel: the whole point is that this path never needs one.
  await page.notebook.setCell(0, 'code', '> add a doubling function');

  await page.notebook.runCell(0);

  // The generated cell lands directly below the prompt...
  await expect
    .poll(async () => (await cells(page)).length, { timeout: 60000 })
    .toBeGreaterThan(1);
  const after = await cells(page);
  expect(after[1].source).toContain('def double(x):');
  expect(after[1].source).toContain('return x * 2');
  // ...and the prompt cell settles into markdown holding the prompt.
  expect(after[0].type).toBe('markdown');
  expect(after[0].source).toContain('add a doubling function');
});

test('a plain code cell is untouched by the executor', async ({ page }) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', 'x = 1  # not a prompt');

  await page.notebook.runCell(0);

  // Running the last cell appends an empty one -- that is core Jupyter, not
  // us. What matters is that nothing was generated and nothing was rewritten.
  const after = await cells(page);
  expect(after[0].source).toBe('x = 1  # not a prompt');
  expect(after[0].type).toBe('code');
  expect(after.slice(1).every(c => c.source === '')).toBe(true);
});

test('a proposed edit is reviewed, not applied, until accepted', async ({
  page
}) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', 'value = 1');
  await page.notebook.addCell('code', '> double the value above');

  await page.notebook.runCell(1);

  // The overlay stands in for the editor while the verdict is pending, and
  // the cell itself is untouched until then.
  const overlay = page.locator('.jv-diff-overlay');
  await expect(overlay).toBeVisible({ timeout: 60000 });
  expect((await cells(page))[0].source).toBe('value = 1');

  // No click first: review is a keyboard flow, so beginning a review has to
  // leave the keyboard able to answer it.
  await page.keyboard.press('y');
  await expect(overlay).toBeHidden();
  await expect
    .poll(async () => (await cells(page))[0].source)
    .toContain('value = 2');
});

test('rejecting leaves the notebook exactly as it was', async ({ page }) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', 'value = 1');
  await page.notebook.addCell('code', '> double the value above');

  await page.notebook.runCell(1);
  await expect(page.locator('.jv-diff-overlay')).toBeVisible({
    timeout: 60000
  });
  await page.keyboard.press('n');

  await expect(page.locator('.jv-diff-overlay')).toBeHidden();
  expect((await cells(page))[0].source).toBe('value = 1');
});
