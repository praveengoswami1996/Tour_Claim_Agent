// Phase 1b parity harness, Node side. Bundled by build1b.mjs (esbuild, Tour_React's tsconfig paths) and run as
//   node out1b.mjs cases1b.json out_js1b.json
import fs from 'fs';
const NOW = 1790000000000;
const RealDate = Date;
class FakeDate extends RealDate { constructor(...a) { if (a.length === 0) super(NOW); else super(...a); } static now() { return NOW; } }
globalThis.Date = FakeDate;
Math.random = () => 0.123456789;
import * as TM from '@/lib/claimChatbot/tourMatcher';
import * as CP from '@/lib/claimChatbot/claimProgress';
import * as BS from '@/lib/claimChatbot/billSummary';
import * as LT from '@/lib/claimChatbot/loadAssistantTour';
import * as CL from '@/lib/claimLink';
import * as PB from '@/lib/claimPayloadBuilder';

const norm = (v) => JSON.parse(JSON.stringify(v, (k, x) => {
  if (k === 'uploadDate' || k === 'url' || k === 'file') return undefined;
  if (x instanceof Map) return [...x.entries()];
  if (x instanceof Set) return [...x];
  if (typeof x === 'number' && !Number.isFinite(x)) return { num: String(x) };
  return x;
}) ?? null);
// a file with a documentId is a new upload (the browser held its File)
const withFiles = (rows, key) => (rows ?? []).map((r) => (r && Array.isArray(r[key]) ? { ...r, [key]: r[key].map((f) => (f && f.documentId != null ? { ...f, file: new File([], f.name ?? 'x') } : f)) } : r));
const filesOf = (s) => ({ ...s, transportationRows: withFiles(s.transportationRows, 'uploadedFiles'), accommodationRows: withFiles(s.accommodationRows, 'billFiles') });

const fns = {
  tourWindow: (t) => TM.tourWindow(t),
  norm: (s) => TM.norm(s),
  samePlace: (a, b) => TM.samePlace(a, b),
  matchTours: (s, tours) => TM.matchTours(s, tours),
  findTourByText: (text, tours) => TM.findTourByText(text, tours),
  triageRows: (m, tours, picked) => TM.triageRows(m, tours, picked ?? undefined),
  eachDay: (a, b) => CP.eachDay(a, b),
  cpDayText: (a) => CP.dayText(a),
  cpRangeText: (a, b) => CP.rangeText(a, b),
  groupDays: (d) => CP.groupDays(d),
  coveredDays: (s) => CP.coveredDays(s),
  uncoveredDays: (s, t) => CP.uncoveredDays(s, t),
  coveredText: (s) => CP.coveredText(s),
  findRelationIssues: (s) => CP.findRelationIssues(s),
  findTimeOverlaps: (s) => CP.findTimeOverlaps(s),
  findClaimIssues: (s, r) => CP.findClaimIssues(s, r ?? undefined),
  buildProgress: (p) => CP.buildProgress(p),
  progressText: (p) => CP.progressText(CP.buildProgress(p)),
  stayBillProblem: (row, rules, journeys) => CP.stayBillProblem(row, rules ?? undefined, journeys),
  describeRow: (r) => BS.describeRow(r),
  bsDayText: (v) => BS.dayText(v ?? undefined),
  bsRangeText: (a, b) => BS.rangeText(a ?? undefined, b ?? undefined),
  findingsByFile: (rows) => BS.findingsByFile(rows),
  fileLine: (name, found, fate, opts) => BS.fileLine(name, found, fate ?? undefined, opts),
  groupOf: (fate, found) => BS.groupOf(fate ?? undefined, found),
  uploadSortedText: (files, fates) => BS.uploadSortedText(files, new Map(fates)),
  readSummaryText: (s) => BS.readSummaryText({ ...s, fates: new Map(s.fates) }),
  claimFooter: (c) => BS.claimFooter(c),
  readHeader: (n, o) => BS.readHeader(n, o),
  strongerFate: (a, b) => BS.strongerFate(a ?? undefined, b),
  claimLink: (t, origin) => CL.claimLink(t, origin),
  stateFromClaimLink: (q) => CL.stateFromClaimLink(q),
  clean: (o) => PB.cleanObjectForPayload(o) ?? '<undefined>',
  buildClaimPayload: (p) => { const r = PB.buildClaimPayload(filesOf(p)); return { ...r, text: JSON.stringify(r.draftPayload) }; },
  collectNewFiles: (s) => { const r = PB.collectNewFiles(filesOf(s).transportationRows, filesOf(s).accommodationRows); return [r.transportationFiles.map((f) => f.name), r.accommodationFiles.map((f) => f.name)]; },
  // saveDraftFromAssistant (RaiseClaim.tsx) as it was: retained ids computed but hasResumedDraft not passed
  assistantSave: (s, empId, tourId, draftId, overwrite) => {
    s = filesOf(s);
    const overwriting = !!overwrite && draftId != null;
    const { draftPayload } = PB.buildClaimPayload({
      transportationRows: s.transportationRows, accommodationRows: s.accommodationRows, daRowsState: [], leaveRows: s.leaveRows,
      currentTab: 'Transportation', formData: {}, includeLeave: s.includeLeave, includeAccommodation: s.includeAccommodation,
      empId, empTourPlanId: tourId, draftId,
      retainedTransportationFileIds: overwriting ? s.transportationRows.flatMap((r) => (r.uploadedFiles ?? []).map((f) => f.fileId).filter(Boolean)) : null,
      retainedAccommodationFileIds: overwriting ? s.accommodationRows.flatMap((r) => (r.billFiles ?? []).map((f) => f.fileId).filter(Boolean)) : null,
    });
    const { transportationFiles, accommodationFiles } = PB.collectNewFiles(s.transportationRows, s.accommodationRows);
    return { text: JSON.stringify(draftPayload), parts: [...transportationFiles.map((f) => ['transportationFiles', f.name]), ...accommodationFiles.map((f) => ['accommodationFiles', f.name])] };
  },
  fetchAssistantTour: async (tourId, claimBody, planBody) => {
    globalThis.__AXIOS = { [`/Claim/RaiseClaimRequest/${tourId}`]: claimBody, [`/TourPlan/GetTourDetails/${tourId}`]: planBody };
    return LT.fetchAssistantTour(tourId);
  },
};
const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = [];
for (const c of cases) {
  try { out.push({ ok: norm(await fns[c.fn](...c.args)) }); } catch (e) { out.push({ err: String(e.constructor.name), msg: String(e.message) }); }
}
fs.writeFileSync(process.argv[3], JSON.stringify(out));
