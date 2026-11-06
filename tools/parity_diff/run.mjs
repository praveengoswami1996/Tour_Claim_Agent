import fs from 'fs';
const NOW = 1790000000000;
const RealDate = Date;
class FakeDate extends RealDate { constructor(...a) { if (a.length === 0) super(NOW); else super(...a); } static now() { return NOW; } }
globalThis.Date = FakeDate;
Math.random = () => 0.123456789;
const CA = await import('./ts/claimAssistant.ts');
const RO = await import('./ts/rowOrder.ts');
const OM = await import('./ts/ocrMapping.ts');
const PC = await import('./ts/placeCheck.ts');
const IN = await import('./ts/intents.ts');
const OR = await import('./ts/ocrResults.ts');
const OF = await import('./ts/ocrReferences.ts');
const ids = (rows) => rows.map((r) => r.id);
const pad = (n) => String(n).padStart(2, '0');
const norm = (v) => JSON.parse(JSON.stringify(v, (k, x) => {
  if (typeof x === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(x) && k === 'invoiceDate') { const d = new RealDate(x); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
  if (k === 'uploadDate' || k === 'url' || k === 'file') return undefined;
  if (typeof x === 'number' && !Number.isFinite(x)) return { num: String(x) };
  return x;
}));
const fns = {
  applyEdits: (...a) => CA.applyEdits(...a),
  readClaimEdits: (l) => CA.readClaimEdits(l),
  findMissingFields: (s) => CA.findMissingFields(s),
  findAmountIssues: (s) => CA.findAmountIssues(s),
  findFormOnlyIssues: (s) => CA.findFormOnlyIssues(s),
  describeMissing: (s) => CA.describeMissing(s, CA.findMissingFields(s)),
  summarizeJourneys: (s) => CA.summarizeJourneys(s),
  summarizeStays: (s) => CA.summarizeStays(s),
  summarizeLeave: (s) => CA.summarizeLeave(s),
  snapshot: (...a) => CA.snapshot(...a),
  mergeOcrRows: (s, m) => CA.mergeOcrRows(s, m),
  tourRulesFrom: (c) => CA.tourRulesFrom(c),
  journeysInDateOrder: (s) => ids(RO.journeysInDateOrder(s.transportationRows)),
  staysInDateOrder: (s) => ids(RO.staysInDateOrder(s.accommodationRows)),
  journeyOverlaps: (s) => RO.journeyOverlaps(s.transportationRows).map((o) => [o.earlier.id, o.later.id]),
  tripEnds: (s) => { const t = RO.tripEnds(s.transportationRows); return [t.first?.id ?? null, t.last?.id ?? null]; },
  keptFileIds: (s) => RO.keptFileIds([...s.transportationRows, ...s.accommodationRows]),
  keys: (s) => [s.transportationRows.map((r) => [CA.journeyKey(r), CA.isEmptyTransport(r)]), s.accommodationRows.map((r) => [CA.stayKey(r), CA.isEmptyAccommodation(r)]), s.leaveRows.map(CA.isEmptyLeave)],
  toIsoDate: (v) => OM.toIsoDate(v),
  toTime: (v) => OM.toTime(v),
  mapTravelMode: (v) => OM.mapTravelMode(v),
  placeProblem: (v) => PC.placeProblem(v),
  samePlaceBothEnds: (a, b) => PC.samePlaceBothEnds(a, b),
  cleanMessage: (v) => IN.cleanMessage(v),
  checkFiles: (fs_) => { const r = IN.checkFiles(fs_.map((f) => Object.defineProperty(new File([], f.name, { type: f.type }), 'size', { value: f.size }))); return { accepted: r.accepted.map((f) => f.name), problems: r.problems, rejected: r.rejected }; },
  number: (v) => Number(v),
  parseFloat: (v) => parseFloat(v),
  string: (v) => String(Number(v)),
  inr: (v) => (Number(v) || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
  radix: (v) => v.toString(36),
  showDate: (v) => new RealDate(`${OM.toIsoDate(String(v))}T00:00:00`).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }),
  claimTourOf: (v) => OF.claimTourOf(v),
  mapOcr: (result, bills) => { const r = OM.mapOcrToRows(result, bills.map((b) => ({ ...b, file: new File([new Uint8Array(b.file.size)], b.file.name, { type: b.file.type }) }))); return r; },
  combine: (files) => OR.combineOcrResults(files),
  combineMap: (files, bills) => fns.mapOcr(OR.combineOcrResults(files), bills),
  uniquelyNamed: (names, taken) => OR.uniquelyNamed(names.map((n) => new File([], n)), taken).map((f) => f.name),
};
const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = cases.map((c) => { try { return { ok: norm(fns[c.fn](...c.args)) }; } catch (e) { return { err: String(e.constructor.name) }; } });
fs.writeFileSync(process.argv[3], JSON.stringify(out));
