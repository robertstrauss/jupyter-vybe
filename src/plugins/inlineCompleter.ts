import type {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { IEditorExtensionRegistry } from '@jupyterlab/codemirror';
import { EditorExtensionRegistry } from '@jupyterlab/codemirror';
import { INotebookTracker, type NotebookPanel } from '@jupyterlab/notebook';
import type { EditorView } from '@codemirror/view';

import { IVybeSettings } from '../tokens';
import {
  currentGhost,
  dismissGhost,
  ghostExtension,
  showGhost
} from '../completion/ghost';
import { notebookAsScript } from '../completion/notebookScript';
import { predict } from '../completion/predict';
import { complete, inspect } from '../introspect';

/**
 * Ghost text, on demand (decision D5).
 *
 * The first version of this was a JupyterLab inline completion provider that
 * fired on a debounce as you typed. It was withdrawn: a suggestion costs a
 * model call, and asking for one every time the user pauses spends their
 * machine on work they did not request and mostly do not want. Nothing is
 * requested now until the trigger key is pressed.
 *
 * What that buys, beyond the calls not made:
 *
 *   - pressing the trigger again *extends* the suggestion rather than
 *     replacing it, so a prediction grows a line or two at a time and the
 *     user decides how far it goes;
 *   - the request can be expensive, because it was asked for -- so it carries
 *     the whole notebook as a script, and what the kernel knows about the
 *     names on the current line;
 *   - Tab accepts and Backspace rejects, which are only ours to bind because
 *     the ghost text is ours (see completion/ghost.ts).
 */

const HINT_LIMIT = 800;

class Predictor {
  /** Cancels the request in flight; there is only ever one. */
  private _abort: AbortController | null = null;

  constructor(
    private readonly settings: IVybeSettings,
    private readonly tracker: INotebookTracker
  ) {}

  /**
   * Ask for a suggestion, or for more of the one already showing.
   *
   * Returns true whenever the key was ours to consume, including while the
   * request is still in flight: returning false would let the editor insert a
   * tab underneath the prediction being written.
   */
  request = (view: EditorView): boolean => {
    if (!this.settings.enabled || !this.settings.completionEnabled) {
      return false;
    }
    const panel = this.tracker.currentWidget;
    const cell = panel?.content.activeCell;
    if (!panel || !cell || cell.editor?.host?.contains(view.dom) === false) {
      return false;
    }
    void this._run(view, panel);
    return true;
  };

  private async _run(view: EditorView, panel: NotebookPanel): Promise<void> {
    this._abort?.abort();
    const abort = new AbortController();
    this._abort = abort;

    const cursor = view.state.selection.main.head;
    const document = view.state.doc.toString();
    // Pressing the trigger with a suggestion on screen means "and then what?",
    // so the suggestion is part of what the model is continuing -- but it is
    // still not in the document, and is not written there unless accepted.
    const existing = currentGhost(view);
    const accepted = existing?.text ?? '';
    const typed = document.slice(0, cursor) + accepted;

    const cellId = panel.content.activeCell?.model.id ?? '';
    const script = notebookAsScript(panel, cellId, typed);
    if (!script) {
      return;
    }

    const hints = await this._hints(panel, typed, abort.signal);
    if (abort.signal.aborted) {
      return;
    }

    let latest = accepted;
    try {
      await predict(
        {
          script: script.script,
          cell: script.cellNumber,
          typed,
          maxLines: this.settings.completionMaxLines,
          hints,
          agentId: this.settings.completionAgentId,
          options: this.settings.agentOptions,
          timeoutSeconds: this.settings.completionTimeoutSeconds
        },
        text => {
          if (abort.signal.aborted) {
            return;
          }
          // The suggestion already on screen is kept in front of the new
          // one: the user asked for *more*, and having the accepted part
          // flicker away and back would read as it being reconsidered.
          latest = accepted + text;
          showGhost(view, latest, cursor);
        },
        abort.signal
      );
    } catch {
      // A prediction nobody sees is not worth an error in the editor.
    }
    if (!abort.signal.aborted && !latest.trim()) {
      dismissGhost(view);
    }
  }

  /**
   * What the kernel knows, for the names on the line being written.
   *
   * This is the one thing a local model cannot infer from the notebook: what
   * an object actually offers in *this* session, after however many cells
   * have run. It is the same data the user would get from Tab and Shift+Tab,
   * asked for on their behalf.
   */
  private async _hints(
    panel: NotebookPanel,
    typed: string,
    signal: AbortSignal
  ): Promise<string> {
    const line = typed.split('\n').pop() ?? '';
    const target = /([A-Za-z_][\w.]*)$/.exec(line)?.[1];
    if (!target || !target.includes('.')) {
      // Only for attribute access. A bare name is one the notebook already
      // shows, and asking the kernel about it buys nothing.
      return '';
    }
    try {
      const [completions, help] = await Promise.all([
        complete(panel, line),
        inspect(panel, target.replace(/\.[^.]*$/, ''))
      ]);
      if (signal.aborted) {
        return '';
      }
      const parts: string[] = [];
      if (completions && !completions.startsWith('no ')) {
        parts.push(`completions for ${target}:\n${completions}`);
      }
      if (help && !help.startsWith('no ')) {
        parts.push(help.slice(0, HINT_LIMIT));
      }
      return parts.join('\n\n').slice(0, HINT_LIMIT * 2);
    } catch {
      // The kernel is busy, or dead. The notebook is still context enough.
      return '';
    }
  }
}

export const inlineCompleterPlugin: JupyterFrontEndPlugin<void> = {
  id: 'jupyter-vybe:inline-completer',
  description: 'On-demand ghost text from the configured agent.',
  autoStart: true,
  requires: [IEditorExtensionRegistry, IVybeSettings, INotebookTracker],
  activate: (
    _app: JupyterFrontEnd,
    extensions: IEditorExtensionRegistry,
    settings: IVybeSettings,
    tracker: INotebookTracker
  ): void => {
    const predictor = new Predictor(settings, tracker);
    extensions.addExtension({
      name: 'jupyter-vybe:ghost-text',
      factory: () =>
        EditorExtensionRegistry.createImmutableExtension(
          ghostExtension({
            // Read once, when the editor is built. A trigger that changed
            // under a live editor would need every open editor rebuilt;
            // reopening the notebook is the honest price for changing it.
            trigger: settings.completionTriggerKey,
            predict: view => predictor.request(view)
          })
        )
    });
  }
};

export default inlineCompleterPlugin;
