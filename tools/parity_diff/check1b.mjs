// The expected values of the phase 1b "our own" Python tests, asserted against the TypeScript.
// node build1b.mjs <out.mjs> check1b.mjs && node <out.mjs>
import assert from 'node:assert/strict';
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
import { initialClaimState, newTransportRow, newAccommodationRow } from '@/lib/claimChatbot/claimAssistant';

// ── tour matcher ──
const PUNE = { tourId: 5, source: 'New Delhi, Delhi', destination: 'Pune', tourStartDate: '2026-01-16', tantetiveEndDate: '2026-01-17', statusId: 2, totalDays: 2 };
const SEP = { tourId: 1, source: 'Noida', destination: 'Delhi', tourStartDate: '2026-09-10', tantetiveEndDate: '2026-09-12', statusId: 2, totalDays: 3 };
const JAN = { tourId: 2, source: 'Noida', destination: 'Jaipur', tourStartDate: '2026-01-18', tantetiveEndDate: '2026-01-20', statusId: 2, totalDays: 3 };
const ticket = (from, to, date) => ({ ...newTransportRow(), source: from, destination: to, DateOfTravelSource: date, DateOfTravelDestination: date, uploadedFiles: [{ name: `${to}-${date}.pdf`, size: 1, type: 'application/pdf' }] });
const tstay = (location, i, o) => ({ ...newAccommodationRow(), location, checkInDate: i, checkOutDate: o, billFiles: [] });
assert.deepEqual(TM.tourWindow(PUNE), { start: '2026-01-16', end: '2026-01-17' });
assert.deepEqual(TM.tourWindow({ tourStartDate: '30/12/2026', totalDays: 3 }), { start: '2026-12-30', end: '2027-01-01' });
assert.deepEqual(TM.tourWindow({ tourStartDate: '2026-01-16', totalDays: 0 }), { start: '2026-01-16', end: '2026-01-16' });
assert.deepEqual(TM.tourWindow({ tourStartDate: '2026-01-16', totalDays: 'abc' }), { start: '2026-01-16', end: 'NaN-NaN-NaN' });
assert.equal(TM.tourWindow({ tourStartDate: 'garbage', totalDays: 3 }), null);
assert.equal(TM.norm('  New Delhi (NDLS) '), 'new delhi ndls');
assert.equal(TM.norm(null), '');
{
  const claim = { ...initialClaimState(), transportationRows: [ticket('New Delhi', 'Pune', '2026-01-16')] };
  const other = { ...PUNE, tourId: 6, destination: 'Goa', tourStartDate: '2026-03-01', tantetiveEndDate: '2026-03-02' };
  const r = TM.matchTours(claim, [other, PUNE]);
  assert.deepEqual(r.ranked.map((m) => m.tour.tourId), [5, 6]);
  assert.equal(r.ranked[0].dateFit, 1); assert.equal(r.ranked[0].score, 4); assert.equal(r.confident, r.ranked[0]); assert.equal(r.hasClues, true);
  assert.equal(TM.matchTours(claim, [PUNE, { ...PUNE, tourId: 7 }]).confident, null);
  const blank = TM.matchTours(initialClaimState(), [PUNE]);
  assert.equal(blank.hasClues, false); assert.equal(blank.ranked[0].dateFit, null);
  const tours = [PUNE, { ...PUNE, tourId: 45, destination: 'Jaipur' }];
  assert.equal(TM.findTourByText('tour 45 please', tours).tourId, 45);
  assert.equal(TM.findTourByText('jaipur', tours).tourId, 45);
  assert.equal(TM.findTourByText('delhi', tours), null);
  assert.equal(TM.findTourByText('go', tours), null);
  const jan = { ...JAN, source: 'Delhi' };
  const t = TM.triageRows({ transportationRows: [ticket('Delhi', 'Jaipur', '2026-01-18')], accommodationRows: [tstay('Jaipur', '2026-09-30', '')] }, [SEP, jan], SEP);
  assert.deepEqual(t.groups, []);
  assert.equal(t.dropped[0].label, 'ticket Delhi → Jaipur on 18 Jan 2026');
  assert.deepEqual(t.dropped[0].files, ['Jaipur-2026-01-18.pdf|1']);
  assert.equal(t.dropped[0].belongsTo, jan);
  assert.deepEqual(JSON.parse(JSON.stringify(t.dropped[1])), { label: 'hotel stay in Jaipur, 30 Sept 2026', files: [] });
}

