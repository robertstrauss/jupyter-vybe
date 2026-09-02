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
