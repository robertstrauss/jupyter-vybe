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
 * Custom settings widgets (docs/04), registered through IFormRendererRegistry
 * -- the supported JupyterLab 4 path, no forked settings editor.
 *
 * Three fields earn one, and only these three:
 *
 * * `agent.id`, because the list of agents lives on the server and whether one
 *   is *reachable* cannot be known from a schema at all;
 * * `trigger.prefix`, because a prefix that parses as code silently swallows
 *   real code cells, and finding that out at runtime is a bad way to find out;
 * * `agent.command` / `agent.endpoint`, which apply to one adapter each and
 *   are noise for everyone else.
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

interface IAgentInfo {
  id: string;
  label: string;
  contextWindow: number;
  local: boolean;
  requiresConfig: string[];
}

async function fetchAgents(): Promise<IAgentInfo[]> {
  const settings = ServerConnection.makeSettings();
  const response = await ServerConnection.makeRequest(
    URLExt.join(settings.baseUrl, 'jupyter-vybe', 'agents'),
    { method: 'GET' },
    settings
  );
  if (!response.ok) {
    throw new ServerConnection.ResponseError(response);
  }
  return ((await response.json()).agents ?? []) as IAgentInfo[];
}

async function fetchHealth(agentId: string): Promise<{
  ok: boolean;
  detail: string;
  host: string;
  hostname: string;
}> {
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
  return await response.json();
}

/** The agent picker, with a live reachability probe beneath it. */
function AgentField(props: any): JSX.Element {
  const [agents, setAgents] = React.useState<IAgentInfo[]>([]);
  const [status, setStatus] = React.useState<string>('checking…');
  const [ok, setOk] = React.useState<boolean | null>(null);
  const value: string = props.formData ?? '';

  React.useEffect(() => {
    let live = true;
    fetchAgents()
      .then(list => live && setAgents(list))
      .catch(() => live && setAgents([]));
    return () => {
      live = false;
    };
  }, []);

  React.useEffect(() => {
    let live = true;
    setStatus('checking…');
    setOk(null);
    if (!value) {
      return;
    }
    fetchHealth(value)
      .then(health => {
        if (!live) {
          return;
        }
        setOk(health.ok);
        // The hostname is here on purpose: a tunnel aimed at the wrong
        // machine looks exactly like a working one otherwise (docs/13 R12).
        setStatus(
          health.ok
            ? `reachable — ${health.detail}${health.hostname ? ` on ${health.hostname}` : ''}`
            : health.detail
        );
      })
      .catch(err => live && (setOk(false), setStatus(String(err))));
    return () => {
      live = false;
    };
  }, [value]);

  const known = agents.length
    ? agents
    : [
        {
          id: value,
          label: value,
          contextWindow: 0,
          local: true,
          requiresConfig: []
        }
      ];

  return (
    <div className="jv-settings-field">
      <label className="jv-settings-label">Agent</label>
      <select
        className="jv-settings-select"
        value={value}
        onChange={event => props.onChange(event.target.value)}
      >
        {known.map(agent => (
          <option key={agent.id} value={agent.id}>
            {agent.label}
            {agent.local ? '' : ' (remote)'}
          </option>
        ))}
      </select>
      <div
        className={`jv-settings-status ${
          ok === null ? '' : ok ? 'jv-ok' : 'jv-bad'
        }`}
      >
        {status}
      </div>
      {AGENT_NOTES[value] ? (
        <div className="jv-settings-status">{AGENT_NOTES[value]}</div>
      ) : null}
    </div>
  );
}

/** A prefix that parses as code would swallow real cells; say so immediately. */
function PrefixField(props: any): JSX.Element {
  const value: string = props.formData ?? '';
  const verdict = validatePrefix(value);
  return (
    <div className="jv-settings-field">
      <label className="jv-settings-label">Prompt cell prefix</label>
      <input
        className="jv-settings-input"
        value={value}
        onChange={event => props.onChange(event.target.value)}
      />
      <div className={`jv-settings-status ${verdict.ok ? 'jv-ok' : 'jv-bad'}`}>
        {verdict.ok
          ? `A code cell starting with ${value} becomes a prompt.`
          : verdict.reason}
      </div>
    </div>
  );
}

/** A field that only matters for one adapter, labelled as such. */
function adapterField(
  label: string,
  forAgent: string,
  placeholder: string,
  help: string
) {
  return function AdapterField(props: any): JSX.Element {
    return (
      <div className="jv-settings-field">
        <label className="jv-settings-label">
          {label} <span className="jv-settings-scope">agent: {forAgent}</span>
        </label>
        <input
          className="jv-settings-input"
          value={props.formData ?? ''}
          placeholder={placeholder}
          onChange={event => props.onChange(event.target.value)}
        />
        <div className="jv-settings-status">{help}</div>
      </div>
    );
  };
}

export const settingsRenderersPlugin: JupyterFrontEndPlugin<void> = {
  id: 'jupyter-vybe:settings-renderers',
  description:
    'Custom settings widgets for the agent, prefix, and adapter fields.',
  autoStart: true,
  requires: [IFormRendererRegistry],
  activate: (_app: JupyterFrontEnd, registry: IFormRendererRegistry): void => {
    const add = (property: string, component: (props: any) => JSX.Element) =>
      registry.addRenderer(`${SETTINGS_PLUGIN}.${property}`, {
        fieldRenderer: component
      });

    add('agent', AgentField);
    add('trigger', PrefixField);
    add(
      'agent.command',
      adapterField(
        'Command',
        'command',
        'claude -p',
        'Split with shlex and run without a shell. The prompt is the last argument.'
      )
    );
    add(
      'agent.endpoint',
      adapterField(
        'Endpoint',
        'openai-compat',
        'http://127.0.0.1:8000',
        'Base URL. The health check lists the models it actually serves.'
      )
    );
  }
};

export default settingsRenderersPlugin;