// ── claim progress ──
const rules = { allowAir: false, minDate: '2026-10-05', maxDate: '2026-10-11' };
const journey = (o = {}) => ({ ...newTransportRow(), source: 'Noida', destination: 'Pune', DateOfTravelSource: '2026-10-05', TimeOfTravelSource: '10:30', DateOfTravelDestination: '2026-10-05', TimeOfTravelDestiNation: '16:30', ModeOfTravel: 'rail', ...o });
const claim = () => ({ ...initialClaimState(), transportationRows: [journey()] });
assert.equal(CP.dayText('2026-09-05'), '05 Sept 2026');
assert.equal(CP.rangeText('2026-10-05', '2026-10-07'), '05 Oct 2026 – 07 Oct 2026');
assert.equal(CP.groupDays(['2026-10-14', '2026-10-11', '2026-10-12', '2026-10-11']), '11 Oct 2026 – 12 Oct 2026, 14 Oct 2026');
assert.deepEqual(CP.eachDay('2026-12-30', '2027-01-01'), ['2026-12-30', '2026-12-31', '2027-01-01']);
assert.equal(CP.eachDay('2026-01-01', '2027-01-01').length, 120);
assert.deepEqual(CP.eachDay('2026-10-05', '2026-10-04'), []);
{
  const s = { ...claim(), includeLeave: true, leaveRows: [{ id: 'l', startDate: '2026-10-07', endDate: '', daDeductable: 'Yes', amount: '0' }] };
  assert.deepEqual([...CP.coveredDays(s)], ['2026-10-05', '2026-10-07']);
  assert.equal(CP.coveredText(s), '05 Oct 2026, 07 Oct 2026');
  const tour = { tourStartDate: '2026-10-05', tantetiveEndDate: '2026-10-08' };
  assert.deepEqual(CP.uncoveredDays(s, tour), ['2026-10-06', '2026-10-08']);
  assert.deepEqual(CP.uncoveredDays(initialClaimState(), tour), []);
}
const j = (from, to, dep, depT, arr, arrT, mode) => ({ ...newTransportRow(), source: from, destination: to, DateOfTravelSource: dep, TimeOfTravelSource: depT, DateOfTravelDestination: arr, TimeOfTravelDestiNation: arrT, ModeOfTravel: mode });
const ambala = () => [
  j('AMBALA CANT JN', 'NEW DELHI', '2026-08-24', '07:38', '2026-08-24', '10:15', 'air'),
  j('New Delhi Railway Station', 'Noida Corporate Office', '2026-08-24', '10:15', '2026-08-24', '11:33', 'public-transport'),
  j('Noida Corporate Office', 'New Delhi Railway Station', '2026-08-24', '17:00', '2026-08-24', '19:00', 'public-transport'),
  j('NEW DELHI (NDLS)', 'AMBALA CANT JN (UMB)', '2026-08-24', '17:15', '2026-08-24', '19:50', 'air'),
];
assert.deepEqual(CP.findTimeOverlaps({ ...initialClaimState(), transportationRows: ambala() }), [
  "The times of journey 3 and journey 4 overlap: journey 4 (NEW DELHI (NDLS) → AMBALA CANT JN (UMB)) departs at 17:15 on 24 Aug 2026, before journey 3 (Noida Corporate Office → New Delhi Railway Station) arrives at 19:00 on 24 Aug 2026. Please correct journey 4's departure or journey 3's arrival in the claim (the fields are marked in red), or tell me the right times.",
]);
{
  const s = { ...initialClaimState(), transportationRows: [journey({ DateOfTravelSource: '2026-10-04', DateOfTravelDestination: '2026-10-12' }), journey({ source: '', destination: '', DateOfTravelSource: '2026-12-01', DateOfTravelDestination: '2026-12-01' })], includeLeave: true, leaveRows: [{ id: 'l', startDate: '2026-10-20', endDate: '', daDeductable: 'Yes', amount: '0' }] };
  assert.deepEqual(CP.findClaimIssues(s, rules), [
    'Journey 1 (Noida → Pune): the departure date 04 Oct 2026 is outside your approved tour (05 Oct 2026 – 11 Oct 2026).',
    'Journey 1 (Noida → Pune): the arrival date 12 Oct 2026 is outside your approved tour (05 Oct 2026 – 11 Oct 2026).',
    'Journey 2: 01 Dec 2026 is outside your approved tour (05 Oct 2026 – 11 Oct 2026).',
    'Leave 1: 20 Oct 2026 is outside your approved tour (05 Oct 2026 – 11 Oct 2026).',
  ]);
}
{
  const s = { ...initialClaimState(), transportationRows: [journey(), journey({ source: 'Mumbai', destination: 'Noida', DateOfTravelSource: '2026-10-08', DateOfTravelDestination: '2026-10-08' })], includeAccommodation: true,
    accommodationRows: [{ ...newAccommodationRow(), location: 'Goa', checkInDate: '2026-10-05' }, { ...newAccommodationRow(), location: 'Pune', checkInDate: '2026-10-01', checkOutDate: '2026-10-03' }] };
  assert.deepEqual(CP.findRelationIssues(s), [
    'Journey 2 starts from Mumbai, but journey 1 ended in Pune. Is a ticket missing in between?',
    'Stay 1 is in Goa, but none of your journeys go there. If this isn\'t your bill for this trip, say "remove stay 1".',
    'Stay 2 (Pune) falls outside your travel dates (05 Oct 2026 – 08 Oct 2026). If this isn\'t your bill for this trip, say "remove stay 2".',
  ]);
}
{
  const tour = { tourId: 1, source: 'Noida', destination: 'Pune', tourStartDate: '2026-10-05', totalDays: 3 };
  const s = { ...initialClaimState(), transportationRows: [journey({ TimeOfTravelSource: '', ModeOfTravel: 'select' })] };
  const p = CP.buildProgress({ state: s, tour, rules, staysAnswered: true, leaveAnswered: false, doneUploading: false });
  assert.equal(CP.progressText(p), [
    "Here's where we are.",
    '✅ Done\n• Tour confirmed: Noida → Pune\n• 1 journey added\n• Stays checked',
    '⬜ Still to do\n• Journey 1 (Noida → Pune): departure time, mode of travel\n• Nothing yet for 06 Oct 2026 – 07 Oct 2026: do you have more tickets or bills?\n• Tell me whether you were on leave on any day',
    "Tell me what you have, and we'll finish it together.",
  ].join('\n\n'));
  const ready = CP.buildProgress({ state: claim(), tour, staysAnswered: true, leaveAnswered: true, doneUploading: true });
  assert.ok(CP.progressText(ready).endsWith('Everything looks good. Say "submit" or "fill the form" when you\'re ready.'));
}
{
  const travel = [{ DateOfTravelSource: '2026-10-05', DateOfTravelDestination: '2026-10-05' }, { DateOfTravelSource: '2026-10-08', DateOfTravelDestination: '2026-10-08' }];
  assert.equal(CP.stayBillProblem({ checkInDate: '2026-12-01', checkOutDate: '2026-12-01' }, rules, travel), 'its dates (01 Dec 2026) are outside your tour dates (05 Oct 2026 – 11 Oct 2026)');
  assert.equal(CP.stayBillProblem({ checkInDate: '2026-10-01', checkOutDate: '2026-10-06' }, undefined, travel), "its dates (01 Oct 2026 to 06 Oct 2026) don't fit your travel dates (05 Oct 2026 – 08 Oct 2026)");
  assert.equal(CP.stayBillProblem({ checkInDate: '2026-10-06', checkOutDate: '2026-10-06', checkInTime: '12:00', checkOutTime: '09:00' }, rules, travel), 'the check-out date is before the check-in date');
}

