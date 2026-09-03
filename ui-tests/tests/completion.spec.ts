import { expect, galata, test } from '@jupyterlab/galata';
import * as path from 'path';

/**
 * Ghost text end to end (D5). The agent is a script returning a fixed, fenced
 * continuation, so this tests the wiring -- the trigger, the stream, the
 * cleaning, and what the keys do -- rather than a model.
 */

const PLUGIN = 'jupyter-vybe:settings';
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
      completion: { enabled: true, timeoutSeconds: 20, maxLines: 2 },
      history: { persist: false }
    }
  }
});

test.afterEach(async ({ page }) => {
  await page.notebook.close(true).catch(() => undefined);
});

const ghost = (page: any) => page.locator('.jv-ghost');

/**
 * The first cell's source, read from the model.
 *
 * Not galata's `getCellTextInput`, which reads the rendered editor and came
 * back empty here: the point of every assertion below is what the *document*
 * holds, which is precisely what ghost text must never appear in.
 */
async function cellSource(page: any): Promise<string> {
  return await page.evaluate(() => {
    const app = (window as any).jupyterapp;
    const model = app.shell.currentWidget?.content?.model;
    return model ? model.cells.get(0).sharedModel.getSource() : '';
  });
}

async function typeInFirstCell(page: any, text: string) {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.enterCellEditingMode(0);
  await page.keyboard.type(text);
}

test('nothing is suggested until it is asked for', async ({ page }) => {
  await typeInFirstCell(page, 'df = ');

  // The whole reason the automatic version was withdrawn: a suggestion costs
  // a model call, and typing is not a request for one.
  await page.waitForTimeout(2000);
  expect(await ghost(page).count()).toBe(0);
});

test('the trigger key predicts the rest of the cell', async ({ page }) => {
  await typeInFirstCell(page, 'df = ');

  await page.keyboard.press('Alt+Tab');

  await expect(ghost(page)).toBeVisible({ timeout: 30000 });
  await expect(ghost(page)).toContainText('read_csv');
  // Fenced by the model, never fenced on screen.
  await expect(ghost(page)).not.toContainText('```');
  // And it is a suggestion, not text: the cell still holds what was typed.
  expect(await cellSource(page)).toBe('df = ');

  // The tooltip says what the keys do, where the user is looking.
  await expect(page.locator('.jv-ghost-tip')).toContainText('tab to accept');
});

test('tab accepts the suggestion into the cell', async ({ page }) => {
  await typeInFirstCell(page, 'df = ');
  await page.keyboard.press('Alt+Tab');
  await expect(ghost(page)).toBeVisible({ timeout: 30000 });

  await page.keyboard.press('Tab');

  await expect
    .poll(async () => await cellSource(page))
    .toContain("pd.read_csv('data.csv')");
  await expect(ghost(page)).toHaveCount(0);
});

test('backspace rejects the suggestion without eating the code', async ({
  page
}) => {
  await typeInFirstCell(page, 'df = ');
  await page.keyboard.press('Alt+Tab');
  await expect(ghost(page)).toBeVisible({ timeout: 30000 });

  await page.keyboard.press('Backspace');

  await expect(ghost(page)).toHaveCount(0);
  // The failure this exists to prevent: rejecting a suggestion is a user
  // saying "no thank you", and it must not cost them a character.
  expect(await cellSource(page)).toBe('df = ');
});

test('typing rejects the suggestion and keeps what was typed', async ({
  page
}) => {
  await typeInFirstCell(page, 'df = ');
  await page.keyboard.press('Alt+Tab');
  await expect(ghost(page)).toBeVisible({ timeout: 30000 });

  await page.keyboard.type('x');

  await expect(ghost(page)).toHaveCount(0);
  expect(await cellSource(page)).toBe('df = x');
});

test('the trigger again extends the suggestion rather than replacing it', async ({
  page
}) => {
  await typeInFirstCell(page, 'df = ');
  await page.keyboard.press('Alt+Tab');
  await expect(ghost(page)).toContainText('read_csv', { timeout: 30000 });

  // The suggestion already on screen is part of what the model is continuing,
  // so asking again grows it. This is what makes a two-line default workable:
  // it is how much you are asked to read at once, not a ceiling on the total.
  await page.keyboard.press('Alt+Tab');

  await expect(ghost(page)).toContainText('df.head()', { timeout: 30000 });
  // The first part is still there, not reconsidered and redrawn.
  await expect(ghost(page)).toContainText('read_csv');
  // And none of it is in the document until it is accepted.
  expect(await cellSource(page)).toBe('df = ');

  await page.keyboard.press('Tab');
  await expect.poll(async () => await cellSource(page)).toContain('df.head()');
});

test.describe('with completion off', () => {
  test.use({
    mockSettings: {
      ...galata.DEFAULT_SETTINGS,
      [PLUGIN]: {
        agent: { id: 'command', command: SCRIPTED_AGENT },
        completion: { enabled: false },
        history: { persist: false }
      }
    }
  });

  test('the trigger key does nothing', async ({ page }) => {
    await typeInFirstCell(page, 'df = ');
    await page.keyboard.press('Alt+Tab');
    await page.waitForTimeout(2000);
    expect(await ghost(page).count()).toBe(0);
  });
});
