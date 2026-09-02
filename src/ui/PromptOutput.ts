import { Widget } from '@lumino/widgets';

/**
 * What a prompt cell shows where its output would go (docs/03).
 *
 * Progress and the review summary belong *at the cell*, not in a corner of
 * the screen: that is where the user is looking, and a notification far from
 * its cause is a notification you have to go and find.
 *
 * Nothing here touches the notebook model. It is a widget attached to the
 * cell's DOM, so the `.ipynb` gains no outputs and no metadata, and a
 * collaborator without the extension opens an ordinary notebook (D4). The
 * cost is that it does not survive a reload, which is correct: an unfinished
 * round does not either.
 */

export type PromptOutputKind = 'thinking' | 'review' | 'problem' | 'idle';

export class PromptOutput extends Widget {
  private _headline: HTMLElement;
  private _notes: HTMLElement;
  private _hint: HTMLElement;
  private _kind: PromptOutputKind = 'idle';

  constructor() {
    const node = document.createElement('div');
    node.className = 'jv-prompt-output';
    // Announced when it changes, but never interrupting: a token counter that
    // preempts a screen reader once a second would be unusable.
    node.setAttribute('role', 'status');
    node.setAttribute('aria-live', 'polite');
    super({ node });
    this.addClass('jv-prompt-output-widget');

    this._headline = document.createElement('div');
    this._headline.className = 'jv-prompt-headline';
    this._notes = document.createElement('div');
    this._notes.className = 'jv-prompt-notes';
    this._hint = document.createElement('div');
    this._hint.className = 'jv-prompt-hint';
    node.append(this._headline, this._notes, this._hint);
  }

  get kind(): PromptOutputKind {
    return this._kind;
  }

  /**
   * Generation in progress. The rate is an estimate from characters received
   * (docs/09's estimator), not a count from the model: the point is to show
   * that something is happening and roughly how fast, and a real tokenizer
   * costs 2MB of WASM to be right about a number nobody acts on.
   */
  showThinking(
    tokensPerSecond: number,
    notes: string[],
    elapsedSeconds = 0
  ): void {
    this._kind = 'thinking';
    // Before the first token there is no rate, and "0 tokens/sec" reads as
    // broken rather than as busy. That gap is the model reading the prompt --
    // seconds of it on a first turn, where nothing has been cached yet -- so
    // the honest thing to show is how long it has been.
    const rate = Math.round(tokensPerSecond);
    this._headline.textContent =
      rate > 0
        ? `Thinking, ${rate} tokens/sec`
        : `Thinking, reading the notebook (${Math.max(
            1,
            Math.round(elapsedSeconds)
          )}s)`;
    this._renderNotes(notes);
    this._hint.textContent = '';
    this.node.classList.add('jv-thinking');
    this.node.classList.remove('jv-review', 'jv-problem', 'jv-notice');
  }

  /** Waiting on the user: how many edits, and the two keys that resolve them. */
  showReview(count: number, notes: string[]): void {
    this._kind = 'review';
    this._headline.textContent = `${count} edit${
      count === 1 ? '' : 's'
    } proposed`;
    this._renderNotes(notes);
    this._hint.textContent = 'Enter to review · N to reject all';
    this.node.classList.add('jv-review');
    this.node.classList.remove('jv-thinking', 'jv-problem', 'jv-notice');
  }

  /**
   * Something went wrong, or something worth knowing happened.
   *
   * Here rather than in a floating bubble: the round reports on itself in one
   * place, and a failure belongs next to the prompt that caused it. The
   * settled markdown cell keeps the text too, so it survives the round
   * (docs/13 R14).
   */
  showProblem(message: string, severity: 'problem' | 'notice'): void {
    this._kind = 'problem';
    this._headline.textContent =
      severity === 'problem' ? 'The agent hit a problem' : 'Worth knowing';
    this._notes.textContent = '';
    this._hint.textContent = message;
    this.node.classList.remove('jv-thinking', 'jv-review');
    this.node.classList.add('jv-problem');
    this.node.classList.toggle('jv-notice', severity === 'notice');
  }

  private _renderNotes(notes: string[]): void {
    this._notes.textContent = '';
    for (const note of notes) {
      const line = document.createElement('em');
      line.className = 'jv-prompt-note';
      line.textContent = note;
      this._notes.appendChild(line);
    }
  }
}

/**
 * Where to attach it: the cell's output wrapper when there is one, so it lines
 * up with real output, and the cell itself otherwise.
 */
export function outputHostFor(cellNode: HTMLElement): HTMLElement {
  return (
    (cellNode.querySelector('.jp-Cell-outputWrapper') as HTMLElement | null) ??
    cellNode
  );
}
