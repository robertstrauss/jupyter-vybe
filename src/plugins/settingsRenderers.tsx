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
 * The settings panel (docs/04).
 *
 * Every group is rendered from its own schema by one component rather than by
 * JupyterLab's default object field, which buys three things the default
 * cannot give: a control can be greyed out when the rest of the settings make
 * it irrelevant, an empty box can show an example of what belongs in it, and
 * the prose can move out of the panel and behind an (i) that is only opened by
 * someone who wants it. A panel where a third of the text is about agents you
 * are not using is a panel nobody reads.
 *
 * TWO RULES, both learned by breaking them:
 *
 * 1. **Only top-level properties can have renderers.** `addRenderer` splits
 *    the id at its *last* dot, so `jupyter-vybe:settings.agent.command`
 *    registers against a plugin called `jupyter-vybe:settings.agent`, which
 *    does not exist. Two renderers were silently dead that way. T-RENDERERS
 *    in scripts/check-invariants.sh now fails on it.
 *
 * 2. **`formData` is the whole group.** Rendering it as a React child throws
 *    (React #31) and blanks the entire settings panel -- every setting for
 *    every group, gone. Sub-values are read and written by key.
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

/**
 * Examples, shown greyed in an empty box.
 *
 * Only for fields whose right answer is a shape rather than a value: knowing
 * that a command looks like `claude -p` is most of what there is to know about
 * the field, and it is not something a description can convey as quickly.
 */
const PLACEHOLDERS: Record<string, string> = {
  'agent.command': 'claude -p',
  'agent.endpoint': 'http://localhost:8000/v1',
  'agent.model': 'qwen2.5-coder:7b',
  'agent.apiKeyEnv': 'OPENAI_API_KEY',
  'agent.sidecarUrl': 'http://127.0.0.1:8791',
  'completion.agentId': 'same as the agent above',
  'completion.triggerKey': 'Alt-\\',
  'prompts.global': 'The shipped default is shown; edit it to replace it.'
};

/** Fields that want room to write in rather than a line to type on. */
const MULTILINE = new Set(['prompts.global']);

/**
 * Which fields the rest of the settings make irrelevant.
 *
 * Greyed rather than hidden: a control that disappears leaves the user
 * wondering whether they imagined it, and the reason it is off is exactly the
 * setting sitting above it.
 */
const RELEVANCE: Record<string, (group: Record<string, any>) => string | null> =
  {
    'agent.command': g =>
      g.id === 'command' ? null : 'Only used by the "command" agent.',
    'agent.commandPersistent': g =>
      g.id === 'command' ? null : 'Only used by the "command" agent.',
    'agent.endpoint': g =>
      g.id === 'openai-compat'
        ? null
        : 'Only used by the "openai-compat" agent.',
    'agent.model': g =>
      g.id === 'openai-compat'
        ? null
        : 'Only used by the "openai-compat" agent.',
    'agent.apiKeyEnv': g =>
      g.id === 'openai-compat'
        ? null
        : 'Only used by the "openai-compat" agent.',
    'agent.sidecarUrl': g =>
      g.host === 'client'
        ? null
        : 'Only used when the agent runs on your machine.',
    'context.maxOutputChars': g =>
      g.includeOutputs ? null : 'Outputs are not being sent.',
    'history.location': g => (g.persist ? null : 'History is not being kept.'),
    'completion.enabled': () => null,
    'completion.timeoutSeconds': g =>
      g.enabled ? null : 'Inline completion is off.',
    'completion.triggerKey': g =>
      g.enabled ? null : 'Inline completion is off.',
    'completion.maxLines': g =>
      g.enabled ? null : 'Inline completion is off.',
    'completion.agentId': g => (g.enabled ? null : 'Inline completion is off.')
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

/** The shipped prompt text, so the global prompt box can start from it. */
async function fetchDefaults(): Promise<Record<string, string>> {
  const settings = ServerConnection.makeSettings();
  const url = URLExt.join(settings.baseUrl, 'jupyter-vybe', 'defaults');
  const response = await ServerConnection.makeRequest(
    url,
    { method: 'GET' },
    settings
  );
  if (!response.ok) {
    throw new ServerConnection.ResponseError(response);
  }
  return (await response.json()) as Record<string, string>;
}

/**
 * The (i). Opens on hover for a glance and stays open on click for a read,
 * which is the difference between a description you skim past and one you go
 * looking for.
 */
function Info(props: { text: string }): JSX.Element {
  const [pinned, setPinned] = React.useState(false);
  return (
    <span className={`jv-info ${pinned ? 'jv-info-pinned' : ''}`}>
      <button
        type="button"
        className="jv-info-button"
        aria-expanded={pinned}
        aria-label="What this does"
        onClick={() => setPinned(p => !p)}
      >
        i
      </button>
      <span className="jv-info-bubble" role="note">
        {props.text}
      </span>
    </span>
  );
}

/**
 * The group's own heading.
 *
 * JupyterLab's default object field draws one, and replacing that field means
 * taking the heading on too -- without it the panel is an unbroken column of
 * controls with no way to tell where the agent settings end and the context
 * settings begin.
 */
function GroupHeading(props: { schema: any }): JSX.Element | null {
  const title = props.schema?.title;
  if (!title) {
    return null;
  }
  return (
    <h3 className="jv-settings-heading">
      {title}
      {props.schema.description ? (
        <Info text={props.schema.description} />
      ) : null}
    </h3>
  );
}

/**
 * The group to write back, carrying only what is actually set.
 *
 * rjsf hands us `formData` already filled in with every schema default, so
 * writing it back verbatim persists all of them -- and a value in the user's
 * settings file is a value that no longer follows the default when we change
 * it. That is not hypothetical: it pinned `triggerKey: "Alt-Tab"` into a real
 * settings file, so moving the default to `Alt-\` had no effect for the one
 * person who had ever opened this panel.
 *
 * So a key is written only when it differs from its default, or when it is
 * the one being changed. Anything left at a default stays absent, and keeps
 * following it.
 */
function groupUpdate(
  data: Record<string, any>,
  properties: Record<string, any>,
  key: string,
  value: any
): Record<string, any> {
  const next: Record<string, any> = {};
  for (const name of Object.keys(data)) {
    const current = name === key ? value : data[name];
    const fallback = properties[name]?.default;
    if (name === key || fallback === undefined || current !== fallback) {
      next[name] = current;
    }
  }
  if (!(key in next)) {
    next[key] = value;
  }
  return next;
}

interface IFieldProps {
  group: string;
  name: string;
  schema: any;
  value: any;
  disabledReason: string | null;
  onChange: (value: any) => void;
}

function Field(props: IFieldProps): JSX.Element {
  const { schema, value, disabledReason } = props;
  const key = `${props.group}.${props.name}`;
  const id = `jv-${props.group}-${props.name}`;
  const disabled = disabledReason !== null;
  const placeholder = PLACEHOLDERS[key];
  const common = { id, disabled, className: 'jv-field-control' };

  let control: JSX.Element;
  if (schema.type === 'boolean') {
    control = (
      <input
        {...common}
        type="checkbox"
        checked={!!value}
        onChange={e => props.onChange(e.target.checked)}
      />
    );
  } else if (Array.isArray(schema.enum)) {
    control = (
      <select
        {...common}
        value={value ?? ''}
        onChange={e => props.onChange(e.target.value)}
      >
        {schema.enum.map((option: string) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  } else if (schema.type === 'integer' || schema.type === 'number') {
    control = (
      <input
        {...common}
        type="number"
        value={value ?? ''}
        onChange={e => {
          const next = e.target.value;
          // An empty box is a box being cleared, not a zero: writing 0 into
          // the settings under the user's cursor makes the field unusable.
          props.onChange(next === '' ? undefined : Number(next));
        }}
      />
    );
  } else if (MULTILINE.has(key)) {
    control = (
      <textarea
        {...common}
        rows={12}
        placeholder={placeholder}
        value={value ?? ''}
        onChange={e => props.onChange(e.target.value)}
      />
    );
  } else {
    control = (
      <input
        {...common}
        type="text"
        placeholder={placeholder}
        value={value ?? ''}
        onChange={e => props.onChange(e.target.value)}
      />
    );
  }

  return (
    <div className={`jv-field ${disabled ? 'jv-field-disabled' : ''}`}>
      <label className="jv-field-label" htmlFor={id}>
        {schema.title ?? props.name}
        {schema.description ? <Info text={schema.description} /> : null}
      </label>
      {control}
      {disabledReason ? (
        <div className="jv-field-reason">{disabledReason}</div>
      ) : null}
    </div>
  );
}

/** Renders one settings group from its schema, and whatever else it needs. */
function group(
  name: string,
  extra?: (data: Record<string, any>) => React.ReactNode
) {
  return function Group(props: any): JSX.Element {
    const data: Record<string, any> = props?.formData ?? {};
    const properties: Record<string, any> = props?.schema?.properties ?? {};
    const set = (key: string, value: any) =>
      props.onChange(groupUpdate(data, properties, key, value));

    return (
      <div className="jv-settings-group">
        <GroupHeading schema={props?.schema} />
        {Object.keys(properties).map(key => (
          <Field
            key={key}
            group={name}
            name={key}
            schema={properties[key]}
            value={data[key]}
            disabledReason={RELEVANCE[`${name}.${key}`]?.(data) ?? null}
            onChange={value => set(key, value)}
          />
        ))}
        {extra ? extra(data) : null}
      </div>
    );
  };
}

/** Whether the chosen agent can actually be reached, and what it is. */
function AgentHealth(props: { agentId: string }): JSX.Element {
  const { agentId } = props;
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

  return (
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

/**
 * The global prompt, starting from the text that is actually in use.
 *
 * An empty box means "use the shipped default", which tells the user nothing
 * about what that default says -- so the box is filled with it. Nothing is
 * written to settings until they change something: pinning today's default
 * into their configuration as a side effect of opening the panel would mean
 * they never see the next one.
 */
function GlobalPrompt(props: any): JSX.Element {
  const [shipped, setShipped] = React.useState('');
  React.useEffect(() => {
    let live = true;
    fetchDefaults()
      .then(d => {
        if (live) {
          setShipped(d.globalPrompt ?? '');
        }
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  const data: Record<string, any> = props?.formData ?? {};
  const properties: Record<string, any> = props?.schema?.properties ?? {};
  const set = (key: string, value: any) =>
    props.onChange(groupUpdate(data, properties, key, value));

  return (
    <div className="jv-settings-group">
      <GroupHeading schema={props?.schema} />
      {Object.keys(properties).map(key => (
        <Field
          key={key}
          group="prompts"
          name={key}
          schema={properties[key]}
          value={key === 'global' ? data.global || shipped : data[key]}
          disabledReason={null}
          onChange={value => set(key, value)}
        />
      ))}
    </div>
  );
}

/** The trigger group, plus a verdict on the prefix as it is typed. */
const TriggerField = group('trigger', data => {
  const verdict = validatePrefix(data.prefix ?? '');
  return (
    <div className={`jv-settings-status ${verdict.ok ? 'jv-ok' : 'jv-bad'}`}>
      {verdict.ok
        ? `A code cell starting with ${data.prefix} becomes a prompt.`
        : verdict.reason}
    </div>
  );
});

const AgentField = group('agent', data => (
  <AgentHealth agentId={data.id ?? ''} />
));

export const settingsRenderersPlugin: JupyterFrontEndPlugin<void> = {
  id: 'jupyter-vybe:settings-renderers',
  description: 'The settings panel, rendered from the schema.',
  autoStart: true,
  requires: [IFormRendererRegistry],
  activate: (_app: JupyterFrontEnd, registry: IFormRendererRegistry): void => {
    registry.addRenderer(`${SETTINGS_PLUGIN}.agent`, {
      fieldRenderer: AgentField
    });
    registry.addRenderer(`${SETTINGS_PLUGIN}.trigger`, {
      fieldRenderer: TriggerField
    });
    for (const name of [
      'context',
      'agentTools',
      'review',
      'completion',
      'history',
      'privacy'
    ]) {
      registry.addRenderer(`${SETTINGS_PLUGIN}.${name}`, {
        fieldRenderer: group(name)
      });
    }
    registry.addRenderer(`${SETTINGS_PLUGIN}.prompts`, {
      fieldRenderer: GlobalPrompt
    });
  }
};

export default settingsRenderersPlugin;
