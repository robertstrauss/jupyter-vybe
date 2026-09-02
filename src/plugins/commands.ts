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
import { focusActiveCell } from './rounds';

export namespace CommandIDs {
  export const accept = 'jupyter-vybe:accept-proposal';
  export const reject = 'jupyter-vybe:reject-proposal';
  export const editProposal = 'jupyter-vybe:edit-proposal';
  export const undoRejectAll = 'jupyter-vybe:undo-reject-all';
  export const gotoFirst = 'jupyter-vybe:goto-first-proposal';
  export const rejectAll = 'jupyter-vybe:reject-all';
  export const cancel = 'jupyter-vybe:cancel-round';
  export const reopenPrompt = 'jupyter-vybe:reopen-prompt';
  export const undo = 'jupyter-vybe:undo';
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
/**
 * The prompt cell while it is showing a review summary. Unchorded keys are
 * safe here because the class only exists in that one state: `Enter` still
 * means "edit this cell" everywhere else, and `N` has no core binding at all.
 *
 * `:focus` is load-bearing, not decoration. Core binds Enter to
 * `.jp-Notebook.jp-mod-commandMode .jp-Cell:focus`, which computes to the same
 * specificity as this selector without it -- and on a tie core won, so Enter
 * opened the editor instead of starting the review. One extra pseudo-class
 * settles it, and says the true thing besides: these keys are for the cell the
 * user is actually on.
 */
const PROMPT_PENDING_SELECTOR =
  '.jp-Notebook.jp-mod-commandMode .jp-Cell.jv-prompt-pending:focus';

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
            // Focus the cell so the Y / N / Enter bindings, which select on
            // the cell, actually match.
            focusActiveCell(panel);
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

    app.commands.addCommand(CommandIDs.undoRejectAll, {
      label: 'Undo reject all proposed edits',
      isEnabled: () =>
        !!current() && proposals.undoableRejection(current()!) > 0,
      execute: () => {
        const panel = current();
        if (panel && proposals.undoRejectAll(panel)) {
          void app.commands.execute(CommandIDs.gotoFirst);
        }
      }
    });

    app.commands.addCommand(CommandIDs.undo, {
      label: 'Undo the last review verdict',
      isEnabled: () => {
        const panel = current();
        return (
          !!panel &&
          (proposals.canUndo(panel) ||
            rounds.hasActiveRound(panel) ||
            rounds.canRevertLast(panel))
        );
      },
      execute: () => {
        const panel = current();
        if (!panel) {
          return;
        }
        // Three layers, most local first: take back one verdict, cancel a
        // round still in flight, then undo a round that has already settled
        // -- which removes what it inserted and gives the prompt back as an
        // editable code cell (docs/02). When none of them apply the command is
        // disabled, Lumino skips it, and core's own undo gets the key.
        if (proposals.canUndo(panel)) {
          // The prompt cell goes back to code *first*: undoLast re-renders
          // the review, and converting afterwards would replace the cell node
          // that render had just attached the summary to.
          rounds.reopenForReview(panel);
          proposals.undoLast(panel);
          // Converting a cell replaces its widget, but the notebook rebuilds
          // it on the next frame -- after the render that undoLast triggers,
          // which therefore attaches the diff and the summary to a node about
          // to be thrown away. Render once more when the new node exists.
          requestAnimationFrame(() => proposals.refresh(panel));
          return;
        }
        if (rounds.hasActiveRound(panel)) {
          rounds.cancel(panel);
          return;
        }
        rounds.revertLast(panel);
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
    // Two keys, no chords: from the prompt cell, Enter starts the review and
    // N rejects the lot. A chord for the primary action of the tool was the
    // wrong default.
    bind(CommandIDs.gotoFirst, ['Enter'], PROMPT_PENDING_SELECTOR);
    bind(CommandIDs.rejectAll, ['N'], PROMPT_PENDING_SELECTOR);
    bind(CommandIDs.cancel, ['Ctrl C'], ROUND_SELECTOR);
    // Bound at the *cell* level, where core's notebook-level undo selector
    // does not reach: Lumino walks up from the focused element and stops at
    // the first node with a match, so ours fires on the focused cell and core's
    // still fires everywhere else. When this command is disabled -- nothing of
    // ours left to undo -- Lumino skips it and core's undo gets the key, which
    // is exactly the handover we want.
    bind(
      CommandIDs.undo,
      ['Accel Z'],
      '.jp-Notebook.jp-mod-commandMode .jp-Cell:focus'
    );
    // Not scoped to ROUND_SELECTOR: after a reject-all the round is over, so
    // the class that selector matches is already gone.
    bind(
      CommandIDs.undoRejectAll,
      ['Alt Shift Z'],
      '.jp-Notebook.jp-mod-commandMode'
    );
    bind(
      CommandIDs.reopenPrompt,
      ['Accel Shift Enter'],
      '.jp-Notebook.jp-mod-commandMode'
    );
  }
};

export default commandsPlugin;
