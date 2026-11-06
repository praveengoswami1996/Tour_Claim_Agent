// Phase 2a parity harness, Node side: assistantActions.ts on every state in a JSON-lines file.
// Bundled by build1b.mjs and run as: node out_actions.mjs states.jsonl out_js.txt
// Each output line is the sha256 of JSON.stringify({ q: openQuestion ?? null, actions }) for that state.
import fs from 'fs';
import crypto from 'crypto';
import { outsideActions, outsideOpenQuestion, withinActions, withinOpenQuestion } from '@/lib/claimChatbot/assistantActions';

const [, , inPath, outPath] = process.argv;
const out = [];
for (const line of fs.readFileSync(inPath, 'utf8').split('\n')) {
  if (!line.trim()) continue;
  const { kind, state } = JSON.parse(line);
  const q = kind === 'outside' ? outsideOpenQuestion(state) : withinOpenQuestion(state);
  const actions = kind === 'outside' ? outsideActions(state) : withinActions(state);
  const text = JSON.stringify({ q: q ?? null, actions });
  out.push(crypto.createHash('sha256').update(text, 'utf8').digest('hex'));
}
fs.writeFileSync(outPath, out.join('\n') + '\n');
console.log(`${out.length} states`);
