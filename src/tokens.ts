import { Token } from '@lumino/coreutils';
import type { NotebookPanel } from '@jupyterlab/notebook';
import type { INotebookCellExecutor } from '@jupyterlab/notebook';
import type { ISignal } from '@lumino/signaling';

/** Live settings, re-read on every change (docs/04). */
export interface IVybeSettings {
  readonly enabled: boolean;
  readonly prefix: string;
  readonly agentId: string;
  /** Per-adapter configuration, passed through to the server as-is. */
  readonly agentOptions: Record<string, string>;
  readonly persistent: boolean;
  readonly globalPrompt: string;
  readonly showAssembled: boolean;
  readonly maxInsertions: number;
  readonly maxProposals: number;
  readonly maxToolTurns: number;
  readonly kernelIntrospection: 'off' | 'inspect-only' | 'inspect+complete';
  readonly acceptKey: 'yn' | 'enter-backspace';
  readonly mergeCommentStyle: 'auto' | 'hash' | 'slashes' | 'none';
  readonly autoScroll: boolean;
  readonly includeOutputs: boolean;
  readonly maxOutputChars: number;
  readonly cellWindow: number;
  readonly windowTokens: number;
  readonly historyPersist: boolean;
  readonly historyMaxEntries: number;
  readonly historyLocation: 'user' | 'adjacent';
  readonly completionEnabled: boolean;
  readonly completionTimeoutSeconds: number;
  readonly completionMaxLines: number;
  readonly completionTriggerKey: string;
  readonly completionAgentId: string;
  readonly changed: ISignal<IVybeSettings, void>;
}
export const IVybeSettings = new Token<IVybeSettings>(
  'jupyter-vybe:IVybeSettings'
);

/** What the status bubble needs to know while a round is in flight. */
export interface IRoundStatus {
  panel: NotebookPanel;
  /** The cell the round belongs to, so its progress can be shown there. */
  promptCellId: string;
  /** 'problem' is not terminal: the round carries on and settles. */
  phase: 'generating' | 'ended' | 'problem';
  elapsedSeconds: number;
  /** Estimated from characters received; see PromptOutput.showThinking. */
  tokensPerSecond: number;
  /** Activity worth narrating, e.g. a kernel lookup. Newest last. */
  notes: string[];
  /** Estimated tokens in the notebook portion of the prompt (docs/09). */
  contextTokens: number;
  /** Budget the ladder was working against; 0 when truncation is off. */
  budgetTokens: number;
  /** What the ladder had to drop to fit, in the order it dropped it. */
  dropped: string[];
  /** Set on 'problem': what went wrong, in the words the user should see. */
  message?: string;
  /** 'notice' is information the user should have, not a failure. */
  severity?: 'problem' | 'notice';
}

/** The round state machine; the cell executor hands prompt cells to it. */
export interface IVybeRounds {
  begin(options: INotebookCellExecutor.IRunCellOptions): Promise<boolean>;
  cancel(panel: NotebookPanel): void;
  /**
   * Undo a round that has already settled: remove what it inserted, discard
   * its proposals, and give the prompt cell back as an editable code cell.
   * Returns false when there is nothing to take back, so the caller can fall
   * through to core undo.
   */
  revertLast(panel: NotebookPanel): boolean;
  canRevertLast(panel: NotebookPanel): boolean;
  /** Undo re-opened a review: give the prompt cell back as code. */
  reopenForReview(panel: NotebookPanel): void;
  readonly hasActiveRound: (panel: NotebookPanel) => boolean;
  readonly statusChanged: ISignal<IVybeRounds, IRoundStatus>;
}
export const IVybeRounds = new Token<IVybeRounds>('jupyter-vybe:IVybeRounds');
