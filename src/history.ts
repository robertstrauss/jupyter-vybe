import { ServerConnection } from '@jupyterlab/services';
import { URLExt } from '@jupyterlab/coreutils';

/**
 * Client for the sidecar prompt record (docs/05, decision D12).
 *
 * The server writes prompts, prose, insertions, and proposals as it streams
 * them. One thing it cannot know is the *verdict*: accept and reject happen
 * entirely here. Posting it back is what makes a rejected proposal legible
 * later, which is the point of keeping the record at all.
 */

export interface IHistoryEntry {
  kind: string;
  roundId?: string;
  ts?: number;
  [key: string]: unknown;
}

export interface IHistoryPayload {
  path: string;
  file: string;
  entries: IHistoryEntry[];
  /** Set when no record was found but one exists naming this basename. */
  orphan?: string;
}

const ENDPOINT = ['jupyter-vybe', 'history'];

function url(): string {
  const settings = ServerConnection.makeSettings();
  return URLExt.join(settings.baseUrl, ...ENDPOINT);
}

export async function readHistory(
  path: string,
  limit?: number
): Promise<IHistoryPayload> {
  const settings = ServerConnection.makeSettings();
  const query = URLExt.objectToQueryString({
    path,
    ...(limit ? { limit: String(limit) } : {})
  });
  const response = await ServerConnection.makeRequest(
    `${url()}${query}`,
    { method: 'GET' },
    settings
  );
  if (!response.ok) {
    throw new ServerConnection.ResponseError(response);
  }
  return (await response.json()) as IHistoryPayload;
}

/**
 * Append one entry. Never throws into a caller: losing a history line is not
 * worth failing a review keystroke over, and the round itself is unaffected.
 */
export async function appendHistory(
  path: string,
  entry: IHistoryEntry
): Promise<void> {
  try {
    const settings = ServerConnection.makeSettings();
    await ServerConnection.makeRequest(
      url(),
      { method: 'POST', body: JSON.stringify({ path, entry }) },
      settings
    );
  } catch (err) {
    console.warn('[jupyter-vybe] history append failed', err);
  }
}
