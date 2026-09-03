import { Widget } from '@lumino/widgets';
import type { IHunk } from './diff';

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

  constructor(onRejectAll: () => void, onJump: () => void) {
    const node = document.createElement('div');
    node.className = 'jv-review-counter';
    node.setAttribute('role', 'status');
    node.setAttribute('aria-live', 'polite');

    const label = document.createElement('span');
    label.className = 'jv-counter-label';

    const action = (
      cls: string,
      text: string,
      aria: string,
      fn: () => void
    ) => {
      const btn = document.createElement('button');
      btn.className = `jv-counter-button ${cls}`;
      btn.textContent = text;
      btn.setAttribute('aria-label', aria);
      btn.onclick = fn;
      return btn;
    };

    const jump = action(
      'jv-counter-jump',
      'jump to next  ⏎',
      'Go to the next proposed edit',
      onJump
    );
    const reject = action(
      'jv-counter-reject',
      'reject the rest  esc',
      'Reject all remaining proposed edits',
      onRejectAll
    );

    const hint = document.createElement('span');
    hint.className = 'jv-counter-hint';
    hint.textContent = '⌘Z undoes a verdict';

    node.append(label, jump, reject, hint);
    super({ node });
    this._label = label;
  }

  showRemaining(count: number): void {
    this._label.textContent = `${count} edit${
      count === 1 ? '' : 's'
    } left to review`;
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
