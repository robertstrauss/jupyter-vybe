const jestJupyterLab = require('@jupyterlab/testutils/lib/jest-config');

const esModules = [
  '@codemirror',
  '@jupyter/ydoc',
  '@jupyterlab/',
  'lib0',
  'nanoid',
  'vscode-ws-jsonrpc',
  'y-protocols',
  'y-websocket',
  'yjs'
].join('|');

const baseConfig = jestJupyterLab(__dirname);

module.exports = {
  ...baseConfig,
  automock: false,
  moduleNameMapper: {
    ...(baseConfig.moduleNameMapper ?? {}),
    // @jupyterlab/ui-components pulls in the web-components stack, which is
    // ESM-only several layers deep and would otherwise have to be transformed
    // wholesale. Nothing here renders a form, so a stub is enough.
    '^@jupyter/react-components$': '<rootDir>/src/__tests__/stubs/empty.js',
    '^@jupyter/web-components$': '<rootDir>/src/__tests__/stubs/empty.js'
  },
  collectCoverageFrom: [
    'src/**/*.{ts,tsx}',
    '!src/**/*.d.ts',
    '!src/**/.ipynb_checkpoints/*'
  ],
  coverageReporters: ['lcov', 'text'],
  testRegex: 'src/.*/.*.spec.ts[x]?$',
  testPathIgnorePatterns: ['/node_modules/', '/src/__tests__/stubs/'],
  transformIgnorePatterns: [`/node_modules/(?!${esModules}).+`]
};
