import type {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { INotebookTracker, type NotebookPanel } from '@jupyterlab/notebook';
import { Widget } from '@lumino/widgets';

import { IVybeProposals, type IProposal } from './proposals';
import { IVybeRounds, IVybeSettings, type IRoundStatus } from '../tokens';
import { CommandIDs } from './commands';
import { DiffOverlay, ReviewControls, ReviewCounter } from '../ui/DiffOverlay';
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
    const counters = new Map<string, ReviewCounter>();
    const overlays = new Map<string, Widget[]>();
    /** One prompt-cell output panel per notebook, moved between cells. */
    const outputs = new Map<string, { widget: PromptOutput; cellId: string }>();
    /**
     * Panels currently showing a problem. Without this the render that
     * follows a failed round hides the message before it can be read: there
     * are no pending proposals, so the ordinary path hides the bubble.
     */
    const showingProblem = new Set<string>();

    /**
     * Created on first use, not on first render: a notebook that has never
     * run a prompt should carry none of our chrome at all.
     */
    const counterFor = (panel: NotebookPanel): ReviewCounter => {
      let counter = counters.get(panel.id);
      if (!counter) {
        counter = new ReviewCounter(() =>
          app.commands.execute(CommandIDs.rejectAll)
        );
        counter.addClass('jv-counter-host');
        Widget.attach(counter, panel.node);
        counters.set(panel.id, counter);
      }
      return counter;
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
      // Reused only if it is still attached to *this* cell's node. Converting
      // the prompt to markdown replaces the cell widget, taking our attached
      // node with it -- so a cached widget can be detached and invisible while
      // still matching by id.
      if (
        existing &&
        existing.cellId === promptCellId &&
        cellWidget.node.contains(existing.widget.node)
      ) {
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

      if (!pending.length) {
        counters.get(panel.id)?.hide();
        if (!showingProblem.has(panel.id)) {
          clearOutput(panel);
        }
        return;
      }
      // The counter exists for one failure mode: walking away with edits
      // still open. So it appears exactly while that is possible.
      const counter = counterFor(panel);
      counter.show();
      counter.showRemaining(pending.length);

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
        cellWidget.node.classList.toggle(
          'jv-proposal-streaming',
          !!proposal.streaming
        );
        if (active && proposal.id === active.id) {
          cellWidget.node.classList.add('jv-proposal-active');
          created.push(attachControls(cellWidget.node, proposal));
        }
        // Hide the live editor and show the diff. The model is untouched.
        cellWidget.node
          .querySelector('.jp-Cell-inputWrapper')
          ?.classList.add('jv-input-hidden');
        const label = proposal.streaming
          ? `writing an edit to cell ${i + 1}…`
          : proposal.targetLabel
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
      if (status.phase === 'problem') {
        // Shown while the round is live. Afterwards the settled markdown cell
        // carries the text -- which is the durable home for it, because
        // converting the prompt replaces the cell's DOM and rendering the
        // markdown replaces it again, taking any attached widget with it.
        showingProblem.add(status.panel.id);
        outputFor(status.panel, status.promptCellId)?.showProblem(
          status.message ?? 'unknown error',
          status.severity ?? 'problem'
        );
      } else if (status.phase === 'generating') {
        // A new round supersedes the last one's complaint.
        showingProblem.delete(status.panel.id);
        // The context meter goes in with the activity lines: it belonged to
        // the bubble, and the bubble is now only a review counter.
        const measured = meter(status);
        outputFor(status.panel, status.promptCellId)?.showThinking(
          status.tokensPerSecond,
          measured ? [...status.notes, measured] : status.notes,
          status.elapsedSeconds
        );
        markPending(status.panel, status.promptCellId, false);
      } else if (
        status.phase === 'ended' &&
        !proposals.pendingCount(status.panel)
      ) {
        showingProblem.delete(status.panel.id);
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
