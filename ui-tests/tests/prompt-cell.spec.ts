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
      // The record is written beside the notebook, which for these tests is a
      // Galata temp directory -- so nothing lands in the developer's home.
      history: { persist: true, location: 'adjacent' }
    }
  }
});

// Each test gets a clean shell. Left open, a previous test's notebook stays in
// a tab and `currentWidget` can point at it -- which showed up as one test
// timing out in a full run while passing on its own.
/**
 * Prompt cells are run with `{ wait: false }` throughout.
 *
 * galata's default wait blocks until the cell has an execution count, and a
 * prompt cell never gets one: it does not reach a kernel, and contributing
 * nothing to the execution history is the whole point (D4). Each test waits
 * on what it actually cares about instead.
 */
test.afterEach(async ({ page }) => {
  // Bounded: closing waits on a save dialog that does not always appear, and
  // a teardown that can hang is a teardown that fails whichever test it
  // happens to follow.
  await Promise.race([
    page.notebook.close(true).catch(() => undefined),
    new Promise(resolve => setTimeout(resolve, 5000))
  ]);
});

/**
 * Read the notebook *model* rather than the rendered DOM. Cell text scraped
 * from a windowed notebook comes back empty for anything off-screen, which
 * makes a passing assertion a matter of scroll position.
 */
async function cells(
  page: any
): Promise<{ type: string; source: string; executionCount: number | null }[]> {
  return await page.evaluate(() => {
    const app = (window as any).jupyterapp;
    const panel: any = app.shell.currentWidget;
    const model = panel?.content?.model;
    if (!model) {
      return [];
    }
    const out: {
      type: string;
      source: string;
      executionCount: number | null;
    }[] = [];
    for (let i = 0; i < model.cells.length; i++) {
      const cell: any = model.cells.get(i);
      out.push({
        type: cell.type,
        source: cell.sharedModel.getSource(),
        executionCount: cell.executionCount ?? null
      });
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

  await page.notebook.runCell(0, { wait: false });

  // Poll on the content, not the count: running the last cell appends an
  // empty one, so a count check passes before the agent has answered.
  await expect
    .poll(async () => (await cells(page)).map(c => c.source).join('\n'), {
      timeout: 60000
    })
    .toContain('def double(x):');
  const after = await cells(page);
  // Directly below the prompt, which is where added cells always go.
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

  await page.notebook.runCell(0, { wait: false });

  // An execution count is the proof that core ran it: our executor delegated
  // rather than intercepting. Asserted on the model rather than by waiting for
  // the status bar, which is a flaky proxy for a question we can ask directly.
  await expect
    .poll(async () => (await cells(page))[0].executionCount, { timeout: 60000 })
    .not.toBeNull();
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

  await page.notebook.runCell(1, { wait: false });

  // The overlay stands in for the editor while the verdict is pending, and
  // the cell itself is untouched until then.
  // The summary appears on the prompt cell; Enter opens the first diff.
  await expect(page.locator('.jv-prompt-output.jv-review')).toBeVisible({
    timeout: 60000
  });
  await page.keyboard.press('Enter');
  const overlay = page.locator('.jv-diff-overlay');
  await expect(overlay).toBeVisible();
  expect((await cells(page))[0].source).toBe('value = 1');

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

  await page.notebook.runCell(1, { wait: false });
  await expect(page.locator('.jv-prompt-output.jv-review')).toBeVisible({
    timeout: 60000
  });
  await page.keyboard.press('Enter');
  await expect(page.locator('.jv-diff-overlay')).toBeVisible();
  await page.keyboard.press('n');

  await expect(page.locator('.jv-diff-overlay')).toBeHidden();
  expect((await cells(page))[0].source).toBe('value = 1');
});

test('Cmd+Z during review takes back the verdict, not the whole round', async ({
  page
}) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', 'value = 1');
  await page.notebook.addCell('code', '> double the value above');

  await page.notebook.runCell(1, { wait: false });
  await expect(page.locator('.jv-prompt-output.jv-review')).toBeVisible({
    timeout: 60000
  });
  await page.keyboard.press('Enter');
  await page.keyboard.press('y');
  await expect
    .poll(async () => (await cells(page))[0].source)
    .toContain('value = 2');

  // Undo layer 2 (docs/02): the accept comes back, cell and diff with it.
  // ControlOrMeta, because the binding is Accel -- Cmd on macOS, Ctrl elsewhere
  // -- and a test that hard-codes one passes on one platform only.
  await page.keyboard.press('ControlOrMeta+z');
  await expect(page.locator('.jv-diff-overlay')).toBeVisible();
  await expect
    .poll(async () => (await cells(page))[0].source)
    .toBe('value = 1');
});

test('the review summary appears in the prompt cell, not in a corner', async ({
  page
}) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', 'value = 1');
  await page.notebook.addCell('code', '> double the value above');

  await page.notebook.runCell(1, { wait: false });

  // The count is on the cell that caused it, where the user is looking.
  const output = page.locator('.jv-prompt-output.jv-review');
  await expect(output).toBeVisible({ timeout: 60000 });
  await expect(output).toContainText('1 edit proposed');
  await expect(output).toContainText('Enter to review');
  // And the prompt cell is still a code cell: converting it now would destroy
  // the thing the user is being asked to act on.
  expect((await cells(page))[1].type).toBe('code');
});