// ── bill summary ──
assert.equal(BS.fileLine('x.pdf', ['A', 'B', 'C', 'D', 'E'], { kind: 'saved', tour: 'T' }, { tagSaved: true }), '• **x.pdf**: A; B; C; and 2 more → **T**');
assert.equal(BS.fileLine('x.pdf', [], { kind: 'problem', reason: 'r' }, { tagSaved: false }), '• **x.pdf**: hotel bill (**not added**: r)');
assert.equal(BS.fileLine('x.pdf', ['A'], { kind: 'saveFailed', tour: 'T' }, { tagSaved: false }), '• **x.pdf**: A (**not saved yet**)');
assert.equal(BS.fileLine('x.pdf', ['A'], { kind: 'alreadySaved', tour: 'T' }, { tagSaved: false }), '• **x.pdf**: A (already in your T draft)');
assert.equal(BS.fileLine('x.pdf', ['A'], { kind: 'unsure' }, { tagSaved: false }), "• **x.pdf**: A (**couldn't tell which tour it's for**)");
assert.equal(BS.fileLine('x.pdf', [], undefined, { tagSaved: false }), '• **x.pdf**: no journey or hotel stay found');
assert.equal(BS.describeRow({ ModeOfTravel: 'toString', source: '', destination: 'Pune' }), 'function toString() { [native code] }, ? → Pune');
assert.equal(BS.describeRow({ location: null, checkInDate: '2026-09-05', checkOutDate: 'garbage' }), 'Hotel stay in ?, **5 Sep 2026**');
assert.equal(BS.readSummaryText({ header: 'h', files: ['a.pdf'], rows: [], fates: new Map([['a.pdf', { kind: 'saveFailed', tour: 'T' }]]), savedTours: [], notes: ['', '', 'note'] }),
  "h\n\n**✅ New (1)**\n• **a.pdf**:  (**not saved yet**)\n\nnote\n\n⚠️ I couldn't save some of them just now. I'll try again the next time you open the assistant.");
