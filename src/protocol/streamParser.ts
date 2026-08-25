import { BODY_TOOLS, KNOWN_TOOLS, TAG_OPEN, VOID_TOOLS } from './grammar';
import type { VybeEvent } from './types';

const ATTR_RE = /([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"/g;
const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*/;

enum State {
  Text,
  Tag,
  Body
}

export class StreamParser {
  private buffer = '';
  private state = State.Text;
  private tool = '';
  private args: Record<string, string> = {};
  private body = '';
  private closed = false;

  feed(chunk: string): VybeEvent[] {
    this.buffer += chunk;
    return this.drain(false);
  }

  close(): VybeEvent[] {
    if (this.closed) {
      return [];
    }
    this.closed = true;
    const events = this.drain(true);
    if (this.state === State.Text) {
      if (this.buffer) {
        events.push({ kind: 'prose', text: this.buffer });
        this.buffer = '';
      }
    } else if (this.state === State.Tag) {
      events.push({
        kind: 'protocol_error',
        text: `stream ended inside a tag: ${pythonRepr(this.buffer.slice(0, 80))}`
      });
    } else {
      events.push({
        kind: 'protocol_error',
        text: `stream ended inside <vybe:${this.tool}> body; partial cell discarded`
      });
    }
    this.buffer = '';
    return events;
  }

  private drain(final: boolean): VybeEvent[] {
    const events: VybeEvent[] = [];
    while (true) {
      if (this.state === State.Text) {
        const index = this.buffer.indexOf(TAG_OPEN);
        if (index === -1) {
          const keep = final ? 0 : partialSuffix(this.buffer, TAG_OPEN);
          const emit = keep ? this.buffer.slice(0, -keep) : this.buffer;
          this.buffer = keep ? this.buffer.slice(-keep) : '';
          if (emit) {
            events.push({ kind: 'prose', text: emit });
          }
          return events;
        }
        if (index) {
          events.push({ kind: 'prose', text: this.buffer.slice(0, index) });
        }
        this.buffer = this.buffer.slice(index);
        this.state = State.Tag;
      } else if (this.state === State.Tag) {
        const end = this.buffer.indexOf('>');
        if (end === -1) {
          return events;
        }
        const raw = this.buffer.slice(0, end + 1);
        this.buffer = this.buffer.slice(end + 1);
        events.push(...this.openTag(raw));
      } else {
        const close = `</${TAG_OPEN.slice(1)}${this.tool}>`;
        const index = this.buffer.indexOf(close);
        if (index === -1) {
          const keep = final ? 0 : partialSuffix(this.buffer, close);
          this.body += keep ? this.buffer.slice(0, -keep) : this.buffer;
          this.buffer = keep ? this.buffer.slice(-keep) : '';
          return events;
        }
        this.body += this.buffer.slice(0, index);
        this.buffer = this.buffer.slice(index + close.length);
        const args = { ...this.args, body: trimBody(this.body) };
        events.push({ kind: 'tool', name: this.tool, args });
        this.tool = '';
        this.args = {};
        this.body = '';
        this.state = State.Text;
      }
    }
  }

  private openTag(raw: string): VybeEvent[] {
    let inner = rstrip(raw.slice(TAG_OPEN.length, -1));
    const voidTag = inner.endsWith('/');
    if (voidTag) {
      inner = rstrip(inner.slice(0, -1));
    }
    const match = NAME_RE.exec(inner);
    if (!match) {
      this.state = State.Text;
      return [
        {
          kind: 'protocol_error',
          text: `malformed tool tag: ${pythonRepr(raw)}`
        }
      ];
    }
    const name = match[0];
    const args: Record<string, string> = {};
    ATTR_RE.lastIndex = 0;
    let attribute: RegExpExecArray | null;
    while ((attribute = ATTR_RE.exec(inner.slice(match[0].length))) !== null) {
      args[attribute[1]] = attribute[2];
    }
    if (!KNOWN_TOOLS.has(name)) {
      this.state = State.Text;
      return [
        {
          kind: 'protocol_error',
          text: `unknown tool ${pythonRepr(name)}; known tools: ${[...KNOWN_TOOLS].sort().join(', ')}`
        }
      ];
    }
    if (voidTag) {
      if (!VOID_TOOLS.has(name)) {
        this.state = State.Text;
        return [
          {
            kind: 'protocol_error',
            text: `<vybe:${name}> requires a body, got a void tag`
          }
        ];
      }
      this.state = State.Text;
      return [{ kind: 'tool', name, args }];
    }
    if (!BODY_TOOLS.has(name)) {
      this.state = State.Text;
      return [
        {
          kind: 'protocol_error',
          text: `<vybe:${name}> is a void tool; use <vybe:${name} ... />`
        }
      ];
    }
    this.tool = name;
    this.args = args;
    this.body = '';
    this.state = State.Body;
    return [];
  }
}

function partialSuffix(buffer: string, token: string): number {
  for (
    let length = Math.min(token.length - 1, buffer.length);
    length > 0;
    length--
  ) {
    if (buffer.slice(-length) === token.slice(0, length)) {
      return length;
    }
  }
  return 0;
}

function trimBody(body: string): string {
  if (body.startsWith('\r\n')) {
    body = body.slice(2);
  } else if (body.startsWith('\n')) {
    body = body.slice(1);
  }
  if (body.endsWith('\r\n')) {
    return body.slice(0, -2);
  }
  if (body.endsWith('\n')) {
    return body.slice(0, -1);
  }
  return body;
}

function rstrip(value: string): string {
  let end = value.length;
  while (end > 0 && isPythonWhitespace(value.charCodeAt(end - 1))) {
    end--;
  }
  return value.slice(0, end);
}

function isPythonWhitespace(code: number): boolean {
  return (
    (code >= 0x0009 && code <= 0x000d) ||
    (code >= 0x001c && code <= 0x001f) ||
    code === 0x0020 ||
    code === 0x0085 ||
    code === 0x00a0 ||
    code === 0x1680 ||
    (code >= 0x2000 && code <= 0x200a) ||
    code === 0x2028 ||
    code === 0x2029 ||
    code === 0x202f ||
    code === 0x205f ||
    code === 0x3000
  );
}

function pythonRepr(value: string): string {
  return `'${value
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '\\r')
    .replace(/\t/g, '\\t')}'`;
}

export function parse(text: string): VybeEvent[] {
  const parser = new StreamParser();
  return [...parser.feed(text), ...parser.close()];
}
