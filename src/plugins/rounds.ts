import type { JupyterFrontEndPlugin } from '@jupyterlab/application';
import {
  INotebookCellExecutor,
  INotebookTracker,
  type NotebookPanel
} from '@jupyterlab/notebook';
import type { CodeCell } from '@jupyterlab/cells';
import { UUID } from '@lumino/coreutils';

import { Signal } from '@lumino/signaling';

import { IVybeAgent, type IAgentFrame } from './agentClient';
import { IVybeSettings, IVybeRounds, type IRoundStatus } from '../tokens';
import { IVybeProposals } from './proposals';
import { setRounds } from '../roundRegistry';
import {
  OperationLog,
  convertPromptToMarkdown,
  findCellModel,
  insertCellsBelow,
  restorePromptForReview,
  revertRound,
  streamIntoCell,
  type ICellSpec
} from '../notebook/mutations';
import {
  isPromptCell,
  promptText,
  toMarkdownSource
} from '../notebook/promptCell';
import { buildSnapshot, estimateTokens } from '../notebook/snapshot';
import { runIntrospection } from '../introspect';
import { normaliseSource } from '../ui/diff';

/**
 * Room kept back from the context window for the preamble, the conversation so
 * far, and the reply. The notebook is the only part the ladder can trim, so it
 * is budgeted against what is left rather than against the whole window.
 */
const RESERVE_TOKENS = 4096;

/**
 * Focus the active cell's own node.
 *
 * Keybindings scoped to a cell only fire when that cell is the focused
 * element; focusing the notebook makes them silently inert.
 */
export function focusActiveCell(panel: NotebookPanel): void {
  const cell = panel.content.activeCell;
  if (cell) {
    cell.node.focus();
  }
}

/**
 * Put the text cursor at the end of the active cell, ready to keep typing.
 *
 * Deferred, because the caller has just changed the cell's type: the widget
 * carrying the editor is built on a later frame, and `editor` is null until
 * then. `ready` resolves once it exists.
 */
export function placeCursorAtEnd(panel: NotebookPanel): void {
  const cell = panel.content.activeCell;
  if (!cell) {
    return;
  }
  void cell.ready.then(() => {
    const editor = cell.editor;
    if (!editor || cell.isDisposed) {
      return;
    }
    const text = cell.model.sharedModel.getSource();
    editor.setCursorPosition(
      editor.getPositionAt(text.length) ?? { line: 0, column: 0 }
    );
    editor.focus();
  });
}

/** One prompt execution and everything it produces (docs/02). */
export class Round {
  readonly id = UUID.uuid4();
  readonly log = new OperationLog();
  readonly insertedIds: string[] = [];
  prose = '';
  /** Anything that went wrong, for the bubble as well as the settled cell. */
  readonly problems: string[] = [];
  /** Activity worth narrating in the cell's output area (docs/03). */
  readonly notes: string[] = [];
  /** Characters received, for the token-rate estimate. */
  receivedChars = 0;
  startedAt = Date.now();
  state: 'running' | 'applying' | 'reviewing' | 'settled' | 'cancelled' =
    'running';

  constructor(
    readonly panel: NotebookPanel,
    readonly promptCellId: string,
    readonly promptSource: string
  ) {}
}

/** Exported for tests; the plugin is the only production construction. */
export class Rounds implements IVybeRounds {
  private _active = new Map<string, Round>();
  private _seq = new Map<string, number>();
  private _statusChanged = new Signal<this, IRoundStatus>(this);
  /** Latest context measurement per panel, so a tick can repeat it. */
  private _context = new Map<
    string,
    { tokens: number; budget: number; dropped: string[] }
  >();
  /**
   * The last round to settle on each panel, kept so Cmd+Z can take the whole
   * thing back. Exactly one: undo reaches back one round, never a history.
   */
  private _lastSettled = new Map<string, Round>();

  get statusChanged() {
    return this._statusChanged;
  }

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
    // A new round replaces the last one's undo: Cmd+Z reaches back one round,
    // and reaching further would delete cells the user has since worked on.
    this._lastSettled.delete(panel.id);
    this._markRevertable(panel);
    const round = new Round(panel, cell.model.id, source);
    this._active.set(panel.id, round);
    // On the *content* node, which is the element carrying .jp-Notebook: the
    // keybinding selectors require both classes on one element, and while this
    // sat on the panel node they matched nothing and Y/N/Enter did nothing at
    // all. Only the buttons worked, which is why it went unnoticed.
    panel.content.node.classList.add('jv-round-active');