assert.equal(BS.readHeader(2, { name: null }), 'Hi, your 2 bills have been read:');
assert.equal(BS.readHeader(1, { earlier: true }), 'The bill you uploaded earlier has been read:');
assert.equal(BS.claimFooter({ journeys: 0, stays: 1, saved: true }), '✅ Added **1 stay** to this claim. The draft is saved.');

// ── claim link ──
assert.equal(CL.claimLink({ tourId: '12 a~*', statusId: 2.5 }, ''), '/raise-claim-approver?tourId=12+a%7E*&isDraft=true&statusId=2.5');
assert.deepEqual(JSON.parse(JSON.stringify(CL.stateFromClaimLink('?tourId=%37&statusId=0x10&isDraft=tr%75e'))), { tourId: 7, statusId: 16, isDraft: true });
assert.deepEqual(JSON.parse(JSON.stringify(CL.stateFromClaimLink('tourId=1e3&statusId=&totalDays=x'))), { tourId: 1000, statusId: 0, isDraft: false });
assert.equal(CL.stateFromClaimLink('?tourId=1.5'), null);

// ── loadAssistantTour ──
{
  const CLAIM = { statusCode: 200, data: { empTourDetails: { source: 'Noida', destination: 'Pune', purposeOfTour: 'Audit', tentativeStartDate: '2026-10-04' }, trDetails: { tentativeStartDate: '2026-10-05T00:00:00' },
    tentativeTourPlans: [{ date: '2026-10-05', travelMode: 'Air' }, { date: '2026-10-07', travelMode: 'rail' }] } };
  const run = async (tourId, claimBody, planBody) => { globalThis.__AXIOS = { [`/Claim/RaiseClaimRequest/${tourId}`]: claimBody, [`/TourPlan/GetTourDetails/${tourId}`]: planBody }; return JSON.parse(JSON.stringify(await LT.fetchAssistantTour(tourId))); };
  assert.deepEqual(await run(4522, CLAIM, { data: { tentativeTourPlans: [{ date: '2026-10-09', travelMode: 'rail' }] } }), {
    tour: { tourId: 4522, source: 'Noida', destination: 'Pune', startDate: '2026-10-05T00:00:00', endDate: '2026-10-09', purpose: 'Audit' },
    rules: { allowAir: true, minDate: '2026-10-05', maxDate: '2026-10-08' },
  });
  for (const plan of [null, { data: { tentativeTourPlans: [] } }, {}]) assert.equal((await run(4522, CLAIM, plan)).tour.endDate, '2026-10-07');
  assert.deepEqual(await run(7, { data: { employeeDetails: { source: 'Delhi', tentativeStartDate: '2026-09-10' } } }, null), {
    tour: { tourId: 7, source: 'Delhi', startDate: '2026-09-10' }, rules: { allowAir: false, minDate: '2026-09-10', maxDate: '2026-09-11' },
  });
}

