// Phase 1c: the expected values of our own tests (tests/claimchat/test_tour_review.py, test_saved_claim.py,
// test_kept_reports.py), printed by the TypeScript. Bundle with build1b.mjs, run with TZ=Asia/Kolkata.
const NOW = 1790000000000;
const RealDate = Date;
class FakeDate extends RealDate { constructor(...a) { if (a.length === 0) super(NOW); else super(...a); } static now() { return NOW; } }
globalThis.Date = FakeDate;
Math.random = () => 0.123456789;
import * as TR from '@/lib/claimChatbot/tourReview';
import * as SC from '@/lib/claimChatbot/savedClaim';
import * as KR from '@/lib/claimChatbot/keptReports';
import { initialClaimState, newAccommodationRow, newTransportRow } from '@/lib/claimChatbot/claimAssistant';

const tour = { tourId: 4521, source: 'Noida', destination: 'Mumbai', tourStartDate: '2026-10-05', tantetiveEndDate: '2026-10-09', totalDays: 5, statusId: 2 };
const rules = { allowAir: true, minDate: '2026-10-05', maxDate: '2026-10-10' };
const j = (from, to, dep, depT, arr, arrT, mode = 'air', extra = {}) => ({ ...newTransportRow(), source: from, destination: to, DateOfTravelSource: dep, TimeOfTravelSource: depT, DateOfTravelDestination: arr, TimeOfTravelDestiNation: arrT, ModeOfTravel: mode, uploadedFiles: [{ name: 'ticket.pdf' }], claimedAmount: '4500', ...extra });
const s = (city, inn, innT, out, outT, extra = {}) => ({ ...newAccommodationRow(), location: city, checkInDate: inn, checkInTime: innT, checkOutDate: out, checkOutTime: outT, selectedType: 'hotel', hotelName: 'Hotel Sea View', invoiceNo: 'INV-1', classOfCity: 'X', billFiles: [{ name: 'ticket.pdf' }], claimed: '5000', billed: '5000', ...extra });
const claim = (journeys, stays = []) => ({ ...initialClaimState(), transportationRows: journeys, accommodationRows: stays.length ? stays : initialClaimState().accommodationRows, includeAccommodation: stays.length > 0 });
const outbound = () => j('Noida', 'Mumbai', '2026-10-05', '10:30', '2026-10-05', '13:00');
const back = () => j('Mumbai', 'Noida', '2026-10-09', '18:00', '2026-10-09', '20:10');
const hotel = () => s('Mumbai', '2026-10-05', '14:00', '2026-10-09', '12:00');
const review = (c) => TR.reviewClaim({ state: c, tour, rules, leaveAnswered: true });
console.log(JSON.stringify(review(claim([outbound()], [hotel()]))));
const lateCab = j('Noida', 'Noida Airport', '2026-10-05', '08:00', '2026-10-05', '09:30', 'public-transport', { claimedAmount: '450' });
const noDate = j('Pune', 'Goa', '', '', '', '', 'rail');
const items = review(claim([outbound(), back(), noDate, lateCab], [hotel()]));
console.log(items.filter((i) => i.id.startsWith('conveyanceBefore:0:')).length, items.find((i) => i.kind === 'routeBreak').id, items.find((i) => i.kind === 'noReturn').id);
const x = review(claim([j('Noida', 'Mumbai', '2026-10-05', '', '2026-10-05', '13:00')], [hotel()]));
console.log(JSON.stringify([TR.reviewQuestionNote(x[1], x.slice(1), true), TR.reviewBlockedNote([x[1]]), TR.reviewSettledNote(x[1], 'later'), TR.reviewSettledNote(x[0], 'confirm'), TR.reviewSettledNote(x[0], 'upload'), TR.reviewDoneNote('C.', true), TR.reviewDoneNote('C.', false)]));
const ODD = { DRAFTDATA: { transportationDetailDtos: [
  { source: 'Delhi', destination: 'Agra', dateOfTravelSource: '16/01/2026', timeOfTravelSource: '9.05 pm', departureModeofTravel: 'air', DistanceINKm: '12.50', BilledAmount: 'abc', IGSTAmount: 3, InvoiceDate: '2026-01-16', isPaidUsingCard: 1, Remark: null },
  { TaxStructure: 'NA', SGSTAmount: 9, BilledAmount: 0, InvoiceDate: 'garbage' }],
  AccommodationDetailDto: JSON.stringify([{ Location: 'Agra', DateOfCheckIn: '2026-01-16T00:00:00', TimeOfCheckIn: '14:00:00', IsFreeAccomodation: true, TaxStructure: 'IGST', ClassOfCity: null, InvoiceDate: '2026-01-17' }]),
  LeaveAvailedDto: '[{"LeaveStartDate": "2026-01-17T00:00:00", "DADeductable": 0, "Amount": "12"}, 5]',
  transportationFileReferences: JSON.stringify([{ FileId: 'f-1', displayName: 'a.pdf', RowIndex: '1', fileSize: '7' }, { fileId: 'f-2', rowIndex: null }]) } };
const st = SC.claimStateFromDraft(ODD);
const t0 = st.transportationRows[0], t1 = st.transportationRows[1], a = st.accommodationRows[0];
console.log(JSON.stringify([t0.source, t0.destination, t0.DateOfTravelSource, t0.TimeOfTravelSource, t0.ModeOfTravel, t0.distanceKm, t0.travelCost, t0.taxStructure, t0.invoiceDate, t0.isPaidUsingCard, t0.remarks, t0.includeTravelCosts, t0.uploadedFiles, t1.uploadedFiles, t1.taxStructure, t1.sgst, t1.ModeOfTravel, String(t1.invoiceDate), a.selectedType, a.taxStructure, a.classOfCity, a.checkInTime, a.invoiceDate, st.leaveRows, st.includeAccommodation, st.includeLeave]));
const m = new Map(); const store = { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => void m.set(k, v), removeItem: (k) => void m.delete(k) };
const key = KR.reportsKey(1001);
console.log(KR.keepReport(store, key, { scope: 'page', text: 'A', tone: 'error', tourIds: [1, 2] }), m.get(key), KR.reportsKey(null));
const draft = { draftId: 1, draftData: { TransportaionDetailDtos: JSON.stringify(['2026-09-10', '2026-09-12'].map((d) => ({ DateOfTravelSource: `${d}T00:00:00`, TimeOfTravelSource: '09:00:00', Source: 'Noida', ModeOfTravel: 'rail', DateOfTravelDestination: `${d}T00:00:00`, TimeOfTravelDestiNation: '18:00:00', Destination: 'Pune', BilledAmount: 500 }))), AccomodationDetailDto: '[]' } };
const saved = SC.claimStateFromDraft(draft);
const added = { transportationRows: [{ ...newTransportRow(), DateOfTravelSource: '2026-09-10', TimeOfTravelSource: '09:00', source: 'Noida', DateOfTravelDestination: '2026-09-10', TimeOfTravelDestiNation: '18:00', destination: 'Pune', ModeOfTravel: 'rail' }], accommodationRows: [] };
const none = { transportationRows: [], accommodationRows: [] };
console.log(JSON.stringify(SC.savedClaimText({ claim: 'T', saved, added, alreadySaved: none, merged: SC.addToSaved(saved, added), tour: { tourId: 7, source: 'Noida', destination: 'Pune', tourStartDate: '2026-09-10', tantetiveEndDate: '2026-09-12', statusId: 2, totalDays: 3 } })));
