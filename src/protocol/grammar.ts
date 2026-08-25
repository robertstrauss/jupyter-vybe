export const TAG_OPEN = '<vybe:';

export const BODY_TOOLS = new Set(['add_cell', 'propose_edit']);
export const VOID_TOOLS = new Set(['ipython_help', 'complete']);
export const KNOWN_TOOLS = new Set([...BODY_TOOLS, ...VOID_TOOLS]);
