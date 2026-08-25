export type ProseEvent = {
  kind: 'prose';
  text: string;
};

export type ToolEvent = {
  kind: 'tool';
  name: string;
  args: Record<string, string>;
};

export type ProtocolErrorEvent = {
  kind: 'protocol_error';
  text: string;
};

export type VybeEvent = ProseEvent | ToolEvent | ProtocolErrorEvent;
