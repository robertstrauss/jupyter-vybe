import type { JupyterFrontEndPlugin } from '@jupyterlab/application';
import {
  INotebookCellExecutor,
  INotebookTracker,
  type NotebookPanel
} from '@jupyterlab/notebook';
import type { CodeCell } from '@jupyterlab/cells';
import { UUID } from '@lumino/coreutils';

import { IVybeAgent, type IAgentFrame } from './agentClient';
import { IVybeSettings, IVybeRounds } from '../tokens';
import { IVybeProposals } from './proposals';
import { setRounds } from '../roundRegistry';
import {
  OperationLog,
  convertPromptToMarkdown,
  insertCellsBelow,
  revertRound,
  type ICellSpec
} from '../notebook/mutations';
import {
  isPromptCell,
  promptText,
  toMarkdownSource
} from '../notebook/promptCell';
import { buildSnapshot } from '../notebook/snapshot';
import { runIntrospection } from '../introspect';

/** One prompt execution and everything it produces (docs/02). */
export class Round {
  readonly id = UUID.uuid4();
  readonly log = new OperationLog();
  readonly insertedIds: string[] = [];
  prose = '';
  state: 'running' | 'applying' | 'reviewing' | 'settled' | 'cancelled' =
    'running';

  constructor(
    readonly panel: NotebookPanel,
    readonly promptCellId: string,
    readonly promptSource: string
  ) {}
}

class Rounds implements IVybeRounds {
  private _active = new Map<string, Round>();
  private _seq = new Map<string, number>();

  constructor(
    private readonly tracker: INotebookTracker,
    private readonly agent: IVybeAgent,
    private readonly settings: IVybeSettings,
    private readonly proposals: IVybeProposals
  ) {}

  hasActiveRound = (panel: NotebookPanel): boolean =>
    this._active.has(panel.id);

  cancel(panel: NotebookPanel): void {
    const round = this._active.get(panel.id);
    if (!round) {
      return;
    }
    this.agent.cancel(panel.context.path);
    round.state = 'cancelled';
    this.proposals.clear(panel, round.id);
    revertRound(panel, round.log);
    this._finish(round, /* restorePromptCell */ true);
  }

  async begin(
    options: INotebookCellExecutor.IRunCellOptions
  ): Promise<boolean> {
    const cell = options.cell as CodeCell;
    const panel = this._panelFor(cell);
    if (!panel) {
      return false;
    }
    if (this._active.has(panel.id)) {
      // Serialise: a second prompt while one is in flight is refused rather
      // than interleaved (docs/12 D10).
      return false;
    }

    const source = cell.model.sharedModel.getSource();
    const round = new Round(panel, cell.model.id, source);
    this._active.set(panel.id, round);
    panel.node.classList.add('jv-round-active');

    // [*] with no kernel involvement. executionCount is never assigned, so the
    // cell contributes nothing to the notebook's execution history (docs/02).
    const codeModel = cell.model as CodeCell['model'];
    codeModel.executionState = 'running';
    options.onCellExecutionScheduled({ cell });

    try {
      await this._drive(round, cell);
      return true;
    } finally {
      codeModel.executionState = 'idle';
      options.onCellExecuted({ cell, success: round.state !== 'cancelled' });
    }
  }