// ── payload builder (the parts that are the old behaviour) ──
{
  assert.deepEqual(PB.cleanObjectForPayload({ a: '', b: null, c: 0, d: false, e: { x: '' }, f: [], g: ['', 1] }), { c: 0, d: false, f: [], g: [{}, 1] });
  assert.deepEqual(Object.keys(PB.cleanObjectForPayload({ b: 1, 10: 2, 1: 3 })), ['1', '10', 'b']);
  assert.equal(JSON.stringify({ a: 5.0, b: NaN, c: undefined, d: [undefined, 1e21], e: 'é\n\x01' }), '{"a":5,"b":null,"d":[null,1e+21],"e":"é\\n\\u0001"}');
  const stay = (o = {}) => ({ ...newAccommodationRow(), location: 'Pune', checkInDate: '2026-10-05', checkOutDate: '2026-10-06', ...o });
  const s = { ...initialClaimState(), accommodationRows: [stay()], includeAccommodation: true };
  const { draftPayload } = PB.buildClaimPayload({ transportationRows: s.transportationRows, accommodationRows: s.accommodationRows, daRowsState: [], leaveRows: s.leaveRows, currentTab: 'Transportation', formData: {}, includeLeave: s.includeLeave, includeAccommodation: s.includeAccommodation, empId: 101, empTourPlanId: 4522, draftId: null, retainedTransportationFileIds: null, retainedAccommodationFileIds: null });
  assert.equal(JSON.stringify(draftPayload), '{"EmpId":101,"EmpTourPlanId":4522,"LastEditedSection":"Transportation","IsPaidViaCreditCard":false,"TransportaionDetailDtos":"[{\\"Id\\":0,\\"ModeOfTravel\\":\\"select\\",\\"DistanceINKm\\":0,\\"BilledAmount\\":0,\\"ClaimedAmount\\":0,\\"SGSTAmount\\":0,\\"CGSTAmount\\":0,\\"IGSTAmount\\":0,\\"BillSGSTAmount\\":0,\\"BillCGSTAmount\\":0,\\"BillIGSTAmount\\":0,\\"TaxStructure\\":\\"SGST_CGST\\",\\"StartFileIndexCount\\":-1,\\"EndFileIndexCount\\":-1}]","AccomodationDetailDto":"[{\\"Id\\":0,\\"Location\\":\\"Pune\\",\\"ClassOfCity\\":\\"Select\\",\\"IsOwnArrangement\\":false,\\"IsFreeAccomodation\\":false,\\"isCompanyLodginPaid\\":false,\\"HotelCity\\":\\"Pune\\",\\"DateOfCheckIn\\":\\"2026-10-05\\",\\"DateOfCheckOut\\":\\"2026-10-06\\",\\"BilledAmount\\":0,\\"ClaimedAmount\\":0,\\"BillSGSTAmount\\":0,\\"BillCGSTAmount\\":0,\\"BillIGSTAmount\\":0,\\"CGSTAmount\\":0,\\"SGSTAmount\\":0,\\"IGSTAmount\\":0,\\"TaxStructure\\":\\"SGST_CGST\\",\\"StartFileIndexCount\\":-1,\\"EndFileIndexCount\\":-1}]"}');
  const r = PB.buildClaimPayload({ transportationRows: [journey()], accommodationRows: [], daRowsState: [{ startDate: '2026-10-05T00:00:00', startTime: '10:30:00', Hours: 6, Minutes: 5, DAPercentage: 70, DAOfTheDay: '350', applicable: true }], leaveRows: [], currentTab: 'DA',
    formData: { recipientId: 5, claimAmount: 0, documents: { hotel: true } }, includeLeave: false, includeAccommodation: false, empId: 101, empTourPlanId: 4522, draftId: 77, hasResumedDraft: true, myEntitlement: { daAmount: 500 } });
  assert.equal(r.draftPayload.DraftId, 77); assert.equal(r.draftPayload.RecipentId, 5); assert.ok(!('ClaimAmount' in r.draftPayload)); assert.equal(r.draftPayload.NoOfDays, 1);
  assert.deepEqual(JSON.parse(r.draftPayload.DailyAllowanceDto), [{ Id: 1, Date: '2026-10-05', SourceTime: '10:30', DASlab: '500', TotalHoursMinutes: '06:05', PercentageAdmissible: 70, Amount: 350, IsApplicable: true, TourNumber: 1 }]);
  assert.deepEqual(r.draftPayload.DocumentsAttached, { IsApprovedTourProgramme: false, IsTickets_BoardingPass_Bills: false, IsHotelBills: true });
  assert.deepEqual(r.draftPayload.RetainedTransportationFileIds, []); assert.ok(!('RetainedAccomodationFileIds' in r.draftPayload));
}
console.log('check1b: all TypeScript expectations hold');
