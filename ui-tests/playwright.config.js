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

/**
 * Not 8888.
 *
 * 8888 is where a person's own JupyterLab lives, and the developer of this
 * extension is very likely to have one open — testing the extension. Sharing
 * the port means the suite either refuses to start or, worse, quietly drives
 * their session. Its own port costs nothing.
 */
const PORT = 8977;

module.exports = {
  ...baseConfig,
  use: {
    ...baseConfig.use,
    baseURL: `http://localhost:${PORT}`
  },
  webServer: {
    command: `jlpm start --port ${PORT}`,
    url: `http://localhost:${PORT}/lab`,
    timeout: 120 * 1000,
    /*
     * Never reuse a running server.
     *
     * Reuse means a server started before your change keeps answering after
     * it -- including with the old config and a pile of state from previous
     * runs. This project has now lost hours to that twice: once to a stale
     * dev server, once to a galata server started before this very file set
     * JUPYTER_CONFIG_DIR. A ten-second boot per run is the cheaper mistake.
     */
    reuseExistingServer: false,
    env: {
      ...process.env,
      JUPYTER_CONFIG_DIR: CONFIG_DIR
    }
  }
};
