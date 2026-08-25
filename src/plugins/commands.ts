import type {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { INotebookTracker, type NotebookPanel } from '@jupyterlab/notebook';

import { IVybeProposals } from './proposals';
import { IVybeRounds, IVybeSettings } from '../tokens';
import { commentFor, mergedSource } from '../ui/diff';
import { materialiseMerge, reopenPromptCell } from '../notebook/mutations';
import {
  fromMarkdownSource,
  isSettledPromptCell
} from '../notebook/promptCell';
import { kernelInfo } from '../notebook/snapshot';

export namespace CommandIDs {
  export const accept = 'jupyter-vybe:accept-proposal';
  export const reject = 'jupyter-vybe:reject-proposal';
  export const editProposal = 'jupyter-vybe:edit-proposal';
  export const gotoFirst = 'jupyter-vybe:goto-first-proposal';
  export const rejectAll = 'jupyter-vybe:reject-all';
  export const cancel = 'jupyter-vybe:cancel-round';
  export const reopenPrompt = 'jupyter-vybe:reopen-prompt';
}

/**
 * All bindings are scoped to `.jv-round-active` in command mode, so they are
 * inert while typing and inert outside a round. `Y` overrides
 * notebook:change-cell-to-code only during review, where changing a cell's type
 * is meaningless anyway (docs/03).
 */
const REVIEW_SELECTOR =
  '.jp-Notebook.jp-mod-commandMode.jv-round-active .jp-Cell.jv-proposal-active';
const ROUND_SELECTOR = '.jp-Notebook.jp-mod-commandMode.jv-round-active';

export const commandsPlugin: JupyterFrontEndPlugin<void> = {
  id: 'jupyter-vybe:commands',
  description: 'Commands and keybindings for reviewing proposed edits.',
  autoStart: true,
  requires: [INotebookTracker, IVybeProposals, IVybeRounds, IVybeSettings],
  activate: (
    app: JupyterFrontEnd,
    tracker: INotebookTracker,
    proposals: IVybeProposals,
    rounds: IVybeRounds,
    settings: IVybeSettings
  ): void => {
    const current = (): NotebookPanel | null => tracker.currentWidget;

    app.commands.addCommand(CommandIDs.accept, {
      label: 'Accept proposed edit',
      isEnabled: () => !!current() && !!proposals.activeProposal(current()!),
      execute: () => {
        const panel = current();
        const p = panel && proposals.activeProposal(panel);
        if (panel && p) {
          proposals.accept(panel, p.id);
        }
      }
    });

    app.commands.addCommand(CommandIDs.reject, {
      label: 'Reject proposed edit',
      isEnabled: () => !!current() && !!proposals.activeProposal(current()!),
      execute: () => {
        const panel = current();
        const p = panel && proposals.activeProposal(panel);
        if (panel && p) {
          proposals.reject(panel, p.id);
        }
      }
    });

    // Enter keeps its native "go edit this" meaning, and lands the user in the
    // merged version: accepted lines live, replaced lines commented beneath
    // (docs/03, decision D6). Materialised on demand, never ambiently.
    app.commands.addCommand(CommandIDs.editProposal, {
      label: 'Edit proposed change',
      isEnabled: () => !!current() && !!proposals.activeProposal(current()!),
      execute: () => {
        const panel = current();
        const p = panel && proposals.activeProposal(panel);
        if (!panel || !p) {
          return;
        }
        const cell = panel.content.activeCell;
        if (!cell) {
          return;
        }
        const style = settings.mergeCommentStyle;
        const comment =
          style === 'none'
            ? ''
            : style === 'hash'
              ? '#'
              : style === 'slashes'
                ? '//'
                : commentFor(kernelInfo(panel).language, cell.model.type);
        const merged = mergedSource(p.hunks, p.after, comment);
        const cellId = p.cellId;
        proposals.accept(panel, p.id);
        materialiseMerge(panel, cellId, merged, proposals.logFor(panel));
        panel.content.mode = 'edit';
      }
    });

    app.commands.addCommand(CommandIDs.gotoFirst, {
      label: 'Go to first proposed edit',
      execute: () => {
        const panel = current();
        const p = panel && proposals.activeProposal(panel);
        if (!panel || !p) {
          return;
        }
        const cells = panel.content.model?.cells;
        if (!cells) {
          return;
        }
        for (let i = 0; i < cells.length; i++) {
          if (cells.get(i).id === p.cellId) {
            panel.content.activeCellIndex = i;
            panel.content.mode = 'command';
            panel.content.widgets[i]?.node.scrollIntoView({
              behavior: settings.autoScroll ? 'smooth' : 'auto',
              block: 'center'
            });
            return;
          }
        }
      }
    });

    app.commands.addCommand(CommandIDs.rejectAll, {
      label: 'Reject all proposed edits',
      execute: () => {
        const panel = current();
        if (panel) {
          proposals.rejectAll(panel);
        }
      }
    });

    app.commands.addCommand(CommandIDs.cancel, {
      label: 'Cancel agent round',
      execute: () => {
        const panel = current();
        if (panel) {
          rounds.cancel(panel);
        }
      }
    });

    // Available forever, not just inside the undo window (docs/02).
    app.commands.addCommand(CommandIDs.reopenPrompt, {
      label: 'Reopen as prompt cell',
      isEnabled: () => {
        const cell = current()?.content.activeCell;
        return !!cell && isSettledPromptCell(cell.model, settings.prefix);
      },
      execute: () => {
        const panel = current();
        const cell = panel?.content.activeCell;
        if (
          !panel ||
          !cell ||
          !isSettledPromptCell(cell.model, settings.prefix)
        ) {
          return;
        }
        const restored = fromMarkdownSource(
          cell.model.sharedModel.getSource(),
          settings.prefix
        );
        reopenPromptCell(panel, restored);
      }
    });

    const yn = settings.acceptKey !== 'enter-backspace';
    const bind = (command: string, keys: string[], selector: string) =>
      app.commands.addKeyBinding({ command, keys, selector });

    bind(CommandIDs.accept, yn ? ['Y'] : ['Enter'], REVIEW_SELECTOR);
    bind(CommandIDs.reject, yn ? ['N'] : ['Backspace'], REVIEW_SELECTOR);
    if (yn) {
      bind(CommandIDs.editProposal, ['Enter'], REVIEW_SELECTOR);
    }
    bind(CommandIDs.gotoFirst, ['Alt E'], ROUND_SELECTOR);
    bind(CommandIDs.rejectAll, ['Alt Shift E'], ROUND_SELECTOR);
    bind(CommandIDs.cancel, ['Ctrl C'], ROUND_SELECTOR);
    bind(
      CommandIDs.reopenPrompt,
      ['Accel Shift Enter'],
      '.jp-Notebook.jp-mod-commandMode'
    );
  }
};

export default commandsPlugin;
