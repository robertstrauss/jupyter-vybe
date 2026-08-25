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

/** Sticky top-right counter; also the round status while generating. */
export class StatusBubble extends Widget {
  private _label: HTMLElement;
  private _hint: HTMLElement;
  private _sub: HTMLButtonElement;

  constructor(
    onJump: () => void,
    onRejectAll: () => void,
    onUndo: () => void,
    onCancel: () => void
  ) {
    const node = document.createElement('div');
    node.className = 'jv-status-bubble';

    // The main button is the whole top of the bubble and does the safe thing:
    // take me to the first edit. Reject-all is a small, separate, clearly
    // labelled control below a divider -- previously the two sat close enough
    // that a click meaning "show me" could discard the round (docs/14 N3).
    const main = document.createElement('button');
    main.className = 'jv-bubble-main';
    main.onclick = onJump;

    const hint = document.createElement('div');
    hint.className = 'jv-bubble-hint';

    const rule = document.createElement('div');
    rule.className = 'jv-bubble-rule';

    const sub = document.createElement('button');
    sub.className = 'jv-bubble-sub';
    sub.textContent = 'reject all  ⌥⇧E';
    sub.onclick = onRejectAll;

    node.append(main, hint, rule, sub);
    super({ node });
    this._label = main;
    this._hint = hint;
    this._sub = sub;
    this._onUndo = onUndo;
    this._onRejectAll = onRejectAll;
    this._onCancel = onCancel;
  }

  private _onUndo: () => void;
  private _onRejectAll: () => void;
  private _onCancel: () => void;

  showGenerating(elapsedSeconds: number): void {
    this._label.textContent = `⚡ thinking… ${elapsedSeconds}s`;
    this._hint.textContent = '';
    this._sub.textContent = 'cancel  ⌃C';
    // The button said "cancel" but still ran reject-all: it was wired once in
    // the constructor and never rebound (docs/14 N3).
    this._sub.onclick = this._onCancel;
    this.node.classList.add('jv-generating');
    this.node.classList.remove('jv-undo');
  }

  showPending(count: number, jumpKey = '⌥E'): void {
    this._label.textContent = `⚡ ${count} edit${count === 1 ? '' : 's'} proposed`;
    this._hint.textContent = `click or ${jumpKey} to review`;
    this._sub.textContent = 'reject all  ⌥⇧E';
    this._sub.onclick = this._onRejectAll;
    this.node.classList.remove('jv-generating', 'jv-undo');
  }

  /** After a reject-all: the round is gone, but not irrecoverably. */
  showUndo(count: number, undoKey = '⌥⇧Z'): void {
    this._label.textContent = `rejected ${count} edit${count === 1 ? '' : 's'}`;
    this._hint.textContent = '';
    this._sub.textContent = `undo  ${undoKey}`;
    this._sub.onclick = this._onUndo;
    this.node.classList.remove('jv-generating');
    this.node.classList.add('jv-undo');
  }
}
