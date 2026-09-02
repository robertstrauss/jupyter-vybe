/**
 * Configuration for Playwright using default from @jupyterlab/galata
 */
const path = require('path');

const baseConfig = require('@jupyterlab/galata/lib/playwright-config');

/**
 * The test server gets its own config directory.
 *
 * Without this it inherits `~/.jupyter`, which holds the developer's own
 * saved workspace — so JupyterLab restores whatever notebook they last had
 * open, the Launcher is never the active tab, and galata's readiness check
 * times out on every single test. That is a suite that fails because of what
 * you were doing in your browser an hour ago, which is a miserable thing to
 * debug and a worse thing to hand to CI.
 */
const CONFIG_DIR = path.join(__dirname, '.jupyter-test');

module.exports = {
  ...baseConfig,
  webServer: {
    command: 'jlpm start',
    url: 'http://localhost:8888/lab',
    timeout: 120 * 1000,
    reuseExistingServer: !process.env.CI,
    env: {
      ...process.env,
      JUPYTER_CONFIG_DIR: CONFIG_DIR
    }
  }
};
