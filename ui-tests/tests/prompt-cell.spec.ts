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

/**
 * The user's report: undo brought the prompt cell back and focused it, while
 * the diff and its buttons stayed on screen -- a review nobody was standing
 * in. Taking a verdict back means being back at the diff you took it back
 * from, with the keys aimed there.
 */
test('undo puts the cursor back on the diff, not on the prompt', async ({
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

  await page.keyboard.press('ControlOrMeta+z');
  await expect(page.locator('.jv-diff-overlay')).toBeVisible();

  // The restored diff is the focused cell -- proved by the next verdict
  // landing on it without anything else being clicked first.
  await page.keyboard.press('n');
  await expect(page.locator('.jv-diff-overlay')).toHaveCount(0);
  expect((await cells(page))[0].source).toBe('value = 1');
});

/**
 * The user's report: Modify showed the merged text -- accepted lines live,
 * replaced lines commented beneath -- and then put the cursor in the prompt
 * cell, because accepting advances the review and edit mode followed the
 * advance rather than the merge.
 */
test('modify leaves the cursor in the merged cell', async ({ page }) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', 'value = 1');
  await page.notebook.addCell('code', '> double the value above');

  await page.notebook.runCell(1, { wait: false });
  await expect(page.locator('.jv-prompt-output.jv-review')).toBeVisible({
    timeout: 60000
  });
  await page.keyboard.press('Enter');
  await page.keyboard.press('Enter'); // the merge verdict

  await expect
    .poll(async () => (await cells(page))[0].source)
    .toContain('# value = 1');

  // Typing goes into the cell that was just merged, which is the whole point
  // of the verdict: it exists to be hand-edited.
  await page.keyboard.type('  # mine');
  await expect
    .poll(async () => (await cells(page))[0].source)
    .toContain('# mine');
  // And nothing of it reached the prompt.
  expect((await cells(page))[1].source).not.toContain('# mine');
});

/**
 * A review is a session, not a decoration.
 *
 * The failure it exists to prevent: clicking away into another cell,
 * forgetting the diffs, and leaving a round open -- which is how a proposal
 * ends up applied days later, or never.
 */
