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

/** The ✓ / ✗ stack floating at the cell's right edge, with its shortcuts. */
export class ReviewControls extends Widget {
  constructor(
    onAccept: () => void,
    onReject: () => void,
    acceptKey: string,
    rejectKey: string
  ) {
    const node = document.createElement('div');
    node.className = 'jv-review-controls';

    const make = (glyph: string, key: string, cls: string, fn: () => void) => {
      const wrap = document.createElement('div');
      wrap.className = 'jv-review-button-wrap';
      const btn = document.createElement('button');
      btn.className = `jv-review-button ${cls}`;
      btn.textContent = glyph;
      btn.onclick = fn;
      const label = document.createElement('div');
      label.className = 'jv-review-key';
      label.textContent = key;
      wrap.append(btn, label);
      return wrap;
    };

    node.append(
      make('✓', acceptKey, 'jv-accept', onAccept),
      make('✗', rejectKey, 'jv-reject', onReject)
    );
    super({ node });
    this.addClass('jv-review-controls-widget');
  }
}

/** Sticky top-right counter; also the round status while generating. */
export class StatusBubble extends Widget {
  private _label: HTMLElement;
  private _sub: HTMLElement;

  constructor(onJump: () => void, onRejectAll: () => void) {
    const node = document.createElement('div');
    node.className = 'jv-status-bubble';
    const main = document.createElement('button');
    main.className = 'jv-bubble-main';
    main.onclick = onJump;
    const sub = document.createElement('button');
    sub.className = 'jv-bubble-sub';
    sub.textContent = 'reject all  ⌥⇧E';
    sub.onclick = onRejectAll;
    node.append(main, sub);
    super({ node });
    this._label = main;
    this._sub = sub;
  }

  showGenerating(elapsedSeconds: number): void {
    this._label.textContent = `⚡ thinking… ${elapsedSeconds}s`;
    this._sub.textContent = 'cancel  ⌃C';
    this.node.classList.add('jv-generating');
  }

  showPending(count: number): void {
    this._label.textContent = `⚡ ${count} edit${count === 1 ? '' : 's'} proposed   ⌥E`;
    this._sub.textContent = 'reject all  ⌥⇧E';
    this.node.classList.remove('jv-generating');
  }
}
