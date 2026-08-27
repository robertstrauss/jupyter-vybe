import type { JupyterFrontEndPlugin } from '@jupyterlab/application';

import { cellExecutor } from './plugins/cellExecutor';
import { settingsPlugin } from './plugins/settings';
import { agentPlugin } from './plugins/agentClient';
import { proposalsPlugin } from './plugins/proposals';
import { roundsPlugin } from './plugins/rounds';
import { commandsPlugin } from './plugins/commands';
import { reviewUIPlugin } from './plugins/reviewUI';
import { historyPanePlugin } from './plugins/historyPane';

/**
 * Jupyter Vybe — fine-grained AI code generation in Jupyter notebooks.
 *
 * jupyter-vybe:cell-executor replaces the core provider (disabled in
 * package.json) and must be listed first: it is a hard dependency of
 * @jupyterlab/notebook-extension:tracker, so nothing else can matter if it
 * fails to activate.
 */
const plugins: JupyterFrontEndPlugin<any>[] = [
  cellExecutor,
  settingsPlugin,
  agentPlugin,
  proposalsPlugin,
  roundsPlugin,
  commandsPlugin,
  reviewUIPlugin,
  historyPanePlugin
];

export default plugins;
