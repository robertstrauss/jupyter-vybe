import type {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { INotebookTracker, type NotebookPanel } from '@jupyterlab/notebook';
import { Widget } from '@lumino/widgets';

import { IVybeProposals, type IProposal } from './proposals';
import { IVybeSettings } from '../tokens';
import { CommandIDs } from './commands';
import { DiffOverlay, ReviewControls, StatusBubble } from '../ui/DiffOverlay';

/** Renders the diff overlays, the per-cell controls, and the status bubble. */
export const reviewUIPlugin: JupyterFrontEndPlugin<void> = {
  id: 'jupyter-vybe:review-ui',
  description: 'Diff overlays, accept/reject controls, and the status bubble.',
  autoStart: true,
  requires: [INotebookTracker, IVybeProposals, IVybeSettings],
  activate: (
    app: JupyterFrontEnd,
    tracker: INotebookTracker,
    proposals: IVybeProposals,
    settings: IVybeSettings
  ): void => {
    const bubbles = new Map<string, StatusBubble>();
    const overlays = new Map<string, Widget[]>();

    const bubbleFor = (panel: NotebookPanel): StatusBubble => {
      let b = bubbles.get(panel.id);
      if (!b) {
        b = new StatusBubble(
          () => app.commands.execute(CommandIDs.gotoFirst),
          () => app.commands.execute(CommandIDs.rejectAll)
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
        bubble.hide();
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
        const overlay = new DiffOverlay(proposal.hunks, proposal.note);
        Widget.attach(overlay, cellWidget.node);
        created.push(overlay);
      }
      overlays.set(panel.id, created);
    };

    const attachControls = (host: HTMLElement, proposal: IProposal): Widget => {
      const yn = settings.acceptKey !== 'enter-backspace';
      const controls = new ReviewControls(
        () => app.commands.execute(CommandIDs.accept),
        () => app.commands.execute(CommandIDs.reject),
        yn ? 'Y' : '⏎',
        yn ? 'N' : '⌫'
      );
      void proposal;
      Widget.attach(controls, host);
      return controls;
    };

    proposals.changed.connect((_s, panel) => render(panel));
    tracker.currentChanged.connect((_t, panel) => {
      if (panel) {
        render(panel);
      }
    });
  }
};

export default reviewUIPlugin;