    // [*] with no kernel involvement. executionCount is never assigned, so the
    // cell contributes nothing to the notebook's execution history (docs/02).
    const codeModel = cell.model as CodeCell['model'];
    codeModel.executionState = 'running';
    options.onCellExecutionScheduled({ cell });

    // A local model can take half a minute to answer. Without a ticking
    // indicator the only feedback is [*], which reads the same at one second
    // and at forty (docs/03).
    const startedAt = Date.now();
    const tick = window.setInterval(() => {
      this._emit(round, 'generating', startedAt);
    }, 1000);
    this._emit(round, 'generating', startedAt);

    try {
      await this._drive(round, cell);
      return true;
    } finally {
      window.clearInterval(tick);
      this._emit(round, 'ended', startedAt);
      codeModel.executionState = 'idle';
      // A round that reached review is still live -- Proposals owns it until
      // the last verdict, and calls back to endReview. Anything else ends
      // here, including a throw out of _drive: a round left in _active would
      // silently refuse every later prompt in this notebook, which looks to
      // the user like pressing shift-enter doing nothing at all.
      if (round.state !== 'reviewing') {
        this._finish(round, false);
      }
      options.onCellExecuted({ cell, success: round.state !== 'cancelled' });
    }
  }

  private async _drive(round: Round, cell: CodeCell): Promise<void> {
    const panel = round.panel;
    const path = panel.context.path;
    const snap = buildSnapshot(panel, {
      includeOutputs: this.settings.includeOutputs,
      maxOutputChars: this.settings.maxOutputChars,
      // The notebook gets what is left after the preamble and the reply. The
      // ladder is a latency feature as much as a budget one: prefill cost
      // grows faster than linearly in prompt length, so an oversized notebook
      // is paid for on every single turn (docs/14 N4).
      budgetTokens: Math.max(0, this.settings.windowTokens - RESERVE_TOKENS),
      cellWindow: this.settings.cellWindow,
      promptCellId: round.promptCellId
    });
    this._context.set(panel.id, {
      tokens: snap.estimatedTokens,
      budget: Math.max(0, this.settings.windowTokens - RESERVE_TOKENS),
      dropped: snap.dropped
    });
    const seq = (this._seq.get(path) ?? -1) + 1;
    this._seq.set(path, seq);

    const pending: ICellSpec[] = [];
    let insertions = 0;
    let proposals = 0;
    /**
     * The tag currently arriving. A cell is created the moment its tag opens
     * and filled as the body streams, so the wait is spent watching the code
     * appear rather than watching nothing.
     */
    let streaming: { name: string; cellId: string; body: string } | null = null;

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
      if (frame.op === 'progress') {
        // Cumulative, from the server's view of the raw stream -- the only
        // place the whole reply is visible as it arrives.
        round.receivedChars = frame.chars ?? round.receivedChars;
        this._emit(round, 'generating', round.startedAt);
      } else if (frame.op === 'prose' && frame.delta) {
        round.prose += frame.delta;
      } else if (frame.op === 'proseReset') {
        // The server salvaged fenced code blocks from a reply that used no
        // tool tags; the prose it already streamed was that code, and must
        // not also land in the settled markdown cell (docs/13 R2).
        round.prose = '';
      } else if (frame.op === 'toolStart') {
        const name = frame.name!;
        const args = frame.args ?? {};
        if (name === 'add_cell' && insertions < this.settings.maxInsertions) {
          // Created empty, in its final position, before a byte of body has
          // arrived. Everything after this is filling it in.
          const ids = insertCellsBelow(
            panel,
            this._anchorId(round, round.insertedIds.length),
            [
              {
                type: args.type === 'markdown' ? 'markdown' : 'code',
                source: ''
              }
            ],
            round.log
          );
          if (ids.length) {
            round.insertedIds.push(...ids);
            streaming = { name, cellId: ids[0], body: '' };
          }
        } else if (
          name === 'propose_edit' &&
          proposals < this.settings.maxProposals
        ) {
          // An edit cannot stream into the notebook -- nothing is applied
          // without a verdict -- so it streams into the diff instead, which
          // shows the same progress without touching the user's cell.
          const staged = this.proposals.stage(panel, round.id, {
            cellId: args.cell ?? '',
            after: '',
            note: args.note,
            prefix: this.settings.prefix,
            streaming: true
          });
          if (staged) {
            streaming = { name, cellId: staged, body: '' };
          }
        }
      } else if (frame.op === 'toolDelta') {
        if (streaming) {
          streaming.body += frame.text ?? '';
          if (streaming.name === 'add_cell') {
            streamIntoCell(panel, streaming.cellId, streaming.body);
          } else {
            this.proposals.updateStaged(
              panel,
              streaming.cellId,
              streaming.body
            );
          }
        }
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
          // The closing tag is authoritative: whatever the stream guessed
          // about fences and indentation, this is the finished body.
          if (streaming && streaming.name === 'add_cell') {
            streamIntoCell(panel, streaming.cellId, args.body ?? '');
          } else {
            const ids = insertCellsBelow(
              panel,
              this._anchorId(round, round.insertedIds.length),
              [pending[pending.length - 1]],
              round.log
            );
            round.insertedIds.push(...ids);
          }
          streaming = null;
        } else if (name === 'propose_edit') {
          if (proposals >= this.settings.maxProposals) {
            continue;
          }
          const target = findCellModel(panel, args.cell ?? '');
          const current = target?.sharedModel.getSource() ?? '';
          if (
            target &&
            normaliseSource(current) === normaliseSource(args.body ?? '')
          ) {
            // An "edit" that rewrites a cell into itself proposes nothing, so
            // there is no diff to review -- but dropping it in silence made
            // the streamed preview vanish before the user could act, which
            // read as the extension losing their edit. Say what happened.
            const label = current.split('\n')[0].slice(0, 60);
            const why = args.note ? `: ${args.note}` : '';
            const said = `The agent proposed no change to \`${label}\`${why}`;
            round.prose += `\n\n_${said}_`;
            this._emitProblem(round, said, 'notice');
          }
          if (streaming && streaming.name === 'propose_edit') {
            this.proposals.finishStaged(
              panel,
              streaming.cellId,
              args.body ?? ''
            );
            proposals++;
          } else {
            const staged = this.proposals.stage(panel, round.id, {
              cellId: args.cell ?? '',
              after: args.body ?? '',
              note: args.note,
              prefix: this.settings.prefix
            });
            if (staged) {
              proposals++;
            }
          }
          streaming = null;
        } else if (frame.callId) {
          // Narrated because it is otherwise a silent pause: the agent has
          // stopped generating to ask the kernel something, and from outside
          // that is indistinguishable from having hung.
          const target = args.target ?? args.prefix ?? '';
          round.notes.push(`Used IPython tools to look up ${target || name}`);
          this._emit(round, 'generating', round.startedAt);
          const { ok, result } = await runIntrospection(
            panel,
            this.settings.kernelIntrospection,
            name,
            args
          );
          this.agent.toolResult(path, frame.callId, ok, result);
        }
      } else if (frame.op === 'assembledPrompt') {
        // prompts.showAssembled. Logged rather than shown: it is thousands of
        // tokens, and the console is where someone debugging is already
        // looking (docs/04).
        console.info(
          `[jupyter-vybe] assembled prompt for round ${round.id}:\n${frame.prompt}`
        );
      } else if (frame.op === 'salvaged') {
        // The model answered with a fenced code block instead of tool tags and
        // the server recovered cells from it. Say so: the cells are real, but
        // the user should know they came from a reply the protocol did not
        // fit, because that is when to check them harder (docs/13 R2).
        const salvaged =
          `the agent replied without tool tags; ${frame.count ?? 0} cell(s) ` +
          'were recovered from a code block';
        // Recorded as well as shown: the live output area belongs to the
        // round, and this is exactly the kind of thing worth knowing when
        // reading the notebook back a week later.
        round.prose += `\n\n_Note: ${salvaged}_`;
        this._emitProblem(round, salvaged, 'notice');
      } else if (frame.op === 'resync') {
        // The server has lost its picture of this notebook and wants
        // everything again. Unhandled, this frame ended the round in silence.
        round.problems.push('resync');
        this._emitProblem(
          round,
          'the agent lost track of the notebook; run the prompt again to resend it'
        );
      } else if (frame.op === 'retrying') {
        // The reply misused the protocol and the server is asking the model
        // to send it again. Everything the failed attempt produced goes: it
        // is about to be replaced, and leaving it would duplicate whatever
        // part of it was well-formed.
        revertRound(panel, round.log);
        round.insertedIds.length = 0;
        round.prose = '';
        this.proposals.clear(panel, round.id);
        insertions = 0;
        proposals = 0;
        streaming = null;
        round.notes.push('The agent misused a tool tag; asking it again.');
        this._emit(round, 'generating', round.startedAt);
      } else if (frame.op === 'protocolError') {
        // Shown and recorded, never written into the notebook. A complaint
        // about tag syntax in place of the code the user asked for is worse
        // than the malformed code would have been.
        round.problems.push(frame.message ?? 'unknown error');
        this._emitProblem(round, frame.message ?? 'unknown error');
      } else if (frame.op === 'error') {
        // A real failure -- the adapter died, the model errored -- is worth
        // keeping in the notebook, because the round produced nothing and the
        // cell has to say why.
        round.problems.push(frame.message ?? 'unknown error');
        round.prose += `\n\n_Agent error: ${frame.message}_`;
        this._emitProblem(round, frame.message ?? 'unknown error');
      }
    }

    if (round.state === 'cancelled') {
      return;
    }
    this._settle(round, cell);
  }

  private _emitProblem(
    round: Round,
    message: string,
    severity: 'problem' | 'notice' = 'problem'
  ): void {
    this._statusChanged.emit({
      panel: round.panel,
      promptCellId: round.promptCellId,
      phase: 'problem',
      elapsedSeconds: 0,
      tokensPerSecond: 0,
      notes: [...round.notes],
      contextTokens: 0,
      budgetTokens: 0,
      dropped: [],
      message,
      severity
    });
  }

  private _emit(
    round: Round,
    phase: 'generating' | 'ended',
    startedAt: number
  ): void {
    const measured = this._context.get(round.panel.id);
    const seconds = Math.max(0.001, (Date.now() - startedAt) / 1000);
    this._statusChanged.emit({
      panel: round.panel,
      promptCellId: round.promptCellId,
      phase,
      elapsedSeconds: Math.round(seconds),
      // Characters over seconds, converted with the same estimator the meter
      // uses. An estimate of an estimate, and honest about it.
      tokensPerSecond:
        estimateTokens('x'.repeat(round.receivedChars)) / seconds,
      notes: [...round.notes],
      contextTokens: measured?.tokens ?? 0,
      budgetTokens: measured?.budget ?? 0,
      dropped: measured?.dropped ?? []
    });
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

    if (this.proposals.pendingCount(panel, round.id) > 0) {
      round.state = 'reviewing';
      // The prompt cell stays a *code* cell holding the review summary until
      // every verdict is in. Converting it now would destroy the thing the
      // user is being asked to act on, and put the count somewhere they are
      // not looking (docs/03).
      this._focusPrompt(round);
      this.proposals.beginReview(panel, {
        id: round.id,
        log: round.log,
        promptCellId: round.promptCellId,
        endReview: () => this._completeRound(round)
      });
    } else {
      this._completeRound(round);
    }
    void cell;
  }

  /**
   * Everything that happens once no verdict is outstanding: the prompt
   * becomes its markdown record, and what the round inserted becomes the
   * selection so the user's next keystroke acts on the new code.
   */
  private _completeRound(round: Round): void {
    const panel = round.panel;
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
        panel.content.mode = 'command';
        // Focused, so the next keystroke -- very often Cmd+Z -- reaches our
        // cell-scoped bindings rather than falling through to core.
        focusActiveCell(panel);
      }
    } else {
      // Nothing was inserted: leave the user on the prompt cell itself.
      const idx = this._indexOf(panel, round.promptCellId);
      if (idx !== -1) {
        panel.content.activeCellIndex = idx;
        panel.content.mode = 'command';
        focusActiveCell(panel);
      }
    }
    this._finish(round, false);
  }

  /**
   * Put the user on the prompt cell, in command mode.
   *
   * Shift+Enter has already moved focus to the next cell by the time a round
   * settles, so without this the review summary appears on a cell nobody is
   * looking at and the keys that resolve it do nothing.
   */
  private _focusPrompt(round: Round): void {
    const panel = round.panel;
    const idx = this._indexOf(panel, round.promptCellId);
    if (idx === -1) {
      return;
    }
    panel.content.activeCellIndex = idx;
    panel.content.deselectAll();
    panel.content.mode = 'command';
    // The *cell* node, not the notebook node. Lumino resolves a keybinding
    // against the focused element, so a binding selecting `.jp-Cell.jv-...`
    // never fires while focus sits on `.jp-Notebook` -- Enter fell through to
    // core's enter-edit-mode instead of starting the review.
    focusActiveCell(panel);
  }

  /**
   * Undo a settled round (docs/02, the last row of the undo table).
   *
   * revertRound replays the operation log backwards, which removes inserted
   * cells and converts the prompt cell back to code with the user's original
   * text -- so the prompt is theirs again to edit and re-run, which is the
   * whole point of undoing a generation you did not want.
   */
  revertLast(panel: NotebookPanel): boolean {
    const round = this._lastSettled.get(panel.id);
    if (!round || round.log.operations.length === 0) {
      return false;
    }
    this._lastSettled.delete(panel.id);
    this.proposals.clear(panel, round.id);
    revertRound(panel, round.log);
    const idx = this._indexOf(panel, round.promptCellId);
    if (idx !== -1) {
      panel.content.activeCellIndex = idx;
      panel.content.deselectAll();
      // Edit mode with the cursor at the end of the prompt text: undoing a
      // round is almost always the prelude to rewording it, and landing at
      // the start of the line would mean pressing End before typing.
      panel.content.mode = 'edit';
      placeCursorAtEnd(panel);
    }
    this._markRevertable(panel);
    return true;
  }

  /**
   * Put a settled round back under review (docs/02).
   *
   * Undoing a verdict re-opens the review, and a review lives on a *code*
   * prompt cell: that is what carries the summary and what `Enter` and `N`
   * select on. Leaving it as the settled markdown would put the user back in
   * a review they cannot drive.
   */
  reopenForReview(panel: NotebookPanel): void {
    const round = this._lastSettled.get(panel.id);
    if (!round) {
      return;
    }
    if (restorePromptForReview(panel, round.promptCellId, round.promptSource)) {
      const idx = this._indexOf(panel, round.promptCellId);
      if (idx !== -1) {
        panel.content.activeCellIndex = idx;
        panel.content.mode = 'command';
        focusActiveCell(panel);
      }
    }
  }

  canRevertLast(panel: NotebookPanel): boolean {
    const round = this._lastSettled.get(panel.id);
    return !!round && round.log.operations.length > 0;
  }

  private _finish(round: Round, restorePromptCell: boolean): void {
    if (this._active.get(round.panel.id) !== round) {
      return; // already finished; _finish is called from several paths
    }
    round.panel.content.node.classList.remove('jv-round-active');
    this._active.delete(round.panel.id);
    if (restorePromptCell) {
      const idx = this._indexOf(round.panel, round.promptCellId);
      if (idx !== -1) {
        round.panel.content.activeCellIndex = idx;
        round.panel.content.mode = 'edit';
      }
    }
    round.state = round.state === 'cancelled' ? 'cancelled' : 'settled';
    if (round.state === 'settled' && round.log.operations.length) {
      this._lastSettled.set(round.panel.id, round);
    }
    this._markRevertable(round.panel);
  }

  /**
   * Advertise on the notebook node that there is a round to undo.
   *
   * The undo keybinding was scoped to the focused *cell*, which is only where
   * focus is if nothing has disturbed it -- and settling a round disturbs it
   * plenty: converting the prompt to markdown replaces that cell's widget, and
   * the browser drops focus to the body along with it. Cmd+Z then went to
   * core, which had nothing of ours to undo, and the round stood. A class on
   * the notebook gives the binding a target that survives the churn, and being
   * strictly more specific than core's selector it wins the tie outright
   * rather than by registration order.
   */
  private _markRevertable(panel: NotebookPanel): void {
    panel.content.node.classList.toggle(
      'jv-can-revert',
      this.canRevertLast(panel)
    );
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

    /*
     * Warm the agent when a notebook is opened, not when the first prompt is
     * run. A local model costs about three seconds to load and 0.6s once
     * loaded (docs/08), and paying that on the first prompt is precisely what
     * makes a first prompt look like nothing happened.
     *
     * Opening the notebook is the right moment because it is the last one
     * before the user could possibly ask for anything, and because it is
     * already a moment they expect to wait a beat. Nothing is warmed when
     * vybe is switched off: loading a model for someone who has disabled the
     * extension would be pure imposition.
     */
    const warmed = new Set<string>();
    const warm = (panel: NotebookPanel | null) => {
      if (!panel || !settings.enabled) {
        return;
      }
      const path = panel.context.path;
      if (warmed.has(path)) {
        return;
      }
      warmed.add(path);
      agent.warm(path);
    };
    tracker.widgetAdded.connect((_t, panel) => warm(panel));

    return rounds;
  }
};

export default roundsPlugin;
