/**
 * A trigger prefix must be unambiguous: it cannot be something the kernel
 * already claims, and it cannot begin a valid line of code, or the extension
 * could not tell a prompt from an expression (docs/02).
 */

/** Claimed by IPython / the kernel. */
const RESERVED = ['!', '?', '??', '%', '%%', '/', '$'];

/**
 * Characters that can legally start a Python expression or statement. A prefix
 * starting with one of these is rejected: `-x`, `(a)`, `[1]`, `*args`, `~m` and
 * bare identifiers are all real code.
 */
const CODE_START = /^[A-Za-z0-9_\-+~([{'"*.@]/;

export interface IPrefixVerdict {
  ok: boolean;
  reason?: string;
}

export function validatePrefix(prefix: string): IPrefixVerdict {
  if (!prefix) {
    return { ok: false, reason: 'The prefix cannot be empty.' };
  }
  if (/\s/.test(prefix)) {
    return { ok: false, reason: 'The prefix cannot contain whitespace.' };
  }
  if (RESERVED.includes(prefix)) {
    return {
      ok: false,
      reason: `'${prefix}' is already used by the kernel (shell, help, or magics).`
    };
  }
  if (RESERVED.some(r => prefix.startsWith(r))) {
    return {
      ok: false,
      reason: `A prefix starting with '${prefix[0]}' collides with a kernel feature.`
    };
  }
  if (CODE_START.test(prefix)) {
    return {
      ok: false,
      reason: `'${prefix}' can start a valid line of code, so it would be ambiguous.`
    };
  }
  return { ok: true };
}
