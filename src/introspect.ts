import type { NotebookPanel } from '@jupyterlab/notebook';

/**
 * The ONLY file in this codebase permitted to touch a kernel connection
 * (docs/07, decision D1).
 *
 * `inspect_request` and `complete_request` are distinct Jupyter messaging
 * protocol messages from `execute_request`: they read the kernel's namespace,
 * they do not run user code, and they do not advance the execution counter.
 *
 * `requestExecute` is never called here or anywhere else in src/, and CI greps
 * for it. If you are adding a kernel call, it does not belong in this project.
 */

const HELP_LIMIT = 2000;
const COMPLETION_LIMIT = 60;

export type IntrospectionMode = 'off' | 'inspect-only' | 'inspect+complete';

function kernel(panel: NotebookPanel) {
  return panel.sessionContext?.session?.kernel ?? null;
}

export async function inspect(
  panel: NotebookPanel,
  target: string
): Promise<string> {
  const k = kernel(panel);
  if (!k) {
    return 'no kernel available';
  }
  const reply = await k.requestInspect({
    code: target,
    cursor_pos: target.length,
    detail_level: 0
  });
  const content = reply.content;
  if (content.status !== 'ok' || !content.found) {
    return `no information found for ${target}`;
  }
  const text = (content.data as Record<string, string>)['text/plain'] ?? '';
  return stripAnsi(text).slice(0, HELP_LIMIT);
}

export async function complete(
  panel: NotebookPanel,
  prefix: string
): Promise<string> {
  const k = kernel(panel);
  if (!k) {
    return 'no kernel available';
  }
  const reply = await k.requestComplete({
    code: prefix,
    cursor_pos: prefix.length
  });
  const content = reply.content;
  if (content.status !== 'ok') {
    return `no completions for ${prefix}`;
  }
  const matches = content.matches.slice(0, COMPLETION_LIMIT);
  return matches.length ? matches.join('\n') : `no completions for ${prefix}`;
}

/** Route a tool call, honouring the settings gate. */
export async function runIntrospection(
  panel: NotebookPanel,
  mode: IntrospectionMode,
  name: string,
  args: Record<string, string>
): Promise<{ ok: boolean; result: string }> {
  if (mode === 'off') {
    return { ok: false, result: 'introspection is disabled' };
  }
  try {
    if (name === 'ipython_help') {
      return { ok: true, result: await inspect(panel, args.target ?? '') };
    }
    if (name === 'complete') {
      if (mode === 'inspect-only') {
        return { ok: false, result: 'completion is disabled' };
      }
      return { ok: true, result: await complete(panel, args.prefix ?? '') };
    }
  } catch (err) {
    return { ok: false, result: `${err}` };
  }
  return { ok: false, result: `unknown tool ${name}` };
}

function stripAnsi(text: string): string {
  // eslint-disable-next-line no-control-regex
  return text.replace(/\u001b\[[0-9;]*m/g, '');
}
