import { Platform } from '@lumino/domutils';
import { Widget } from '@lumino/widgets';
import type { IHunk } from './diff';
import type { IVerdict } from '../plugins/proposals';

/**
 * Read-only unified diff shown in place of a cell's editor while a proposal is
 * pending. The notebook model is untouched until accept, so rejecting is free
 * (docs/03).
 */
export class DiffOverlay extends Widget {
  constructor(hunks: IHunk[], note?: string) {
    super({ node: DiffOverlay.build(hunks, note) });
    this.addClass('jv-diff-overlay');
  }

  static build(hunks: IHunk[], note?: string): HTMLElement {
    const root = document.createElement('div');
    // Announced as a group, not as 30 unrelated lines of text.
    root.setAttribute('role', 'region');
    root.setAttribute(
      'aria-label',
      note ? `Proposed edit: ${note}` : 'Proposed edit'
    );
    if (note) {
      const n = document.createElement('div');
      n.className = 'jv-diff-note';
      n.textContent = note;
      root.appendChild(n);
    }
    hunks.forEach((hunk, i) => {
      if (i > 0) {
        const sep = document.createElement('div');
        sep.className = 'jv-diff-sep';
        sep.textContent = '⋯';
        root.appendChild(sep);
      }
      for (const line of hunk.lines) {
        const el = document.createElement('div');
        el.className = `jv-diff-line jv-diff-${line.kind}`;
        const marker = document.createElement('span');
        marker.className = 'jv-diff-marker';
        marker.textContent =
          line.kind === 'add' ? '+' : line.kind === 'del' ? '-' : ' ';
        // Colour is the only cue for add/remove otherwise, which fails both
        // screen readers and anyone who cannot distinguish the two greens.
        if (line.kind !== 'context') {
          marker.setAttribute(
            'aria-label',
            line.kind === 'add' ? 'added line' : 'removed line'
          );
        }
        const text = document.createElement('span');
        text.className = 'jv-diff-text';
        text.textContent = line.text;
        el.append(marker, text);
        root.appendChild(el);
      }
    });
    return root;
  }
}

export interface IReviewActions {
  onAccept: () => void;
  onReject: () => void;
  /** The third verdict: open the merged source and hand-edit it (D6). */
  onModify?: () => void;
  acceptKey: string;
  rejectKey: string;
  modifyKey?: string;
}

/** The verdict stack at the cell's right edge: what each does, and its key. */
export class ReviewControls extends Widget {
  constructor(actions: IReviewActions) {
    const node = document.createElement('div');
    node.className = 'jv-review-controls';
    node.setAttribute('role', 'group');
    node.setAttribute('aria-label', 'Review this proposed edit');

    const make = (
      glyph: string,
      label: string,
      key: string,
      cls: string,
      title: string,
      fn: () => void
    ) => {
      const wrap = document.createElement('div');
      wrap.className = 'jv-review-button-wrap';
      const btn = document.createElement('button');
      btn.className = `jv-review-button ${cls}`;
      btn.title = `${title} (${key})`;
      btn.setAttribute('aria-label', `${title} (keyboard: ${key})`);
      btn.onclick = fn;

      // The word, not only the symbol. A ✎ beside a ✓ and a ✗ is a guess;
      // "Modify" is not. The key stays outside the button, where it reads as
      // a caption rather than as part of what the button says.
      const mark = document.createElement('span');
      mark.className = 'jv-review-glyph';
      mark.textContent = glyph;
      const word = document.createElement('span');
      word.className = 'jv-review-word';
      word.textContent = label;
      btn.append(mark, word);

      const caption = document.createElement('div');
      caption.className = 'jv-review-key';
      caption.textContent = key;
      wrap.append(btn, caption);
      return wrap;
    };

    node.append(
      make(
        '✓',
        'Accept',
        actions.acceptKey,
        'jv-accept',
        'Accept',
        actions.onAccept
      ),
      make(
        '✗',
        'Reject',
        actions.rejectKey,
        'jv-reject',
        'Reject',
        actions.onReject
      )
    );
    // The merge verdict has always existed but was invisible: only the two
    // buttons were ever drawn, so nothing said Enter did anything (docs/14
    // N2). It now says so on the button, which is why the separate line of
    // hint text underneath is gone -- it repeated the caption beside it.
    if (actions.onModify && actions.modifyKey) {
      node.append(
        make(
          '✎',
          'Modify',
          actions.modifyKey,
          'jv-modify',
          'Modify: merge and edit by hand',
          actions.onModify
        )
      );
    }
    super({ node });
    this.addClass('jv-review-controls-widget');
  }
}

/**
 * The one floating thing: the review session's counter (docs/03).
 *
 * It exists for a single failure mode -- walking away with edits still
 * unresolved -- and it is the session's only control surface, so it carries
 * every way out: jump to the next edit, reject the rest, or take a verdict
 * back. Translucent, because it is a reminder rather than a panel, and set
 * below the toolbar rather than over it: floating across the notebook's own
 * chrome made it read as part of JupyterLab instead of part of the round.
 *
 * It is also where a refusal is reported. Clicking a greyed-out cell during a
 * session does nothing, and doing nothing silently is indistinguishable from
 * being broken -- so the border flashes red here, which both explains the
 * refusal and points at the thing that ends it.
 */
