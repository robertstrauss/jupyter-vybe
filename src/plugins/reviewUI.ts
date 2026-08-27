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
      bubble.show();
      bubble.showPending(pending.length);

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
      if (status.phase === 'generating') {
        bubble.show();
        bubble.showGenerating(status.elapsedSeconds, meter(status));
      } else if (!proposals.pendingCount(status.panel)) {
        bubble.hide();
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