  private async _drive(round: Round, cell: CodeCell): Promise<void> {
    const panel = round.panel;
    const path = panel.context.path;
    const snap = buildSnapshot(panel, {
      includeOutputs: this.settings.includeOutputs,
      maxOutputChars: this.settings.maxOutputChars
    });
    const seq = (this._seq.get(path) ?? -1) + 1;
    this._seq.set(path, seq);

    const pending: ICellSpec[] = [];
    let insertions = 0;
    let proposals = 0;

    const frames = this.agent.send(path, {
      roundId: round.id,
      prompt: promptText(round.promptSource, this.settings.prefix),
      promptCellId: round.promptCellId,
      snapshot: snap.json,
      snapshotSeq: seq,
      kernelInfo: snap.kernel,
      globalPrompt: this.settings.globalPrompt,
      limits: {
        maxInsertions: this.settings.maxInsertions,
        maxProposals: this.settings.maxProposals,
        maxToolTurns: this.settings.maxToolTurns
      }
    });

    for await (const frame of frames as AsyncIterable<IAgentFrame>) {
      if (round.state === 'cancelled') {
        break;
      }
      if (frame.op === 'prose' && frame.delta) {
        round.prose += frame.delta;
      } else if (frame.op === 'tool') {
        const name = frame.name!;
        const args = frame.args ?? {};
        if (name === 'add_cell') {
          if (insertions >= this.settings.maxInsertions) {
            continue;
          }
          insertions++;
          pending.push({
            type: args.type === 'markdown' ? 'markdown' : 'code',
            source: args.body ?? ''
          });
          // Stream cells in as they arrive; on a 7B model this is the
          // difference between "instant" and "did it hang?".
          const ids = insertCellsBelow(
            panel,
            this._anchorId(round, pending.length - 1),
            [pending[pending.length - 1]],
            round.log
          );
          round.insertedIds.push(...ids);
        } else if (name === 'propose_edit') {
          if (proposals >= this.settings.maxProposals) {
            continue;
          }
          const staged = this.proposals.stage(panel, round.id, {
            cellId: args.cell ?? '',
            after: args.body ?? '',
            note: args.note,
            prefix: this.settings.prefix
          });
          if (staged) {
            proposals++;
          }
        } else if (frame.callId) {
          const { ok, result } = await runIntrospection(
            panel,
            this.settings.kernelIntrospection,
            name,
            args
          );
          this.agent.toolResult(path, frame.callId, ok, result);
        }
      } else if (frame.op === 'error') {
        round.prose += `\n\n_Agent error: ${frame.message}_`;
      }
    }

    if (round.state === 'cancelled') {
      return;
    }
    this._settle(round, cell);
  }

  /** Insertions anchor to the prompt cell, or to the previous insertion. */
  private _anchorId(round: Round, index: number): string {
    return index === 0 || round.insertedIds.length === 0
      ? round.promptCellId
      : round.insertedIds[round.insertedIds.length - 1];
  }

  private _settle(round: Round, cell: CodeCell): void {
    const panel = round.panel;
    round.state = 'applying';

    convertPromptToMarkdown(
      panel,
      round.promptCellId,
      toMarkdownSource(
        promptText(round.promptSource, this.settings.prefix),
        round.prose,
        this.settings.prefix
      ),
      round.log
    );

    // The whole group of inserted cells becomes the selection (docs/03).
    if (round.insertedIds.length) {
      const first = this._indexOf(panel, round.insertedIds[0]);
      const last = this._indexOf(
        panel,
        round.insertedIds[round.insertedIds.length - 1]
      );
      if (first !== -1 && last !== -1) {
        panel.content.activeCellIndex = first;
        panel.content.extendContiguousSelectionTo(last);
      }
    }

    if (this.proposals.pendingCount(panel, round.id) > 0) {
      round.state = 'reviewing';
      this.proposals.beginReview(panel, round);
    } else {
      this._finish(round, false);
    }
    void cell;
  }

  private _finish(round: Round, restorePromptCell: boolean): void {
    round.panel.node.classList.remove('jv-round-active');
    this._active.delete(round.panel.id);
    if (restorePromptCell) {
      const idx = this._indexOf(round.panel, round.promptCellId);
      if (idx !== -1) {
        round.panel.content.activeCellIndex = idx;
        round.panel.content.mode = 'edit';
      }
    }
    round.state = round.state === 'cancelled' ? 'cancelled' : 'settled';
  }

  private _indexOf(panel: NotebookPanel, cellId: string): number {
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

  private _panelFor(cell: CodeCell): NotebookPanel | null {
    const found = this.tracker.find(p => {
      const cells = p.content.model?.cells;
      if (!cells) {
        return false;
      }
      for (let i = 0; i < cells.length; i++) {
        if (cells.get(i).id === cell.model.id) {
          return true;
        }
      }
      return false;
    });
    return found ?? this.tracker.currentWidget ?? null;
  }
}

export const roundsPlugin: JupyterFrontEndPlugin<IVybeRounds> = {
  id: 'jupyter-vybe:rounds',
  description: 'Drives one prompt execution from trigger to settled.',
  autoStart: true,
  provides: IVybeRounds,
  requires: [INotebookTracker, IVybeAgent, IVybeSettings, IVybeProposals],
  activate: (
    _app,
    tracker: INotebookTracker,
    agent: IVybeAgent,
    settings: IVybeSettings,
    proposals: IVybeProposals
  ): IVybeRounds => {
    void isPromptCell;
    const rounds = new Rounds(tracker, agent, settings, proposals);
    setRounds(rounds);
    return rounds;
  }
};

export default roundsPlugin;