export class ReviewCounter extends Widget {
  private _label: HTMLElement;
  private _reviewOnly: HTMLElement[];
  private _mergeOnly: HTMLElement[];
  private _undo: HTMLElement;

  constructor(actions: ICounterActions) {
    const node = document.createElement('div');
    node.className = 'jv-review-counter';
    node.setAttribute('role', 'status');
    node.setAttribute('aria-live', 'polite');

    const label = document.createElement('span');
    label.className = 'jv-counter-label';

    // The card itself is the way to the next diff. `Enter` already does it
    // from the keyboard, so a button for it was a third thing on screen
    // saying what two others already said -- and the card is a bigger, easier
    // target than any button on it.
    node.title = 'click to jump to proposal';
    node.addEventListener('click', event => {
      if (!(event.target as HTMLElement).closest('button')) {
        actions.onJump();
      }
    });

    const row = document.createElement('div');
    row.className = 'jv-counter-actions';

    const rejectAll = button(
      'jv-counter-reject',
      'Reject All',
      'esc',
      actions.onRejectAll
    );
    const confirm = button(
      'jv-counter-confirm',
      'Confirm Merge and Continue',
      'esc',
      actions.onConfirmMerge
    );
    const undo = button(
      'jv-counter-undo',
      'Undo Merge',
      Platform.IS_MAC ? '⌘Z' : 'Ctrl+Z',
      actions.onUndo
    );

    row.append(rejectAll, confirm, undo);
    node.append(label, row);
    super({ node });
    this._label = label;
    this._reviewOnly = [rejectAll];
    this._undo = undo;
    this._mergeOnly = [confirm];
    this.showRemaining(0);
  }

  showRemaining(count: number, verdict: IVerdict | null = null): void {
    this._label.textContent = `${count} edit${
      count === 1 ? '' : 's'
    } left to review`;
    this._setMode(false, verdict);
  }

  /**
   * The merge has its own mode, because it has its own one thing to do.
   *
   * Offering "Reject All" beside a cell the user is in the middle of
   * hand-editing invites them to throw away the edit they are making, and the
   * count is meaningless here besides: the merge is not one of the edits left
   * to review, it is the one being written.
   */
  showMerging(verdict: IVerdict | null = 'merge'): void {
    this._label.textContent = 'Manual proposal review';
    this._setMode(true, verdict);
  }

  private _setMode(merging: boolean, verdict: IVerdict | null): void {
    this.node.classList.toggle('jv-counter-merging', merging);
    for (const el of this._reviewOnly) {
      el.hidden = merging;
    }
    for (const el of this._mergeOnly) {
      el.hidden = !merging;
    }
    // Named, not merely offered: "Undo" alone leaves the user to remember
    // what they last did. With nothing to take back it is not shown at all,
    // rather than shown and inert.
    this._undo.hidden = !verdict;
    if (verdict) {
      const label = `Undo ${verdict[0].toUpperCase()}${verdict.slice(1)}`;
      const text = this._undo.querySelector('span');
      if (text) {
        text.textContent = label;
      }
      this._undo.setAttribute(
        'aria-label',
        `${label} (keyboard: ${Platform.IS_MAC ? '⌘Z' : 'Ctrl+Z'})`
      );
    }
  }

  /**
   * Say no, visibly.
   *
   * Restarted rather than merely re-added: a second refusal during the fade
   * of the first would otherwise not flash at all, which is precisely when
   * the user is jabbing at a cell and most needs the answer.
   */
  refuse(): void {
    this.node.classList.remove('jv-counter-refused');
    void this.node.offsetWidth; // reflow, so the animation restarts
    this.node.classList.add('jv-counter-refused');
  }
}

export interface ICounterActions {
  /** Also fired by clicking the card itself, away from any button. */
  onJump: () => void;
  onRejectAll: () => void;
  onConfirmMerge: () => void;
  onUndo: () => void;
}

/**
 * One action: what it does, and the key that does the same thing.
 *
 * The key is a `<kbd>`, set in the code font on a grey field, so it reads as
 * a key rather than as more of the sentence -- which matters most for `esc`,
 * the only one that is a word and would otherwise disappear into the label
 * beside it.
 */
function button(
  cls: string,
  label: string,
  key: string,
  onClick: () => void
): HTMLElement {
  const btn = document.createElement('button');
  btn.className = `jv-counter-button ${cls}`;
  btn.setAttribute('aria-label', `${label} (keyboard: ${key})`);
  btn.onclick = onClick;

  const text = document.createElement('span');
  text.textContent = label;
  const kbd = document.createElement('kbd');
  kbd.className = 'jv-key';
  kbd.textContent = key;
  btn.append(text, kbd);
  return btn;
}
