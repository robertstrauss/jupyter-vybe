import type { NotebookPanel } from '@jupyterlab/notebook';
import type * as nbformat from '@jupyterlab/nbformat';

/**
 * Serialise the in-memory notebook for the agent, per prompts/filtering.md.
 *
 * Always from `panel.content.model.toJSON()` -- the live, unsaved model, never
 * the file on disk. Works with no kernel and with an untitled notebook.
 */

export interface ISnapshotOptions {
  includeOutputs: boolean;
  maxOutputChars: number;
}

export interface IKernelInfo {
  name: string;
  language: string;
  version: string;
}

export interface ISnapshot {
  json: string;
  kernel: IKernelInfo;
  cellIds: string[];
}

const RICH_TEXT = ['text/html', 'text/markdown', 'text/latex'];
const RICH_TEXT_LIMIT = 500;
const TRACEBACK_LINES = 20;

export function kernelInfo(panel: NotebookPanel): IKernelInfo {
  const meta = (
    panel.content.model?.toJSON() as nbformat.INotebookContent | undefined
  )?.metadata as Record<string, any> | undefined;
  const spec = meta?.kernelspec ?? {};
  const info = meta?.language_info ?? {};
  return {
    name: spec.name ?? spec.display_name ?? '',
    language: info.name ?? spec.language ?? '',
    version: info.version ?? ''
  };
}

export function buildSnapshot(
  panel: NotebookPanel,
  opts: ISnapshotOptions
): ISnapshot {
  const content = panel.content.model?.toJSON() as
    nbformat.INotebookContent | undefined;
  if (!content) {
    return {
      json: '{}',
      kernel: { name: '', language: '', version: '' },
      cellIds: []
    };
  }
  const meta = content.metadata as Record<string, any>;
  const cellIds: string[] = [];

  const cells = (content.cells ?? []).map(cell => {
    const c = cell as Record<string, any>;
    if (c.id) {
      cellIds.push(c.id);
    }
    const out: Record<string, any> = {
      id: c.id,
      cell_type: c.cell_type,
      source: normaliseSource(c.source)
    };
    if (c.cell_type === 'code') {
      out.execution_count = c.execution_count ?? null;
      const outputs = opts.includeOutputs
        ? filterOutputs(c.outputs ?? [], opts)
        : [];
      if (outputs.length) {
        out.outputs = outputs;
      }
    }
    const tags = c.metadata?.tags;
    if (Array.isArray(tags) && tags.length) {
      out.metadata = { tags };
    }
    if (c.attachments) {
      out.attachments = Object.keys(c.attachments).map(
        k => `<attachment: ${k}>`
      );
    }
    return out;
  });

  const filtered = {
    nbformat: content.nbformat,
    nbformat_minor: content.nbformat_minor,
    metadata: {
      kernelspec: meta?.kernelspec,
      language_info: meta?.language_info
    },
    cells
  };

  // Compact JSON: ~15% cheaper in tokens, no loss of comprehension.
  return { json: JSON.stringify(filtered), kernel: kernelInfo(panel), cellIds };
}

function normaliseSource(source: string | string[]): string {
  return Array.isArray(source) ? source.join('') : (source ?? '');
}

function filterOutputs(outputs: any[], opts: ISnapshotOptions): any[] {
  return outputs.map(o => {
    switch (o.output_type) {
      case 'stream':
        return {
          output_type: 'stream',
          name: o.name,
          text: elide(normaliseSource(o.text), opts.maxOutputChars)
        };
      case 'error':
        // Errors are the highest-signal content in a notebook and are exactly
        // what a "fix this" prompt is about, so they survive aggressively.
        return {
          output_type: 'error',
          ename: o.ename,
          evalue: o.evalue,
          traceback: (o.traceback ?? []).slice(-TRACEBACK_LINES)
        };
      case 'execute_result':
      case 'display_data':
        return {
          output_type: o.output_type,
          data: filterBundle(o.data ?? {}, opts)
        };
      default:
        return { output_type: o.output_type };
    }
  });
}

function filterBundle(
  data: Record<string, any>,
  opts: ISnapshotOptions
): Record<string, string> {
  const out: Record<string, string> = {};
  const plain = data['text/plain'];
  if (plain !== undefined) {
    out['text/plain'] = elide(normaliseSource(plain), opts.maxOutputChars);
  }
  for (const mime of Object.keys(data)) {
    if (mime === 'text/plain') {
      continue;
    }
    if (mime.startsWith('image/') || mime === 'application/pdf') {
      out[mime] = `<image ${mime}>`;
    } else if (mime.startsWith('application/vnd')) {
      out[mime] = `<rich output: ${mime}>`;
    } else if (RICH_TEXT.includes(mime)) {
      if (out['text/plain'] === undefined) {
        out[mime] = elide(normaliseSource(data[mime]), RICH_TEXT_LIMIT);
      }
    }
  }
  return out;
}

/** Middle-elide, so both the head and the tail of an output survive. */
export function elide(text: string, limit: number): string {
  if (text.length <= limit) {
    return text;
  }
  const half = Math.floor((limit - 24) / 2);
  const dropped = text.length - half * 2;
  return `${text.slice(0, half)}\n… ${dropped} chars elided …\n${text.slice(-half)}`;
}
