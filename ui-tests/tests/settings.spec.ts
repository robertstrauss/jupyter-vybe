import { expect, galata, test } from '@jupyterlab/galata';

/**
 * The settings pane has to actually render. It did not: custom field renderers
 * were registered for whole object groups, which replaced those groups'
 * rendering with a single control and dropped every other setting in them.
 */

test.use({ autoGoto: false });

async function openVybeSettings(page: any) {
  await page.goto();
  await page.evaluate(async () => {
    const app = (window as any).jupyterapp;
    await app.commands.execute('settingeditor:open', {
      query: 'Jupyter Vybe'
    });
  });
  await page.waitForTimeout(2500);
}

test('the settings pane renders every group', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => {
    if (m.type() === 'error') {
      errors.push(m.text());
    }
  });

  await openVybeSettings(page);

  const text = await page.evaluate(
    () => document.querySelector('.jp-SettingsPanel')?.textContent ?? ''
  );
  // Every group the schema declares should be reachable from the pane.
  for (const heading of [
    'Agent',
    'Context',
    'Inline completion',
    'History',
    'Review',
    'Trigger'
  ]) {
    expect(text).toContain(heading);
  }
  expect(errors.filter(e => /vybe/i.test(e))).toEqual([]);
});

test('the settings pane keeps the fields inside a customised group', async ({
  page
}) => {
  await openVybeSettings(page);

  const text = await page.evaluate(
    () => document.querySelector('.jp-SettingsPanel')?.textContent ?? ''
  );
  // `agent` has a custom renderer for the picker; the rest of the group must
  // survive it. These vanished when the renderer replaced the whole object.
  expect(text).toContain('Where the agent runs');
  expect(text).toContain('Per-turn timeout');
  expect(text).toContain('Sidecar URL');
});

test('the panel greys out what the chosen agent does not use', async ({
  page
}) => {
  await openVybeSettings(page);

  // A local model is the default, so the fields belonging to the other two
  // agents are inert -- present, so they can be found, but visibly not in
  // play, with the reason beside them.
  const command = page.locator('#jv-agent-command');
  await expect(command).toBeDisabled();
  await expect(page.locator('#jv-agent-endpoint')).toBeDisabled();
  await expect(page.locator('#jv-agent-apiKeyEnv')).toBeDisabled();

  // The example of what belongs in the box, rather than a description of it.
  await expect(command).toHaveAttribute('placeholder', 'claude -p');

  // Choosing that agent brings its fields to life, and puts the others out.
  await page.locator('#jv-agent-id').selectOption('command');
  await expect(command).toBeEnabled();
  await expect(page.locator('#jv-agent-endpoint')).toBeDisabled();
});

test('descriptions are behind an (i), not printed down the page', async ({
  page
}) => {
  await openVybeSettings(page);

  const panel = page.locator('.jp-SettingsPanel');
  const wording = 'Cells outside it are summarised';
  // Present in the DOM, because it is the answer to the question the label
  // raises -- but not on screen until asked for.
  await expect(panel.getByText(wording, { exact: false })).toBeHidden();

  await page
    .locator('label[for="jv-context-cellWindow"] .jv-info-button')
    .click();

  await expect(panel.getByText(wording, { exact: false })).toBeVisible();
});

test('the global prompt is a textarea holding the shipped default', async ({
  page
}) => {
  await openVybeSettings(page);

  const box = page.locator('textarea#jv-prompts-global');
  await expect(box).toBeVisible();
  // Filled with what is actually in use, so it can be edited rather than
  // guessed at. An empty box means "the default", which says nothing.
  await expect(box).toHaveValue(/Match the style of the code already/);
});

test('completion gets its own agent, and is greyed while it is off', async ({
  page
}) => {
  await openVybeSettings(page);

  // Inline completion is off by default -- it costs a model call -- so the
  // rest of the group, its agent included, is inert.
  const agent = page.locator('#jv-completion-agentId');
  await expect(agent).toBeDisabled();

  await page.locator('#jv-completion-enabled').check();
  await expect(agent).toBeEnabled();
  // Blank means "whatever writes the cells"; the alternatives are the same
  // agents, because a completion agent is an agent.
  await expect(agent).toHaveValue('');
});
