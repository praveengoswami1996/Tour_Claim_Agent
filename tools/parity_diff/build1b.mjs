// Usage: node build1b.mjs <out.mjs> [entry, default run1b.mjs]. Bundles the entry with the TypeScript sources of Tour_React (its tsconfig paths resolve '@/'); axios is stubbed:
// GET answers from globalThis.__AXIOS[url] (null/undefined there = the call fails).
import { createRequire } from 'module';
const require = createRequire('D:/CETPA/Tour_React/package.json');
const esbuild = require('esbuild');
const stub = {
  name: 'axios-stub',
  setup(b) {
    b.onResolve({ filter: /^@\/services\/axiosInstance$/ }, () => ({ path: 'axios-stub', namespace: 'stub' }));
    b.onLoad({ filter: /.*/, namespace: 'stub' }, () => ({
      contents: `export default { get: async (url) => { const d = globalThis.__AXIOS?.[url]; if (d == null) throw new Error('404'); return { data: d }; } };`,
      loader: 'js',
    }));
  },
};
await esbuild.build({
  entryPoints: [process.argv[3] ?? new URL('./run1b.mjs', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')],
  bundle: true, platform: 'node', format: 'esm', outfile: process.argv[2],
  tsconfig: 'D:/CETPA/Tour_React/tsconfig.json', plugins: [stub], logLevel: 'warning',
});
