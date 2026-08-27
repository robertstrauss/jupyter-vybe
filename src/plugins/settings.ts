import type { JupyterFrontEndPlugin } from '@jupyterlab/application';
import { ISettingRegistry } from '@jupyterlab/settingregistry';
import { Signal } from '@lumino/signaling';

import { IVybeSettings } from '../tokens';
import { validatePrefix } from '../notebook/prefixCheck';

const PLUGIN_ID = 'jupyter-vybe:settings';

class VybeSettings implements IVybeSettings {
  private _s: ISettingRegistry.ISettings | null = null;
  private _changed = new Signal<this, void>(this);

  bind(settings: ISettingRegistry.ISettings): void {
    this._s = settings;
    settings.changed.connect(() => this._changed.emit());
    this._changed.emit();
  }

  get changed() {
    return this._changed;
  }

  private group<T>(name: string): Record<string, T> {
    return ((this._s?.composite[name] as unknown) ?? {}) as Record<string, T>;
  }

  get enabled(): boolean {
    return (this._s?.composite['enabled'] as boolean) ?? true;
  }

  get prefix(): string {
    const raw = (this.group<string>('trigger')['prefix'] as string) ?? '>';
    // A prefix that parses as code would make execution ambiguous; fall back
    // rather than silently swallowing real code cells (docs/02).
    return validatePrefix(raw).ok ? raw : '>';
  }

  get agentId(): string {
    return (
      (this.group<string>('agent')['id'] as string) ?? 'ollama-qwen25-coder-7b'
    );
  }
  get agentHost(): 'server' | 'client' {
    return (
      (this.group<string>('agent')['host'] as 'server' | 'client') ?? 'server'
    );
  }
  get sidecarUrl(): string {
    return (
      (this.group<string>('agent')['sidecarUrl'] as string) ??
      'http://127.0.0.1:8137'
    );
  }
  get agentOptions(): Record<string, string> {
    // Whatever the chosen adapter needs. Sent as strings: the server owns
    // the meaning of each key, and the API key is a variable *name*, never
    // the key itself.
    const agent = this.group<unknown>('agent');
    return {
      command: String(agent['command'] ?? ''),
      persistent: String(agent['commandPersistent'] ?? false),
      endpoint: String(agent['endpoint'] ?? ''),
      model: String(agent['model'] ?? ''),
      apiKeyEnv: String(agent['apiKeyEnv'] ?? '')
    };
  }
  get persistent(): boolean {
    return (this.group<boolean>('context')['persistent'] as boolean) ?? true;
  }
  get windowTokens(): number {
    return (this.group<number>('context')['windowTokens'] as number) ?? 32768;
  }
  get cellWindow(): number {
    return (this.group<number>('context')['cellWindow'] as number) ?? 0;
  }
  get includeOutputs(): boolean {
    return (
      (this.group<boolean>('context')['includeOutputs'] as boolean) ?? true
    );
  }
  get maxOutputChars(): number {
    return (this.group<number>('context')['maxOutputChars'] as number) ?? 2000;
  }
  get historyPersist(): boolean {
    // On by default (D12): a record that vanishes when a cell is tidied away
    // is not a record.
    return (this.group<boolean>('history')['persist'] as boolean) ?? true;
  }
  get historyMaxEntries(): number {
    // Bounds what the pane renders, never what is stored.
    return (this.group<number>('history')['maxEntries'] as number) ?? 200;
  }
  get globalPrompt(): string {
    return (this.group<string>('prompts')['global'] as string) ?? '';
  }
  get maxInsertions(): number {
    return (this.group<number>('agentTools')['maxInsertions'] as number) ?? 8;
  }
  get maxProposals(): number {
    return (this.group<number>('agentTools')['maxProposals'] as number) ?? 12;
  }
  get maxToolTurns(): number {
    return (this.group<number>('agentTools')['maxToolTurns'] as number) ?? 6;
  }
  get kernelIntrospection(): 'off' | 'inspect-only' | 'inspect+complete' {
    return (
      (this.group<string>('agentTools')['kernelIntrospection'] as
        'off' | 'inspect-only' | 'inspect+complete') ?? 'inspect+complete'
    );
  }
  get acceptKey(): 'yn' | 'enter-backspace' {
    return (
      (this.group<string>('review')['acceptKey'] as 'yn' | 'enter-backspace') ??
      'yn'
    );
  }
  get mergeCommentStyle(): 'auto' | 'hash' | 'slashes' | 'none' {
    return (
      (this.group<string>('review')['mergeCommentStyle'] as
        'auto' | 'hash' | 'slashes' | 'none') ?? 'auto'
    );
  }
  get autoScroll(): boolean {
    return (this.group<boolean>('review')['autoScroll'] as boolean) ?? true;
  }
}

export const settingsPlugin: JupyterFrontEndPlugin<IVybeSettings> = {
  id: PLUGIN_ID,
  description: 'Jupyter Vybe settings.',
  autoStart: true,
  provides: IVybeSettings,
  optional: [ISettingRegistry],
  activate: (_app, registry: ISettingRegistry | null): IVybeSettings => {
    const vybe = new VybeSettings();
    if (registry) {
      registry
        .load(PLUGIN_ID)
        .then(s => vybe.bind(s))
        .catch(err =>
          console.error('[jupyter-vybe] settings failed to load', err)
        );
    }
    return vybe;
  }
};

export default settingsPlugin;
