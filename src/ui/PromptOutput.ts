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

export type PromptOutputKind = 'thinking' | 'review' | 'idle';

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
  showThinking(tokensPerSecond: number, notes: string[]): void {
    this._kind = 'thinking';
    this._headline.textContent = `Thinking, ${Math.round(
      tokensPerSecond
    )} tokens/sec`;
    this._renderNotes(notes);
    this._hint.textContent = '';
    this.node.classList.add('jv-thinking');
    this.node.classList.remove('jv-review');
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
    this.node.classList.remove('jv-thinking');
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
