import type { JupyterFrontEndPlugin } from '@jupyterlab/application';
import {
  INotebookCellExecutor,
  runCell as coreRunCell
} from '@jupyterlab/notebook';

import { isPromptCell } from '../notebook/promptCell';
import { IVybeRounds, IVybeSettings } from '../tokens';

/**
 * Replaces `@jupyterlab/notebook-extension:cell-executor` (disabled in
 * package.json) so that prompt cells never reach a kernel.
 *
 * Every execution route in NotebookActions -- run, runAndAdvance, runCells,
 * runAll, runAllAbove, runAllBelow, restart-and-run-all -- funnels through
 * Private.runCell, which ends in `executor.runCell(options)`. Overriding this
 * one token therefore covers keyboard, toolbar, menu, palette, and programmatic
 * execution. There is no second door (docs/02).
 *
 * BOOTSTRAP HAZARD: this plugin is a hard `requires` of
 * @jupyterlab/notebook-extension:tracker. If activate throws, the tracker never
 * activates and JupyterLab loses notebooks entirely. So activate is synchronous
 * and allocation-only; every lookup happens lazily inside runCell, and both the
 * activate body and the runCell body fall back to the core implementation on any
 * error (docs/13 R1).
 */
export const cellExecutor: JupyterFrontEndPlugin<INotebookCellExecutor> = {
  id: 'jupyter-vybe:cell-executor',
  description: 'Routes prompt cells to the agent instead of the kernel.',
  autoStart: true,
  provides: INotebookCellExecutor,
  optional: [IVybeSettings, IVybeRounds],
  activate: (
    _app,
    settings: IVybeSettings | null,
    rounds: IVybeRounds | null
  ): INotebookCellExecutor => {
    try {
      return Object.freeze({
        runCell: async (options: INotebookCellExecutor.IRunCellOptions) => {
          try {
            if (!settings?.enabled || !rounds) {
              return coreRunCell(options);
            }
            if (!isPromptCell(options.cell, settings.prefix)) {
              return coreRunCell(options);
            }
            return await rounds.begin(options);
          } catch (err) {
            // A bug in round handling degrades to "the cell ran as code",
            // never to "the cell silently did nothing".
            console.error(
              '[jupyter-vybe] round failed, delegating to core',
              err
            );
            return coreRunCell(options);
          }
        }
      });
    } catch (err) {
      console.error('[jupyter-vybe] executor setup failed, using core', err);
      return Object.freeze({ runCell: coreRunCell });
    }
  }
};

export default cellExecutor;
