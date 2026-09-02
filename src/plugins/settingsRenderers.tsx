import type {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { IFormRendererRegistry } from '@jupyterlab/ui-components';
import { ServerConnection } from '@jupyterlab/services';
import { URLExt } from '@jupyterlab/coreutils';
import * as React from 'react';

import { validatePrefix } from '../notebook/prefixCheck';

/**
 * Custom settings widgets (docs/04), registered through IFormRendererRegistry.
 *
 * TWO RULES, both learned by breaking them:
 *
 * 1. **A field renderer must render the default field, not replace it.**
 *    `addRenderer` keys on `<pluginId>.<property>`, and our properties are
 *    object groups, so `formData` is the whole group. The first version built
 *    its own control from it and rendered the object as a React child, which
 *    throws (React #31) and blanks the entire settings panel — every setting
 *    for every group, gone. Wrapping `registry.fields.ObjectField` keeps
 *    JupyterLab's own rendering of every sub-field and adds ours beneath.
 *
 * 2. **Only top-level properties can have renderers.** `addRenderer` splits
 *    the id at its *last* dot, so `jupyter-vybe:settings.agent.command`
 *    registers against a plugin called `jupyter-vybe:settings.agent`, which
 *    does not exist. Two renderers were silently dead that way. T-RENDERERS
 *    in scripts/check-invariants.sh now fails on it.
 */

const SETTINGS_PLUGIN = 'jupyter-vybe:settings';

/**
 * What the benches actually found, per agent (docs/14). A dropdown that only
 * lists names makes the choice look arbitrary; it is not, and the trade is not
 * the one people assume -- the two local models are tied on tool use and are
 * not tied on the code they write.
 */
const AGENT_NOTES: Record<string, string> = {
  'ollama-qwen25-coder-7b':
    'Fast: about a second a call. Writes correct code 19 times in 24 on the quality bench.',
  'ollama-qwen25-coder-14b': 'Slower than the 7B, and not benchmarked here.',
  'ollama-qwen25-coder-3b':
    'Fastest and weakest; worth trying if the 7B is too slow.',
  'turbofieldfare-gemma4':
    'Writes correct code 8 times in 8, but a first prompt costs 17-30s. Later turns are far quicker.',
  command: 'Runs any binary that writes to stdout, e.g. claude -p.',
  'openai-compat':
    'Any OpenAI-compatible endpoint: vLLM, llama.cpp, LM Studio, hosted.'
};

interface IHealth {
  ok: boolean;
  detail: string;
  hostname: string;
}

async function fetchHealth(agentId: string): Promise<IHealth> {
  const settings = ServerConnection.makeSettings();
  const url =
    URLExt.join(settings.baseUrl, 'jupyter-vybe', 'health') +
    URLExt.objectToQueryString({ agent: agentId });
  const response = await ServerConnection.makeRequest(
    url,
    { method: 'GET' },
    settings
  );
  if (!response.ok) {
    throw new ServerConnection.ResponseError(response);
  }
  return (await response.json()) as IHealth;
}

/** Renders the group as JupyterLab would, then adds a line of our own. */
function withDefaultField(props: any, extra: React.ReactNode): JSX.Element {
  const ObjectField = props?.registry?.fields?.ObjectField;
  return (
    <div className="jv-settings-group">
      {ObjectField ? <ObjectField {...props} /> : null}
      {extra}
    </div>
  );
}

/** The agent group, plus a live reachability probe for the chosen agent. */
function AgentField(props: any): JSX.Element {
  const agentId: string = props?.formData?.id ?? '';
  const [status, setStatus] = React.useState('checking…');
  const [ok, setOk] = React.useState<boolean | null>(null);

  React.useEffect(() => {
    let live = true;
    setStatus('checking…');
    setOk(null);
    if (!agentId) {
      return;
    }
    fetchHealth(agentId)
      .then(health => {
        if (!live) {
          return;
        }
        setOk(health.ok);
        // The hostname is here on purpose: a tunnel aimed at the wrong
        // machine otherwise looks exactly like a working one (docs/13 R12).
        setStatus(
          health.ok
            ? `reachable — ${health.detail}${
                health.hostname ? ` on ${health.hostname}` : ''
              }`
            : health.detail
        );
      })
      .catch(err => {
        if (live) {
          setOk(false);
          setStatus(String(err));
        }
      });
    return () => {
      live = false;
    };
  }, [agentId]);

  return withDefaultField(
    props,
    <div className="jv-settings-extra">
      <div
        className={`jv-settings-status ${
          ok === null ? '' : ok ? 'jv-ok' : 'jv-bad'
        }`}
      >
        {status}
      </div>
      {AGENT_NOTES[agentId] ? (
        <div className="jv-settings-status">{AGENT_NOTES[agentId]}</div>
      ) : null}
    </div>
  );
}

/** The trigger group, plus a verdict on the prefix as it is typed. */
function TriggerField(props: any): JSX.Element {
  const prefix: string = props?.formData?.prefix ?? '';
  const verdict = validatePrefix(prefix);
  return withDefaultField(
    props,
    <div className={`jv-settings-status ${verdict.ok ? 'jv-ok' : 'jv-bad'}`}>
      {verdict.ok
        ? `A code cell starting with ${prefix} becomes a prompt.`
        : verdict.reason}
    </div>
  );
}

export const settingsRenderersPlugin: JupyterFrontEndPlugin<void> = {
  id: 'jupyter-vybe:settings-renderers',
  description: 'Custom settings widgets for the agent and the trigger prefix.',
  autoStart: true,
  requires: [IFormRendererRegistry],
  activate: (_app: JupyterFrontEnd, registry: IFormRendererRegistry): void => {
    registry.addRenderer(`${SETTINGS_PLUGIN}.agent`, {
      fieldRenderer: AgentField
    });
    registry.addRenderer(`${SETTINGS_PLUGIN}.trigger`, {
      fieldRenderer: TriggerField
    });
  }
};

export default settingsRenderersPlugin;