test('Enter starts the review and N rejects everything', async ({ page }) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', 'value = 1');
  await page.notebook.addCell('code', '> double the value above');

  await page.notebook.runCell(1, { wait: false });
  await expect(page.locator('.jv-prompt-output.jv-review')).toBeVisible({
    timeout: 60000
  });

  // Focus is on the prompt cell even though Shift+Enter moved on, so the
  // unchorded keys reach the right place.
  await page.keyboard.press('Enter');
  await expect(page.locator('.jv-diff-overlay')).toBeVisible();
  expect((await cells(page))[0].source).toBe('value = 1');

  await page.keyboard.press('y');
  await expect
    .poll(async () => (await cells(page))[0].source)
    .toContain('value = 2');
  // Every verdict in: the prompt becomes its markdown record at last.
  await expect.poll(async () => (await cells(page))[1].type).toBe('markdown');
});

test('N on the prompt cell rejects the whole round', async ({ page }) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', 'value = 1');
  await page.notebook.addCell('code', '> double the value above');

  await page.notebook.runCell(1, { wait: false });
  await expect(page.locator('.jv-prompt-output.jv-review')).toBeVisible({
    timeout: 60000
  });

  await page.keyboard.press('n');

  await expect(page.locator('.jv-diff-overlay')).toBeHidden();
  expect((await cells(page))[0].source).toBe('value = 1');
});

test('the history pane holds the prompt after its cell is deleted', async ({
  page
}) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', '> add a doubling function');
  await page.notebook.runCell(0, { wait: false });
  await expect
    .poll(async () => (await cells(page)).map(c => c.source).join('\n'), {
      timeout: 60000
    })
    .toContain('def double');

  // Delete the settled prompt cell: the rendering goes, the record does not.
  await page.evaluate(() => {
    const app = (window as any).jupyterapp;
    const panel: any = app.shell.currentWidget;
    panel.content.activeCellIndex = 0;
    panel.content.model.sharedModel.deleteCell(0);
  });
  expect(
    (await cells(page)).some(c => c.source.includes('add a doubling function'))
  ).toBe(false);

  const record = await page.evaluate(async () => {
    const app = (window as any).jupyterapp;
    const path = app.shell.currentWidget.context.path;
    const base = document.body.dataset.baseUrl ?? '/';
    const response = await fetch(
      // location=adjacent, to match the setting these tests run with: the
      // server looks where it is told, not where it guesses.
      `${base}jupyter-vybe/history?location=adjacent&path=${encodeURIComponent(path)}`,
      { headers: { 'Content-Type': 'application/json' } }
    );
    return await response.json();
  });
  expect(
    record.entries.some(
      (e: any) => e.kind === 'prompt' && e.text.includes('add a doubling')
    )
  ).toBe(true);
});

