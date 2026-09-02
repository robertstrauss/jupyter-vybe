import type {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { INotebookTracker, type NotebookPanel } from '@jupyterlab/notebook';
import { Widget } from '@lumino/widgets';

import { IVybeProposals, type IProposal } from './proposals';
import { IVybeRounds, IVybeSettings, type IRoundStatus } from '../tokens';
import { CommandIDs } from './commands';
import { DiffOverlay, ReviewControls, StatusBubble } from '../ui/DiffOverlay';
import { PromptOutput, outputHostFor } from '../ui/PromptOutput';

/** Renders the diff overlays, the per-cell controls, and the status bubble. */
export const reviewUIPlugin: JupyterFrontEndPlugin<void> = {
  id: 'jupyter-vybe:review-ui',
  description: 'Diff overlays, accept/reject controls, and the status bubble.',
  autoStart: true,
  requires: [INotebookTracker, IVybeProposals, IVybeSettings, IVybeRounds],
  activate: (
    app: JupyterFrontEnd,
    tracker: INotebookTracker,
    proposals: IVybeProposals,
    settings: IVybeSettings,
    rounds: IVybeRounds
  ): void => {
    const bubbles = new Map<string, StatusBubble>();
    const overlays = new Map<string, Widget[]>();
    /** One prompt-cell output panel per notebook, moved between cells. */
    const outputs = new Map<string, { widget: PromptOutput; cellId: string }>();
    /**
     * Panels currently showing a problem. Without this the render that
     * follows a failed round hides the message before it can be read: there
     * are no pending proposals, so the ordinary path hides the bubble.
     */
    const showingProblem = new Set<string>();

    const bubbleFor = (panel: NotebookPanel): StatusBubble => {
      let b = bubbles.get(panel.id);
      if (!b) {
        b = new StatusBubble(
          () => app.commands.execute(CommandIDs.gotoFirst),
          () => app.commands.execute(CommandIDs.rejectAll),
          () => app.commands.execute(CommandIDs.undoRejectAll),
          () => app.commands.execute(CommandIDs.cancel)
        );
        b.addClass('jv-bubble-host');
        Widget.attach(b, panel.node);
        bubbles.set(panel.id, b);
      }
      return b;
    };

    /**
     * Attach (or move) the prompt cell's output panel. It follows the cell
     * rather than the index, because a round inserts cells above nothing and
     * below everything and indices shift under it.
     */
    const outputFor = (
      panel: NotebookPanel,
      promptCellId: string
    ): PromptOutput | null => {
      const cells = panel.content.model?.cells;
      if (!cells) {
        return null;
      }
      let index = -1;
      for (let i = 0; i < cells.length; i++) {
        if (cells.get(i).id === promptCellId) {
          index = i;
          break;
        }
      }
      const cellWidget = index === -1 ? null : panel.content.widgets[index];
      if (!cellWidget) {
        return null;
      }
      const existing = outputs.get(panel.id);
      if (existing && existing.cellId === promptCellId) {
        return existing.widget;
      }
      existing?.widget.dispose();
      const widget = new PromptOutput();
      Widget.attach(widget, outputHostFor(cellWidget.node));
      cellWidget.node.classList.add('jv-prompt-cell');
      outputs.set(panel.id, { widget, cellId: promptCellId });
      return widget;
    };

    /**
     * `jv-prompt-pending` is what the Enter and N bindings select on, so it
     * marks exactly the state where those keys mean review -- not while the
     * agent is still thinking, when Enter must still mean "edit this cell".
     */
    const markPending = (
      panel: NotebookPanel,
      promptCellId: string,
      pending: boolean
    ) => {
      const cells = panel.content.model?.cells;
      if (!cells) {
        return;
      }
      for (let i = 0; i < cells.length; i++) {
        const widget = panel.content.widgets[i];
        if (!widget) {
          continue;
        }
        widget.node.classList.toggle(
          'jv-prompt-pending',
          pending && cells.get(i).id === promptCellId
        );
      }
    };

    const clearOutput = (panel: NotebookPanel) => {
      const existing = outputs.get(panel.id);
      if (!existing) {
        return;
      }
      for (const widget of panel.content.widgets) {
        widget.node.classList.remove('jv-prompt-cell', 'jv-prompt-pending');
      }
      existing.widget.dispose();
      outputs.delete(panel.id);
    };

    const clearOverlays = (panel: NotebookPanel) => {
      for (const w of overlays.get(panel.id) ?? []) {
        w.parent = null;
        w.dispose();
      }
      overlays.set(panel.id, []);
      for (const cell of panel.content.widgets) {
        cell.node.classList.remove('jv-proposal', 'jv-proposal-active');
        cell.node
          .querySelector('.jp-Cell-inputWrapper')
          ?.classList.remove('jv-input-hidden');
      }
    };

    const render = (panel: NotebookPanel) => {
      clearOverlays(panel);
      const pending = proposals.list(panel).filter(p => p.state === 'pending');
      const bubble = bubbleFor(panel);

      if (!pending.length) {
        clearOutput(panel);
      }
      if (showingProblem.has(panel.id)) {
        return; // a problem is on screen; leave it until it is dismissed
      }
      if (!pending.length) {
        // A reject-all leaves nothing pending but is worth offering back for
        // a moment, in case the click was not meant (docs/14 N3).
        const undoable = proposals.undoableRejection(panel);
        if (undoable) {
          bubble.show();
          bubble.showUndo(undoable);
        } else {
          bubble.hide();
        }
        return;
      }
      // The count lives in the prompt cell's output area, where the user is
      // already looking, rather than in a corner of the screen. The bubble is
      // now only for things that went wrong.
      bubble.hide();
      const promptCellId = proposals.roundPromptCellId(panel);
      if (promptCellId) {
        outputFor(panel, promptCellId)?.showReview(pending.length, []);
        markPending(panel, promptCellId, true);
      }

      const active = proposals.activeProposal(panel);
      const created: Widget[] = [];
      const cells = panel.content.model?.cells;
      if (!cells) {
        return;
      }
      for (let i = 0; i < cells.length; i++) {
        const proposal = pending.find(p => p.cellId === cells.get(i).id);
        if (!proposal) {
          continue;
        }
        const cellWidget = panel.content.widgets[i];
        if (!cellWidget) {
          continue;
        }
        cellWidget.node.classList.add('jv-proposal');
        if (active && proposal.id === active.id) {
          cellWidget.node.classList.add('jv-proposal-active');
          created.push(attachControls(cellWidget.node, proposal));
        }
        // Hide the live editor and show the diff. The model is untouched.
        cellWidget.node
          .querySelector('.jp-Cell-inputWrapper')
          ?.classList.add('jv-input-hidden');
        const label = proposal.targetLabel
          ? `editing cell ${i + 1}: ${proposal.targetLabel}`
          : `editing cell ${i + 1}`;
        const overlay = new DiffOverlay(
          proposal.hunks,
          proposal.note ? `${label} — ${proposal.note}` : label
        );
        Widget.attach(overlay, cellWidget.node);
        created.push(overlay);
      }
      overlays.set(panel.id, created);
    };

    const attachControls = (host: HTMLElement, proposal: IProposal): Widget => {
      const yn = settings.acceptKey !== 'enter-backspace';
      const controls = new ReviewControls({
        onAccept: () => app.commands.execute(CommandIDs.accept),
        onReject: () => app.commands.execute(CommandIDs.reject),
        // Enter is the merge verdict only when accept is Y/N; in
        // enter-backspace mode Enter already means accept.
        onModify: yn
          ? () => app.commands.execute(CommandIDs.editProposal)
          : undefined,
        acceptKey: yn ? 'Y' : '⏎',
        rejectKey: yn ? 'N' : '⌫',
        modifyKey: yn ? '⏎' : undefined
      });
      void proposal;
      Widget.attach(controls, host);
      return controls;
    };

    /**
     * The context meter (docs/09): estimated tokens against the budget the
     * ladder works to, plus what it had to drop. Shown while generating,
     * which is when it is actionable -- afterwards it is history.
     */
    const meter = (status: IRoundStatus): string => {
      if (!status.contextTokens) {
        return '';
      }
      const k = (n: number) =>
        n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
      const budget = status.budgetTokens ? ` / ${k(status.budgetTokens)}` : '';
      const trimmed = status.dropped.length
        ? ` · trimmed ${status.dropped.join(', ')}`
        : '';
      return `${k(status.contextTokens)}${budget} context${trimmed}`;
    };

    rounds.statusChanged.connect((_r, status: IRoundStatus) => {
      const bubble = bubbleFor(status.panel);
      if (status.phase === 'problem') {
        showingProblem.add(status.panel.id);
        bubble.show();
        bubble.showProblem(
          status.message ?? 'unknown error',
          () => showingProblem.delete(status.panel.id),
          status.severity ?? 'problem'
        );
      } else if (status.phase === 'generating') {
        // A new round supersedes the last one's complaint.
        showingProblem.delete(status.panel.id);
        outputFor(status.panel, status.promptCellId)?.showThinking(
          status.tokensPerSecond,
          status.notes
        );
        markPending(status.panel, status.promptCellId, false);
        bubble.show();
        bubble.showGenerating(status.elapsedSeconds, meter(status));
      } else if (
        !proposals.pendingCount(status.panel) &&
        !showingProblem.has(status.panel.id)
      ) {
        bubble.hide();
      }
      if (status.phase === 'ended' && !proposals.pendingCount(status.panel)) {
        clearOutput(status.panel);
      }
    });

    proposals.changed.connect((_s, panel) => render(panel));
    tracker.currentChanged.connect((_t, panel) => {
      if (panel) {
        render(panel);
      }
    });
  }
};

export default reviewUIPlugin;
