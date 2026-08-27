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
  /** Token budget for the notebook portion. 0 disables truncation. */
  budgetTokens?: number;
  /** Cells this far from the prompt cell are "distant" (0 = no window). */
  cellWindow?: number;
  promptCellId?: string;
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
  /** What the ladder had to drop, in the order it dropped it (docs/09). */
  dropped: string[];
  estimatedTokens: number;
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
      cellIds: [],
      dropped: [],
      estimatedTokens: 0
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

  const dropped = truncate(cells, opts);

  // Compact JSON: ~15% cheaper in tokens, no loss of comprehension.
  const json = JSON.stringify(filtered);
  return {
    json,
    kernel: kernelInfo(panel),
    cellIds,
    dropped,
    estimatedTokens: estimateTokens(json)
  };
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

/**
 * Deliberately not a real tokenizer (docs/09): shipping one costs ~2MB of WASM
 * and several hundred ms of startup, for a number that only has to be right to
 * within about 10%.
 */
export function estimateTokens(
  text: string,
  kind: 'code' | 'prose' = 'code'
): number {
  return Math.ceil(text.length / (kind === 'code' ? 3.6 : 4.2));
}

const IMPORT_RE = /^[ \t]*(?:import|from|require|library|using)\b/m;
const SIGNATURE_RE =
  /^[ \t]*(?:async[ \t]+)?(?:def|class|function|const|let|var|import|from)\b.*$/gm;

/** The signature summary from ladder stage 4: the namespace at ~5% of the cost. */
function signatureSummary(source: string): string {
  const lines = source.match(SIGNATURE_RE) ?? [];
  const body = lines.map(l => `${l.trim()} …`).join('\n');
  return body || '# (body omitted to fit the context window)';
}

/**
 * Drop content until the notebook fits, in the order docs/09 specifies.
 *
 * The prompt cell, its immediate neighbours, and every import-bearing cell are
 * never touched: the first two are what the request is about, and the third is
 * the namespace the agent needs to write code that runs. Stage 4 keeps
 * signatures rather than deleting bodies outright, which also keeps the
 * identifier casing that style inference reads (D8) -- so style degrades
 * gracefully instead of falling back to the language default.
 *
 * Mutates `cells` in place and returns a description of what went.
 */
export function truncate(
  cells: Record<string, any>[],
  opts: ISnapshotOptions
): string[] {
  const budget = opts.budgetTokens ?? 0;
  if (!budget) {
    return [];
  }
  const dropped: string[] = [];
  const size = () => estimateTokens(JSON.stringify(cells));
  if (size() <= budget) {
    return dropped;
  }

  const promptIndex = cells.findIndex(c => c.id === opts.promptCellId);
  const near = (i: number, window: number) =>
    promptIndex === -1 || Math.abs(i - promptIndex) <= window;
  const protectedCell = (c: Record<string, any>, i: number) =>
    near(i, 3) || IMPORT_RE.test(String(c.source ?? ''));

  const stage = (
    label: string,
    apply: (cell: Record<string, any>, index: number) => boolean
  ): boolean => {
    let touched = 0;
    for (let i = 0; i < cells.length; i++) {
      if (protectedCell(cells[i], i)) {
        continue;
      }
      if (apply(cells[i], i)) {
        touched++;
      }
      if (size() <= budget) {
        break;
      }
    }
    if (touched) {
      dropped.push(`${label} (${touched} cell${touched === 1 ? '' : 's'})`);
    }
    return size() <= budget;
  };

  const window = opts.cellWindow ?? 0;
  const hasOutputs = (c: Record<string, any>) => (c.outputs ?? []).length > 0;

  if (
    window &&
    stage('outputs of distant cells', (c, i) => {
      if (near(i, window) || !hasOutputs(c)) {
        return false;
      }
      delete c.outputs;
      return true;
    })
  ) {
    return dropped;
  }
  if (
    stage('outputs except errors', c => {
      if (!hasOutputs(c)) {
        return false;
      }
      const errors = (c.outputs as any[]).filter(
        o => o.output_type === 'error'
      );
      if (errors.length === c.outputs.length) {
        return false;
      }
      if (errors.length) {
        c.outputs = errors;
      } else {
        delete c.outputs;
      }
      return true;
    })
  ) {
    return dropped;
  }
  if (
    stage('markdown sources', c => {
      if (c.cell_type !== 'markdown' || !c.source) {
        return false;
      }
      c.source = '<markdown omitted to fit the context window>';
      return true;
    })
  ) {
    return dropped;
  }
  stage('code bodies, signatures kept', c => {
    if (c.cell_type !== 'code' || !c.source) {
      return false;
    }
    const summary = signatureSummary(String(c.source));
    if (summary.length >= String(c.source).length) {
      return false;
    }
    c.source = summary;
    return true;
  });
  return dropped;
}
