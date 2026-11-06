// Runs the original withinTourPlan.ts / withinAssistant.ts (Tour_React) on the cases from within_gen.py.
// Usage: node within_run.mjs within_cases.json within_out_js.json
// The .ts files are copied to a temp folder with their imports pointed at each other and at Tour_React's date-fns;
// Node runs the TypeScript directly. The browser ran in India Standard Time, and the clock is fixed per case.
process.env.TZ = 'Asia/Kolkata';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { pathToFileURL } from 'url';

const REACT = 'D:/CETPA/Tour_React';
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'within-parity-'));
const dateFns = pathToFileURL(`${REACT}/node_modules/date-fns/index.mjs`).href;
const copy = (from, name) => {
  const text = fs
    .readFileSync(`${REACT}/${from}`, 'utf8')
    .replaceAll("from 'date-fns'", `from '${dateFns}'`)
    .replaceAll("from '@/lib/claimChatbot/ocrMapping'", "from './ocrMapping.ts'")
    .replaceAll("from './ocrMapping'", "from './ocrMapping.ts'")
    .replaceAll("from '@/lib/withinTourPlan'", "from './withinTourPlan.ts'");
  fs.writeFileSync(path.join(dir, name), text);
};
copy('src/lib/claimChatbot/ocrMapping.ts', 'ocrMapping.ts');
copy('src/lib/withinTourPlan.ts', 'withinTourPlan.ts');
copy('src/lib/claimChatbot/withinAssistant.ts', 'withinAssistant.ts');

let NOW = 1793934000000;
const RealDate = Date;
class FakeDate extends RealDate {
  constructor(...a) {
    if (a.length === 0) super(NOW);
    else super(...a);
  }
  static now() {
    return NOW;
  }
}
globalThis.Date = FakeDate;

const TP = await import(pathToFileURL(path.join(dir, 'withinTourPlan.ts')).href);
const WA = await import(pathToFileURL(path.join(dir, 'withinAssistant.ts')).href);

const norm = (v) => {
  if (v === undefined) return { undef: true };
  return JSON.parse(
    JSON.stringify(v, (k, x) => {
      if (typeof x === 'number' && !Number.isFinite(x)) return { num: String(x) };
      return x;
    })
  );
};
const fns = {
  mapWithinTravelMode: (v) => TP.mapWithinTravelMode(v),
  mapVehicleType: (v, m) => TP.mapVehicleType(v, m),
  mapTourPlanToTours: (r) => TP.mapTourPlanToTours(r),
  isoOf: (v) => WA.isoOf(v),
  journeysOf: (t, n) => WA.journeysOf(t, n),
  setJourneyField: (t, l, f, v) => WA.setJourneyField(t, l, f, v),
  hasTourData: (ts) => WA.hasTourData(ts),
  tourDiscrepancies: (ts) => WA.tourDiscrepancies(ts),
  tourReadyToEnd: (ts) => ts.map((t) => WA.tourReadyToEnd(t)),
  findGaps: (ts) => WA.findGaps(ts),
  answerGap: (ts, g, text) => WA.answerGap(ts, g, text),
  answerFound: (ts, i, text) => WA.answerGap(ts, WA.findGaps(ts)[i], text),
  applyWithinEdits: (ts, es) => WA.applyWithinEdits(ts, es),
  readWithinEdits: (l) => WA.readWithinEdits(l),
  tourSummaryRows: (ts) => WA.tourSummaryRows(ts),
  claimTotals: (ts) => WA.claimTotals(ts),
  totalsText: (ts) => WA.totalsText(ts),
  progressText: (ts, c) => WA.progressText(ts, c),
  snapshot: (ts) => WA.snapshot(ts),
  blankTour: () => WA.blankTour(),
  // a tour plan read, every question answered, then the model's edits and every text the chat shows
  planFlow: (plan, answers, edits, ctx) => {
    let ts = TP.mapTourPlanToTours(plan).tours;
    const gaps = WA.findGaps(ts);
    const errors = gaps.map((g, i) => {
      const r = WA.answerGap(ts, g, answers[i % answers.length]);
      ts = r.tours;
      return r.error ?? null;
    });
    const e = WA.applyWithinEdits(ts, edits);
    return [gaps, errors, e, WA.findGaps(e.tours), WA.tourDiscrepancies(e.tours), e.tours.map(WA.tourReadyToEnd), WA.snapshot(e.tours),
      WA.tourSummaryRows(e.tours), WA.progressText(e.tours, ctx), WA.totalsText(e.tours), WA.hasTourData(e.tours)];
  },
  constants: () => [WA.WITHIN_EDIT_FORMAT, WA.WITHIN_DA_RULES, TP.WITHIN_TRAVEL_MODES, TP.TOUR_PLAN_MIN_DATE],
};
// a key the generator wrote as "__missing__" is left out (an array item reads as undefined)
const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'), (k, x) => (x === '__missing__' ? undefined : x));
const out = cases.map((c) => {
  NOW = c.now ?? 1793934000000;
  try {
    return { ok: norm(fns[c.fn](...c.args)) };
  } catch (e) {
    return { err: e.name === 'TourPlanError' ? `TourPlanError: ${e.message}` : String(e.constructor.name) };
  }
});
fs.writeFileSync(process.argv[3], JSON.stringify(out));
fs.rmSync(dir, { recursive: true, force: true });
