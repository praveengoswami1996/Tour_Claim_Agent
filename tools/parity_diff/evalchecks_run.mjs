// Phase 2a parity harness, Node side: the checks of Tour_React scripts/ai-eval/cases.ts on generated model answers.
// Bundled by build1b.mjs and run as: node out_evalchecks.mjs probes.jsonl out_js.jsonl
// Each probe is {i, u} (an Understood for understand case i) or {i, reply} (a reply for narrate case i).
import './fake_runtime.mjs';
import fs from 'fs';
import cases from '../../../Tour_React/scripts/ai-eval/cases';

const [, , inPath, outPath] = process.argv;
const out = [];
for (const line of fs.readFileSync(inPath, 'utf8').split('\n')) {
  if (!line.trim()) continue;
  const p = JSON.parse(line);
  const c = cases[p.i];
  let r;
  try {
    r = c.kind === 'understand' ? c.check(p.u) : c.check(p.reply);
  } catch (e) {
    r = 'THROW';
  }
  out.push(JSON.stringify({ i: p.i, id: c.id, r: r ?? null }));
}
fs.writeFileSync(outPath, out.join('\n') + '\n');
console.log(`${out.length} probes`);