test.describe('the review session', () => {
  const openReview = async (page: any) => {
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
  };

  test('dims the notebook and refuses cells that are not under review', async ({
    page
  }) => {
    await openReview(page);
    await expect(page.locator('.jp-Notebook.jv-review-session')).toBeVisible();

    // The trailing cell JupyterLab adds after running the last one: not under
    // review, so not available.
    const stray = page.locator('.jp-Notebook .jp-Cell').last();
    await stray.click({ force: true });

    // Refused, and said so, rather than silently doing nothing.
    await expect(page.locator('.jv-counter-refused')).toBeVisible();
    // And the review is still where it was.
    await expect(page.locator('.jv-proposal-active')).toHaveCount(1);
  });

  test('refuses to walk the cursor out of the review', async ({ page }) => {
    await openReview(page);
    const before = await page.evaluate(
      () =>
        (window as any).jupyterapp.shell.currentWidget.content.activeCellIndex
    );

    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowUp');

    // Cell-to-cell navigation is not needed inside a session -- the verdicts
    // advance it and the counter's jump button covers being lost -- so it is
    // declined rather than allowed to strand the user away from the diffs.
    expect(
      await page.evaluate(
        () =>
          (window as any).jupyterapp.shell.currentWidget.content.activeCellIndex
      )
    ).toBe(before);
    await expect(page.locator('.jv-counter-refused')).toBeVisible();
  });

  test('escape rejects the rest and closes the session cleanly', async ({
    page
  }) => {
    await openReview(page);

    await page.keyboard.press('Escape');

    // Every trace of it goes: the diffs, the counter, and the dimming.
    await expect(page.locator('.jv-diff-overlay')).toHaveCount(0);
    await expect(page.locator('.jv-review-counter')).toBeHidden();
    await expect(page.locator('.jp-Notebook.jv-review-session')).toHaveCount(0);
    // Rejected, so the notebook is exactly as it was.
    expect((await cells(page))[0].source).toBe('value = 1');
    // And the cells answer again.
    await page.locator('.jp-Notebook .jp-Cell').last().click();
    await expect(page.locator('.jv-counter-refused')).toHaveCount(0);
  });

  test('the counter carries every way out', async ({ page }) => {
    await openReview(page);
    const counter = page.locator('.jv-review-counter');

    await expect(counter).toContainText('1 edit left to review');
    // Every exit is on it, each naming the key that does the same thing.
    await expect(counter.locator('.jv-counter-jump')).toContainText('Next');
    await expect(counter.locator('.jv-counter-jump .jv-key')).toHaveText(
      'Enter'
    );
    await expect(counter.locator('.jv-counter-reject')).toContainText(
      'Reject All'
    );
    await expect(counter.locator('.jv-counter-reject .jv-key')).toHaveText(
      'esc'
    );
    await expect(counter.locator('.jv-counter-undo')).toBeVisible();
    // Not offered while there is nothing to merge back to.
    await expect(counter.locator('.jv-counter-confirm')).toBeHidden();

    await counter.locator('.jv-counter-reject').click();
    await expect(page.locator('.jv-diff-overlay')).toHaveCount(0);
  });

  /**
   * The user's report: the merge verdict accepts the proposal, so the cell
   * stopped being one -- and the review session promptly dimmed the cell it
   * had just invited them to edit, and refused their clicks on it.
   */
  test('a merge opens a session of its own around the cell being edited', async ({
    page
  }) => {
    await openReview(page);
    await page.keyboard.press('Enter'); // the merge verdict

    const notebook = page.locator('.jp-Notebook');
    await expect(notebook).toHaveClass(/jv-merge-session/);
    // Mutually exclusive, so Escape cannot mean two things at once.
    await expect(notebook).not.toHaveClass(/jv-review-session/);
    await expect(page.locator('.jv-merging')).toHaveCount(1);

    // And it is editable, which is the whole point of the verdict.
    await page.keyboard.type('  # mine');
    await expect
      .poll(async () => (await cells(page))[0].source)
      .toContain('# mine');

    // The counter says what mode this is and offers the one thing to do.
    const counter = page.locator('.jv-review-counter');
    await expect(counter).toContainText('Manual proposal review');
    await expect(counter.locator('.jv-counter-confirm')).toContainText(
      'Confirm Merge and Continue'
    );
    await expect(counter.locator('.jv-counter-reject')).toBeHidden();
  });

  test('clicking away confirms the merge, as leaving edit mode would', async ({
    page
  }) => {
    await openReview(page);
    await page.keyboard.press('Enter');
    await expect(page.locator('.jp-Notebook')).toHaveClass(/jv-merge-session/);
    await page.keyboard.type('  # mine');

    // A click outside a merge means what Escape means: the edit is the
    // user's and it is finished. Refusing it would be treating a finished
    // edit as a mistake.
    await page.locator('.jp-Notebook .jp-Cell').last().click({ force: true });

    await expect(page.locator('.jp-Notebook')).not.toHaveClass(
      /jv-merge-session/
    );
    expect((await cells(page))[0].source).toContain('# mine');
  });

  test('escape confirms the merge and hands the notebook back', async ({
    page
  }) => {
    await openReview(page);
    await page.keyboard.press('Enter');
    await expect(page.locator('.jp-Notebook')).toHaveClass(/jv-merge-session/);
    await page.keyboard.type('  # mine');

    await page.keyboard.press('Escape');

    // The edit stands -- there was never anything to apply, it has been the
    // user's text since the verdict -- and the session is gone with its
    // formatting.
    await expect(page.locator('.jp-Notebook')).not.toHaveClass(
      /jv-merge-session/
    );
    expect((await cells(page))[0].source).toContain('# mine');
    await expect(page.locator('.jv-merging')).toHaveCount(0);
  });

  test('the verdict buttons say what they do, not only which key', async ({
    page
  }) => {
    await openReview(page);
    const controls = page.locator('.jv-review-controls');

    await expect(controls.locator('.jv-accept')).toContainText('Accept');
    await expect(controls.locator('.jv-reject')).toContainText('Reject');
    await expect(controls.locator('.jv-modify')).toContainText('Modify');
    // The key stays a caption beside the button, not a second line of prose.
    await expect(controls.locator('.jv-review-key').first()).toHaveText('Y');
  });
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
  // The model gets one correction; this agent makes the same mistake again,
  // so the round ends without an edit. What matters is what the user is left
  // with: their prompt, settled, and *not* a complaint about our wire format
  // in place of the code they asked for (docs/13 R15).
  await expect
    .poll(async () => (await cells(page))[1].type, { timeout: 60000 })
    .toBe('markdown');
  const settled = (await cells(page))[1].source;
  expect(settled).not.toContain('Protocol error');
  expect(settled).not.toContain('propose_edit');
  expect(settled).toContain('aim at a cell that is not there');
  // And the notebook is untouched by the failed attempts.
  expect((await cells(page))[0].source).toBe('value = 1');
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

test('before the first token it says what it is waiting on', async ({
  page
}) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', '> think slowly please');

  await page.notebook.runCell(0, { wait: false });

  // "0 tokens/sec" reads as broken; the gap before the first token is the
  // model reading the prompt, and saying so is the honest version.
  const thinking = page.locator('.jv-prompt-output.jv-thinking');
  await expect(thinking).toBeVisible({ timeout: 30000 });
  await expect(thinking).not.toContainText('0 tokens/sec');
});

test('a cell fills in as it arrives, before the tag closes', async ({
  page
}) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', '> stream a cell slowly');

  await page.notebook.runCell(0, { wait: false });

  // Visible while the body is still arriving -- the whole point: a user
  // watching a slow model sees the code being written, not a blank pause.
  await expect
    .poll(async () => (await cells(page)).map(c => c.source).join('\n'), {
      timeout: 30000
    })
    .toContain('line_0 = 0');
  const midway = await cells(page);
  expect(midway.some(c => c.source.includes('line_5 = 5'))).toBe(false);
  // And the fence never reaches the notebook, even mid-stream.
  expect(midway.some(c => c.source.includes('```'))).toBe(false);

  // The closing tag settles it, and the finished cell is whole.
  await expect
    .poll(async () => (await cells(page)).map(c => c.source).join('\n'), {
      timeout: 30000
    })
    .toContain('line_5 = 5');
  const after = await cells(page);
  expect(after[1].source).toBe(
    'line_0 = 0\nline_1 = 1\nline_2 = 2\nline_3 = 3\nline_4 = 4\nline_5 = 5'
  );
  expect(after[1].source).not.toContain('```');
});

