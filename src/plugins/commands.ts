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
import { focusActiveCell, placeCursorAtEnd } from './rounds';

/** Index of a cell by id, or -1. Local: mutations keeps its own, unexported. */
function indexOfCellId(panel: NotebookPanel, cellId: string): number {
  const cells = panel.content.model?.cells;
  if (!cells) {
    return -1;
  }
  for (let i = 0; i < cells.length; i++) {
    if (cells.get(i).id === cellId) {
      return i;
    }
  }
  return -1;
}

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
  export const confirmMerge = 'jupyter-vybe:confirm-merge';
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
/**
 * Anywhere in a notebook whose review session is open.
 *
 * Deliberately the whole notebook rather than a cell: Escape has to work from
 * wherever the user has ended up, because it is the way out. A session the
 * user cannot leave without finding the right cell first is a trap, and a
 * modal state with no escape hatch is the thing people rightly hate.
 */
const SESSION_SELECTOR = '.jp-Notebook.jp-mod-commandMode.jv-review-session';
/**
 * A merge being hand-edited. Mutually exclusive with the review session, so
 * `Escape` cannot mean "confirm this merge" and "reject everything" at once.
 *
 * Two selectors because the user is normally in edit mode here and may step
 * out of it, and both carry three classes so they outrank core's
 * `.jp-Notebook.jp-mod-editMode` on specificity rather than on which of us
 * registered last.
 */
const MERGE_SELECTORS = [
  '.jp-Notebook.jp-mod-editMode.jv-merge-session',
  '.jp-Notebook.jp-mod-commandMode.jv-merge-session'
];

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
        // Declared before the verdict lands. Accepting removes the proposal,
        // and the review session dims everything that is not one -- so
        // without this the cell the user was just invited to edit greys out
        // and stops answering under their cursor.
        proposals.beginManualMerge(panel, cellId);
        proposals.accept(panel, p.id);
        materialiseMerge(panel, cellId, merged, proposals.logFor(panel));
        // Accepting advances the review, which moves the active cell on to
        // the next proposal -- or, when that was the last one, to the prompt.
        // Edit mode then landed the cursor there rather than in the merged
        // cell, which is the one place the user has just been invited to
        // type. Put it back before entering edit mode.
        const idx = indexOfCellId(panel, cellId);
        if (idx !== -1) {
          panel.content.activeCellIndex = idx;
          panel.content.deselectAll();
        }
        panel.content.mode = 'edit';
        placeCursorAtEnd(panel);
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

    /**
     * Confirm a hand-edited merge and hand the notebook back to the review.
     *
     * There is nothing to apply: the merged text has been in the cell since
     * the verdict, and every keystroke since is the user's own. What ends is
     * the session -- so this closes it and walks on to the next diff, which
     * is what "and continue" means on the button.
     */
    app.commands.addCommand(CommandIDs.confirmMerge, {
      label: 'Confirm merge and continue reviewing',
      isEnabled: () => !!current() && !!proposals.manualMerge(current()!),
      execute: () => {
        const panel = current();
        if (!panel || !proposals.endManualMerge(panel)) {
          return;
        }
        panel.content.mode = 'command';
        if (proposals.activeProposal(panel)) {
          void app.commands.execute(CommandIDs.gotoFirst);
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
    // Modify exists under both key schemes. In Y/N it is Enter, which is
    // free; in Enter/Backspace, Enter is already accept, so it is Space --
    // which has no meaning in command mode and is the only remaining key
    // that sits under the hand. Losing a verdict because of a keyboard
    // preference would have made the two schemes quietly unequal.
    bind(CommandIDs.editProposal, yn ? ['Enter'] : ['Space'], REVIEW_SELECTOR);
    // The way out of a review session, from anywhere in it.
    bind(CommandIDs.rejectAll, ['Escape'], SESSION_SELECTOR);
    // And the way out of a merge, which is a different session with a
    // different Escape: confirm, and walk on to the next diff.
    for (const selector of MERGE_SELECTORS) {
      bind(CommandIDs.confirmMerge, ['Escape'], selector);
    }
    // Two keys, no chords: from the prompt cell, Enter starts the review and
    // N rejects the lot. A chord for the primary action of the tool was the
    // wrong default.
    bind(CommandIDs.gotoFirst, ['Enter'], PROMPT_PENDING_SELECTOR);
    bind(CommandIDs.rejectAll, ['N'], PROMPT_PENDING_SELECTOR);
    bind(CommandIDs.cancel, ['Ctrl C'], ROUND_SELECTOR);
    // Two selectors, because there are two situations.
    //
    // The cell-level one fires during review, when focus is on the cell the
    // user is walking. The notebook-level one covers the case that made undo
    // look broken: settling a round replaces the prompt cell's widget, focus
    // falls back to the document, and a binding that required a focused cell
    // never ran. `jv-can-revert` is on the notebook exactly while there is a
    // round of ours to take back, and carrying that third class it outranks
    // core's `.jp-Notebook.jp-mod-commandMode` on specificity rather than on
    // registration order.
    //
    // Both hand over cleanly: when the command is disabled -- nothing of ours
    // left to undo -- Lumino skips it and core's undo gets the key.
    //
    // `Accel` is Cmd on macOS and Ctrl everywhere else, which is the whole of
    // the cross-platform story for every binding here.
    bind(
      CommandIDs.undo,
      ['Accel Z'],
      '.jp-Notebook.jp-mod-commandMode .jp-Cell:focus'
    );
    bind(
      CommandIDs.undo,
      ['Accel Z'],
      '.jp-Notebook.jp-mod-commandMode.jv-can-revert'
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
