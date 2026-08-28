/**
 * The inline completer's whole job outside the network call is *not showing
 * stale text*: a suggestion that arrives after the user has typed on is worse
 * than no suggestion, because it flickers something wrong in front of them.
 */

// No @types/node in this project, so the shared handle is declared rather
// than imported.
declare const global: { __request?: any };

jest.mock('@jupyterlab/services', () => ({
  ServerConnection: {
    makeSettings: () => ({ baseUrl: 'http://localhost:8888/' }),
    makeRequest: (...args: unknown[]) => (global as any).__request(...args)
  }
}));

jest.mock('@jupyterlab/coreutils', () => ({
  URLExt: { join: (...parts: string[]) => parts.join('/') }
}));

jest.mock('@jupyterlab/completer', () => ({}), { virtual: true });

import { inlineCompleterPlugin } from '../plugins/inlineCompleter';

const settings = (over: Record<string, unknown> = {}) =>
  ({
    enabled: true,
    completionEnabled: true,
    completionDebounceMs: 5,
    completionMaxTokens: 64,
    completionTimeoutSeconds: 4,
    completionMultiline: true,
    agentId: 'ollama-qwen25-coder-7b',
    agentOptions: {},
    ...over
  }) as any;

function providerFor(s: any): any {
  let captured: any;
  const manager: any = {
    registerInlineProvider: (p: any) => {
      captured = p;
    }
  };
  inlineCompleterPlugin.activate({} as any, manager, s);
  return captured;
}

const reply =
  (insertText: string, delayMs = 0) =>
  () =>
    new Promise(resolve =>
      setTimeout(
        () =>
          resolve({
            ok: true,
            json: async () => ({ items: [{ insertText }] })
          }),
        delayMs
      )
    );

const request = (text: string, offset?: number) => ({
  text,
  offset: offset ?? text.length
});

const typing = { triggerKind: 1 } as any;
const invoked = { triggerKind: 0 } as any;

describe('the inline completion provider', () => {
  afterEach(() => {
    delete (global as any).__request;
  });

  it('asks the server and returns what it says', async () => {
    (global as any).__request = reply('np.mean(x)');
    const provider = providerFor(settings());

    const result = await provider.fetch(request('y = '), invoked);

    expect(result.items).toEqual([{ insertText: 'np.mean(x)' }]);
  });

  it('stays silent when completion is switched off', async () => {
    (global as any).__request = jest.fn();
    const provider = providerFor(settings({ completionEnabled: false }));

    const result = await provider.fetch(request('y = '), invoked);

    expect(result.items).toEqual([]);
    expect((global as any).__request).not.toHaveBeenCalled();
  });

  it('stays silent when the extension itself is off', async () => {
    (global as any).__request = jest.fn();
    const provider = providerFor(settings({ enabled: false }));
    expect((await provider.fetch(request('y = '), invoked)).items).toEqual([]);
    expect((global as any).__request).not.toHaveBeenCalled();
  });

  it('drops an answer that a later keystroke has superseded', async () => {
    (global as any).__request = reply('stale', 30);
    const provider = providerFor(settings());

    const first = provider.fetch(request('y = '), typing);
    // The user keeps typing: a second request supersedes the first.
    (global as any).__request = reply('fresh', 0);
    const second = provider.fetch(request('y = np.'), typing);

    expect((await first).items).toEqual([]);
    expect((await second).items).toEqual([{ insertText: 'fresh' }]);
  });

  it('does not interrupt a word in progress', async () => {
    (global as any).__request = jest.fn();
    const provider = providerFor(settings());

    // Cursor inside `value`: the editor's own completer is better at this and
    // this one costs a model call.
    const result = await provider.fetch(
      request('val|ue'.replace('|', ''), 3),
      invoked
    );

    expect(result.items).toEqual([]);
    expect((global as any).__request).not.toHaveBeenCalled();
  });

  it('turns a failed request into silence, never an error in the editor', async () => {
    (global as any).__request = () => Promise.reject(new Error('offline'));
    const provider = providerFor(settings());

    await expect(provider.fetch(request('y = '), invoked)).resolves.toEqual({
      items: []
    });
  });

  it('does not debounce an explicitly invoked completion', async () => {
    (global as any).__request = reply('now');
    const provider = providerFor(settings({ completionDebounceMs: 10_000 }));

    // If Invoke were debounced too, this would take ten seconds.
    const result = await provider.fetch(request('y = '), invoked);

    expect(result.items).toEqual([{ insertText: 'now' }]);
  });
});
