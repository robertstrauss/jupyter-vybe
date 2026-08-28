import { expect, galata, test } from '@jupyterlab/galata';
import * as path from 'path';

/**
 * Inline completion end to end (D5). The agent is a script that returns a
 * fixed continuation, so this tests the wiring -- provider registration, the
 * request, and ghost text reaching the editor -- rather than a model.
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
      // `command` has no raw completion endpoint, so this exercises the
      // instruction fallback path as well as the wiring.
      agent: { id: 'command', command: SCRIPTED_AGENT },
      completion: { enabled: true, debounceMs: 10, timeoutSeconds: 20 },
      history: { persist: false }
    }
  }
});

test.afterEach(async ({ page }) => {
  await page.notebook.close(true).catch(() => undefined);
});

test('inline completion offers ghost text from the agent', async ({ page }) => {
  await page.goto();
  await page.notebook.createNew();
  await page.notebook.enterCellEditingMode(0);
  await page.keyboard.type('total = ');

  // The provider is registered and answers; the completer renders the answer
  // as ghost text rather than as a popup.
  const ghost = page.locator('.jp-GhostText');
  await expect(ghost).toBeVisible({ timeout: 30000 });
  await expect(ghost).toContainText('helper', { ignoreCase: true });
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

  test('nothing is suggested', async ({ page }) => {
    await page.goto();
    await page.notebook.createNew();
    await page.notebook.enterCellEditingMode(0);
    await page.keyboard.type('total = ');
    await page.waitForTimeout(1500);
    // Nothing appears, and nothing errors: the default is silence.
    expect(await page.locator('.jp-GhostText').count()).toBe(0);
  });
});
