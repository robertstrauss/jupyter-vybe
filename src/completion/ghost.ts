import {
  StateEffect,
  StateField,
  Prec,
  type EditorState,
  type Extension
} from '@codemirror/state';
import {
  Decoration,
  EditorView,
  WidgetType,
  keymap,
  showTooltip,
  type DecorationSet,
  type Tooltip
} from '@codemirror/view';

/**
 * Ghost text, owned outright (docs/04, the completion section).
 *
 * JupyterLab has an inline completion framework, and this deliberately does
 * not use it. That framework decides when to ask, what Tab means, and when a
 * suggestion goes away; here all three belong to the user:
 *
 *   - nothing is asked for until Option+Tab, because a suggestion costs a
 *     model call and a call on a keystroke cadence is a cost nobody agreed to;
 *   - Option+Tab again extends the suggestion instead of replacing it, so a
 *     prediction can be grown a couple of lines at a time;
 *   - Backspace over ghost text removes the ghost text and *not* the code
 *     underneath it, which is the one thing users get wrong with ghost text
 *     everywhere it exists.
 *
 * Owning it means a CodeMirror extension: a state field for the text, a widget
 * decoration to draw it, a tooltip to say what the keys do, and a keymap at
 * the highest precedence, because Tab already means indent and Backspace
 * already means delete.
 */

/** Show, extend, or clear the suggestion. */
export const setGhost = StateEffect.define<{ text: string; from: number }>();
export const clearGhost = StateEffect.define<null>();

export interface IGhost {
  /** The suggested text, which is not in the document. */
  text: string;
  /** Where it would be inserted. */
  from: number;
  /** True while the model is still writing it. */
  streaming: boolean;
}

/**
 * Drawn as a single widget rather than as inserted text.
 *
 * The suggestion must not be in the document: if it were, every mechanism
 * that reads the cell -- saving, executing, the next request -- would see
 * code the user never wrote.
 */
class GhostWidget extends WidgetType {
  constructor(private readonly text: string) {
    super();
  }

  eq(other: GhostWidget): boolean {
    return other.text === this.text;
  }

  toDOM(): HTMLElement {
    const wrap = document.createElement('span');
    wrap.className = 'jv-ghost';
    // Newlines in a widget need to survive the browser's whitespace
    // collapsing, hence the explicit break rather than a plain text node.
    const lines = this.text.split('\n');
    lines.forEach((line, i) => {
      if (i > 0) {
        wrap.appendChild(document.createElement('br'));
      }
      wrap.appendChild(document.createTextNode(line));
    });
    return wrap;
  }

  /** Never editable, never a cursor stop: it is not really there. */
  ignoreEvent(): boolean {
    return false;
  }
}

export const ghostState = StateField.define<IGhost | null>({
  create() {
    return null;
  },
  update(value, tr) {
    for (const effect of tr.effects) {
      if (effect.is(setGhost)) {
        return {
          text: effect.value.text,
          from: effect.value.from,
          streaming: true
        };
      }
      if (effect.is(clearGhost)) {
        return null;
      }
    }
    if (!value) {
      return null;
    }
    // Any edit the user makes rejects the suggestion. That is the whole
    // dismissal rule: rather than enumerating the keys that should cancel it,
    // anything that changes the document or moves the cursor does.
    if (tr.docChanged || tr.selection) {
      return null;
    }
    return value;
  },
  provide: field =>
    EditorView.decorations.from(field, ghost => {
      if (!ghost || !ghost.text) {
        return Decoration.none;
      }
      return Decoration.set([
        Decoration.widget({
          widget: new GhostWidget(ghost.text),
          side: 1
        }).range(ghost.from)
      ]) as DecorationSet;
    })
});

/** What the keys mean, said where the user is looking. */
const ghostTooltip = StateField.define<readonly Tooltip[]>({
  create(state) {
    return tooltipFor(state);
  },
  update(_value, tr) {
    return tooltipFor(tr.state);
  },
  provide: field => showTooltip.computeN([field], state => state.field(field))
});

function tooltipFor(state: EditorState): readonly Tooltip[] {
  const ghost = state.field(ghostState, false);
  if (!ghost || !ghost.text) {
    return [];
  }
  return [
    {
      pos: ghost.from,
      above: true,
      strictSide: false,
      arrow: false,
      create: () => {
        const dom = document.createElement('div');
        dom.className = 'jv-ghost-tip';
        dom.textContent =
          'tab to accept · opt+tab to predict more · any other key to reject';
        return { dom };
      }
    }
  ];
}

/** Accept the suggestion: it becomes real text, and the cursor follows it. */
export function acceptGhost(view: EditorView): boolean {
  const ghost = view.state.field(ghostState, false);
  if (!ghost || !ghost.text) {
    return false;
  }
  view.dispatch({
    changes: { from: ghost.from, insert: ghost.text },
    selection: { anchor: ghost.from + ghost.text.length },
    effects: clearGhost.of(null),
    userEvent: 'input.complete'
  });
  return true;
}

/** Read the suggestion currently on screen, if any. */
export function currentGhost(view: EditorView): IGhost | null {
  return view.state.field(ghostState, false) ?? null;
}

export function showGhost(view: EditorView, text: string, from: number): void {
  view.dispatch({ effects: setGhost.of({ text, from }) });
}

export function dismissGhost(view: EditorView): boolean {
  if (!view.state.field(ghostState, false)) {
    return false;
  }
  view.dispatch({ effects: clearGhost.of(null) });
  return true;
}

export interface IGhostKeys {
  /** The configured trigger, in CodeMirror's notation, e.g. `Alt-Tab`. */
  trigger: string;
  /** Ask for a suggestion, or for more of the one on screen. */
  predict: (view: EditorView) => boolean;
}

/**
 * The keymap, at the highest precedence there is.
 *
 * Tab is indent and Backspace is delete, and both of those are bound by
 * CodeMirror's own default keymap. A suggestion on screen has to win, or
 * accepting one indents the line and rejecting one eats the user's code --
 * which is why Backspace is here at all: it does *not* fall through.
 */
export function ghostKeymap(keys: IGhostKeys): Extension {
  return Prec.highest(
    keymap.of([
      {
        key: keys.trigger,
        run: keys.predict,
        // The browser and the OS both want this one. Whether we get it is
        // their decision, but we can at least stop the editor from also
        // inserting a tab when we do.
        preventDefault: true
      },
      {
        key: 'Tab',
        run: acceptGhost // returns false with no ghost, so Tab still indents
      },
      {
        key: 'Escape',
        run: dismissGhost
      },
      {
        key: 'Backspace',
        // Removes the suggestion, never the document. Deleting real text
        // while dismissing a suggestion is the mistake this exists to avoid:
        // the user is reaching for "no thank you", not for an edit.
        run: dismissGhost
      },
      {
        key: 'Delete',
        run: dismissGhost
      }
    ])
  );
}

export function ghostExtension(keys: IGhostKeys): Extension {
  return [ghostState, ghostTooltip, ghostKeymap(keys)];
}
