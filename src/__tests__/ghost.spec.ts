import { EditorState } from '@codemirror/state';

import {
  acceptGhost,
  clearGhost,
  ghostState,
  setGhost
} from '../completion/ghost';

/**
 * Ghost text has one rule that matters more than the rest: it is not in the
 * document. Everything that reads a cell -- saving it, running it, sending it
 * to the model -- must see what the user wrote and nothing else.
 *
 * The second rule is that rejecting a suggestion must not delete anything.
 * Backspace over ghost text is a user saying "no thank you", and taking a
 * character of their code for it is the failure worth testing hardest.
 */

const start = (doc: string) =>
  EditorState.create({ doc, extensions: [ghostState] });

/** Applies a transaction the way the real dispatch would. */
const after = (state: EditorState, spec: any) => state.update(spec).state;

describe('the ghost text state', () => {
  it('holds a suggestion without putting it in the document', () => {
    let state = start('df = ');
    state = after(state, {
      effects: setGhost.of({ text: 'pd.read_csv("data.csv")', from: 5 })
    });

    expect(state.field(ghostState)?.text).toBe('pd.read_csv("data.csv")');
    // The document is untouched. A save here writes what the user typed.
    expect(state.doc.toString()).toBe('df = ');
  });

  it('is rejected by typing, without touching what was typed', () => {
    let state = start('df = ');
    state = after(state, {
      effects: setGhost.of({ text: 'pd.read_csv()', from: 5 })
    });

    state = after(state, { changes: { from: 5, insert: 'x' } });

    expect(state.field(ghostState)).toBeNull();
    expect(state.doc.toString()).toBe('df = x');
  });

  it('is rejected by moving the cursor', () => {
    let state = start('df = ');
    state = after(state, {
      effects: setGhost.of({ text: 'pd.read_csv()', from: 5 })
    });

    state = after(state, { selection: { anchor: 0 } });

    expect(state.field(ghostState)).toBeNull();
  });

  it('goes away on demand, leaving the document alone', () => {
    let state = start('df = ');
    state = after(state, {
      effects: setGhost.of({ text: 'pd.read_csv()', from: 5 })
    });

    state = after(state, { effects: clearGhost.of(null) });

    expect(state.field(ghostState)).toBeNull();
    // The whole point of binding Backspace: no character was taken for it.
    expect(state.doc.toString()).toBe('df = ');
  });

  it('replaces a suggestion rather than stacking them', () => {
    let state = start('df = ');
    state = after(state, { effects: setGhost.of({ text: 'a', from: 5 }) });
    state = after(state, { effects: setGhost.of({ text: 'ab', from: 5 }) });

    expect(state.field(ghostState)?.text).toBe('ab');
  });
});

describe('accepting a suggestion', () => {
  /** A view is more than the state, so only what accept needs is stood up. */
  const fakeView = (state: EditorState) => {
    const dispatched: any[] = [];
    return {
      view: {
        state,
        dispatch: (spec: any) => dispatched.push(spec)
      } as any,
      dispatched
    };
  };

  it('writes the suggestion in and puts the cursor after it', () => {
    let state = start('df = ');
    state = after(state, {
      effects: setGhost.of({ text: 'pd.read_csv()', from: 5 })
    });
    const { view, dispatched } = fakeView(state);

    expect(acceptGhost(view)).toBe(true);

    expect(dispatched[0].changes).toEqual({
      from: 5,
      insert: 'pd.read_csv()'
    });
    expect(dispatched[0].selection).toEqual({ anchor: 18 });
  });

  it('declines the key when there is nothing to accept', () => {
    const { view, dispatched } = fakeView(start('df = '));

    // False, so Tab falls through to CodeMirror and still indents.
    expect(acceptGhost(view)).toBe(false);
    expect(dispatched).toEqual([]);
  });
});
