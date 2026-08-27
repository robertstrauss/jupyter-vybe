import { Token } from '@lumino/coreutils';
import type { NotebookPanel } from '@jupyterlab/notebook';
import type { INotebookCellExecutor } from '@jupyterlab/notebook';
import type { ISignal } from '@lumino/signaling';

/** Live settings, re-read on every change (docs/04). */
export interface IVybeSettings {
  readonly enabled: boolean;
  readonly prefix: string;
  readonly agentId: string;
  readonly agentHost: 'server' | 'client';
  readonly sidecarUrl: string;
  readonly persistent: boolean;
  readonly globalPrompt: string;
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
  readonly changed: ISignal<IVybeSettings, void>;
}
export const IVybeSettings = new Token<IVybeSettings>(
  'jupyter-vybe:IVybeSettings'
);

/** The round state machine; the cell executor hands prompt cells to it. */
export interface IVybeRounds {
  begin(options: INotebookCellExecutor.IRunCellOptions): Promise<boolean>;
  cancel(panel: NotebookPanel): void;
  readonly hasActiveRound: (panel: NotebookPanel) => boolean;
}
export const IVybeRounds = new Token<IVybeRounds>('jupyter-vybe:IVybeRounds');
