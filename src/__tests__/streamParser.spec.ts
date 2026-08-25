import { parse, StreamParser } from '../protocol/streamParser';
import type { VybeEvent } from '../protocol/types';

declare const __dirname: string;
declare const require: (module: string) => unknown;

const { readdirSync, readFileSync } = require('fs') as {
  readdirSync: (path: string) => string[];
  readFileSync: (path: string, encoding: string) => string;
};
const { join } = require('path') as {
  join: (...paths: string[]) => string;
};

const fixtureDirectory = join(__dirname, '../../tests/protocol');

function loadFixture(filename: string): {
  input: string;
  expected: VybeEvent[];
} {
  const contents = readFileSync(join(fixtureDirectory, filename), 'utf8');
  const [input, expected] = contents.split('\n---EXPECTED---\n');
  if (expected === undefined) {
    throw new Error(`missing expected-event delimiter in ${filename}`);
  }
  return {
    input,
    expected: expected
      .split('\n')
      .filter(Boolean)
      .map(line => JSON.parse(line) as VybeEvent)
  };
}

function coalesceProse(events: VybeEvent[]): VybeEvent[] {
  return events.reduce<VybeEvent[]>((coalesced, event) => {
    const previous = coalesced[coalesced.length - 1];
    if (event.kind === 'prose' && previous?.kind === 'prose') {
      previous.text += event.text;
    } else {
      coalesced.push({ ...event });
    }
    return coalesced;
  }, []);
}

describe('StreamParser golden fixtures', () => {
  const fixtures = readdirSync(fixtureDirectory)
    .filter(filename => filename.endsWith('.txt'))
    .sort();

  for (const filename of fixtures) {
    test(`${filename} parses in one shot`, () => {
      const { input, expected } = loadFixture(filename);
      expect(parse(input)).toEqual(expected);
    });

    test(`${filename} is safe across every chunk boundary`, () => {
      const { input, expected } = loadFixture(filename);
      if (input.length >= 400) {
        return;
      }
      const bytes = new TextEncoder().encode(input);
      for (let boundary = 0; boundary <= bytes.length; boundary++) {
        const decoder = new TextDecoder();
        const parser = new StreamParser();
        const actual = [
          ...parser.feed(
            decoder.decode(bytes.slice(0, boundary), { stream: true })
          ),
          ...parser.feed(decoder.decode(bytes.slice(boundary))),
          ...parser.close()
        ];
        expect(coalesceProse(actual)).toEqual(expected);
      }
    });
  }
});
