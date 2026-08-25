// Stub for @jupyter/react-components and @jupyter/web-components (see
// jest.config.js). Both are ESM-only several layers deep; nothing under test
// renders a form or a toolbar, so every export resolves to a callable that
// keeps returning the same stub -- enough for module-level registration calls
// like provideJupyterDesignSystem().register(...) to run.
const stub = new Proxy(function () {}, {
  get: (_t, prop) => (prop === 'then' ? undefined : stub),
  apply: () => stub,
  construct: () => stub
});

module.exports = stub;
