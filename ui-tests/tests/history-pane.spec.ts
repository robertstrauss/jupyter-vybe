import { expect, test } from '@jupyterlab/galata';

/**
 * The prompt record lives in the right sidebar, and it is the only piece of
 * vybe chrome that is on screen whether or not a round is running -- which is
 * why the two things you might want between rounds hang off it: the settings,
 * and forgetting what the agent remembers.
 */

test.use({ autoGoto: false });

test('the pane offers to erase the conversation, per notebook', async ({
  page
}) => {
  await page.goto();
  await page.notebook.createNew();

  await page.sidebar.openTab('jupyter-vybe-history');

  const erase = page.locator('.jv-history-erase');
  // Visible because the conversation is persistent by default. With
  // single-shot prompts there would be nothing accumulated to erase.
  await expect(erase).toBeVisible();
  await expect(erase).toHaveAttribute(
    'aria-label',
    'Erase conversation context'
  );

  await erase.click();

  await expect(page.locator('.jv-history-status')).toContainText(
    'context erased'
  );
});
