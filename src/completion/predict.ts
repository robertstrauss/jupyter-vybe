import { ServerConnection } from '@jupyterlab/services';
import { URLExt } from '@jupyterlab/coreutils';

/**
 * One prediction request, streamed.
 *
 * The server sends newline-delimited JSON and flushes as the model writes, so
 * the ghost text grows in front of the user instead of appearing whole after a
 * silence. `onText` is called with the accumulated suggestion each time, not
 * with a delta: the server re-cleans the whole answer on every frame, because
 * a fence opened on the first line must never reach the editor even for the
 * one frame before its closing partner arrives.
 */

export interface IPredictRequest {
  script: string;
  cell: number;
  typed: string;
  maxLines: number;
  hints: string;
  agentId: string;
  options: Record<string, string>;
  timeoutSeconds: number;
}

export async function predict(
  request: IPredictRequest,
  onText: (text: string) => void,
  signal: AbortSignal
): Promise<void> {
  const settings = ServerConnection.makeSettings();
  const response = await ServerConnection.makeRequest(
    URLExt.join(settings.baseUrl, 'jupyter-vybe', 'predict'),
    { method: 'POST', body: JSON.stringify(request), signal },
    settings
  );
  if (!response.ok || !response.body) {
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    // A frame is only complete at a newline; a partial one is left in the
    // buffer rather than parsed, which would throw on every chunk boundary.
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line.trim()) {
        continue;
      }
      try {
        const frame = JSON.parse(line) as { text?: string; error?: string };
        if (typeof frame.text === 'string') {
          onText(frame.text);
        }
      } catch {
        // A malformed frame is not worth abandoning the stream over.
      }
    }
  }
}
