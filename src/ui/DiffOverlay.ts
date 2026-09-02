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

/** The ✓ / ✗ stack floating at the cell's right edge, with its shortcuts. */
export class ReviewControls extends Widget {
  constructor(actions: IReviewActions) {
    const node = document.createElement('div');
    node.className = 'jv-review-controls';
    node.setAttribute('role', 'group');
    node.setAttribute('aria-label', 'Review this proposed edit');

    const make = (
      glyph: string,
      key: string,
      cls: string,
      title: string,
      fn: () => void
    ) => {
      const wrap = document.createElement('div');
      wrap.className = 'jv-review-button-wrap';
      const btn = document.createElement('button');
      btn.className = `jv-review-button ${cls}`;
      btn.textContent = glyph;
      btn.title = `${title} (${key})`;
      // The glyph is a symbol; the button needs a name.
      btn.setAttribute('aria-label', `${title} (keyboard: ${key})`);
      btn.onclick = fn;
      const label = document.createElement('div');
      label.className = 'jv-review-key';
      label.textContent = key;
      wrap.append(btn, label);
      return wrap;
    };

    node.append(
      make('✓', actions.acceptKey, 'jv-accept', 'Accept', actions.onAccept),
      make('✗', actions.rejectKey, 'jv-reject', 'Reject', actions.onReject)
    );
    // The merge verdict has always existed but was invisible: only the two
    // buttons were ever drawn, so nothing said Enter did anything (docs/14 N2).
    if (actions.onModify && actions.modifyKey) {
      node.append(
        make(
          '✎',
          actions.modifyKey,
          'jv-modify',
          'Modify: merge and edit by hand',
          actions.onModify
        )
      );
      const hint = document.createElement('div');
      hint.className = 'jv-review-hint';
      hint.textContent = `${actions.modifyKey} to modify`;
      node.append(hint);
    }
    super({ node });
    this.addClass('jv-review-controls-widget');
  }
}

/**
 * The one floating thing: a review counter (docs/03).
 *
 * It exists for a single failure mode — walking away with edits still
 * unresolved. So it appears only during review, says how many are left, and
 * offers the two ways out: reject the rest, or take a verdict back. It is
 * top-centre and translucent because it is a reminder, not a control panel,
 * and it must not hide the code being reviewed.
 *
 * Everything else a round has to say now belongs to the prompt cell's own
 * output area, next to the prompt that caused it.
 */
export class ReviewCounter extends Widget {
  private _label: HTMLElement;

  constructor(onRejectAll: () => void) {
    const node = document.createElement('div');
    node.className = 'jv-review-counter';
    node.setAttribute('role', 'status');
    node.setAttribute('aria-live', 'polite');

    const label = document.createElement('span');
    label.className = 'jv-counter-label';

    const reject = document.createElement('button');
    reject.className = 'jv-counter-reject';
    reject.textContent = 'reject the rest';
    reject.setAttribute('aria-label', 'Reject all remaining proposed edits');
    reject.onclick = onRejectAll;

    const hint = document.createElement('span');
    hint.className = 'jv-counter-hint';
    hint.textContent = '⌘Z undoes a verdict';

    node.append(label, reject, hint);
    super({ node });
    this._label = label;
  }

  showRemaining(count: number): void {
    this._label.textContent = `${count} edit${
      count === 1 ? '' : 's'
    } left to review`;
  }
}
