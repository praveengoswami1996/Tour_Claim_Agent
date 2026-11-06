// Phase 1c parity harness, Node side (tourReview, savedClaim, keptReports). Bundled by build1b.mjs and run as
//   node build1b.mjs out1c.mjs run1c.mjs && set TZ=Asia/Kolkata && node out1c.mjs cases1c.json out_js1c.json
import fs from 'fs';
const NOW = 1790000000000;
const RealDate = Date;
class FakeDate extends RealDate { constructor(...a) { if (a.length === 0) super(NOW); else super(...a); } static now() { return NOW; } }
globalThis.Date = FakeDate;
Math.random = () => 0.123456789;
import * as TR from '@/lib/claimChatbot/tourReview';
import * as SC from '@/lib/claimChatbot/savedClaim';
import * as KR from '@/lib/claimChatbot/keptReports';

const pad2 = (n) => String(n).padStart(2, '0');
// a Date on a row is kept as its local day 'YYYY-MM-DD' on the Python side ('NaN-NaN-NaN' for an Invalid Date)
const norm = (v) => JSON.parse(JSON.stringify(v, function (k, x) {
  if (k === 'uploadDate' || k === 'url' || k === 'file') return undefined;
  const raw = this[k];
  if (raw instanceof RealDate) return isNaN(raw.getTime()) ? 'NaN-NaN-NaN' : `${raw.getFullYear()}-${pad2(raw.getMonth() + 1)}-${pad2(raw.getDate())}`;
  if (k === 'uploadDate' || k === 'url' || k === 'file') return undefined;
  if (x instanceof Map) return [...x.entries()];
  if (x instanceof Set) return [...x];
  if (typeof x === 'number' && !Number.isFinite(x)) return { num: String(x) };
  return x;
}) ?? null);

const memory = () => {
  const m = new Map();
  return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => void m.set(k, v), removeItem: (k) => void m.delete(k), m };
};

const fns = {
  reviewClaim: (inp) => TR.reviewClaim(inp),
  short: (inp, excl) => TR.missingInShort(TR.reviewClaim(inp).filter((i) => !excl.includes(i.kind))),
  loop: (inp, actions) => {
    const items = TR.reviewClaim(inp);
    const confirmed = new Set();
    const deferred = new Set();
    const steps = [];
    for (const how of [...actions, null]) {
      const s = TR.nextReviewStep(items, confirmed, deferred);
      steps.push([s.type, s.item?.id ?? null, s.open?.length ?? null, (s.blocking ?? []).map((i) => i.id)]);
      if (s.type !== 'ask' || how == null) break;
      TR.settle(s.item, how, confirmed, deferred);
    }
    return { steps, confirmed: [...confirmed], deferred: [...deferred] };
  },
  notes: (inp, again, claimText) => {
    const items = TR.reviewClaim(inp);
    return {
      q: items.map((it, k) => TR.reviewQuestionNote(it, items.slice(k), again)),
      s: items.map((it) => ['confirm', 'later', 'upload'].map((how) => TR.reviewSettledNote(it, how))),
      b: TR.reviewBlockedNote(items.filter((i) => i.blocking)),
      d: [TR.reviewDoneNote(claimText, true), TR.reviewDoneNote(claimText, false)],
    };
  },
  claimStateFromDraft: (d) => SC.claimStateFromDraft(d),
  savedFlow: (d, incoming, tour, claim) => {
    const saved = SC.claimStateFromDraft(d);
    const { added, alreadySaved } = SC.compareWithSaved(saved, incoming);
    const merged = SC.addToSaved(saved, added);
    return {
      added, alreadySaved, merged, count: SC.rowsCount(added), days: SC.billDays(added), savedDays: SC.billDays(saved),
      text: SC.savedClaimText({ claim, saved, added, alreadySaved, merged, tour }),
    };
  },
  savedDraftSummary: (tour, state, opts, waiting) => {
    const s = SC.savedDraftSummary(tour, state, opts);
    return {
      s, model: SC.draftForModel(s),
      notes: [SC.savedDraftsGreetingNote([s], waiting), SC.savedDraftsGreetingNote([s, { ...s, savedInChat: false }], waiting),
        SC.savedDraftsGreetingNote([s, { ...s, savedInChat: true }, { ...s }], waiting)],
    };
  },
  savedDraftSummaryFromDraft: (tour, d) => SC.savedDraftSummary(tour, SC.claimStateFromDraft(d)),
  kept: (ops) => {
    const store = memory();
    const out = [];
    for (const op of ops) {
      const key = KR.reportsKey(op[1]);
      if (op[0] === 'keep') out.push(KR.keepReport(store, key, op[2], op[3]));
      else if (op[0] === 'drop') out.push(KR.dropReports(store, key, op[2], op[3]) ?? null);
      else if (op[0] === 'for') out.push(KR.reportsFor(store, key, op[2], op[3]));
      else if (op[0] === 'read') out.push(KR.readReports(store, key, op[2]));
      else store.setItem(key, op[2]);
    }
    return { out, store: [...store.m.entries()].sort() };
  },
};
const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = [];
for (const c of cases) {
  try { out.push({ ok: norm(await fns[c.fn](...c.args)) }); } catch (e) { out.push({ err: String(e.constructor.name), msg: String(e.message) }); }
}
fs.writeFileSync(process.argv[3], JSON.stringify(out));