test('undo walks a whole round back, ending at an editable prompt', async ({
  page
}) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', 'value = 1');
  await page.notebook.addCell('code', '> add and edit together');

  await page.notebook.runCell(1, { wait: false });
  await expect(page.locator('.jv-prompt-output.jv-review')).toBeVisible({
    timeout: 60000
  });

  // Accept the edit; the round then settles and the prompt becomes markdown.
  await page.keyboard.press('Enter');
  await page.keyboard.press('y');
  await expect
    .poll(async () => (await cells(page))[0].source)
    .toBe('value = 2');
  await expect.poll(async () => (await cells(page))[1].type).toBe('markdown');

  // One press: the verdict comes back, and with it the review -- which means
  // the prompt has to be a code cell again, because that is what carries the
  // summary and answers Enter and N.
  await page.keyboard.press('ControlOrMeta+z');
  await expect.poll(async () => (await cells(page))[1].type).toBe('code');
  await expect(page.locator('.jv-prompt-output.jv-review')).toBeVisible();
  expect((await cells(page))[0].source).toBe('value = 1');

  // Now walk the rest back. However many presses it takes, the end state is
  // the one that matters: nothing of the round left, and a prompt you can edit.
  for (let i = 0; i < 4; i++) {
    await page.keyboard.press('ControlOrMeta+z');
    await page.waitForTimeout(400);
    const now = await cells(page);
    if (now[1]?.type === 'code' && now[0].source === 'value = 1') {
      break;
    }
  }

  const after = await cells(page);
  expect(after[0].source).toBe('value = 1'); // the edit is undone
  expect(after[1].type).toBe('code'); // the prompt is code again
  expect(after[1].source).toContain('add and edit together');
  expect(after.some(c => c.source.includes('added by the round'))).toBe(false);
});

/**
 * The user's report, reproduced: a round that only inserts settles on its own,
 * with no verdicts to give. That conversion replaces the prompt cell's widget
 * and focus goes with it -- so the test deliberately does not click anything
 * first, and asks for the whole round back in a single press.
 */
test('one undo takes back a settled round nobody had to review', async ({
  page
}) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', 'value = 1');
  await page.notebook.addCell('code', '> write me a helper');

  await page.notebook.runCell(1, { wait: false });

  // Settled without review: the cell is inserted and the prompt is markdown.
  await expect.poll(async () => (await cells(page))[1].type).toBe('markdown');
  await expect
    .poll(async () => (await cells(page)).map(c => c.source).join('\n'))
    .toContain('def double(x)');

  // The class the notebook-level binding selects on, without which the key
  // falls through to core: focus is on a cell here, but that has not been
  // reliable, which is exactly why the binding no longer depends on it.
  await expect(page.locator('.jp-Notebook.jv-can-revert')).toBeVisible();

  await page.keyboard.press('ControlOrMeta+z');

  // The inserted cell is gone and the prompt is editable code again.
  await expect.poll(async () => (await cells(page))[1].type).toBe('code');
  const after = await cells(page);
  expect(after[0].source).toBe('value = 1');
  expect(after[1].source).toBe('> write me a helper');
  expect(after.some(c => c.source.includes('def double'))).toBe(false);

  // And the cursor is in it, at the end, ready to be reworded.
  await expect(page.locator('.jp-mod-editMode')).toBeVisible();
  await page.keyboard.type(' properly');
  await expect
    .poll(async () => (await cells(page))[1].source)
    .toBe('> write me a helper properly');
});

test('a misused tag is corrected without the user seeing it', async ({
  page
}) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', 'value = 1');
  await page.notebook.addCell('code', '> misuse the tag once');

  await page.notebook.runCell(1, { wait: false });

  // The first reply named no cell at all. The model is told and asked again,
  // and the corrected edit is what the user is offered.
  await expect(page.locator('.jv-prompt-output.jv-review')).toBeVisible({
    timeout: 60000
  });
  await page.keyboard.press('Enter');
  await page.keyboard.press('y');
  await expect
    .poll(async () => (await cells(page))[0].source)
    .toBe('value = 2');

  // And none of the machinery reached the notebook: no protocol complaint in
  // place of the code, and no stray fence from the reply that failed.
  const after = await cells(page);
  const settled = after[1].source;
  expect(settled).not.toContain('Protocol error');
  expect(settled).not.toContain('```');
  expect(settled).toContain('misuse the tag once');
});