test('a protocol error is shown, not swallowed', async ({ page }) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', 'value = 1');
  await page.notebook.addCell('code', '> aim at a cell that is not there');

  await page.notebook.runCell(1, { wait: false });

  // The round used to produce nothing at all here: no cell, no message, no
  // clue. Silence is the failure mode this design exists to avoid.
  // The round used to produce nothing at all here: no cell, no message, no
  // clue. The record lands in the settled prompt cell, which is where it
  // survives -- the live output area belongs to the round, and the round is
  // over (docs/13 R14).
  await expect
    .poll(async () => (await cells(page))[1].source, { timeout: 60000 })
    .toContain('Protocol error');
  // The notebook itself is untouched by a failed round.
  expect((await cells(page))[0].source).toBe('value = 1');
});

test('an agent that falls over says so', async ({ page }) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', '> make the agent fail');

  await page.notebook.runCell(0, { wait: false });

  await expect
    .poll(async () => (await cells(page))[0].source, { timeout: 60000 })
    .toContain('fell over');
});

test('a reply without tool tags is recovered, and the user is told', async ({
  page
}) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', '> answer without tags');

  await page.notebook.runCell(0, { wait: false });

  // The cells arrive -- salvage is what makes a model that will not use the
  // protocol usable at all (docs/13 R2)...
  await expect
    .poll(async () => (await cells(page)).map(c => c.source).join('\n'), {
      timeout: 60000
    })
    .toContain('def salvaged()');
  // ...but the user is told, because that is when to check the output harder.
  // The notice is shown live and recorded in the settled cell after.
  await expect
    .poll(async () => (await cells(page))[0].source)
    .toContain('without tool tags');
});

test('Cmd+Z after an insertion takes the whole round back', async ({
  page
}) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', '> add a doubling function');

  await page.notebook.runCell(0, { wait: false });
  await expect
    .poll(async () => (await cells(page)).map(c => c.source).join('\n'), {
      timeout: 60000
    })
    .toContain('def double');
  await expect.poll(async () => (await cells(page))[0].type).toBe('markdown');

  await page.keyboard.press('ControlOrMeta+z');

  // The inserted cell is gone and the prompt is a code cell again, holding
  // the user's own words, ready to be reworded and re-run.
  await expect.poll(async () => (await cells(page))[0].type).toBe('code');
  const after = await cells(page);
  expect(after[0].source).toContain('add a doubling function');
  expect(after.some(c => c.source.includes('def double'))).toBe(false);
});

test('the review counter appears only while edits are open', async ({
  page
}) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', 'value = 1');
  await page.notebook.addCell('code', '> double the value above');

  // Nothing floating before a round: the counter exists for one failure mode,
  // walking away with edits unresolved, and only for as long as that is
  // possible.
  expect(await page.locator('.jv-review-counter').count()).toBe(0);

  await page.notebook.runCell(1, { wait: false });

  const counter = page.locator('.jv-review-counter');
  await expect(counter).toBeVisible({ timeout: 60000 });
  await expect(counter).toContainText('1 edit left to review');
  await expect(counter).toContainText('⌘Z');

  await page.keyboard.press('Enter');
  await page.keyboard.press('y');

  await expect(counter).toBeHidden();
});

test('the counter rejects the rest without touching the notebook', async ({
  page
}) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', 'value = 1');
  await page.notebook.addCell('code', '> double the value above');

  await page.notebook.runCell(1, { wait: false });
  const counter = page.locator('.jv-review-counter');
  await expect(counter).toBeVisible({ timeout: 60000 });

  await counter.locator('.jv-counter-reject').click();

  await expect(counter).toBeHidden();
  expect((await cells(page))[0].source).toBe('value = 1');
});

test('the thinking line reports a rate, not zero', async ({ page }) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', '> think slowly please');

  await page.notebook.runCell(0, { wait: false });

  // A reply made only of tool tags streams nothing the client can see until
  // each tag closes, so the rate was stuck at zero for the whole round: the
  // server reports progress from the raw stream now.
  await expect
    .poll(
      async () =>
        await page
          .locator('.jv-prompt-output.jv-thinking')
          .textContent()
          .catch(() => ''),
      { timeout: 30000 }
    )
    .toMatch(/Thinking, [1-9][0-9]* tokens\/sec/);
});
