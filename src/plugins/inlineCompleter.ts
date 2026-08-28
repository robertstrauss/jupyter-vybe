import type {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import {
  ICompletionProviderManager,
  type CompletionHandler,
  type IInlineCompletionContext,
  type IInlineCompletionItem,
  type IInlineCompletionList,
  type IInlineCompletionProvider
} from '@jupyterlab/completer';
import { ServerConnection } from '@jupyterlab/services';
import { URLExt } from '@jupyterlab/coreutils';

import { IVybeSettings } from '../tokens';

/**
 * Inline completion — ghost text from the same local model (decision D5).
 *
 * Held back from v1.0 on purpose: it is a different subsystem with a different
 * latency budget. A prompt cell may take thirty seconds and that is fine; a
 * completion that arrives after the next keystroke is worse than none, because
 * it flickers something stale in front of someone who has moved on.
 *
 * Everything here follows from that. Requests are debounced, every request
 * supersedes the one before it, and a request whose answer arrives after it
 * has been superseded is dropped rather than shown.
 */

const ENDPOINT = ['jupyter-vybe', 'complete'];

class VybeInlineProvider implements IInlineCompletionProvider {
  readonly identifier = 'jupyter-vybe:inline';
  readonly name = 'Vybe';

  private _generation = 0;

  constructor(private readonly settings: IVybeSettings) {}

  async fetch(
    request: CompletionHandler.IRequest,
    context: IInlineCompletionContext
  ): Promise<IInlineCompletionList<IInlineCompletionItem>> {
    if (!this.settings.enabled || !this.settings.completionEnabled) {
      return { items: [] };
    }
    const text = request.text ?? '';
    const offset = request.offset ?? text.length;
    const prefix = text.slice(0, offset);
    const suffix = text.slice(offset);

    // Typing invalidates whatever is in flight. The generation counter is what
    // makes a late answer harmless rather than wrong.
    const generation = ++this._generation;

    if (context.triggerKind !== 0 /* Invoke */) {
      await new Promise(resolve =>
        window.setTimeout(resolve, this.settings.completionDebounceMs)
      );
      if (generation !== this._generation) {
        return { items: [] };
      }
    }
    // A completion in the middle of a word is usually noise; the editor's own
    // completer is better at that, and this one costs a model call.
    if (/[A-Za-z0-9_]$/.test(prefix) && /^[A-Za-z0-9_]/.test(suffix)) {
      return { items: [] };
    }

    try {
      const items = await this._request(prefix, suffix);
      return generation === this._generation ? { items } : { items: [] };
    } catch (err) {
      // Never surface a completion failure as an error in the editor: the user
      // did not ask for this, and a red box for a suggestion is a bad trade.
      console.debug('[jupyter-vybe] completion failed', err);
      return { items: [] };
    }
  }

  private async _request(
    prefix: string,
    suffix: string
  ): Promise<IInlineCompletionItem[]> {
    const settings = ServerConnection.makeSettings();
    const response = await ServerConnection.makeRequest(
      URLExt.join(settings.baseUrl, ...ENDPOINT),
      {
        method: 'POST',
        body: JSON.stringify({
          prefix,
          suffix,
          agentId: this.settings.agentId,
          options: this.settings.agentOptions,
          multiline: this.settings.completionMultiline,
          maxTokens: this.settings.completionMaxTokens,
          timeoutSeconds: this.settings.completionTimeoutSeconds
        })
      },
      settings
    );
    if (!response.ok) {
      return [];
    }
    const payload = (await response.json()) as {
      items?: { insertText: string }[];
    };
    return payload.items ?? [];
  }
}

export const inlineCompleterPlugin: JupyterFrontEndPlugin<void> = {
  id: 'jupyter-vybe:inline-completer',
  description: 'Ghost-text completions from the configured agent.',
  autoStart: true,
  requires: [ICompletionProviderManager, IVybeSettings],
  activate: (
    _app: JupyterFrontEnd,
    manager: ICompletionProviderManager,
    settings: IVybeSettings
  ): void => {
    manager.registerInlineProvider(new VybeInlineProvider(settings));
  }
};

export default inlineCompleterPlugin;
