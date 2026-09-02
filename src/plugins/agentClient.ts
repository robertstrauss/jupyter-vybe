import type { JupyterFrontEndPlugin } from '@jupyterlab/application';
import { ServerConnection } from '@jupyterlab/services';
import { URLExt } from '@jupyterlab/coreutils';
import { Token } from '@lumino/coreutils';

import { IVybeSettings } from '../tokens';

export interface IAgentFrame {
  op: string;
  roundId?: string;
  state?: string;
  delta?: string;
  name?: string;
  args?: Record<string, string>;
  callId?: string;
  message?: string;
  stopReason?: string;
  count?: number;
  promptTokens?: number;
  completionTokens?: number;
  /** prompts.showAssembled: the prompt the agent was actually given. */
  prompt?: string;
  /** `progress`: characters received so far, cumulative. */
  chars?: number;
  /** `toolDelta`: more of a tag body, cleaned as far as is safe so far. */
  text?: string;
}

export interface IPromptRequest {
  roundId: string;
  prompt: string;
  promptCellId: string;
  snapshot?: string;
  diff?: string;
  snapshotSeq: number;
  kernelInfo: { name: string; language: string; version: string };
  globalPrompt: string;
  limits: { maxInsertions: number; maxProposals: number; maxToolTurns: number };
}

export interface IVybeAgent {
  /** One session per notebook path. Frames arrive on the returned async iterator. */
  send(notebookPath: string, req: IPromptRequest): AsyncIterable<IAgentFrame>;
  cancel(notebookPath: string): void;
  toolResult(
    notebookPath: string,
    callId: string,
    ok: boolean,
    result: string
  ): void;
  dispose(notebookPath: string): void;
}
export const IVybeAgent = new Token<IVybeAgent>('jupyter-vybe:IVybeAgent');

/** A WebSocket wrapped so frames can be consumed with `for await`. */
class AgentChannel {
  private _ws: WebSocket | null = null;
  private _queue: IAgentFrame[] = [];
  private _wake: (() => void) | null = null;
  private _closed = false;

  constructor(
    private readonly url: string,
    private readonly hello: Record<string, unknown>
  ) {}

  private async ensure(): Promise<WebSocket> {
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      return this._ws;
    }
    const ws = new WebSocket(this.url);
    this._ws = ws;
    await new Promise<void>((resolve, reject) => {
      ws.onopen = () => resolve();
      ws.onerror = () =>
        reject(new Error(`cannot reach the Vybe agent at ${this.url}`));
    });
    ws.onmessage = ev => {
      this._queue.push(JSON.parse(ev.data as string));
      this._wake?.();
    };
    ws.onclose = () => {
      // A dropped socket mid-round is a cancel. We never auto-retry a prompt:
      // silent duplicate generation is worse than a visible failure (docs/06).
      this._closed = true;
      this._queue.push({ op: 'done', stopReason: 'error' });
      this._wake?.();
    };
    ws.send(JSON.stringify(this.hello));
    return ws;
  }

  async *run(req: IPromptRequest): AsyncIterable<IAgentFrame> {
    const ws = await this.ensure();
    this._closed = false;
    ws.send(JSON.stringify({ op: 'prompt', ...req }));
    while (true) {
      while (this._queue.length) {
        const frame = this._queue.shift()!;
        yield frame;
        if (frame.op === 'done') {
          return;
        }
      }
      if (this._closed) {
        return;
      }
      await new Promise<void>(resolve => (this._wake = resolve));
      this._wake = null;
    }
  }

  post(payload: Record<string, unknown>): void {
    if (this._ws?.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify(payload));
    }
  }

  dispose(): void {
    this._ws?.close();
    this._ws = null;
  }
}

class VybeAgent implements IVybeAgent {
  private _channels = new Map<string, AgentChannel>();

  constructor(private readonly settings: IVybeSettings) {}

  private channel(notebookPath: string): AgentChannel {
    let ch = this._channels.get(notebookPath);
    if (!ch) {
      ch = new AgentChannel(this.url(), {
        op: 'hello',
        notebookPath,
        agentId: this.settings.agentId,
        persistent: this.settings.persistent,
        options: {
          // The sidecar prompt record is written server-side as the round
          // streams (docs/05, D12); this is the user's switch for it.
          historyPersist: this.settings.historyPersist,
          historyLocation: this.settings.historyLocation,
          // Ask the server to echo the assembled prompt back, so the user can
          // see what the agent was actually given (docs/04).
          echoPrompt: this.settings.showAssembled,
          ...this.settings.agentOptions
        }
      });
      this._channels.set(notebookPath, ch);
    }
    return ch;
  }

  private url(): string {
    if (this.settings.agentHost === 'client') {
      return `${this.settings.sidecarUrl.replace(/^http/, 'ws')}/ws`;
    }
    const base = ServerConnection.makeSettings().wsUrl;
    return URLExt.join(base, 'jupyter-vybe', 'ws');
  }

  send(notebookPath: string, req: IPromptRequest): AsyncIterable<IAgentFrame> {
    return this.channel(notebookPath).run(req);
  }

  cancel(notebookPath: string): void {
    this._channels.get(notebookPath)?.post({ op: 'cancel' });
  }

  toolResult(
    notebookPath: string,
    callId: string,
    ok: boolean,
    result: string
  ): void {
    this._channels
      .get(notebookPath)
      ?.post({ op: 'toolResult', callId, ok, result });
  }

  dispose(notebookPath: string): void {
    this._channels.get(notebookPath)?.dispose();
    this._channels.delete(notebookPath);
  }
}

export const agentPlugin: JupyterFrontEndPlugin<IVybeAgent> = {
  id: 'jupyter-vybe:agent',
  description: 'WebSocket client for the Vybe agent channel.',
  autoStart: true,
  provides: IVybeAgent,
  requires: [IVybeSettings],
  activate: (_app, settings: IVybeSettings): IVybeAgent =>
    new VybeAgent(settings)
};

export default agentPlugin;
