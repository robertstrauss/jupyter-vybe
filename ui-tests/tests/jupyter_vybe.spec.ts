import { expect, test } from '@jupyterlab/galata';

/**
 * The extension has to load at all, and it has to load without breaking the
 * notebook tracker -- jupyter-vybe:cell-executor is a hard dependency of it,
 * so a throw during activation takes notebooks with it (docs/13 R1).
 */

test.use({ autoGoto: false });

test('activates without errors, and notebooks still work', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', message => {
    if (message.type() === 'error') {
      errors.push(message.text());
    }
  });

  await page.goto();
  await page.notebook.createNew();

  expect(errors.filter(e => e.includes('Cycle detected'))).toHaveLength(0);
  expect(errors.filter(e => e.includes('jupyter-vybe'))).toHaveLength(0);
  // A notebook opened at all, which is the R1 canary.
  expect(await page.notebook.isAnyActive()).toBe(true);
});
