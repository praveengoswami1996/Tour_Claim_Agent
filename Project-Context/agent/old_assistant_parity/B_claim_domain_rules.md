# Spec B — Claim Assistant deterministic domain rules (TS → Python)

Source of truth: `D:\CETPA\Tour_React\src\lib\claimChatbot\*.ts` (+ `src\lib\placeCheck.ts`, which `tourReview.ts` imports).
All file:line references are to that folder unless another path is given. Every quoted string is **verbatim** (including
Unicode `→`, `–`, `—`, `“ ”`, `’`, emoji, `₹`). Python must reproduce them byte-for-byte.

Contents
0. Shared primitives (blank, norm, samePlace, place, date/time parsing, the THREE different date formatters, money)
1. Data shapes (ClaimFormState and its rows with defaults, TourContext, TourRules, ClaimableTour, ClaimEdit, MissingField, ReviewItem, FormProblem)
2. ocrMapping.ts
3. rowOrder.ts
4. claimAssistant.ts (mergeOcrRows, findMissingFields, applyEdits, read-outs, snapshot, prompt constants)
5. claimProgress.ts
6. tourReview.ts (reviewClaim, review loop, notes, missingInShort)
7. placeCheck.ts (dependency of tourReview)
8. tourMatcher.ts
9. savedClaim.ts
10. billSummary.ts
11. billOutcome.ts
12. intents.ts
13. fileHash.ts, keptReports.ts, draftHandoff.ts, loadAssistantTour.ts
14. Test catalogue (to port as pytest)
15. Differences found against the existing Python port

---------------------------------------------------------------------------------------------------

## 0. Shared primitives

### 0.1 `blank(v)` — claimAssistant.ts:86
`v == null || String(v).trim() === '' || v === 'select' || v === 'Select'`
- Note: the `'select'/'Select'` comparison is on the untrimmed value; `' select '` is NOT blank.
- Python: `v is None or str(v).strip() == '' or v in ('select', 'Select')`. (JS `String(false)` = `'false'` → not blank.)

### 0.2 Empty-row predicates — claimAssistant.ts:89-91
- `isEmptyTransport(r)` = blank(source) && blank(destination) && blank(DateOfTravelSource) && blank(DateOfTravelDestination). (Times and mode ignored.)
- `isEmptyAccommodation(r)` = blank(location) && blank(checkInDate) && blank(checkOutDate).
- `isEmptyLeave(r)` = blank(startDate) && blank(endDate).

### 0.3 `norm(s)` — tourMatcher.ts:43
`(s ?? '').toLowerCase().replace(/[^a-z0-9\s]/g, ' ').replace(/\s+/g, ' ').trim()`
- Non-ASCII letters (e.g. accented) become spaces. JS `\s` matches Unicode whitespace; Python `re` with str patterns does too.
- NB: placeCheck.ts has its *own* `norm` (see §7): `v.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()`.

### 0.4 `samePlace(a, b)` — tourMatcher.ts:46
Takes already-normed strings: `a.length >= 3 && b.length >= 3 && (a.includes(b) || b.includes(a))`.
- tourReview `same(a,b)` (tourReview.ts:105) = `!!a && !!b && samePlace(norm(a), norm(b))`.

### 0.5 `place(v)` — two variants
- tourReview.ts:104: `(v || '?').split(',')[0].trim()` → `"New Delhi, Delhi"` → `"New Delhi"`; empty → `"?"`. (`" , x"` → `""`.)
- billSummary.ts:57: `(v || '').split(',')[0].trim() || '?'` (an empty first part also gives `?`).

### 0.6 `toIsoDate(value)` — ocrMapping.ts:27-42  → `'YYYY-MM-DD'` or `''`
1. Falsy (null/undefined/'') → `''`. `s = String(value).trim()`.
2. `^(\d{4})-(\d{1,2})-(\d{1,2})` (prefix only, no end anchor: `"2026-10-05T00:00:00"` and `"2026-10-05abc"` match) → if `isRealDay(y,m,d)` return zero-padded, else `''` (no fall-through).
3. `^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})$` → **day first** (`05/10/2026` = 5 Oct). 2-digit year → `2000+yy`. Real-day check, else `''`.
4. If `/\b(19|20)\d{2}\b/` does not match → `''` (no year = unreadable; "05 Oct" is refused).
5. Strip ordinal suffixes `(\d)(st|nd|rd|th)\b` (case-insensitive) → JS `new Date(text)` (V8 lenient parser, local time) → `''` if invalid else local Y-M-D.
   V8 behaviours observed (Node 26) that Python must emulate for parity:
   - `"October 5, 2026"`, `"5 October 2026"`, `"05-Oct-2026"`, `"5/Oct/2026"`, `"2026/10/05"`, `"Mon, 05 Oct 2026"`, `"05 Oct 2026 10:30"`, `"5th Oct, 2026"`, `"Sept 5 2026"`, `"5 Sept 2026"` → `2026-10-05` / `2026-09-05`.
   - `"Oct 2026"` → `2026-10-01`; `"2026"` → `2026-01-01` (!).
   - `"31 Feb 2026"` → `2026-03-03` (V8 rolls over; the real-day check is NOT applied on this branch).
   - `"5 Oct 2026 PNR 2034"` → `''`; `"10.05.2026x"` → `''` (no word boundary after year).
   Recommended Python: implement steps 1-4 exactly, then for step 5 use a small parser for "D Month Y", "Month D, Y", "D-Mon-Y", "D/Mon/Y", "Y/M/D", optional weekday prefix and trailing time, month names full or 3-4 letter abbreviations ("Sept"); "Mon YYYY" → day 1; bare "YYYY" → Jan 1; day overflow rolls into next month.
- `isRealDay(y,m,d)` (ocrMapping.ts:18): JS Date round-trip; `m=0` or `d=0` fail (e.g. `00/00/2026` → `''`).

### 0.7 `toTime(value)` — ocrMapping.ts:45-63 → `'HH:mm'` or `''`
1. Falsy → `''`. `raw = String(value).trim().toLowerCase()`.
2. `'noon'` → `'12:00'`; `'midnight'` → `'00:00'` (exact whole string).
3. `text = raw.replace(/\s*(hrs?|hours?|h)\.?$/, '')` (strip a trailing hr/hrs/hour/hours/h with optional dot).
4. First matching pattern:
   a. `^(\d{1,2})[:.](\d{1,2})(?::\d{2})?\s*([ap]\.?m\.?)?$` — `10:30`, `9.05 pm`, `09:05:00`, `9:5` (→ 09:05).
   b. `^(\d{1,2})()\s*([ap]\.?m\.?)$` — `10 am` (minutes 0). A bare `"10"` does NOT match → `''`.
   c. `^(\d{2})(\d{2})()$` — exactly four digits `1030`. (`930` → `''`.)
5. `meridiem = m[3]` with dots removed. `pm` and h≠12 → h+12; `am` and h==12 → 0.
6. If `h > 23 || min > 59` → `''` (so `"13 pm"` → 25 → `''`; `"24:00"` → `''`; `"10:60"` → `''`).
7. Return `HH:mm` zero-padded.

### 0.8 Date formatting — THREE different formatters (do not unify!)
| Name | Where | Output for 2026-09-05 | Output for 2026-10-05 |
|---|---|---|---|
| `showDate(iso)` | claimAssistant.ts:63-67 | `05 Sept 2026` | `05 Oct 2026` |
| `dayText(iso)` (claimProgress) | claimProgress.ts:32 | `05 Sept 2026` | `05 Oct 2026` |
| `dayOf(v)` / `shortDay(iso)` | savedClaim.ts:256, tourMatcher.ts:127 | `05 Sept 2026` | `05 Oct 2026` |
| `dayText(value)` (billSummary) | billSummary.ts:43 | `5 Sep 2026` | `5 Oct 2026` |

- en-IN `toLocaleDateString(..., {day:'2-digit', month:'short', year:'numeric'})` yields `DD Mon YYYY` with **September = `Sept`** (verified on Node v26: `05 Jan 2026`, `05 Jun 2026`, `05 Sept 2026`, `05 Dec 2026`). All other months are the 3-letter English abbreviations. Python `strftime('%b')` gives `Sep` → must special-case. Use `EN_IN_MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sept','Oct','Nov','Dec']`.
- billSummary uses its own `MONTHS` array (billSummary.ts:36) with `Sep` and **unpadded day**.
- `showDate(iso)`: `!iso` → `'—'` (em dash U+2014). A `Date` object is formatted directly; otherwise `new Date(toIsoDate(String(iso)) + 'T00:00:00')`; invalid (toIsoDate gave '') → `'—'`.
- `dayText(iso)` (claimProgress) assumes a valid ISO string; caller guarantees. `rangeText(a,b)` (claimProgress.ts:33) = `a === b ? dayText(a) : dayText(a) + ' – ' + dayText(b)` (space, en dash U+2013, space).
- billSummary `rangeText(from,to)` (billSummary.ts:48-55): if either unparsable → `dayText(from) || dayText(to)`; same y+m: same day → `dayText(from)`, else `` `${a.d}–${b.d} ${Mon} ${y}` `` (en dash, no spaces, e.g. `18–20 Jan 2026`); same year: `` `${a.d} ${MonA} – ${b.d} ${MonB} ${y}` `` (`30 Jan – 2 Feb 2026`); else `` `${dayText(from)} – ${dayText(to)}` `` (`30 Dec 2025 – 2 Jan 2026`).

### 0.9 `eachDay(from, to)` — claimProgress.ts:26-30
Inclusive list of ISO days from `from` to `to` by local calendar; stops when `toIso(d) > to` OR after **120** days (cap). `from > to` → `[]`.

### 0.10 `groupDays(days)` — claimProgress.ts:36-45
Dedupe, sort (string sort), merge consecutive days (`eachDay(last[1], d).length === 2`), each group rendered with claimProgress `rangeText`, joined by `', '`.
Example: `['2026-10-07','2026-10-08','2026-10-10']` → `07 Oct 2026 – 08 Oct 2026, 10 Oct 2026`.

### 0.11 `addDay(iso, n)` — tourReview.ts:98, claimProgress.ts:114, tourMatcher.ts:29 (`addDays`) — local calendar arithmetic, zero-padded ISO.

### 0.12 Money `inr(n)` — claimAssistant.ts:61
`'₹' + (Number(n)||0).toLocaleString('en-IN', {min/max fraction 2})` → Indian grouping: `1234567.5` → `₹12,34,567.50`; `9000` → `₹9,000.00`. Python: implement lakh/crore grouping (last 3 digits, then groups of 2), round half-away-from-zero to 2 decimals (JS Intl rounds half-expand), negatives `-₹`? (JS gives `₹-1,234.00`, since the ₹ is concatenated before the formatted number → `₹-1,234.00`).

### 0.13 `MODE_LABELS` — claimAssistant.ts:69-77
`air`→`Air`, `rail`→`Rail`, `personal-vehicle`→`Personal vehicle`, `public-transport`→`Public transport`, `official-entitled`→`Official vehicle (entitled)`, `official-beyond`→`Official vehicle (beyond entitlement)`, `official-dedicated`→`Official vehicle (dedicated)`.

### 0.14 `ACCOM_LABELS` — claimAssistant.ts:79-84
`hotel`→`Hotel`, `own_arrangement`→`Own arrangement`, `company_free`→`Free company lodging`, `company_paid`→`Company lodging (paid)`.

### 0.15 `TRAVEL_MODES` — ocrMapping.ts:78
`['air', 'rail', 'personal-vehicle', 'public-transport', 'official-entitled', 'official-beyond', 'official-dedicated']`

### 0.16 `mapTravelMode(mode)` — ocrMapping.ts:80-92 (order matters; substring tests on lower-cased input)
1. falsy → `'select'`. 2. exact member of TRAVEL_MODES → itself.
3. contains `public` | `bus` | `taxi` | `cab` | `auto` → `public-transport` (quirk: "business class" → public-transport; "automobile" → public-transport).
4. contains `personal` | `own` → `personal-vehicle` (quirk: "town", "down" match).
5. contains `air` | `flight` → `air` (quirk: "chair car" → air).
6. contains `rail` | `train` → `rail`.
7. contains `entitled` → `official-entitled`; `beyond` → `official-beyond`; `dedicated` → `official-dedicated`.
8. else `'select'` (e.g. "office car", "teleport").

### 0.17 `accommodationTypeFields(type)` — ocrMapping.ts:95-102 (lower-cased substring tests, in order)
- contains `own` → `{selectedType:'own_arrangement', ownArrangement:'Yes', freeProvidedByCompany:'No', companyPaid:'No'}`
- contains `free` → `{selectedType:'company_free', ownArrangement:'No', freeProvidedByCompany:'Yes', companyPaid:'No'}`
- contains `paid` or `company` → `{selectedType:'company_paid', ownArrangement:'No', freeProvidedByCompany:'No', companyPaid:'Yes'}`
- else → `{selectedType:'hotel', ownArrangement:'No', freeProvidedByCompany:'No', companyPaid:'No'}`
(“company_free” → `free` wins over `company`; never rejects anything.)

---------------------------------------------------------------------------------------------------

## 1. Data shapes

### 1.1 ClaimFormState — claimAssistant.ts:14-20
```
transportationRows: TransportationRow[]
accommodationRows: AccommodationRow[]
leaveRows: LeaveRow[]
includeAccommodation: bool
includeLeave: bool
```
The state always keeps at least one row per section (the "blank row the form opens with"). Rows are numbered 1-based
**by storage index** in every message unless a function explicitly sorts (tourReview sorts but still names rows by storage index `i+1`).

### 1.2 TransportationRow (fields; `src/pages/RaiseClaim/sections/TransportationSection.tsx:28-58`) and defaults of `newTransportRow(prev?)` (claimAssistant.ts:93-122)
| field | type | default |
|---|---|---|
| id | str | `transport-chat-<Date.now()>-<5 random base36 chars>` |
| DateOfTravelSource | str (YYYY-MM-DD) | `prev?.DateOfTravelDestination ?? ''` |
| TimeOfTravelSource | str (HH:mm) | `''` |
| source | str | `prev?.destination ?? ''` |
| ModeOfTravel | str | `prev.ModeOfTravel` if prev has one and it is not `'select'`, else `'select'` |
| DateOfTravelDestination | str | `''` |
| TimeOfTravelDestiNation (sic, capital N) | str | `''` |
| destination | str | `''` |
| distanceKm | str | `''` |
| invoiceNo | str? | `''` |
| invoiceDate | Date? | `undefined` |
| travelCost | str | `'0'` |
| totalBilledAmount | str | `'0'` |
| claimedAmount | str | `'0'` |
| cgst, igst, sgst | str | `'0'` |
| BillSGSTAmount, BillCGSTAmount, BillIGSTAmount | str | `'0'` |
| taxStructure | 'IGST'/'SGST_CGST'/'NA' | `'SGST_CGST'` |
| billNotRequired | bool | false |
| uploadedFiles | file[] | `[]` |
| includeTravelCosts | bool | false |
| isPaidUsingCard | bool | false |
| remarks | str | `''` |
| financeRemark | str | `''` |
| (optional in type) remark, approvedAmount | | absent |

A "new leg" therefore starts where the previous one ended (source = prev.destination, departure date = prev arrival date, same mode).

### 1.3 AccommodationRow (`AccommodationSection.tsx:58-98`), defaults of `newAccommodationRow()` (claimAssistant.ts:124-155)
| field | default |
|---|---|
| id | `accom-chat-<Date.now()>-<5 base36>` |
| checkInDate, checkInTime, checkOutDate, checkOutTime | `''` |
| location | `''` |
| classOfCity | `'Select'` |
| ownArrangement / freeProvidedByCompany / companyPaid | `'No'` / `'No'` / `'No'` |
| selectedType | `'hotel'` (type allows undefined → treated as `'hotel'` everywhere via `?? 'hotel'`) |
| hotelName | `''` |
| billed, tax, totalBilled, claimed | `'0'` each |
| billFiles | `[]` |
| taxType, taxStructure | `'SGST_CGST'` |
| invoiceNo, invoiceDate | `''` |
| ClaimedIGSTAmount, ClaimedSGSTAmount, ClaimedCGSTAmount | `0` (numbers) |
| BillSGSTAmount, BillCGSTAmount, BillIGSTAmount | `'0'` |
| remarks, financeRemark | `''` |
| isPaidUsingCard | false |
| (optional) approvedAmount, accommodationId, validationErrors | absent |

### 1.4 LeaveRow (`LeaveSection.tsx:23-29`), `newLeaveRow()` (claimAssistant.ts:157)
`{ id: 'leave-chat-<Date.now()>', startDate: '', endDate: '', daDeductable: 'Yes', amount: '0' }`

### 1.5 `initialClaimState()` — claimAssistant.ts:160-166
`{ transportationRows:[newTransportRow()], accommodationRows:[newAccommodationRow()], leaveRows:[newLeaveRow()], includeAccommodation:false, includeLeave:false }`

### 1.6 Files on rows (rowOrder.ts:288-297 `RowFile`)
`{ fileId?, isRetained?, tempFilePath?, name, size?, type?, file?, ... }`. A file with `fileId` is a saved-draft file held by the server (tied to its row by position).

### 1.7 TourContext (claimAssistant.ts:22-29): `{ tourId, source?, destination?, startDate?, endDate?, purpose? }`
### 1.8 TourRules (claimAssistant.ts:32-39): `{ allowAir: bool, minDate?: 'YYYY-MM-DD', maxDate?: 'YYYY-MM-DD' (already +1 day), lockedJourneys?: number[] (1-based) }`
### 1.9 ClaimableTour (tourMatcher.ts:9-18): `{ tourId:int, source, destination, tourStartDate, statusId, totalDays, tantetiveEndDate?, isAdvanceTaken? }`
### 1.10 ClaimEdit (claimAssistant.ts:395-401): `{ section:'transport'|'accommodation'|'leave', row:int (1-based; 0 = new), field:str, value:str }`
### 1.11 MissingField (claimAssistant.ts:231-239): `{ section:'transport'|'accommodation', row:int (1-based storage index), slot, question, label }`, slot ∈ `source|destination|departure|arrival|mode|location|checkIn|checkOut|hotelName|invoiceNo`.
### 1.12 FormProblem (tourReview.ts:25-31): `{ kind:'field'|'cityClass'|'claimedOverBilled'|'overEntitlement'|'noApprover'|'other', section:'transport'|'accommodation'|'general', row:int (0 = not one row), message:str }`
### 1.13 ReviewItem (tourReview.ts:65-86)
`{ id, kind, blocking, resolutions: ('chat'|'upload'|'form'|'confirm')[], yes?: 'upload'|'confirm'|'details', no?: 'confirm'|'remove', section?: 'transport'|'accommodation'|'leave', row?: int, side?: 'onward'|'return'|'middle', newJourney?: bool, fact, question }`.
ReviewKind (tourReview.ts:33-54): `oddPlace, noJourneys, noOutbound, noReturn, routeBreak, overlap, missingField, badDates, uncoveredDays, nightsWithoutStay, stayNotOnRoute, stayOutsideTravel, ticketMissing, hotelBillMissing, amountMissing, formOnly, conveyanceBefore, conveyanceAfter, conveyanceTransfer, localConveyance, askLeave`.
Optional keys that the TS does not set must be **absent/None** (a test asserts `newJourney === undefined` for oddPlace).

---------------------------------------------------------------------------------------------------

## 2. ocrMapping.ts

### 2.1 `fileKey(f)` (line 15): `` `${f.name}|${f.size ?? 0}` ``.
### 2.2 `toIsoDate`, `toTime`, `isRealDay`, `mapTravelMode`, `accommodationTypeFields`, `TRAVEL_MODES` — see §0.
### 2.3 `mapTaxStructure(tax)` (line 72): `'NOT_APPLICABLE'`→`'NA'`; `'IGST'`→`'IGST'`; anything else (incl. null) → `'SGST_CGST'`. Exact, case-sensitive.
### 2.4 `num(v)` (line 114): `v == null || v === '' ? '0' : String(v)`.
### 2.5 `mapOcrToRows(result, bills)` (lines 134-258) → `{ transportationRows, accommodationRows, failedFiles, unmatchedFiles }`
Inputs: OCR result `{ tourDetails?, accommodationDetails?: {entries?}, meta?: {per_file_results?: [{filename, success, ...}]} }`; `bills: [{file, category?, hash?}]`.
- `files = bills.map(b => b.file)`; `used = set()`.
- `filesFor(sourceName)`: if sourceName truthy, first file with `name === sourceName` (exact) → mark used, return `[fileEntry]`, else `[]`. File entry: `{id:'file-<now>-<random>', name, size, type, uploadDate: now, file, url}`.
- `hasContent(leg)` = leg && (`from.trim()` || `to.trim()` || toIsoDate(departureDate) || toIsoDate(arrivalDate)).
- Main journey: if `tourDetails` and hasContent({from, to, departureDate: startDate, arrivalDate: endDate}) → one row id `transport-ocr-main-<stamp>` from `{from, to, departureDate:startDate, departureTime:startTime, arrivalDate:endDate, arrivalTime:endTime, modeOfTravel, distance}` with `tourDetails.amountDetails`.
- Each `tourDetails.tourLegs[idx]` with hasContent → row id `transport-ocr-leg-<idx>-<stamp>` with `leg.amountDetails`.
- Transport row mapping (`transportRow`, lines 144-185):
  `DateOfTravelSource=toIsoDate(departureDate)`, `TimeOfTravelSource=toTime(departureTime)`, `source=from||''`, `ModeOfTravel=mapTravelMode(modeOfTravel)`, `DateOfTravelDestination=toIsoDate(arrivalDate)`, `TimeOfTravelDestiNation=toTime(arrivalTime)`, `destination=to||''`, `distanceKm = distance?.toString() || ''`, `travelCost = num(billedAmount)`, `totalBilledAmount = num(billedAmount)`, `claimedAmount = num(totalClaimedAmount ?? claimedAmount)`, `invoiceNo = invoiceNo||''`, `invoiceDate = Date(toIsoDate(invoiceDate)) or undefined`, `taxStructure = mapTaxStructure(taxStructure)`, `cgst/sgst/igst = num(cgst/sgst/igst)`, `BillCGSTAmount = num(billCGST)`, `BillSGSTAmount = num(billSGST)`, `BillIGSTAmount = num(billIGST)`, `uploadedFiles = filesFor(amount.sourceFileName)`, `includeTravelCosts = !!billedAmount`, `billNotRequired=false`, `isPaidUsingCard=false`, `remarks=''`, `financeRemark=''`. (No `id` default other than the given one; other newTransportRow fields are NOT set.)
- Accommodation rows: `accommodationDetails.entries` filtered by `location?.trim() || toIsoDate(checkInDate) || toIsoDate(checkOutDate)`; each (`idx` = index **after** filtering) → `{ id:'accom-ocr-<idx>-<stamp>', location: location||'', classOfCity: cityClass||'Select', checkInDate: toIsoDate, checkInTime: toTime, checkOutDate: toIsoDate, checkOutTime: toTime, ...accommodationTypeFields(accommodationType), hotelName: hotelName||'', billed: num(inv.billedAmount), tax: String((inv.cgst||0)+(inv.sgst||0)+(inv.igst||0)), totalBilled: num(inv.billedAmount), claimed: num(inv.claimedAmountWithTax ?? inv.claimedAmount), invoiceNo: inv.invoiceNo||'', invoiceDate: toIsoDate(inv.invoiceDate), taxType = taxStructure = mapTaxStructure(inv.taxStructure), ClaimedCGSTAmount: inv.cgst||0, ClaimedSGSTAmount: inv.sgst||0, ClaimedIGSTAmount: inv.igst||0, BillCGSTAmount: num(inv.billCGST), BillSGSTAmount: num(inv.billSGST), BillIGSTAmount: num(inv.billIGST), billFiles: filesFor(acc.sourceFileName ?? inv.sourceFileName), remarks:'', financeRemark:'', isPaidUsingCard:false }` (`inv = acc.invoice`).
- `failedFiles = per_file_results.filter(!success).map(filename)`.
- `unmatchedFiles = bills.filter(name not used and not in failedFiles).map(name)` (bill order).

### 2.6 `attachUnmatched(rows, files, bills)` (lines 264-283)
Copies rows. For each name in `files`: `bill = bills.find(file.name === name)`; none → skip. Entry = new file entry.
- `bill.category === 'accommodation'` and stays exist → append to `stays[0].billFiles`;
- else if journeys exist → append to `journeys[0].uploadedFiles`;
- else if stays exist → `stays[0].billFiles`;
- else skip (not attached). Attached names returned in `attached` (order of `files`).

---------------------------------------------------------------------------------------------------

## 3. rowOrder.ts

- `stampOf(date, time)` (rowOrder.ts:26; journeysInDateOrder :29, hhmm :43, journeyOverlaps :59, staysInDateOrder :79, keptFileIds :94, tripEnds :102, inStoredOrder :118, idsOf :130): `date ? String(date).slice(0,10) + ' ' + (time || '00:00') : ''`. (Raw slice, not toIsoDate.)
- `journeysInDateOrder(rows)`: stable sort; key = stampOf(depDate, depTime) || stampOf(arrDate, arrTime). Both empty → original order; empty key sorts last; otherwise string compare (`localeCompare` — for these ASCII digit strings equals ordinal compare), ties → original order.
- `staysInDateOrder(rows)`: key = stampOf(checkInDate, checkInTime); same rules.
- `hhmm(t)`: `^(\d{1,2}):(\d{2})` prefix → zero-padded `HH:MM`, else `''`.
- `journeyOverlaps(rows)` → `[{earlier, later}]`: `shown = journeysInDateOrder(rows)`; for each `later` at j with `depDay = toIsoDate(later.DateOfTravelSource)` (skip if empty), `depTime = hhmm(later.TimeOfTravelSource)`; for each i<j: `arrDay = toIsoDate(earlier.DateOfTravelDestination)` (skip if empty), `arrTime = hhmm(earlier.TimeOfTravelDestiNation)`; overlap iff `depDay < arrDay || (depDay === arrDay && depTime && arrTime && depTime < arrTime)`. Order: by `later` (j ascending), then `earlier` (i ascending). Arriving exactly when the next leaves is fine; missing times on the same day are not judged.
- `keptFileIds(rows)`: all `fileId`s of `uploadedFiles` then `billFiles` per row, rows in order, skipping files without fileId.
- `tripEnds(rows)`: `dated = rows with DateOfTravelSource || DateOfTravelDestination` (raw truthy). None dated → `{first: rows[0], last: rows[-1]}` (both None for []). `dep(r) = stampOf(depDate,depTime) || stampOf(arrDate,arrTime)`, `arr(r) = stampOf(arrDate,arrTime) || stampOf(depDate,depTime)`. `first` = reduce keeping `b` only if `dep(b) < dep(a)` (first wins ties); `last` = reduce keeping `b` if `arr(b) >= arr(a)` (**last** wins ties).
- `inStoredOrder(view, storedIds)`: `storedIds` empty → `view` unchanged. Else rows whose id is in storedIds, in storedIds order, then the remaining view rows in view order. Ids not found are skipped.
- `idsOf(rows)`: ids in order.

---------------------------------------------------------------------------------------------------

## 4. claimAssistant.ts

### 4.1 `tourRulesFrom(claimData)` (lines 42-57)
- `plans = claimData.tentativeTourPlans || claimData.tentativeTourPlanList || []`.
- `dates = [claimData.trDetails?.tentativeStartDate ?? claimData.employeeDetails?.tentativeStartDate, ...plans.map(p => p.date)]` → toIsoDate → drop '' → string sort.
- `allowAir = plans.some(p => String(p.travelMode ?? '').toLowerCase() === 'air')` (exact 'air').
- No dates → `{allowAir}`. Else `{allowAir, minDate: dates[0], maxDate: lastDate + 1 day}`.

### 4.2 `journeyKey(r)` / `stayKey(r)` (lines 170-175)
`key(...parts) = parts.map(p => String(p ?? '').trim().toLowerCase()).join('|')` (no punctuation stripping).
- journeyKey = key(source, destination, toIsoDate(DateOfTravelSource) || DateOfTravelSource, toTime(TimeOfTravelSource) || TimeOfTravelSource).
- stayKey = key(location, toIsoDate(checkInDate) || checkInDate, toIsoDate(checkOutDate) || checkOutDate).
Example: saved `"2026-09-10T00:00:00"` + `"09:00:00"` and bill `"2026-09-10"` + `"09:00"` give the same key `noida|pune|2026-09-10|09:00`.

### 4.3 `mergeOcrRows(state, mapped)` (lines 188-225) → `{state, addedJourneys, addedStays, duplicates}`
1. `journeys` = state transport rows that are not empty (blank rows dropped). `seen = set(journeyKey(journeys))`.
2. For each mapped transport row in order: key in seen → `duplicates += 1`, skip; else add key to seen and keep (so duplicates *within* the OCR batch are also counted).
3. `holdsSavedFiles(rows)` = any row has any `uploadedFiles`/`billFiles` entry with a truthy `fileId`.
   - holds → `allJourneys = journeys + journeysInDateOrder(newJourneys)` (existing order untouched).
   - else → `allJourneys = journeysInDateOrder(journeys + newJourneys)`.
4. Stays: `stays = includeAccommodation ? non-empty accommodation rows : []`; same dedupe with stayKey (duplicates counter shared); same ordering rule with `staysInDateOrder`.
5. Result state: `transportationRows = allJourneys if non-empty else state.transportationRows` (keeps the blank row); same for accommodation; `includeAccommodation = true if allStays non-empty else unchanged`. Leave untouched. `addedJourneys = len(newJourneys)`, `addedStays = len(newStays)`.

### 4.4 `findMissingFields(state)` (lines 242-286) — order of questions is exactly this
**Transport: iterates ALL transport rows, including blank ones** (an untouched blank row yields 5 items: source, destination, departure, arrival, mode). For row index i (row = i+1):
- `route = (source || destination) ? ` (${source || '?'} → ${destination || '?'})` : ''` (raw values, not place()).
- `leg = 'journey ' + row + route`.
1. blank(source) → slot `source`, label `starting point`, question `Where did ${leg} start from?` (line 249)
2. blank(destination) → slot `destination`, label `destination`, question `And where did ${leg} end?` (line 250)
3. blank(DateOfTravelSource) or blank(TimeOfTravelSource) → slot `departure`:
   - date blank: label `departure date and time`, question `When did you leave for ${leg}? A date and time works, like "5 Oct, 10:30 am".` (line 256)
   - else: label `departure time`, question `What time did you leave for ${leg} on ${showDate(DateOfTravelSource)}?` (line 257)
4. blank(DateOfTravelDestination) or blank(TimeOfTravelDestiNation) → slot `arrival`:
   - date blank: label `arrival date and time`, question `When did you arrive for ${leg}? ("same day 4:30 pm" works too)` (line 265)
   - else: label `arrival time`, question `What time did you arrive for ${leg} on ${showDate(DateOfTravelDestination)}?` (line 266)
5. blank(ModeOfTravel) (incl. `'select'`) → slot `mode`, label `mode of travel`, question `How did you travel for ${leg}: air, rail, bus/taxi, or a personal or official vehicle?` (line 269)

**Accommodation: only if includeAccommodation; empty stay rows skipped.** `stay = 'stay ' + row + (location ? ` (${location})` : '')`:
1. blank(location) → `location`, label `city`, `Which city was ${stay} in?` (277)
2. blank(checkInDate) or blank(checkInTime) → `checkIn`, label `check-in date and time`, `When did you check in for ${stay}? Date and time, please.` (278)
3. blank(checkOutDate) or blank(checkOutTime) → `checkOut`, label `check-out date and time`, `And when did you check out of ${stay}?` (279)
4. `type = selectedType ?? 'hotel'`; type `hotel` and blank(hotelName) → `hotelName`, label `hotel name`, `What was the hotel's name for ${stay}?` (281)
5. type `hotel` or `company_paid`, and blank(invoiceNo) → `invoiceNo`, label `invoice number`, `What's the invoice number on the bill for ${stay}?` (282)
Leave rows are never checked.

### 4.5 `findAmountIssues(state)` (lines 289-304) → string[]
- Transport row i: skip if empty, `billNotRequired`, or `uploadedFiles.length === 0`; if `!(Number(claimedAmount) > 0)` → `` `Journey ${i+1}${route}: amount not read. Enter it on the form.` `` with route as in 4.4 (raw names).
- If includeAccommodation, stay i: skip empty; type hotel/company_paid and `!(Number(claimed) > 0)` (attached bills NOT required) → `` `Stay ${i+1}${location ? ` (${location})` : ''}: amount not read. Enter it on the form.` ``.
(`Number('')` = 0, `Number('abc')` = NaN → both count as "not read".)

### 4.6 `findFormOnlyIssues(state)` (lines 307-324)
`issues = findAmountIssues(state)`; if !includeAccommodation return. For each non-empty stay i, `stay = Stay ${i+1}${loc?` (${loc})`:''}`:
- `!blank(location) && (blank(classOfCity) || classOfCity === 'Select')` → `${stay}: the city class fills in when you pick the city on the form, so please re-select it there.`
- type hotel/company_paid: `billed = Number(billed)||0`; `tax = Number(ClaimedIGSTAmount||0)+Number(ClaimedCGSTAmount||0)+Number(ClaimedSGSTAmount||0)`; `claimedBase = (Number(claimed)||0) - tax`; if `claimedBase > billed` → `${stay}: the claimed amount (${inr(claimedBase)}) is more than the billed amount (${inr(billed)}); adjust it on the form.`

### 4.7 `describeMissing(state, missing)` (lines 327-346)
Group labels by `section:row` (insertion order of first appearance). Lines:
- transport: `• Journey ${row}${route}: ${labels.join(', ')}` (route from current state row, raw names)
- accommodation: `• Stay ${row}${loc ? ` (${loc})` : ''}: ${labels.join(', ')}`
Joined with `\n`.

### 4.8 Read-outs (lines 350-382)
- `summarizeJourneys`: non-empty rows only, renumbered 1..n: `` `${n}. ${src||'?'} → ${dst||'?'} · ${showDate(dep)} ${depTime||'--:--'} to ${showDate(arr)} ${arrTime||'--:--'} · ${MODE_LABELS[mode] ?? 'mode not set'}${Number(claimedAmount) ? ` · ${inr(claimedAmount)}` : ''}` `` joined `\n`; none → `No journeys yet.`
- `summarizeStays`: `!includeAccommodation` or no non-empty rows → `No stays added.`; else `` `${n}. ${loc||'?'} · ${showDate(in)} to ${showDate(out)} · ${ACCOM_LABELS[selectedType ?? 'hotel'] ?? 'Hotel'}${Number(claimed) ? ` · ${inr}` : ''}` ``.
- `summarizeLeave`: `!includeLeave` or none → `No leave.`; else `` `${n}. ${showDate(start)} to ${showDate(end || start)} · DA ${daDeductable === 'Yes' ? 'deducted' : 'not deducted'}` ``.
- `DA_RULES` (lines 384-391), joined `\n`:
```
DA is worked out per calendar day from your first departure to your last arrival.
The first day counts from departure to 23:59, full days in between count 24 hours, and the last day counts from 00:00 to arrival.
Hours in a day: up to 6h → 30%, over 6h up to 12h → 70%, over 12h → 100% of your grade’s daily DA rate.
On a day with free company lodging you get 25% of that.
If you were promoted during the tour, days before the promotion date use your previous grade’s rate.
Leave days marked "DA deductable" are subtracted from the claim.
```

### 4.9 `snapshot(state, tour?)` (lines 404-437) — the claim for the LLM
Lines joined by `\n`:
1. With tour: `Today: ${today}. Approved tour #${tourId}: ${source ?? '?'} to ${destination ?? '?'}, ${startDate ?? '?'} to ${endDate ?? '?'}.` Without: `Today: ${today}. The tour is not known yet; it is worked out from the bills.` — `today = toIsoDate(new Date().toISOString())` = **UTC** date (00:00–05:30 IST gives yesterday).
2. `IN THE CLAIM NOW: ${filled} journey${filled===1?'':'s'}, ${stays} stay${stays===1?'':'s'}.` (filled = non-empty transport; stays = non-empty when includeAccommodation else 0)
3. `JOURNEYS`, then for EVERY transport row i: `T${i+1}: from=${source||'-'} to=${destination||'-'} departure=${DateOfTravelSource||'-'} ${TimeOfTravelSource||'-'} arrival=${DateOfTravelDestination||'-'} ${TimeOfTravelDestiNation||'-'} mode=${ModeOfTravel||'-'} bills=${uploadedFiles.length}` + (`' (blank row)'` if empty).
4. `STAYS`, then if includeAccommodation for every stay row: `A${i+1}: location=${location||'-'} checkIn=${checkInDate||'-'} ${checkInTime||'-'} checkOut=${checkOutDate||'-'} ${checkOutTime||'-'} type=${selectedType||'hotel'} hotelName=${hotelName||'-'} invoiceNo=${invoiceNo||'-'} bills=${billFiles.length}` + `' (blank row)'`; else the single line `(no stays)`.
5. `LEAVE`, then if includeLeave for every leave row: `L${i+1}: ${startDate||'-'} to ${endDate||'-'} deductable=${daDeductable}`; else `(no leave)`.

### 4.10 `CLAIM_EDIT_FORMAT` (lines 440-449) — verbatim prompt text (with `${TRAVEL_MODES.join(', ')}` expanded):
```
"edits" is a list of {"section","row","field","value"}, one per field. One message often covers several journeys: add an edit for EVERY value mentioned, and give a value with no journey number to the journey whose field is still "-".
- section "transport", row = journey number (a new journey: 0, or for several new journeys the numbers after the last one, e.g. 4 and 5 when the claim has 3; a blank T row counts as a journey to fill); fields from, to, departureDate, departureTime, arrivalDate, arrivalTime, mode (one of air, rail, personal-vehicle, public-transport, official-entitled, official-beyond, official-dedicated), distanceKm.
  mode: train/rail/express = rail; flight/plane/air = air; bus, taxi, cab, auto, metro = public-transport; own car/bike/scooter = personal-vehicle; office/company vehicle = official-entitled.
- section "accommodation", row = stay number (0 = add a new stay: a stay in a city the claim does not have yet is a new stay); fields location, checkInDate, checkInTime, checkOutDate, checkOutTime, hotelName, invoiceNo, type (hotel, own_arrangement, company_free = free company guest house/lodging, company_paid).
- section "leave", row = leave number (0 = add); fields startDate, endDate, daDeductable (Yes/No). Only when the employee clearly says they were on leave.
- to delete a wrong or duplicate journey, stay or leave entry: field "remove", value "yes" (with its section and row number).
Dates as YYYY-MM-DD (use the tour's year and month when the employee gives only a day), times as HH:mm 24-hour (8 pm = 20:00, 9.15 in the morning = 09:15, noon = 12:00). Convert exactly what the employee wrote; never reuse a value from the claim or from this format.
When the employee gives a date with the time, list the date too. An arrival time with no date is on the journey's departure date ("next day" = the day after).
Never an amount (fare, cost, rupees): those are not edits, and never a distance taken from an amount.
Shape: {"action":"edit","args":{},"edits":[{"section":"<section>","row":<number>,"field":"<field>","value":"<value>"}]}
```

### 4.11 `readClaimEdits(list)` (lines 452-457)
Keep items that are objects with `section ∈ {transport, accommodation, leave}`, `Number.isFinite(Number(e.row))` (JS: `Number('')`=0, `Number(null)`=0, `Number(true)`=1, `Number('2')`=2, `Number('2a')`=NaN → dropped), `typeof field === 'string'`, `value != null`. Map `row = Number(row)`, `value = String(value)` (JS: `true`→`'true'`, `5`→`'5'`, `5.0`→`'5'`, objects→`'[object Object]'`). Other keys kept.

### 4.12 `applyEdits(input, edits, rules = {allowAir: true})` (lines 467-731) → `{state, applied: str[], rejected: str[]}`
Never mutates `input` (deep-copies each row object; arrays inside rows like uploadedFiles are shared, not mutated).

Helpers:
- `outsideTour(iso) = (minDate && iso < minDate) || (maxDate && iso > maxDate)` (string compare).
- `windowText = minDate && maxDate ? `${showDate(minDate)} – ${showDate(maxDate)}` : ''` (so with only one bound the message shows `()`).
- `lengthAtStart[section]` = number of rows in the INPUT per section (blank rows included).
- `CREATES = /^(from|to|departureDate|arrivalDate|location|checkInDate|checkOutDate|startDate|endDate)$/`.
- `creatable` = set of `section:row` for edits with `Number(row) > lengthAtStart[section]` and field matching CREATES (computed over the whole batch up front, so a `t(4,'departureTime')` is accepted if the same batch has `t(4,'from')`).
- `resolveRow(section, rows, row, isEmpty, create)`:
  1. `1 <= row <= lengthAtStart` → `row-1`.
  2. `isNew = row === 0 || (lengthAtStart < row <= lengthAtStart + 5 && creatable has section:row)`; not new → `-1`.
  3. Same `section:row` key already resolved in this batch → same index (all row-0 edits in a batch share ONE new row).
  4. Else reuse the first row in the *current* state rows that `isEmpty` and isn't already taken by another new key; otherwise append `create()` (transport: `newTransportRow(last current row)` — inherits source/date/mode!; accommodation: `newAccommodationRow()`; leave: `newLeaveRow()`). Note: creating happens before field validation, so a rejected edit on row 0 may still leave an appended blank row.

Main loop over edits in order (`value = String(e.value ?? '').trim()`, `row = Number(e.row)`):
1. **Locked**: `section === 'transport'` and `rules.lockedJourneys` includes `row` → reject `` `Journey ${row}${r ? ` (${r.source || '?'} → ${r.destination || '?'})` : ''} was not changed: the ride asked about is added as a new journey, not written over an existing one.` `` (r = input row row-1; applies to `remove` too). `continue`.
2. **Remove** (`field === 'remove'`): value not matching `/^(y|yes|true)$/i` → silently ignored (nothing pushed). `row === 0`: count non-empty INPUT rows of that section; if count ≠ 1 → reject `` `Which ${journey|stay|leave entry} should go? Please give its number.` ``; if exactly one → queue removal of its (1-based) storage index. Else queue the edit. Removals are applied after all other edits.
3. **transport**: idx = resolveRow(...) ; `idx < 0` → reject `There is no journey ${row}.` Label `Journey ${idx+1}`.
   - `from`/`to`: empty value → fall to generic reject; else set source/destination; applied `${label}: from ${value}` / `${label}: to ${value}`.
   - `departureDate`/`arrivalDate`: `d = toIsoDate(value)`; '' → generic reject; outsideTour(d) → reject `${label}: ${showDate(d)} is outside your approved tour (${windowText}).`; else set; applied `${label}: departure date ${showDate(d)}` / `${label}: arrival date ${showDate(d)}`.
   - `departureTime`/`arrivalTime`: `t = toTime(value)`; '' → generic reject; set; applied `${label}: departure time ${t}` / `${label}: arrival time ${t}`.
   - `mode`: `m = mapTravelMode(value)`; `'select'` → generic reject; `m === 'air' && !rules.allowAir` → reject `${label}: your approved tour has no air travel, so air can't be claimed.`; else set; applied `${label}: ${MODE_LABELS[m]}`.
   - `distanceKm`: `km = parseFloat(value)` (JS: leading-number parse: `'120 km'`→120, `'1e3'`→1000, `'far'`→NaN); `!(km > 0)` → generic reject; set `distanceKm = String(km)` (JS number→string: `120`, `12.5`); applied `${label}: ${km} km`.
   - anything else / fallthrough → reject `${label}: couldn't use "${value}" for ${e.field}.`
4. **accommodation**: idx<0 → reject `There is no stay ${row}.`. **Then `includeAccommodation = true` (even if this edit is later rejected).** Label `Stay ${idx+1}`.
   - `location` → applied `${label}: ${value}`; `hotelName` → `${label}: hotel ${value}`; `invoiceNo` → `${label}: invoice no. ${value}` (empty value → generic reject).
   - `checkInDate`/`checkOutDate`: as dates above; outside → `${label}: ${showDate(d)} is outside your approved tour (${windowText}).`; applied `${label}: check-in ${showDate(d)}` / `${label}: check-out ${showDate(d)}`.
   - `checkInTime`/`checkOutTime`: toTime; applied `${label}: check-in time ${t}` / `${label}: check-out time ${t}`.
   - `type`: `fields = accommodationTypeFields(value)`; if `fields.selectedType === r.selectedType` → applied `${label}: already ${ACCOM_LABELS[type]}`; else assign fields plus resets `billed:'0', tax:'0', totalBilled:'0', claimed:'0', hotelName:'', billFiles:[], BillSGSTAmount:'0', BillCGSTAmount:'0', BillIGSTAmount:'0', validationErrors:{}`; applied `${label}: ${ACCOM_LABELS[type]}` + (`' (bill amounts cleared)'` unless type is `hotel`). Never rejected.
   - else → `${label}: couldn't use "${value}" for ${e.field}.`
5. **leave**: idx<0 → reject `There is no leave entry ${row}.`. **Then `includeLeave = true`.** Label `Leave ${idx+1}`.
   - `startDate`/`endDate`: `d = toIsoDate(value)`; d and outside → reject `${label}: ${showDate(d)} is outside your tour (${windowText}).` (note: "your tour", not "approved tour"); d → set; applied `${label}: from ${showDate(d)}` / `${label}: to ${showDate(d)}`; '' → generic reject.
   - `daDeductable`: `'Yes'` if value matches `/^(y|yes|true)$/i` else `'No'`; applied `${label}: DA deductable ${Yes|No}`. Never rejected.
   - else → `${label}: couldn't use "${value}" for ${e.field}.`
6. Unknown section → reject `Couldn't apply a change to ${e.section} ${e.field}.`

Removals: sorted by row descending (stable). For each: `rows` = current state rows of the section; `!(1 <= row <= len(rows))` → reject `There is no ${journey|stay|leave entry} ${row} to remove.`; else delete index row-1; if the section becomes empty: transport → push `newTransportRow()`; accommodation → push `newAccommodationRow()` and `includeAccommodation=false`; leave → push `newLeaveRow()` and `includeLeave=false`. Applied `Removed ${name} ${row}`. (Quirk: the same row queued twice deletes two different rows.)
Applied removal messages come after all field messages. Rejected messages are in encounter order, with removal rejections last.

---------------------------------------------------------------------------------------------------

## 5. claimProgress.ts

### 5.1 `coveredDays(state)` (lines 48-60) → set of ISO days
`addSpan(a,b)`: `from = toIsoDate(a)`, `to = toIsoDate(b)`; both and `to >= from` → all of `eachDay(from,to)` (120-day cap); else add each of from/to that is non-empty.
- non-empty transport rows: addSpan(DateOfTravelSource, DateOfTravelDestination)
- if includeAccommodation: non-empty stays: addSpan(checkInDate, checkOutDate)
- if includeLeave: non-empty leave: addSpan(startDate, endDate || startDate)

### 5.2 `uncoveredDays(state, tour)` (63-69): no tour / no `tourWindow` → `[]`; `covered` empty → `[]` (!); else days of `eachDay(window.start, window.end)` not covered.
### 5.3 `coveredText(state)` = `groupDays([...coveredDays(state)])`.

### 5.4 `findRelationIssues(state)` (77-112) — storage order, non-empty journeys
- Consecutive pairs (prev = journeys[i-1]): `from = norm(r.source)`, `to = norm(prev.destination)`; both non-empty and `!samePlace` → `Journey ${i+1} starts from ${r.source}, but journey ${i} ended in ${prev.destination}. Is a ticket missing in between?` (i is the index in the *filtered* list).
- If includeAccommodation and ≥1 journey: `places = norm of every source & destination` (non-empty), `dates = all toIsoDate dep & arr` (non-empty, sorted). For each non-empty stay (storage index i):
  - `city = norm(location)`; city and no place `samePlace` → `Stay ${i+1} is in ${r.location}, but none of your journeys go there. If this isn't your bill for this trip, say "remove stay ${i+1}".` then next stay.
  - `first = dates[0]`, `last = dates[-1]`; `early = first && inn && inn < first-1day`; `late = journeys.length > 1 && last && out && out > last+1day`; early or late → `Stay ${i+1}${loc?` (${loc})`:''} falls outside your travel dates (${rangeText(first, last)}). If this isn't your bill for this trip, say "remove stay ${i+1}".`

### 5.5 `findTimeOverlaps(state)` (125-138)
For each `journeyOverlaps(non-empty rows)` pair (numbers a, b = storage index+1 of earlier/later):
`when(date,time) = [time, toIsoDate(date) ? dayText(toIsoDate(date)) : ''].filter(Boolean).join(' on ')`; `name(r) = journey ${idx+1} (${source||'?'} → ${destination||'?'})`.
Text: `The times of journey ${a} and journey ${b} overlap: ${name(later)} departs at ${when(later dep)}, before ${name(earlier)} arrives at ${when(earlier arr)}. Please correct journey ${b}'s departure or journey ${a}'s arrival in the claim (the fields are marked in red), or tell me the right times.`

### 5.6 `findClaimIssues(state, rules?)` (143-180)
`outside(iso)` as applyEdits (with optional rules); `window = minDate && maxDate ? ` (${rangeText(minDate, maxDate)})` : ''`; `stamp(date,time) = toIsoDate(date) ? `${toIsoDate(date)} ${time || '00:00'}` : ''`.
For each non-empty transport row i: `label = Journey ${i+1}${route}` (route raw as 4.4).
- `dep`, `arr` iso. If `dep && dep === arr`: outside(dep) → `${label}: ${dayText(dep)} is outside your approved tour${window}.`
  else for each of [dep, arr] (k=0 departure, 1 arrival) non-empty & outside → `${label}: the ${departure|arrival} date ${dayText(d)} is outside your approved tour${window}.`
- `a = stamp(dep)`, `b = stamp(arr)`; `a && b && b < a` → `${label}: it arrives before it departs. Please check the dates and times.` (times not required here — missing time defaults 00:00).
Then all `findTimeOverlaps(state)` lines.
Then if includeAccommodation: each non-empty stay with stamps `b < a` → `Stay ${i+1}${loc?` (${loc})`:''}: the check-out is before the check-in. Please check the dates and times.`
Then if includeLeave: each non-empty leave, for each of [startDate, endDate]: iso and outside → `Leave ${i+1}: ${dayText(iso)} is outside your approved tour${window}.` (two lines if both outside).

### 5.7 `buildProgress({state, tour, rules, staysAnswered, leaveAnswered, doneUploading})` (207-237) → `{done, left, heads, notes, ready}`
`plural(n, w) = `${n} ${w}${n===1?'':'s'}``. Counts: journeys (non-empty), stays (non-empty, only if includeAccommodation), leaves (non-empty, only if includeLeave). `missing = findMissingFields(state)`.
In order:
1. tour → done `Tour confirmed: ${tour.source || '?'} → ${tour.destination || '?'}`; else left `Confirm which tour these bills belong to`.
2. journeys → done `${plural(journeys,'journey')} added`; else left `Add your travel details (upload a ticket, or tell me about the trip)`.
3. stays → done `${plural(stays,'stay')} added`. 4. leaves → done `${plural(leaves,'leave entry')} added` (→ "2 leave entrys" — verbatim quirk).
5. journeys && missing empty → done `All journey and stay details are filled in`.
6. missing non-empty → each line of `describeMissing(state, missing)` with leading `•\s*` removed → left. (Note: a blank-only claim has missing items for the blank row, so "Journey 1: starting point, destination, departure date and time, arrival date and time, mode of travel" is in `left`.)
7. tour && !doneUploading: `gaps = uncoveredDays(state, tour)`; non-empty → left `Nothing yet for ${groupDays(gaps)}: do you have more tickets or bills?`
8. journeys && staysAnswered → done `Stays checked`; else if journeys → left `Tell me whether you stayed anywhere overnight`.
9. journeys && leaveAnswered → done `Leave checked`; else if journeys → left `Tell me whether you were on leave on any day`.
`heads = findFormOnlyIssues(state) + findClaimIssues(state, rules)`; `notes = findRelationIssues(state)`; `ready = left empty`.

### 5.8 `progressText(p)` (240-248)
Parts joined by `\n\n`: `Here's where we are.`; if done: `✅ Done\n` + `• x` lines; if left: `⬜ Still to do\n…`; if heads: `⚠️ The form will flag\n…`; if notes: `👀 Worth a check\n…`; final line:
- ready and heads → `You can fix the flagged points here or on the form, then you're good to go.`
- ready, no heads → `Everything looks good. Say "submit" or "fill the form" when you're ready.`
- not ready → `Tell me what you have, and we'll finish it together.`

### 5.9 `stayBillProblem(row, rules, journeys)` (254-273) → reason string or null
1. `inn = toIsoDate(checkInDate)`, `out = toIsoDate(checkOutDate)`; neither → null.
2. both and (`out < inn` or (`out === inn` and both times truthy and `checkOutTime < checkInTime` (string compare))) → `the check-out date is before the check-in date`.
3. `stayDates` = the distinct values of `[rangeText(inn,inn), rangeText(out,out)]` (i.e. `dayText`) for the present dates, joined `' to '` → e.g. `01 Dec 2026 to 03 Dec 2026` or `05 Oct 2026` (same day/one date).
4. `outsideTour(d) = d && ((minDate && d < minDate) || (maxDate && d > maxDate))` (rules may be undefined → never outside). inn or out outside → `its dates (${stayDates}) are outside your tour dates${minDate && maxDate ? ` (${rangeText(minDate, maxDate)})` : ''}`.
5. `dates` = all toIsoDate of given journeys' DateOfTravelSource & DateOfTravelDestination, non-empty, sorted. If any: `early = inn && inn < first-1day`; `late = journeys.length > 1 && out && out > last+1day` (journeys.length counts the passed list, not dated ones); → `its dates (${stayDates}) don't fit your travel dates (${rangeText(first, last)})`.
6. null.

---------------------------------------------------------------------------------------------------

## 6. tourReview.ts

### 6.1 Local helpers (lines 98-124)
- `addDay(iso, n)`; `stamp(date, time) = toIsoDate(date) ? `${toIsoDate(date)} ${time || '00:00'}` : ''` (time used raw, string-compared).
- `place(v)` §0.5; `same(a,b)` §0.4.
- `journeyName(i, r) = journey ${i+1} (${place(source)} → ${place(destination)})` (i = storage index; lower-case "journey").
- `stayName(i, r) = stay ${i+1}${location ? ` (${place(location)})` : ''}`.
- `TICKETED = {air, rail, public-transport, official-beyond}` (bill normally attached).
- `MAIN_MODES = {air, rail}`; `isLocal(mode) = !!mode && mode !== 'select' && !MAIN_MODES.has(mode)` (any other non-empty string, incl. unknown raw strings, is local).
- `toMinutes(date, time)`: `iso = toIsoDate(date)`; none → null; time used only if it matches `^\d{1,2}:\d{2}$`, else `12:00`; returns minutes since epoch of that local datetime (Python: minutes from any fixed naive origin; only differences/comparisons matter).
- `CONVEYANCE_WINDOW_MIN = 480` (8 h); `CONNECTION_MIN = 240` (4 h); transfer window 720 (12 h, inline); slack −30 min (inline).
- `stationOf(mode, where) = ${mode === 'air' ? 'the airport' : 'the railway station'} in ${place(where)}`.

### 6.2 `reviewClaim({state, tour, rules?, formProblems = [], leaveAnswered = false})` (lines 127-665) → ReviewItem[] in this exact order
Setup:
- `journeys` = non-empty transport rows as `{r, i}` (i = storage index), **sorted** by `toMinutes(DateOfTravelSource, TimeOfTravelSource)` (null → +∞), ties (incl. ∞ vs ∞) by `i`.
- `stays` = includeAccommodation ? non-empty stay rows `{r, i}` (storage order) : `[]`.
- `leaves` = includeLeave ? non-empty leave rows : `[]`.
- `home = tour?.source?.trim()` (may be undefined/'' → falsy). `window = tour ? tourWindow(tour) : null` (§8.1).

**Step 0 — oddPlace** (lines 140-184). For each sorted journey: `fromProblem = placeProblem(source)`, `toProblem = placeProblem(destination)` (§7).
- fromProblem → item (field from), toProblem → item (field to):
  - id `oddPlace:transport:${i}:${field}:${norm(value)}`, kind oddPlace, blocking false, resolutions `['chat','confirm']`, yes `'confirm'`, section transport, row i+1 (no `no`, no `newJourney`).
  - fact: `Journey ${i+1} has “${value}” as its ${field==='from' ? 'starting point (From)' : 'destination (To)'}, but ${problem}. It was probably misread from the bill.`
  - question: `Journey ${i+1} has “${value}” as its ${field==='from' ? 'starting point' : 'destination'}, which doesn't look like a place (${problem}). ${field==='from' ? `Where did journey ${i+1} start from?` : `Where did journey ${i+1} end?`}`
- neither problem and `samePlaceBothEnds(source, destination)` → id `oddPlace:transport:${i}:same:${norm(source)}`, same flags; fact `Journey ${i+1} goes from “${source}” to “${destination}”, the same place at both ends.`; question `Journey ${i+1} goes from ${place(source)} to ${place(destination)}, the same place. Where did it actually start, and where did it end?`
For each stay with `placeProblem(location)`: id `oddPlace:accommodation:${i}:${norm(location)}`, section accommodation, row i+1, same flags; fact `Stay ${i+1} has “${location}” as its city, but ${problem}. It was probably misread from the bill.`; question `Stay ${i+1} has “${location}” as its city, which doesn't look like a place (${problem}). Which city was the stay in?`

**Step 1 — route** (lines 187-269).
- No journeys → `noJourneys`: id `noJourneys`, blocking **true**, resolutions `['upload','chat']`, yes `upload`; fact `The claim has no journeys yet.`; question `There are no journey tickets or bills in the claim yet. Do you have them to upload? You can also tell me about the journey.` (no section/row).
- Else `first = journeys[0].r`, `last = journeys[-1].r` (sorted by departure, NOT by arrival); `closedLoop = journeys.length > 1 && same(first.source, last.destination)`.
  - **noOutbound** if `home && first.source && !same(first.source, home) && !closedLoop`: id `noOutbound:${norm(first.source)}`, newJourney true, blocking false, resolutions `['upload','chat','confirm']`, yes upload, no confirm, section transport, row `journeys[0].i+1`; fact `Your tour starts from ${place(home)}, but the first journey in the claim starts from ${place(first.source)}.`; question `There is no onward journey ticket from ${place(home)} to ${place(first.source)}. Do you have it?`
  - **noReturn** if `last.destination && !closedLoop && !(home && same(last.destination, home))` (fires even without a tour): id `noReturn:${norm(last.destination)}`, newJourney true, blocking false, resolutions `['upload','chat','confirm']`, yes upload, no confirm, section transport (no row); fact `The last journey in the claim ends in ${place(last.destination)}${home ? `, and the tour started from ${place(home)}` : ''}: there is no journey back.`; question `There are no return journey tickets/bills uploaded${home ? ` (${place(last.destination)} → ${place(home)})` : ''}. Do you have them?`
  - **routeBreak**: `chain = journeys with mode in MAIN_MODES` if any, else all journeys. For k ≥ 1 with `x.r.source && prev.r.destination && !same(x.r.source, prev.r.destination)`: id `routeBreak:${k}:${norm(prev.r.destination)}:${norm(x.r.source)}`, newJourney true, blocking false, resolutions `['upload','chat','confirm']`, yes upload, no confirm, section transport, row x.i+1; fact `${journeyName(prev)} ends in ${place(prev.destination)}, but ${journeyName(x)} starts from ${place(x.source)}.`; question `Is a ticket missing between ${place(prev.r.destination)} and ${place(x.r.source)}? Do you have it?`
  - **overlap**: for each pair of `journeyOverlaps(sorted rows)` (§3; it re-sorts by stamp): `prevArrives = stamp(prev arr date, arr time)`, `departs = stamp(x dep date, dep time)`; id `overlap:${x.i}:${prev.i}:${departs}:${prevArrives}`, blocking **true**, resolutions `['chat','form']`, section transport, row x.i+1 (x = later); fact `The times of journey ${prev.i+1} and journey ${x.i+1} overlap: ${journeyName(x)} departs (${departs}) before ${journeyName(prev)} arrives (${prevArrives}). Both times are marked in red on the claim form.`; question `${journeyName(x)} departs at ${departs}, before ${journeyName(prev)} arrives at ${prevArrives}, so their times overlap. Which time is wrong? Tell me the right time, or correct it on the claim form (marked in red).` (Note: question starts with lower-case "journey".)

**Step 2 — missingField** (lines 272-283). For each `findMissingFields(state)` item m (§4.4 order; includes the blank transport row!): id `missing:${m.section}:${m.row}:${m.slot}`, blocking **true**, resolutions `['chat','form']`, section, row; fact `${m.section==='transport' ? 'Journey' : 'Stay'} ${m.row}: the ${m.label} is missing.`; question `m.question`.

**Step 3 — badDates** (lines 286-343). `outside(iso)` with optional rules; `windowText = minDate && maxDate ? ` (${rangeText(minDate, maxDate)})` : ''` (claimProgress rangeText).
For each sorted journey:
- `out = [dep iso, arr iso]` non-empty and outside. Non-empty → id `badDates:transport:${i}:outside:${out.join(',')}`, blocking true, resolutions `['chat']`, section transport, row i+1; fact `${journeyName}: ${dayText(out[0])} is outside your approved tour${windowText}.`; question `${journeyName} is dated ${dayText(out[0])}, outside your approved tour${windowText}. What is the right date?`
- `a = stamp(dep)`, `b = stamp(arr)`; `a && b && b < a && TimeOfTravelSource && TimeOfTravelDestiNation` (both raw times required) → id `badDates:transport:${i}:backwards:${a}:${b}`, blocking true, resolutions `['chat']`; fact `${journeyName} arrives (${b}) before it departs (${a}).`; question `${journeyName} arrives before it departs. What are the right departure and arrival times?`
For each stay (storage order) — NB order is backwards first, then outside:
- `a = stamp(checkIn)`, `b = stamp(checkOut)`; `a && b && b < a` (times NOT required) → id `badDates:accommodation:${i}:${a}:${b}`, section accommodation; fact `${stayName}: the check-out (${b}) is before the check-in (${a}).`; question `For ${stayName}, the check-out is before the check-in. What are the right check-in and check-out dates and times?`
- outside → id `badDates:accommodation:${i}:outside:${out.join(',')}`; fact `${stayName}: ${dayText(out[0])} is outside your approved tour${windowText}.`; question `${stayName} is dated ${dayText(out[0])}, outside your approved tour${windowText}. What are the right dates?`

**Step 4 — days & nights** (lines 346-462), only if journeys exist.
- `firstDay` = min of toIsoDate(dep) over journeys (non-empty); `lastDay` = max of (toIsoDate(arr) || toIsoDate(dep)).
- `hasReturnGap` = an item of kind noReturn exists; `hasOutboundGap` = noOutbound exists.
- **uncoveredDays** (only if `window`): `covered` from `span(a,b)`: `f = toIsoDate(a)`, `t = toIsoDate(b) || f`; if `f && t && t >= f` add `eachDay(f,t)` (NB: unlike coveredDays, nothing is added when a > b or a is missing). Spans: journeys (dep, arr), stays (in, out), leaves (start, end||start). `gaps = eachDay(window.start, window.end)` minus covered, minus days `> lastDay` when hasReturnGap, minus days `< firstDay` when hasOutboundGap. Non-empty → id `uncovered:${gaps.join(',')}`, blocking false, resolutions `['upload','chat','confirm']`, yes upload, no confirm (no section); fact `Your tour runs ${rangeText(window.start, window.end)}, but nothing in the claim covers ${groupDays(gaps)}.`; question `Nothing in the claim covers ${groupDays(gaps)} of your tour. Do you have tickets or bills for those days?`
- **nightsWithoutStay** if `firstDay && lastDay && lastDay > firstDay`: for each `night` in `eachDay(firstDay, lastDay - 1)`, `next = night+1`:
  - `stayed` = any stay with in & out isos, `in <= night && out >= next`;
  - `travelling` = any journey with dep & arr isos, `dep <= night && arr >= next`;
  - `onLeave` = any leave with `s = iso(start)`, `e = iso(end) || s`, `s <= night && e >= night`;
  - `arrived` = journeys (sorted order) with `(toIsoDate(arr) || '') <= night` (a journey without arrival date counts as arrived, since '' sorts first); `where = arrived.length ? arrived[-1].r.destination : journeys[0].r.destination`;
  - `atHome = (home && same(where, home)) || same(where, journeys[0].r.source)`;
  - none of stayed/travelling/onLeave/atHome → push `{night, city: place(where)}`.
  Runs: consecutive nights (`run.to + 1 === night`) with identical city string merge. Each run: `when = from === to ? `the night of ${dayText(from)}` : `the nights of ${rangeText(from, to)}``; id `nights:${from}:${to}:${norm(city)}`, blocking false, resolutions `['upload','chat','confirm']`, yes upload, no confirm, section accommodation (no row); fact `There is no stay in the claim for ${when} in ${city}.`; question `Where did you stay on ${when} in ${city}? Upload the hotel bill, tell me (hotel, guest house or own arrangement), or say if you had no stay to claim.`
- **stays vs journeys**: `places` = every journey source & destination (truthy). For each stay (storage order):
  - `location && !places.some(p => same(p, location))` → **stayNotOnRoute**: id `stayNotOnRoute:${i}:${norm(location)}`, blocking false, resolutions `['confirm','chat']`, yes confirm, no **remove**, section accommodation, row i+1; fact `${stayName} is in ${place(location)}, but none of the journeys go there.`; question `${stayName} is in ${place(location)}, but none of your journeys go there. Is this stay part of this tour?` — then skip the next check for this stay.
  - `early = firstDay && in && in < firstDay-1`; `late = lastDay && out && out > lastDay+1 && !hasReturnGap` → **stayOutsideTravel**: id `stayOutsideTravel:${i}:${in}:${out}` (empty strings when missing), resolutions `['confirm','chat']`, yes confirm, no remove, section accommodation, row i+1; fact `${stayName} (${rangeText(in || out, out || in)}) falls outside your travel dates (${rangeText(firstDay, lastDay)}).`; question `${stayName} falls outside your travel dates (${rangeText(firstDay, lastDay)}). Are its dates right?`

**Step 4b — conveyance** (lines 465-589), only if `mains` (sorted journeys with mode air/rail) is non-empty. `locals` = sorted journeys with isLocal(mode). `used` = set of storage indexes.
- `depOf(r) = toMinutes(depDate, depTime)`; `arrOf(r) = toMinutes(arrDate || depDate, arrTime)`.
- `before(m)` = first local l (sorted order; **used ones are NOT excluded**) with `reach = arrOf(l)`, `leave = depOf(m)` both non-null, `gap = leave - reach`, and `(-30 <= gap <= 480) || (iso(l.arrDate) === iso(m.depDate) && same(l.destination, m.source) && gap >= -30)`.
- `after(m)` = first local l with `leave = depOf(l)`, `reached = arrOf(m)`, `gap = leave - reached`, and `(-30 <= gap <= 480) || (iso(l.depDate) === iso(m.arrDate || m.depDate) && same(l.source, m.destination) && gap >= -30)`.
- `firstSource = journeys[0].r.source` (first sorted journey of ANY mode). `nearHome(w) = (home && same(w, home)) || same(w, firstSource)`.
- `stayStartsThere(w, day)` = a stay with same(location, w) and iso(checkIn) === day; `stayEndsThere(w, day)` = same with iso(checkOut).
- `conveyance(kind, key, row, fact, question, side)` pushes `{id: `${kind}:${key}`, kind, blocking:false, resolutions:['upload','chat','confirm'], yes:'upload', no:'confirm', section:'transport', row, fact, question, newJourney:true, side}`.
- `sideOf(k) = (mains.length === 1 || k === 0) ? 'onward' : (k === mains.length-1 ? 'return' : 'middle')`.
Loop 1 over mains (k, m; prev = mains[k-1], next = mains[k+1]); `leaveDay = iso(m.dep)`, `arriveDay = iso(m.arr) || leaveDay`, `mode = m is air ? 'flight' : 'train'`, `name = `${journeyName(m)} by ${air ? 'air' : 'rail'}``, `dayOrQ(d) = d ? dayText(d) : '?'`.
1. `prevGap = prev ? (depOf(m) ?? 0) - (arrOf(prev) ?? 0) : +∞`; `isTransferLeg = prev && 0 <= prevGap <= 720`. If not transfer leg: `b = before(m)`; found → `used.add(b.i)`; else **conveyanceBefore**: `from = (k === 0 && nearHome(m.source)) ? 'home' : stayEndsThere(m.source, leaveDay) ? 'your hotel' : 'your home, hotel or office'`; key `${m.i}:${norm(m.source)}:${leaveDay}` (leaveDay '' if missing); row m.i+1; side sideOf(k);
   fact `There is no cab, taxi or other conveyance in the claim for getting to ${stationOf(mode, m.source)} for ${name} on ${dayOrQ(leaveDay)}.`;
   question `How did you get from ${from} to ${stationOf(mode, m.source)} for your ${mode} on ${dayOrQ(leaveDay)}? If you took a cab, taxi or auto, do you have the bill?`
2. `nextGap = next ? (depOf(next) ?? 0) - (arrOf(m) ?? 0) : +∞`. If `next && 0 <= nextGap <= 720`:
   `connection = same(m.destination, next.source) && nextGap <= 240 && m.mode === next.mode`; `ride` = first local l with `t = depOf(l)` non-null and `(arrOf(m) ?? 0) - 30 <= t <= (depOf(next) ?? 0)`. ride → used.add; else if !connection → **conveyanceTransfer**: key `${m.i}:${next.i}:${norm(m.destination)}:${norm(next.source)}`, row next.i+1, side `'middle'`;
   fact `There is no cab, taxi or other conveyance in the claim between ${journeyName(m)} arriving in ${place(m.destination)} and ${journeyName(next)} leaving from ${place(next.source)}.`;
   question `How did you get from ${stationOf(m.mode, m.destination)} to ${stationOf(next.mode, next.source)} for your next ${next is air ? 'flight' : 'train'}? If you took a cab, taxi or auto, do you have the bill?`
   Then **return** (no conveyanceAfter for this main).
3. Else `a = after(m)`; found → used.add; else **conveyanceAfter**: `to = (!next && nearHome(m.destination)) ? 'home' : stayStartsThere(m.destination, arriveDay) ? 'your hotel' : 'your hotel, office or site'`; key `${m.i}:${norm(m.destination)}:${arriveDay}`, row m.i+1, side sideOf(k);
   fact `There is no cab, taxi or other conveyance in the claim for getting from ${stationOf(mode, m.destination)} to ${to === 'home' ? 'home' : 'the hotel or office'} after ${name} on ${dayOrQ(arriveDay)}.`;
   question `After your ${mode} reached ${place(m.destination)} on ${dayOrQ(arriveDay)}, how did you get to ${to}? If you took a cab, taxi or auto, do you have the bill?` (→ "how did you get to home?" verbatim.)
Loop 2 (after loop 1 finished, so `used` is complete) over mains with a next: `inDay = iso(m.arr) || iso(m.dep)`, `outDay = iso(next.dep)`; skip if missing or `outDay <= inDay`; `city = m.destination`; skip if nearHome(city); `around` = any local not in `used` with `d = iso(l.dep)` and `inDay <= d <= outDay`. Not around → **localConveyance**: id `localConveyance:${norm(city)}:${inDay}:${outDay}`, blocking false, resolutions `['upload','chat','confirm']`, yes upload, no confirm, section transport, newJourney true (no row, no side);
fact `The claim has no local travel (cab, taxi, auto) in ${place(city)} between ${rangeText(inDay, outDay)}.`;
question `While you were in ${place(city)} (${rangeText(inDay, outDay)}), did you take any cabs, taxis or autos to get around, for example from the hotel to the office or site and back? Do you have those bills?`

**Step 5 — bills & amounts** (lines 592-632).
- Sorted journeys with `!billNotRequired && uploadedFiles empty && TICKETED.has(mode)` → **ticketMissing**: id `ticket:${i}:${norm(source)}:${norm(destination)}`, blocking false, resolutions `['upload','confirm']`, yes upload, no confirm, section transport, row i+1; fact `No ticket or bill is attached for ${journeyName}.`; question `No ticket or bill is attached for ${journeyName}. Do you have it to upload?`
- Stays (type hotel/company_paid, billFiles empty) → **hotelBillMissing**: id `hotelBill:${i}:${norm(location)}`, same flags, section accommodation, row i+1; fact `No hotel bill is attached for ${stayName}.`; question `No hotel bill is attached for ${stayName}. Do you have it to upload?`
- For each `findAmountIssues(state)` issue (k = index): **amountMissing** id `amount:${k}:${issue}`, blocking false, resolutions `['form','confirm']` (no yes/no/section/row); fact = issue; question = `issue.replace(/ Enter it on the form\.?$/, '') + '. Please enter it on the claim form. Shall we carry on with the rest meanwhile?'` → verbatim result has a **double period**: `Journey 1 (Noida → Mumbai): amount not read.. Please enter it on the claim form. Shall we carry on with the rest meanwhile?`

**Step 6 — formOnly** (lines 635-648): each formProblem with kind ≠ `'field'` (in input order): id `form:${kind}:${section}:${row}`, blocking **true**, resolutions `['form']`, section = undefined if `'general'` else section, row = `row || undefined`; fact `The claim form says: ${message}`; question `The claim form won't accept this yet: ${message} Please fix it on the form and tell me when it's done.`

**Step 7 — askLeave** (651-662): `!leaveAnswered && leaves.length === 0 && journeys.length > 0` → id `askLeave`, blocking false, resolutions `['chat','confirm']`, no confirm, yes details, section leave; fact `The claim has no leave entries, and the employee has not said whether they took leave during the tour.`; question `Were you on leave on any day of the tour? If yes, which days?`

### 6.3 Review loop (lines 681-692)
- `nextReviewStep(items, confirmed, deferred)`: `open = items whose id ∉ confirmed ∪ deferred`; non-empty → `{type:'ask', item: open[0], open}`; else `blocking = items.filter(blocking)` (ALL blocking items, also confirmed/deferred ones) → non-empty `{type:'blocked', blocking}` else `{type:'done'}`.
- `settle(item, how, confirmed, deferred)`: `how === 'confirm' && !blocking` → confirmed.add(id); `how === 'later'` → (blocking ? deferred : confirmed).add(id); `confirm` on a blocking item does nothing.

### 6.4 Notes for the model (lines 696-731)
- `howToSettle(item)`: parts (in order, `'; '`-joined) for resolutions: upload → `they can attach the ticket or bill with the 📎 button`; chat → `they can tell you the details in the chat`; form → `it is fixed on the claim form itself`; confirm and !blocking → `or they can confirm the claim is right as it is (for example they have no ticket, or there was nothing to claim)`.
- `reviewQuestionNote(item, open, again=false)` = (again ? `This is still not sorted out. ` : '') + `Before the claim can be submitted, it is checked for everything that makes the tour incomplete or that the claim form would reject. ` + (open.length === 1 ? `This is the last thing to check` : `${open.length} things are left to check (${blockingCount} of them must be fixed before submitting)`) + `.\n` + `The problem: ${item.fact}\n` + (item.blocking ? `This must be fixed before the claim can be submitted. ` : '') + `How it can be settled: ${howToSettle(item)}.\n` + `Ask the employee exactly this question, and only this one: ${item.question}` (blockingCount = blocking items among `open`).
- `reviewBlockedNote(blocking)` = `Everything else is checked, but the claim form will not accept the claim until these are fixed on the form:\n` + lines `• ${fact}` joined `\n` + `\nAsk the employee to fix them on the form, then say "submit" again.`
- `reviewDoneNote(claimText, submit)`: submit → `The review is complete: nothing is missing and the form's checks pass. ${claimText}\n\nBy submitting, the employee declares that these details and the attached bills are genuine and correct. Ask whether to submit the claim now.`; else `The review is complete: nothing is missing and the form's checks pass. ${claimText}\n\nTell the employee the claim is complete, and that they can say "submit" when they are ready.`
- `reviewSettledNote(item, how)`: upload → `The employee has it. Ask them to attach it with the 📎 button now (PDF or photo); the check carries on once it is read. If they would rather type the details, they can.`; confirm → `The employee confirmed this is right as it is: ${fact} It is left as it is.`; later → `The employee will fix this on the claim form: ${fact}` + (blocking ? ` The claim cannot be submitted until it is fixed.` : '').

### 6.5 `missingInShort(items)` (lines 743-794) → one sentence or `''`
`parts` built in this order:
1. `odd = count(oddPlace)` > 0 → `a place name to correct (misread from the bill)` (1) / `${odd} place names to correct (misread from the bill)`.
2. has noJourneys → `journey tickets`. 3. has noOutbound → `Onward journey ticket`. 4. has noReturn → `Return journey ticket`.
5. each routeBreak: regex `/between (.+?) and (.+?)\?/` on question → `ticket between ${1} and ${2}` else `a ticket in between`.
6. each overlap: `/journey (\d+) and journey (\d+) overlap/` on **fact** → `correct times for journeys ${1} and ${2} (they overlap)` else `correct times for journeys that overlap`.
7. `conv` = items of kind conveyanceBefore/After/Transfer. If `conv.length > 0 || has noReturn`: `onward = conv some side 'onward'`; `back = has noReturn || conv some side 'return'`; sides = both → `for both onward and return journeys`, onward → `for the onward journey`, back → `for the return journey`; non-empty → `cabs/taxis and other conveyance bills ${sides}`. `transfers = conv with side 'middle'`; non-empty → `cab/taxi bills for going between ${count(conv kind conveyanceTransfer) > 1 ? 'stations and airports' : 'stations and airports on the way'}`.
8. each localConveyance: `/While you were in (.+?) \(/` → `cab/taxi bills for local travel in ${city}` (or without ` in …`).
9. each nightsWithoutStay: `/on (the nights? of .+?) in (.+?)\?/` → `hotel stay for ${1} in ${2}` else `a hotel stay`.
10. each uncoveredDays: `/covers (.+?) of your tour/` → `tickets or bills for ${days ?? 'some days of the tour'}`.
11. ticketMissing questions `/for (journey \d+ \(.+?\))/` (collected, missing matches dropped) → one part `ticket or bill file for ${joined ' and '}`.
12. hotelBillMissing questions `/for (stay \d+(?: \(.+?\))?)\./` → `hotel bill for ${joined ' and '}`.
13. `details = count(missingField)` → `${details} detail${details===1?'':'s'} (dates, times or places) to fill in`.
Ignored kinds: badDates, stayNotOnRoute, stayOutsideTravel, amountMissing, formOnly, askLeave.
Result: no parts → `''`; else `Missing ${[upper(first), ...rest.map(lower)].join(', ')}.` where upper/lower change **only the first character**.
Examples (tests): `Missing Return journey ticket, cabs/taxis and other conveyance bills for both onward and return journeys.`; `Missing Cabs/taxis and other conveyance bills for both onward and return journeys, cab/taxi bills for local travel in Mumbai.`

---------------------------------------------------------------------------------------------------

## 7. placeCheck.ts (`src/lib/placeCheck.ts`) — used by step 0
`placeProblem(raw)` (lines 15-30) → reason or null. `value = String(raw ?? '').trim()`; empty → null. Checks in order:
1. PLACEHOLDER `^(n\/?a|na|nil|null|undefined|unknown|not available|not applicable|none|tbd|tba|select|-+|\.+|\?+|x+)$` (i) → `it is a placeholder, not a place name`
2. `value.length < 2` → `it is too short to be a place name`
3. MONEY `[₹$€£]|\b(rs|inr|usd)\.?\s*\d|\b\d{1,3}(,\d{2,3})+(\.\d{1,2})?\b|\b\d+\.\d{2}\b` (i) → `it looks like an amount from the bill`
4. DATE_OR_TIME `\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}\s*(am|pm|hrs)?\b` (i) → `it looks like a date or time from the bill`
5. CONTACT `@|https?:\/\/|www\.|\.com\b|\.in\b` (i) → `it looks like an email or web address`
6. BILL_WORDS `\b(total|sub ?total|grand total|net amount|net payable|amount|amt|fare|base fare|gst|cgst|sgst|igst|tax|taxes|vat|cess|invoice|receipt|pnr|booking|booking id|ticket no|passenger|pax|seat|berth|coach|quota|charges?|fees?|convenience|discount|paid|payment|payable|balance|refund|cancellation|toll|surcharge|insurance|reference|ref no|txn|transaction|gstin|hsn|sac)\b` (i) → `it looks like a label from the bill, not a place`
7. `letters` = count of Unicode letters (`\p{L}`), `digits` = count of `\d`. letters 0 → `it has no letters, so it is not a place name`; `digits >= 4 && digits > letters` → `it is mostly numbers (like a PNR or booking number)`.
8. `length > 80` or more than 12 whitespace-separated words → `it is too long to be a place name`.
`samePlaceBothEnds(from, to)` (line 35): both non-blank after trim and `norm2(from) === norm2(to)` with `norm2 = lower().replace(/[^a-z0-9]+/g,' ').trim()`.
(Python: `\b` semantics with ASCII words match JS; `₹` check is a plain char class. For `\p{L}` use `str.isalpha()` per char.)

---------------------------------------------------------------------------------------------------

## 8. tourMatcher.ts (already ported in `tour_match.py`; see §15 for differences)

### 8.1 `tourWindow(tour)` (36-41): `start = toIsoDate(tourStartDate)`; none → null; `end = toIsoDate(tantetiveEndDate) || start + max(0, (totalDays || 1) - 1) days`. No clamping (end may precede start).
### 8.2 `cluesFrom(state)` (49-62): dates = toIsoDate of every transport row's dep & arr date (only truthy raw values; blank results dropped) + (if includeAccommodation) every stay's check-in/out; places = norm of every truthy source/destination (+ stay locations if includeAccommodation), empties dropped. Blank rows contribute nothing.
### 8.3 `matchTours(state, tours)` (66-86) → `{ranked, confident, hasClues}`
Per tour: `window`; `toDestination = places.length === 0 || places.some(q => samePlace(norm(tour.destination), q))`; `inside` = count of dates with `start <= d <= end` or (`toDestination` and `d === start-1` or `d === end+1`) (0 without window); `dateFit = dates.length && window ? inside/dates.length : 0`; `routeHits` = how many of [norm(source), norm(destination)] samePlace any clue place; result `{tour, dateFit: (dates.length && window) ? dateFit : null, score: dateFit*2 + routeHits}` (score range 0..4). Stable sort by score descending.
`confident = best && best.score >= 2 && (!second || best.score - second.score >= 1) ? best : null`. `hasClues = dates.length > 0 || places.length > 0`.
### 8.4 `findTourByText(text, tours)` (89-99): first `\b(\d{2,})\b` → tour with `String(tourId) === id` → return it. Else `t = norm(text)`; `t.length < 3` → null; tours whose norm(source) or norm(destination) samePlace `t`; exactly one → it, else null.
### 8.5 `judge(probe, tours, picked?)` (138-153)
`{ranked, hasClues} = matchTours(probe, picked ? [picked] : tours)`; `!hasClues || ranked empty` → neutral. `datesRuleOut = ranked.every(m => m.dateFit === 0)` (a `null` dateFit is not 0 → no rule-out). `best.score === 0 || datesRuleOut` → drop, with `belongsTo` = (only when `picked`) the first of `matchTours(probe, tours without picked).ranked` with `(dateFit ?? 0) > 0 || score >= 2` (else undefined). `picked` → assign picked. `second && best.score - second.score < 0.5` → neutral. Else assign best.tour.
### 8.6 `triageRows(mapped, tours, picked?)` (160-190) → `{groups: [{tour, journeys, stays}] (first-seen order), neutral: {journeys, stays}, dropped: [{label, files, belongsTo?}]}`
Each transport row judged alone (`{...initialClaimState(), transportationRows:[row]}`); each stay judged alone with `includeAccommodation: true` (its transport row list is the blank initial row, which contributes nothing). Drop labels:
- journey: `ticket ${source || '?'} → ${destination || '?'}${DateOfTravelSource ? ` on ${shortDay(DateOfTravelSource)}` : ''}` (shortDay = `DD Mon YYYY` en-IN, '' if unparsable)
- stay: `hotel stay in ${location || '?'}${days ? `, ${days}` : ''}` with `days = [shortDay(in), shortDay(out)].filter(Boolean).join(' – ')`
- `files` = `fileKey` (`name|size`) of the row's uploadedFiles / billFiles.

---------------------------------------------------------------------------------------------------

## 9. savedClaim.ts

### 9.1 Readers (lines 34-76)
- `field(o, name)`: case-insensitive key lookup on a plain object (first key in insertion order whose lower-case equals).
- `list(v)`: string → JSON.parse (errors → []); array → itself; else [].
- `str(v) = v == null ? '' : String(v)`; `num(v) = String(Number(v) || 0)` (`'500'`→`'500'`, `null`→`'0'`, `'abc'`→`'0'`, `12.50`→`'12.5'`).
- `day(v) = toIsoDate(str(v)) || str(v)` (keeps the raw text if unparsable); `time(v) = toTime(str(v)) || str(v)`.
- `refsOf(v)` = list(v) mapped to `{fileId: str(fileId), name: str(originalFileName ?? displayName ?? fileId), size: fileSize == null ? undefined : Number(fileSize), rowIndex: Number(rowIndex ?? 0), tempFilePath: tempFilePath ? str(tempFilePath) : undefined}`.
- `savedFiles(refs, rowIndex)` = refs with that rowIndex → `{fileId, name, size: size ?? 0, isRetained: true, tempFilePath, type: '', uploadDate: Date(0)}` (refs with empty fileId are kept, with fileId '').

### 9.2 `claimStateFromDraft(draft)` (79-160)
`data = field(draft,'draftData') ?? draft`. `journeys = list(field(data,'TransportaionDetailDtos') ?? field(data,'TransportationDetailDtos'))` (note `??`: an empty string on the first key does NOT fall back); stays from `AccomodationDetailDto ?? AccommodationDetailDto`; leaves from `LeaveAvailedDto`; travel refs `transportationFileReferences`, stay refs `accomodationFileReferences` (only this spelling).
Transport row idx = `{...newTransportRow(), id: 'transport-draft-${idx}', DateOfTravelSource: day(DateOfTravelSource), TimeOfTravelSource: time(...), source: str(Source), ModeOfTravel: str(ModeOfTravel ?? departureModeofTravel ?? 'select'), DateOfTravelDestination: day(...), TimeOfTravelDestiNation: time(...), destination: str(Destination), distanceKm: num(DistanceINKm), invoiceNo: str(InvoiceNumber), invoiceDate: InvoiceDate ? Date(InvoiceDate) : undefined, travelCost: num(BilledAmount), claimedAmount: num(ClaimedAmount), sgst/cgst/igst: isNA ? '0' : num(SGSTAmount/CGSTAmount/IGSTAmount), BillSGSTAmount/BillCGSTAmount/BillIGSTAmount: isNA ? '0' : num(...), taxStructure, includeTravelCosts: Number(BilledAmount ?? 0) > 0, remarks: str(Remark), isPaidUsingCard: !!isPaidUsingCard, uploadedFiles: savedFiles(travelRefs, idx)}` where `isNA = TaxStructure === 'NA'` and `taxStructure = isNA ? 'NA' : (TaxStructure === 'IGST' || Number(IGSTAmount ?? 0) > 0 ? 'IGST' : 'SGST_CGST')`. (totalBilledAmount stays `'0'`.)
Stay row idx = `{...newAccommodationRow(), id: 'stay-draft-${idx}', checkInDate: day(DateOfCheckIn), checkInTime: time(TimeOfCheckIn), checkOutDate: day(DateOfCheckOut), checkOutTime: time(TimeOfCheckOut), location: str(Location), classOfCity: str(ClassOfCity ?? 'Select'), hotelName: str(HotelName), invoiceNo: str(InvoiceNumber), billed: num(BilledAmount), claimed: num(ClaimedAmount), taxStructure: isNA ? 'NA' : TaxStructure === 'IGST' ? 'IGST' : 'SGST_CGST', selectedType, isPaidUsingCard: !!isPaidUsingCard, billFiles: savedFiles(stayRefs, idx)}` with `selectedType = IsOwnArrangement ? 'own_arrangement' : IsFreeAccomodation ? 'company_free' : isCompanyLodginPaid ? 'company_paid' : 'hotel'` (truthy tests; the ownArrangement/freeProvidedByCompany/companyPaid flags are NOT updated — they stay 'No').
Leave row idx = `{...newLeaveRow(), id: 'leave-draft-${idx}', startDate: day(LeaveStartDate), endDate: day(LeaveEndDate), daDeductable: DADeductable ? 'Yes' : 'No', amount: num(Amount)}`.
Result: `initialClaimState()` with each section replaced when non-empty; `includeAccommodation = stays.length > 0` (even if all stay rows are blank); `includeLeave = any leave item with truthy LeaveStartDate or LeaveEndDate`.

### 9.3 `compareWithSaved(saved, incoming)` (165-180)
`savedJourneys = set(journeyKey(non-empty saved transport rows))`; `savedStays = set(stayKey(non-empty saved stays))` only if `saved.includeAccommodation`. Returns `{added: {rows not in sets}, alreadySaved: {rows in sets}}` (incoming order; duplicates within incoming are NOT collapsed here).
### 9.4 `rowsCount(r)` = journeys + stays.
### 9.5 `addToSaved(saved, added)` (189-200): `journeys` = non-empty saved transport; `stays` = includeAccommodation ? non-empty saved stays : []; `allJourneys = journeys + journeysInDateOrder(added.transport)`; `allStays = stays + staysInDateOrder(added.stays)`; replace sections when non-empty; `includeAccommodation = allStays non-empty || saved.includeAccommodation`.
### 9.6 `billDays(rows)` = sorted `coveredDays({...initialClaimState(), ...rows, includeAccommodation: rows.accommodationRows.length > 0})` (leave never counted).
### 9.7 `savedClaimText({claim, saved, added, alreadySaved, merged, tour})` (216-232) — lines joined by `\n`
1. `savedDays = sorted coveredDays(saved)`: non-empty → `📁 Your saved claim for ${claim} already has ${groupDays(savedDays)}.`; else `📁 You have a saved claim for ${claim}, with no journeys or stays in it yet.`
2. if rowsCount(added) > 0: `newDays = billDays(added) − savedDays` → non-empty `These bills add ${groupDays(newDays)}.` else `These bills add ${rowsText(added)} on days the saved claim already has.`
3. if rowsCount(alreadySaved) > 0 (n): `${rowsText(alreadySaved)} (${groupDays(billDays(alreadySaved))}) ${n===1 ? 'is' : 'are'} already in your saved claim, so I didn't add ${n===1 ? 'it' : 'them'} again.`
4. `missing = uncoveredDays(merged, tour)`: non-empty → `Still no bills for ${groupDays(missing)}.`; else if `coveredDays(merged).size > 0` → `Every day of the tour is now covered.`
`rowsText(rows)` = `[N journey(s), M stay(s)]` (each only if > 0) joined `' and '`.

### 9.8 `savedDraftSummary(tour, saved, opts = {})` (264-291) → SavedDraftSummary
`journeys` = non-empty saved transport; `stays` = (includeAccommodation) non-empty stays; `days = billDays({journeys, stays})`; `review = reviewClaim({state: saved, tour, leaveAnswered: true})` (no rules, no form problems).
`KINDS = [oddPlace, noJourneys, noOutbound, noReturn, routeBreak, overlap, uncoveredDays, missingField, nightsWithoutStay, conveyanceBefore, conveyanceAfter, conveyanceTransfer, localConveyance, ticketMissing, hotelBillMissing]` (excludes badDates, stayNotOnRoute, stayOutsideTravel, amountMissing, formOnly, askLeave).
Fields: `tourId`; `route = ${tour.source || '?'} → ${tour.destination || '?'}`; `tourDates = opts.tourDates ?? ''`; `journeys = ${source||'?'} → ${destination||'?'}${dayOf(dep) ? `, ${dayOf(dep)}` : ''}`; `stays = ${location||'?'}${dayOf(in) ? `, ${dayOf(in)}${dayOf(out) ? ` – ${dayOf(out)}` : ''}` : ''}`; `daysCovered = days.length ? groupDays(days) : ''`; `stillMissing = facts of review items with kind in KINDS`; `missing = missingInShort(those items) || 'Nothing is missing.'`; `lastSaved = opts.lastSaved ? (dayOf(opts.lastSaved) || undefined) : undefined`; `savedInChat = !!opts.savedInChat`. `dayOf` = en-IN `DD Mon YYYY` ('' if unparsable).
`draftForModel(summary)` = summary without `stillMissing`.

### 9.9 `savedDraftsGreetingNote(summaries, waitingWithoutDraft)` (297-313) — concatenation, verbatim:
```
'The chat just opened on the Raise Claim page. Greet the employee by name and introduce yourself as Aryaan, their Claim Assistant. '
+ `Then tell them they already have ${n === 1 ? 'a saved draft' : `${n} saved drafts`} (claims saved but not submitted) for ${n === 1 ? 'this tour' : 'these tours'}, `
+ `name each tour with its route and dates and say briefly what its draft already holds (savedDrafts in the data). `
+ 'For what each draft still lacks, say that draft\'s own "missing" sentence from the data once, exactly as written and word for word, and never a sentence from another draft. Put no label or heading before it, do not shorten, reword or explain it, and add nothing else about what is missing. '
+ (fromChat === n ? 'All of them were saved in an earlier conversation with you: say so ("from our previous conversation"). '
   : fromChat > 0 ? 'Say which of them were saved in an earlier conversation with you (savedInChat); the others were saved from the claim form. '
   : 'They were saved from the claim form, not in a chat with you: do not say they came from a conversation with you. ')
+ `Then say they can upload more tickets and bills with the 📎 button (they are added to the right draft), continue one of these claims with the buttons, `
+ (w > 0 ? `or start on one of the other ${w} approved tour${w === 1 ? '' : 's'} waiting for a claim. ` : '')
+ 'End by asking what they would like to do.'
```
(`fromChat` = count with savedInChat; n = summaries.length. With n=0, fromChat===n → "All of them…" branch.)

---------------------------------------------------------------------------------------------------

## 10. billSummary.ts

- `FileFate` kinds (19-30): `saved{tour}`, `alreadySaved{tour}`, `saveFailed{tour}`, `problem{reason}`, `unsure`, `noTour`, `nothingFound`, `unreadable`, `duplicate`, `rejected{reason}`.
- `RANK` (33) = `['saved','alreadySaved','saveFailed','problem','unsure','noTour','nothingFound','unreadable','duplicate','rejected']`; `strongerFate(a, b)` = `!a || rank(b) < rank(a) ? b : a` (ties keep `a`).
- `dayText`/`rangeText` — billSummary variants, §0.8.
- `MODE_WORD` (58-66): air `Flight`, rail `Train`, public-transport `Cab or bus`, personal-vehicle `Own vehicle`, official-entitled/beyond/dedicated `Office vehicle`; other → `Journey`.
- `describeRow(row)` (69-76): if the object HAS a key `checkInDate` or `location` (key presence, even undefined) → `Hotel stay in ${place(location)}${days ? `, **${days}**` : ''}` with `days = rangeText(checkInDate, checkOutDate)`; else `${MODE_WORD[mode ?? ''] ?? 'Journey'}, ${place(source)} → ${place(destination)}${day ? `, **${day}**` : ''}` with `day = dayText(DateOfTravelSource)`. (place = billSummary variant, §0.5.)
- `findingsByFile(rows)` (81-92): Map file name → distinct describeRow texts, in row order, for every name in `uploadedFiles` then `billFiles`.
- `fileLine(name, found, fate, {tagSaved, inClaim})` (97-124): `what = found.length > 3 ? found[0..3].join('; ') + '; and ' + (len-3) + ' more' : found.join('; ')`; `lead(t) = • **${name}**: ${t}`:
  - unreadable → `⚠️ couldn't be read`; nothingFound → `no journey or hotel stay found`; duplicate → `same file as one already uploaded, skipped`; rejected → `**not uploaded**: ${reason}`;
  - saved → `${what}${tagSaved ? ` → **${tour}**` : ''}`;
  - alreadySaved → `${what} (${inClaim ? 'already in this claim, not added again' : `already in your ${tour} draft`})`;
  - saveFailed → `${what} (**${inClaim ? 'could not be added' : 'not saved yet'}**)`;
  - problem → `${what || 'hotel bill'} (**not added**: ${reason})`;
  - unsure → `${what} (**couldn't tell which tour it's for**)`;
  - noTour → `${what} (**${inClaim ? "doesn't belong to this claim, not added" : "doesn't match any of your approved tours"}**)`;
  - undefined/default → `${what || 'no journey or hotel stay found'}`.
- `GROUP_TITLE` (150): new `✅ New`, duplicate `↩ Duplicate`, notRelevant `🚫 Not relevant`; order new, duplicate, notRelevant.
- `groupOf(fate, found)` (153-166): saved/saveFailed → new; alreadySaved/duplicate → duplicate; undefined → found non-empty ? new : notRelevant; others → notRelevant.
- `groupedLines(files, fates, found, opts)`: for each group in order: names (upload order) in that group; skip empty; blank line `''` between groups; heading `**${title} (${count})**`; then fileLine per name.
- `uploadSortedText(files, fates)` (185-192): lines with empty `found`, opts `{tagSaved:false, inClaim:false}`; `anyNew` = some file in group new; text = `['Here is how I sorted your upload:', '', ...lines, ...(anyNew ? [] : ['', 'Nothing new to read, so nothing was added.'])].join('\n')`, then collapse `\n{3,}` → `\n\n`, trim.
- `readSummaryText(s)` (195-207): `found = findingsByFile(s.rows)`, `tagSaved = savedTours.length > 1`, lines with `inClaim = !!s.claim`.
  - With `s.claim`: `[header, '', ...lines, ...(notes ?? []), '', claimFooter(claim)]`.
  - Else footer: 1 tour → `✅ Saved to your draft for **${t}**. Nothing is submitted.`; >1 → `✅ Saved to your drafts for **${n} tours**. Nothing is submitted.`; any fate saveFailed → `⚠️ I couldn't save some of them just now. I'll try again the next time you open the assistant.`; no saved tours and no fate saveFailed/alreadySaved → `⚠️ **Nothing was saved.**`. Text `[header, '', ...lines, ...notes, '', ...footer]`.
  - join `\n`, collapse 3+ newlines to 2, trim.
- `claimFooter({journeys, stays, saved})` (212-216): parts `N journey(s)`, `M stay(s)` (when > 0); none → `Nothing new was added to this claim.`; else `✅ Added **${parts.join('** and **')}** to this claim.` + (saved ? ` The draft is saved.` : ` ⚠️ The draft could not be saved yet; it is saved again when you leave the form.`).
- `readHeader(count, {earlier?, name?})` (219-228): `hi = name !== undefined ? `Hi${name ? ` ${name}` : ''}, ` : ''`. earlier: text `the bill you uploaded earlier has been read:` / `the ${count} bills you uploaded earlier have been read:`; else `your bill has been read:` / `your ${count} bills have been read:`. With hi → `hi + text`; else capitalise first letter.

## 11. billOutcome.ts
- `KnownBills = {read: set, staged: set}` (tests also pass a `saved` map, ignored).
- `uploadVerdict(hash, known, inUpload)` (lines 18-22): hash falsy → `{kind:'new'}`; in read/staged/inUpload → `{kind:'duplicate'}`; else new.
- `readOutcome(bills, added, failed, alreadySaved=[], empty=[], inClaim=[])` (29-49) — precedence per bill name: added > alreadySaved > inClaim > failed > empty(→`noDetails`) > `notBelonging`. Returns `{added, alreadySaved, inClaim, failed, noDetails, notBelonging}` each filtered from `bills` in order.

## 12. intents.ts
- `MAX_MESSAGE_CHARS = 1000`; `MAX_FILE_BYTES = 20*1024*1024`; `MAX_FILES_PER_READ = 20` (lines 6-10).
- `cleanMessage(raw)` (13-16): remove chars U+0000–U+0008, U+000B, U+000C, U+000E–U+001F, U+007F, U+200B–U+200F, U+202A–U+202E, U+2060, U+FEFF; then collapse `\s+` → single space; trim. (Tab, LF, CR are kept until the whitespace collapse.)
- `checkFiles(files)` (33-48): for each file, `name = f.name || 'This file'`; first matching reason:
  1. name ends `.heic`/`.heif` (i) or type contains heic/heif (i) → `HEIC photos can't be read. Please send it as a JPG or PNG, or a PDF.`
  2. name ends `.doc(x)`, `.xls(x)`, `.ppt(x)`, `.txt`, `.csv`, `.zip`, `.rar` (i) → `I can only read PDFs and photos or scans (JPG, PNG), not this kind of file.`
  3. not readable (name not ending `.pdf|.jpg|.jpeg|.png` (i), or type not in `'' | application/pdf | image/jpeg | image/png`) → `I can only read PDFs and JPG or PNG images.`
  4. size 0 → `the file is empty. Please attach it again.`
  5. size > 20 MiB → `it is ${mb(size)}, which is too large (the limit is 20 MB). Please send a smaller copy.` with `mb(b) = Math.round(b/1024/1024) + ' MB'` (JS round = half up).
  Returns `{accepted, problems: ['${name}: ${reason}'], rejected: [{name, reason}]}`. No count limit here.

## 13. fileHash.ts, keptReports.ts, draftHandoff.ts, loadAssistantTour.ts
- `hashFile(blob)`: lowercase hex SHA-256 of the bytes (64 chars); `null` if hashing unavailable or reading fails (→ treated as new).
- keptReports: `KeptReport = {id, scope ('page' | 'tour-<id>'), text, tone?: 'error'|'success', tourIds: int[], at: ms}`; `REPORT_KEEP_MS = 14 days`; `reportsKey(empId) = 'aryaan.billReports.v1.' + (empId ?? 'anon')`.
  `readReports(store, key, now)`: JSON parse of stored list (missing → '[]'); not an array / parse error / store throws → `[]`; keeps entries with string `text`, string `scope`, and `now - Number(at) < KEEP`.
  `keepReport(store, key, report, now)`: `id = '${now}-' + 6 random base36 chars`; writes `readReports(...) + [{...report, id, at: now}]` (expired ones pruned by the read); returns id; never throws.
  `dropReports(store, key, ids, now)`: rewrites list without those ids; empty list → `removeItem(key)`; never throws.
  `reportsFor(store, key, scope, now)`: readReports filtered by scope, oldest first (storage order).
- draftHandoff: a single in-memory slot. `setAssistantDraft({tourId, state})` overwrites; `takeAssistantDraft(tourId)` returns and clears it only if `String(pending.tourId) === String(tourId)`, else null (slot kept).
- loadAssistantTour `fetchAssistantTour(tourId)`: GET `/Claim/RaiseClaimRequest/{tourId}` and GET `/TourPlan/GetTourDetails/{tourId}` (failure → null) in parallel. `claimData = payload.data`; `details = claimData.empTourDetails ?? claimData.employeeDetails`; `plans = plan.data.tentativeTourPlans` if non-empty else `claimData.tentativeTourPlans || claimData.tentativeTourPlanList || []`. Returns `tour = {tourId, source: details.source, destination: details.destination, startDate: claimData.trDetails?.tentativeStartDate ?? details.tentativeStartDate, endDate: plans[-1]?.date, purpose: details.purposeOfTour}`, `rules = tourRulesFrom(claimData)` (NB: rules use the claim request's plans, not the TourPlan ones).

---------------------------------------------------------------------------------------------------

## 14. Test catalogue (`D:\CETPA\Tour_React\tests\claimChatbot\`) — port each as a pytest

Shared fixtures used below:
- claimAssistant.test: `rules = {allowAir:false, minDate:'2026-10-05', maxDate:'2026-10-11'}`; `journey(over)` = newTransportRow + Noida→Pune, 2026-10-05 10:30 → 2026-10-05 16:30, rail; `claim()` = initial state with `[journey()]`.
- tourReview.test: `tour = {tourId:4521, source:'Noida', destination:'Mumbai', tourStartDate:'2026-10-05', tantetiveEndDate:'2026-10-09', totalDays:5, statusId:2}`; `rules = {allowAir:true, minDate:'2026-10-05', maxDate:'2026-10-10'}`; `j(from,to,dep,depT,arr,arrT,mode='air',extra)` has `uploadedFiles:[file]`, `claimedAmount:'4500'`; `s(city,in,inT,out,outT,extra)` = hotel, hotelName 'Hotel Sea View', invoiceNo 'INV-1', classOfCity 'X', billFiles [file], claimed/billed '5000'; `outbound()` Noida→Mumbai 10-05 10:30→13:00 air; `back()` Mumbai→Noida 10-09 18:00→20:10 air; `hotel()` Mumbai 10-05 14:00 → 10-09 12:00; `cab(...)` public-transport with claimedAmount '450'; `mumbaiCabs()` = [Noida→Noida Airport 10-05 08:00-09:30, Mumbai Airport→Mumbai Hotel 10-05 13:20-14:00, Mumbai Hotel→Mumbai Office 10-07 09:00-09:40, Mumbai Hotel→Mumbai Airport 10-09 16:00-17:00, Noida Airport→Noida 10-09 20:30-21:30]; `claim(journeys, stays, leave?)` with includeAccommodation = stays non-empty; `kinds(c, opts)` = kinds of reviewClaim with leaveAnswered default true.

### billDuplicates.test.ts
1. `hashFile`: same bytes under different names → equal 64-hex digests; different bytes → different.
2. `uploadVerdict`: hash in read / staged / current upload → duplicate.
3. `uploadVerdict`: new hash → new; `null` hash → new.
4. `readOutcome(['a','b','c','d'], ['a'], ['d'])` → `{added:['a'], alreadySaved:[], inClaim:[], failed:['d'], noDetails:[], notBelonging:['b','c']}`.
5. `readOutcome(['again','saved'], [], [], ['saved'], [], ['again'])` → alreadySaved ['saved'], inClaim ['again'], rest empty.

### billSummary.test.ts
1. describeRow: train → `Train, New Delhi → Jaipur, **18 Jan 2026**`; stay → `Hotel stay in Jaipur, **18–20 Jan 2026**`.
2. rangeText: `('2026-01-30','2026-02-02')` → `30 Jan – 2 Feb 2026`; `('2025-12-30','2026-01-02')` → `30 Dec 2025 – 2 Jan 2026`; same day → `18 Jan 2026`.
3. readHeader: `(3, {earlier:true, name:'Anil'})` → `Hi Anil, the 3 bills you uploaded earlier have been read:`; `(1, {})` → `Your bill has been read:`.
4. strongerFate: saved beats noTour; saved kept against unreadable (identity).
5. readSummaryText exact text: header, '', `**✅ New (2)**`, ticket line, hotel line, '', `**🚫 Not relevant (1)**`, `• **blur.jpg**: ⚠️ couldn't be read`, '', `✅ Saved to your draft for **Delhi → Jaipur (16–20 Jan 2026)**. Nothing is submitted.`
6. groupOf table: saved/saveFailed → new; alreadySaved/duplicate → duplicate; noTour/nothingFound/unreadable/unsure/problem/rejected → notRelevant; undefined+found → new; undefined+[] → notRelevant.
7. readSummaryText groups: New (1) then Duplicate (1) (`same file as one already uploaded, skipped`) then Not relevant (2) with `doesn't match any of your approved tours**)` and `**not uploaded**: I can only read PDFs…`; group order asserted.
8. A group with no files is not shown (no "Duplicate"/"Not relevant").
9. uploadSortedText exact: `Here is how I sorted your upload:`, '', `**↩ Duplicate (1)**`, `• **same.pdf**: same file as one already uploaded, skipped`, '', `**🚫 Not relevant (1)**`, `• **blank.pdf**: **not uploaded**: the file is empty. Please attach it again.`, '', `Nothing new to read, so nothing was added.`
10. Several tours: line ends `→ **Delhi → Jaipur**`; footer `Saved to your drafts for **2 tours**`; a noTour-only upload says `doesn't match any of your approved tours` and `Nothing was saved.`
11. In a claim (positive): ticket line has no tour tag (line ends after the date); footer `Added **1 journey** and **1 stay** to this claim. The draft is saved.`
12. In a claim: `already in this claim, not added again`, `doesn't belong to this claim, not added`, `couldn't be read`, `not added**: the check-out date is before the check-in date`, footer `Nothing new was added to this claim.`
13. In a claim (negative): for each fate saved/alreadySaved/noTour/unreadable/nothingFound/problem/duplicate the text never matches `/missing|you should|please upload|do you have|approved tours|draft for/i`.
14. claimFooter `{journeys:2, stays:0, saved:false}` matches `Added **2 journeys**…could not be saved yet`.

### claimAssistant.test.ts
1. applyEdits fills the blank row: row-0 edits from/to/departureDate/departureTime `10:30 am`/mode `train` → no rejections, 1 row, values `['Noida','Pune','2026-10-05','10:30','rail']`.
2. applyEdits adds a stay (row 0) location/check-in/out/type company_free → includeAccommodation true, selectedType company_free, no rejections.
3. Remove: 2 journeys, remove 1 → 1 row (Goa destination remains); remove 1 again → 1 blank row (source '').
4. Rejections (each alone on `claim()` with rules; applied empty; rejected text matches; transport row 0 unchanged): `departureDate 2026-02-30` → couldn't use; `2026-11-20` / arrival `2025-01-01` → outside your approved tour; `departureTime 25:99` → couldn't use; `mode air` (allowAir false) → no air travel; `mode teleport` → couldn't use; `distanceKm -5` and `far` → couldn't use; `from '   '` → couldn't use; `t(9,'from','Delhi')` → `There is no journey 9`; `a(0,'checkInDate','2026-12-25')` → outside your approved tour; `a(0,'checkInTime','99:00')` → couldn't use; `l(0,'startDate','2026-12-25')` → outside your tour; `l(0,'startDate','not a date')` → couldn't use; unknown section → `Couldn't apply`; `t(1,'unknownField')` → couldn't use.
5. `[t(5,'remove','yes'), t(1,'remove','maybe')]` → rejected contains `no journey 5 to remove`; still 1 row; input JSON unchanged.
6. mergeOcrRows: `[later(10-08 Pune→Noida), journey()]` on claim() → addedJourneys 1, duplicates 1, destinations `['Pune','Noida']`; merging `later` again → added 0, duplicates 1.
7. mergeOcrRows with empty mapped → all counts 0, 1 row.
8. findMissingFields: (tautological first assert); journey with mode 'select' and empty departure time → sorted slots `['departure','mode']`; complete claim → `[]`.
9. findClaimIssues: arrives-before-departs, outside approved tour (journey on 2026-12-01), stay check-out before check-in; `findClaimIssues(claim(), rules)` → `[]`.
10. stayBillProblem with travel 10-05 and 10-08: (10-05→10-08) null; (10-08→10-06) `before the check-in`; (12-01→12-03) `outside your tour dates`; (10-05→10-20, rules {allowAir:true}) `travel dates`; `{}` → null.
11. buildProgress: no tour → not ready; with tour {Noida→Pune, 2026-10-05, 7 days} and stays/leave answered → ready, heads empty; initial state with tour → not ready.
12. Several new journeys rows 1 and 2 on an initial state (1 blank row): 2 rows with source; row 2 source 'Mumbai'; then `t(9,'arrivalTime','17:00')` → row count unchanged, exactly 1 rejection.
13. Locked journeys: state with journey 1 Noida→Mumbai air (allowAir false in rules! — the first applyEdits uses `rules` with allowAir false, so 'air' is rejected there; the journey keeps mode 'select'); then edits `t(1,'departureTime')` + row-0 new journey Noida→Noida Airport taxi with `lockedJourneys:[1]`, allowAir true → row 0 departure time still `''`, row 1 destination `Noida Airport`, first rejection matches `Journey 1 (Noida → Mumbai) was not changed`.

### claimLink.test.ts (src/lib/claimLink — outside this spec's files, listed for completeness)
`claimLink({tourId:4522,statusId:3,totalDays:6}, base)` → `${base}/raise-claim-approver?tourId=4522&isDraft=true&statusId=3&totalDays=6`; null status/days omitted; `stateFromClaimLink` parses back; empty / no tourId / `abc` / `-4` → null; missing isDraft → `isDraft:false`.

### dropButtonLines.test.ts, isAcknowledgement.test.ts (aiAssistant.ts — not in this spec's scope; spec A covers them)
- dropButtonLines removes `[Button]` lines, bulleted button labels and "Tap the button below…" lines; keeps fact lines. cleanReply strips an echoed `QUESTION: …` label.
- isAcknowledgement true for `ok, Okay!, thanks, Thank you so much, yes, hmm, theek hai, ok ji, Hello Aryaan`; false for questions/requests.

### formattedText.test.ts (UI component; not domain) — parseBlocks/plainText.

### intents.test.ts
1. cleanMessage: `'  hello\u200B   world\u0007\n'` → `hello world`; `'\u0000\u0001'` → `''`.
2. checkFiles: pdf + jpg accepted; [notes.docx, photo.heic, empty.pdf, 50 MB pdf, 'mystery' (no ext, type ''), data.zip] → 0 accepted, 6 problems & rejected; `rejected[0] = {name:'notes.docx', reason:'I can only read PDFs and photos or scans (JPG, PNG), not this kind of file.'}`; `problems[0] = 'notes.docx: I can only read …'`; `rejected[4].name = 'mystery'`; 20 PDFs all accepted.

### keptReports.test.ts
1. Two reports in scopes 'page' and 'tour-4600' are returned per scope with their tourIds.
2. After dropReports of the only id, scope empty and the storage key is removed; other scope never sees it.
3. Another employee key (`reportsKey(2002)`) sees nothing.
4. Expired (`t0 + KEEP + 1`) → []; corrupt JSON → []; throwing store → [] and keep doesn't throw; `null` store doesn't throw.

### ocrReferences.test.ts / ocrResults.test.ts (src/lib/ocr — not in scope except mapOcrToRows)
- `mapOcrToRows` on combined results (out.pdf, hotel.pdf, back.pdf): transport rows' files `[[out],[back]]`, stay files `[[stay]]`, failedFiles `[]`, unmatchedFiles `[]`.
- (combineOcrResults/uniquelyNamed/claim refs belong to the OCR client spec.)

### parsers.test.ts
toIsoDate `''` for `2026-02-30`, `31/02/2026`, `2026-13-01`, `00/00/2026`, `garbage`, `''`; `2026-10-05` unchanged; toTime `''` for `24:00`, `10:60`, `abc`, `''`; `9.05 PM` → `21:05`.

### placeCheck.test.ts
1. null for: Noida, New Delhi, Mumbai CST, Hazrat Nizamuddin (NZM), Indira Gandhi International Airport, New Delhi, Terminal 3, IGI Airport, T2 Chhatrapati Shivaji Maharaj International Airport, Sector 62, Noida, Netaji Subhas Chandra Bose International Airport, Billimora Junction, Bengaluru (SBC), Pune, DFCCIL Office, Pragati Maidan, Hotel Sea View, Juhu, Ahmedabad Jn, Kanpur Central.
2. Empty / spaces / undefined → null.
3. Labels (Total Net Amount, Grand Total, Net Payable, Base Fare, GST, CGST 9%, Invoice No, PNR, Booking ID, Passenger Details, Convenience Fee, Taxes and Charges, Seat 14B, Paid via UPI) → matches `label from the bill|amount|mostly numbers`.
4. `₹1,240.00`, `Rs 450`, `1240.50` → amount; `05/10/2026`, `2026-10-05`, `10:30 AM` → date or time; `8812345670` → no letters|mostly numbers; `PN 88123456` → mostly numbers; `care@irctc.co.in`, `www.makemytrip.com` → email or web.
5. N/A, NA, null, undefined, Unknown, -, ?, ..., Select → placeholder; `###` → no letters; long sentence → too long; `X` → placeholder|too short.
6. samePlaceBothEnds: Mumbai/mumbai true; New Delhi/New-Delhi true; Noida/Mumbai false; ''/Mumbai false.

### savedClaim.test.ts (tour 7: Noida→Pune 2026-09-10..12; draft with saved journeys 10 Sep and 12 Sep, dates `…T00:00:00`, times `09:00:00`, file refs f-10 (row 0), f-12 (row 1))
1. claimStateFromDraft: 2 rows; row0 date `2026-09-10`, time `09:00`; uploadedFiles `[[f-10,true,'temp-uploads/draft-1/a.pdf']],[[f-12,true,'…/b.pdf']]`.
2. compareWithSaved with bills 11 Sep (new) and 10 Sep (copy) → added [11 Sep], alreadySaved [10 Sep]; addToSaved order `[10,12,11]`, fileIds `[f-10,f-12,null]`; journeysInDateOrder `[10,11,12]`; uncoveredDays `[]`; savedClaimText matches `already has ${groupDays([10,12])}`, `These bills add ${groupDays([11])}`, `/1 journey .* is already in your saved claim/`, `Every day of the tour is now covered`.
3. savedClaimText with nothing added → `Still no bills for ${groupDays(['2026-09-11'])}`.
4. mergeOcrRows: saved rows with fileIds keep places → `[10,12,11]`; same rows without files → date order `[10,11,12]`.
5. mergeOcrRows: a saved journey in another date/time format is a duplicate (added 0, duplicates 1).
6. readOutcome(['new','saved','other'], ['new'], [], ['saved']) → notBelonging ['other'].
7. Stored `[10,12,11]` vs shown `[10,11,12]`.
8. inStoredOrder(shown, storedIds) gives stored ids; fileIds per row preserved; keptFileIds `['f-10','f-12']`.
9. applyEdits on the shown order, `row 2 arrivalTime 20:00` (default rules) → stored row 3 (11 Sep) has 20:00; saved rows keep `18:00`.
10. A chat-added journey (id `transport-chat-new`, 9 Sep) goes after stored ids; removing the 11 Sep row gives `[10,12]`; `inStoredOrder(shown, [])` keeps view order.
11. tripEnds: first dep 2026-09-10, last arr 2026-09-12; `tripEnds([])` → both undefined.

### savedDraftsGreeting.test.ts (tour 4521 Noida→Mumbai 2026-10-05..09)
1. One-way draft + Mumbai stay 10-05..10-09, savedInChat, lastSaved `2026-10-27T10:00:00` → tourId 4521, route `Noida → Mumbai`, journeys `['Noida → Mumbai, 05 Oct 2026']`, stays `['Mumbai, 05 Oct 2026 – 09 Oct 2026']`, daysCovered matches `05 Oct 2026 – 09 Oct 2026`, a stillMissing fact matches `/no journey back/i`, savedInChat true, lastSaved `27 Oct 2026`.
2. Complete draft with cabs → stillMissing (excluding `/hotel bill/i`) empty; `missing` matches `^Missing Hotel bill for stay 1` or equals `Nothing is missing.` (the stays here have no billFiles → hotelBillMissing); savedInChat false. Empty draft → journeys [], daysCovered '', a stillMissing fact matches `/no journeys/i`.
3. Greeting note, one chat-saved draft, 2 waiting → contains `a saved draft`, `from our previous conversation`, `📎`, `other 2 approved tours`, `asking what they would like to do`.
4. Two form-saved drafts, 0 waiting → `2 saved drafts`, `do not say they came from a conversation with you`, no `other N approved`.
5. Mixed → `Say which of them were saved in an earlier conversation with you`.
6. Round trip + stay, no cabs → a fact matches `getting to the airport in Noida` and one matches `local travel (cab, taxi, auto) in Mumbai`.

### staysOnly.test.ts (tours: 1 Noida→Delhi 2026-09-10..12, 2 Noida→Jaipur 2026-01-18..20; six hotel bills, one read as unknown with no entry)
1. mapOcrToRows → 0 transport, 5 stays, unmatchedFiles `['ACC2_10SEP2026_12SEP2026.jpeg']`, every stay has exactly 1 bill file.
2. triageRows → tour 1 gets ACC3_10SEP… and ACC_10SEP…, tour 2 gets ACC2_18JAN… and ACC_18JAN…; dropped files `[['ACC_06NOV2025_07NOV2025.jpeg|1']]`.
3. attachUnmatched with the tour-1 stays → attached `['ACC2_10SEP2026_12SEP2026.jpeg']`, first stay has 2 files; with no rows → attached [].
4. readOutcome with empty → noDetails `['empty.jpeg']`, notBelonging `['nov.jpeg']`.

### timeOverlap.test.ts (Ambala trip: air 07:38→10:15; cab 10:15→11:33; cab 17:00→19:00; air 17:15→19:50, all 2026-08-24)
1. journeyOverlaps → exactly 1: earlier = row 3 (cab 17:00-19:00), later = row 4.
2. First two rows (arrive 10:15, leave 10:15) → none; a row without arrival time is not judged.
3. A long journey (08-24 08:00 → 08-25 08:00) overlapping two later ones → 2 pairs.
4. findTimeOverlaps → 1 line matching `journey 3 and journey 4 overlap`, `departs at 17:15`, `arrives at 19:00`, `correct .* in the claim`; findClaimIssues contains an overlap line.
5. reviewClaim (tour null, leaveAnswered) → 1 overlap item, blocking, row 4; missingInShort matches `journeys 3 and 4 (they overlap)`.

### tourMatch.test.ts (tour 5: New Delhi, Delhi → Pune, 2026-01-16..17)
1. Ticket New Delhi (DEL) → Jaipur (JAI) on 01-18 → no groups, 1 dropped.
2. Out the night before (→ Pune 01-15) and back the morning after (Pune → New Delhi 01-18) → group tour 5.
3. Pune → Mumbai on 01-17 (inside dates) → group tour 5.
4. Hotel in Jaipur 01-18..20 → no groups.

### tourReview.test.ts
1. Complete tour (outbound, back, 5 cabs, hotel) → `[]`.
2. Multi-city complete tour (Mumbai 2 nights, Pune 2 nights, 8 cabs) → `[]`.
3. Round trip from Delhi (nearby station) → no noOutbound/noReturn.
4. Return to `Noida, Uttar Pradesh` still home → `[]`.
5. No journeys → items[0] noJourneys, blocking.
6. One-way → noReturn question matches `There are no return journey tickets/bills uploaded (Mumbai → Noida). Do you have them?`, not blocking, yes upload; no uncoveredDays, no stayOutsideTravel.
7. First journey from Delhi → noOutbound question `onward journey ticket from Noida to Delhi`.
8. Outbound + Pune→Noida → routeBreak `between Mumbai and Pune`.
9. Overlap (return leaving 12:00 before 13:00 arrival) → blocking overlap.
10. Missing departure time on journey 1 → exactly one missingField, blocking, `What time did you leave for journey 1`.
11. Hotel without name and invoice → missingField ids `['missing:accommodation:1:hotelName','missing:accommodation:1:invoiceNo']`.
12. Bad dates: journey arriving 09:00 before 10:30 departure, journey on 10-20, stay out 10-04 before in 10-05 → 4 badDates (journey backwards, journey outside, stay backwards, stay outside [10-04 < minDate]), all blocking.
13. Stay only 10-05..10-06 → uncoveredDays question contains `07 Oct 2026 – 08 Oct 2026`.
14. Stay 10-05..10-07 → one nightsWithoutStay with `nights of 07 Oct 2026 – 08 Oct 2026 in Mumbai`.
15. Overnight trains (tour end 10-07) → no nights item; leave on 10-05 with return on 10-06 (tour end 10-06) → no nights item.
16. Same-day trip (tour end 10-05) → no nights item.
17. Goa stay → stayNotOnRoute with `no: 'remove'`; stay starting 10-05 when travel starts 10-08 → stayOutsideTravel.
18. Ticketed journey without file → ticketMissing; personal-vehicle or billNotRequired → none.
19. Hotel without bill → hotelBillMissing; own_arrangement / company_free → none.
20. claimedAmount '0' with file → amountMissing with resolutions `['form','confirm']`.
21. formProblems overEntitlement + field → only `['formOnly']`, blocking, question matches `entitled limit`.
22. Leave asked when not answered; not asked when a leave row exists.
23. Order: single air journey without departure time & files, leaveAnswered false → first `noReturn`, missingField before ticketMissing, last `askLeave`.
24. Ids are stable across calls; adding back() removes noReturn.
25. Review loop: items asked in order (noReturn first); confirm removes non-blocking; a missingField cannot be confirmed away; `later` defers it; confirming everything else ends `blocked` with blocking[0] missingField.
26. Confirming all → done; `nextReviewStep([], ∅, {'form:overEntitlement:accommodation:1'})` → done.
27. No cabs → kinds `['conveyanceBefore','conveyanceAfter','conveyanceBefore','conveyanceAfter','localConveyance']`; questions: `How did you get from home to the airport in Noida for your flight on 05 Oct 2026? If you took a cab, taxi or auto, do you have the bill?`; `After your flight reached Mumbai on 05 Oct 2026, how did you get to your hotel?`; `from your hotel to the airport in Mumbai`; `how did you get to home?`; `While you were in Mumbai (05 Oct 2026 – 09 Oct 2026), did you take any cabs, taxis or autos to get around`; all non-blocking, yes upload, no confirm.
28. Each of the 5 cabs answers exactly one question (dropping cab k brings back `[before, after, local, before, after][k]`).
29. A cab typed in at the end of the list still counts where its time puts it (no conveyanceAfter-home, no routeBreak/noReturn).
30. Office car (official-entitled, no files) counts as conveyance; no ticketMissing for row 3.
31. A cab without bill → ticketMissing question contains `Noida → Noida Airport`.
32. A cab the evening before (10-04 18:00) does not count → conveyanceBefore `from home` still asked.
33. Air Noida→Mumbai 06:00-08:00, rail Mumbai CST→Pune 11:00-14:00, air Pune→Noida 20:00-22:00 → 2 transfers; first `from the airport in Mumbai to the railway station in Mumbai CST for your next train`; no conveyanceAfter row 1, no conveyanceBefore row 2.
34. Tight same-airport connection (Mumbai 08:00 → 09:30 flight) → no transfer for row 2; a cab between flight and train answers the transfer.
35. Same-day trip → no localConveyance; taxi-only tour → no conveyance items at all.
36. One-way → noReturn; conveyance kinds `['conveyanceBefore','conveyanceAfter']`.
37. Overnight train arriving 10-06 08:00 → `After your train reached Mumbai on 06 Oct 2026, how did you get to your hotel?`
38. short(one-way) = `Missing Return journey ticket, cabs/taxis and other conveyance bills for both onward and return journeys.`
39. short(round trip, no cabs) = `Missing Cabs/taxis and other conveyance bills for both onward and return journeys, cab/taxi bills for local travel in Mumbai.`
40. Only cabs 3-5 → `Missing Cabs/taxis and other conveyance bills for the onward journey.`; only cabs 1-3 → `… for the return journey.`
41. One-way with onward cabs → `Missing Return journey ticket, cabs/taxis and other conveyance bills for the return journey.`
42. Complete tour → `''`.
43. Transfer tour → contains `cab/taxi bills for going between stations and airports`; gaps tour → `hotel stay for the nights of 07 Oct 2026 – 08 Oct 2026 in Mumbai`, `ticket or bill file for journey 1 (Noida → Mumbai)`, `hotel bill for stay 1 (Mumbai)`, `1 detail (dates, times or places) to fill in`; never `both ends|airport cabs|\bleg\b`.
44. Destination `Total Net Amount` → items[0] oddPlace with question `Journey 1 has “Total Net Amount” as its destination, which doesn't look like a place (it looks like a label from the bill, not a place). Where did journey 1 end?`, not blocking, `newJourney` undefined; missingInShort starts `Missing A place name to correct (misread from the bill)`.
45. Source `₹1,240.00` → `starting point, which doesn't look like a place (it looks like an amount from the bill). Where did journey 1 start from?`; destination Noida → `goes from Noida to Noida, the same place`; stay city `GSTIN 07AAACD1234F1Z5` → `Stay 1 has “GSTIN 07AAACD1234F1Z5” as its city`.
46. Real station/airport names → no oddPlace.

(Excluded per instructions: withinAssistant.test.ts, aiAssistant.test.ts.)

---------------------------------------------------------------------------------------------------

## 15. Differences found against the existing Python port
(`D:\CETPA\TourClaim_AI_Agent\src\tourclaim_agent\drafting\*.py`; "TS" = Tour_React.)

### 15.1 Not ported at all
- No Python equivalent of: `findMissingFields`, `applyEdits`/`readClaimEdits`/`CLAIM_EDIT_FORMAT`, `snapshot`, `describeMissing`, `summarize*`, `findAmountIssues`/`findFormOnlyIssues`, `buildProgress`/`progressText`, `findClaimIssues`/`findTimeOverlaps`/`findRelationIssues`, `journeyOverlaps`/`tripEnds`/`inStoredOrder`, `reviewClaim`/`nextReviewStep`/`settle`/notes/`missingInShort`, `placeProblem`/`samePlaceBothEnds`, `savedDraftSummary`/`savedDraftsGreetingNote`, `matchTours.confident`, `findTourByText`, billSummary texts, `readOutcome`, `checkFiles`/`cleanMessage`, `tourRulesFrom`.
- `analysis.py` is NOT a port of `tourReview.ts`: different kinds (EMPTY, MISSING_DATE, MISSING_PLACE, BEFORE_TOUR…), different wording, different severities (e.g. outside-tour dates are WARNING in Python but blocking `badDates` in TS), no oddPlace/noOutbound/overlap/uncovered days/nights/conveyance/askLeave, NO_RETURN ignores the TS closed-loop rule, BROKEN_CHAIN runs in stored order over all modes (TS: time-sorted, air/rail legs only when any exist), ticket expectation by keyword list (TS: `TICKETED` set {air, rail, public-transport, official-beyond}), hotel-bill check excludes own/free only (TS: requires bill only for hotel/company_paid), and it adds checks TS doesn't have (tax mismatch, duplicate invoice, claimed>billed as blocking).

### 15.2 Date / time parsing (affects everything)
- **Slash dates**: TS `toIsoDate('05/10/2026')` = **2026-10-05** (day first, always). Python `parse_date` tries `%m/%d/%Y` before `%d/%m/%Y` → **2026-05-10**. Dash/dot dates agree (day first).
- TS accepts 2-digit years (`05/10/26` → 2026); Python does not.
- TS accepts free text with a year (`5th October 2026`, `Oct 5, 2026`, `Sept 5 2026`, `Mon, 05 Oct 2026`); Python only `%d %b %Y`/`%d %B %Y` (and `%b` in Python wants `Sep`, not `Sept`).
- TS refuses impossible days in ISO/numeric forms; Python strptime also refuses — same. But TS's V8 branch rolls `31 Feb 2026` to 3 Mar; Python returns None.
- TS keeps the raw text of an unparsable saved date/time (`day(v) = toIsoDate(v) || v`, `time(v) = toTime(v) || v`); Python stores `None`. This changes journeyKey equality and `blank()` behaviour (a row with an unparsable date is "not empty" in TS).
- **Time**: Python `_time` (ocr_mapping.py:59) does not understand am/pm (`9.05 PM` → None; TS 21:05), `noon`/`midnight`, `1030`, `10 AM`, `10:30 hrs`; Python accepts a bare `"10"` → `10:00` (TS → `''`); Python truncates input to 8 chars before parsing.
- `snapshot`'s "Today" is the UTC date in TS.

### 15.3 Date wording
- TS en-IN dates are `05 Oct 2026` with **`Sept`** for September; Python `day_text` uses `%d %b %Y` → `Sep`.
- TS ranges (claimProgress `rangeText`/`groupDays`): `07 Oct 2026 – 08 Oct 2026` (full dates, spaced en dash). Python `span_text`/`days_text` (bill_pipeline.py:72-91): `07–08 Oct 2026`, `07 Oct–08 Nov 2026` (no spaces) — a third format matching neither TS variant (billSummary's is `7–8 Oct 2026` / `30 Jan – 2 Feb 2026`, unpadded days).

### 15.4 tour_match.py vs tourMatcher.ts
- `tour_window`: Python clamps `end = max(start, end)`; TS does not (end < start gives an empty window list). Python parses tour dates with `parse_date` (month-first slashes), TS with `toIsoDate`.
- `judge` "dates rule out": TS `ranked.every(m => m.dateFit === 0)` — any tour with `dateFit null` (no window) prevents the rule-out. Python ignores no-window tours (`windowed` subset) → can drop a bill TS would keep neutral/assigned.
- `belongsTo`/`looks_like`: TS sets it only when a tour is already picked, from the *other* tours, with `dateFit > 0 || score >= 2`; Python always sets it (first ranked with date_fit > 0, including when no tour is picked, and ignores the `score >= 2` alternative).
- TS supports `picked` (judge against the confirmed tour only, assign anything not ruled out); Python has no picked mode (it always judges against all tours, `claim_tour_id` only receives neutral rows).
- Neutral rows: TS returns them as `neutral` for the caller; Python assigns them (single group / claim tour / only tour) or marks them unsure — extra policy not in tourMatcher.ts.
- Drop labels differ: TS `ticket A → B on 05 Oct 2026` / `hotel stay in X, 05 Oct 2026 – 07 Oct 2026`; Python `journey_found` = `ticket A → B, 05 Oct 2026`, `stay_found` = `hotel stay in X, 05–07 Oct 2026` (and falls back to hotel name).
- `matchTours.confident` and `findTourByText` are missing in Python.
- TS `cluesFrom` reads stay clues only when `includeAccommodation`; Python always reads StayRow clues (equivalent for triage, since TS sets it true).

### 15.5 merge.py vs mergeOcrRows / compareWithSaved / addToSaved
- **Key normalisation**: TS `journeyKey`/`stayKey` only trim + lower-case (punctuation kept: `New Delhi (NDLS)` ≠ `New Delhi NDLS`); Python uses `norm()` (punctuation → space). Python therefore treats more rows as duplicates.
- **Who counts as already there**: TS dedupes against every non-empty row in the claim (incl. rows typed in chat and earlier bills) and also within the same OCR batch; Python `_held` only against saved rows (non-placeholder) or rows holding a bill; rows typed by the employee or plan rows are not duplicates.
- **Ordering**: TS re-sorts ALL rows by date when no row holds a saved file, otherwise keeps every existing row in place and appends only the new ones (date-sorted). Python always puts `Origin.SAVED` rows first and re-sorts all non-saved rows (so earlier chat/bill rows move even when saved files exist).
- **Sort key**: TS `stampOf(depDate, depTime) || stampOf(arrDate, arrTime)` (falls back to arrival; rows with no date last, stable); Python `(depart_date is None, depart_date, depart_time)` (no arrival fallback). Stays: TS check-in stamp; Python same idea.
- **Plan-leg filling** (`_score`, `is_placeholder`, "filled" result, amount only on the first leg): Python-only behaviour; the TS outside-jurisdiction assistant starts from one blank row, never from plan legs, and never fills rows from bills.
- `merge.same_place` (shared word ≥ 3 letters) ≠ TS `samePlace` (substring containment of normalised strings): e.g. "New Delhi" vs "Delhi Cantt" → Python True, TS False; "Navi Mumbai" vs "Mumbai" → both True; "Pune Jn" vs "Pune" → TS True (contains), Python True.
- TS `mergeOcrRows` sets `includeAccommodation = true` when stays exist and ignores stays already in the claim when `includeAccommodation` is false; Python has no includeAccommodation flag.

### 15.6 saved_draft.py vs claimStateFromDraft
- Mode: TS `ModeOfTravel ?? departureModeofTravel ?? 'select'`; Python reads only `ModeOfTravel` (no `departureModeofTravel` fallback; blank → None instead of `''`/'select').
- Journeys list key: TS uses `??` (an empty-string `TransportaionDetailDtos` does not fall back); Python uses `or` (falls back on any falsy, e.g. `[]`/`""`).
- Stay file refs: TS reads only `accomodationFileReferences`; Python also accepts `accommodationFileReferences` (superset, fine).
- File refs: TS keeps refs whose fileId is empty (fileId ''); Python skips them. TS `rowIndex = Number(rowIndex ?? 0)` (NaN never matches); Python skips non-int rowIndex. TS `size = Number(fileSize)`; Python only int/float sizes. TS keeps `tempFilePath`; Python's `SavedFile` has no tempFilePath.
- Stay type: TS derives `company_paid` from `isCompanyLodginPaid`; Python has no company_paid (only own_arrangement/free_accommodation bools).
- Leave rows: TS builds leave rows (`LeaveAvailedDto`, `DADeductable`, `Amount`) and `includeLeave`; Python keeps them only in `raw_draft` (used by `covered_days`).
- Amount strings: TS `num()` → `String(Number(v)||0)` (`'500'`, `'12.5'`); Python `Decimal` quantised to 2 places. taxStructure: TS IGST when `TaxStructure==='IGST'` or `IGSTAmount > 0` (same in Python), but for stays TS ignores IGSTAmount (only `TaxStructure`), Python applies the IGSTAmount rule to stays too.
- `includeAccommodation`: TS true whenever the draft has stay items; Python has no flag (stays always considered).

### 15.7 bill_pipeline.py `stay_problem` vs `stayBillProblem`
- Tour bounds: TS uses `rules.minDate` (earliest of tentative start and plan dates, **no** −1 day) and `rules.maxDate` (last plan date **+1**); with no rules, no tour check at all. Python uses `ws.tour_start − 1 day` and `(tour_end or tour_start) + 1 day` → Python accepts a check-in one day before the tour that TS rejects, and its bounds come from the tour window instead of the plan dates.
- Message dates: TS `its dates (01 Dec 2026 to 03 Dec 2026) are outside your tour dates (05 Oct 2026 – 11 Oct 2026)` (each date in full, joined " to "; tour text = rangeText(minDate, maxDate) incl. the +1 day); Python `its dates (01–03 Dec 2026) are outside your tour dates (05–10 Oct 2026)` via `span_text` and without the +1 day. Same for "don't fit your travel dates (…)".
- Journeys considered: TS uses whatever journey list the caller passes (in the app: the claim's journeys); Python only "travelled" rows (saved or bill-backed), ignoring plan/employee rows.

### 15.8 bill_pipeline.py `covered_days` / `coverage_text` vs `coveredDays` / `savedClaimText`
- covered_days: TS counts every non-empty transport row (incl. chat-typed), stays only when includeAccommodation, leave rows only when includeLeave (from state); Python counts only saved/bill-backed rows and leave from `raw_draft`. TS caps spans at 120 days; Python has no cap.
- coverage_text wording/format differs from savedClaimText: TS `📁 Your saved claim for {claim} already has {days}.` (Python: `Your saved claim already had {days}.`, no 📁, no claim name, past tense); TS line for an empty saved claim (`📁 You have a saved claim for …, with no journeys or stays in it yet.`) is missing in Python; TS `These bills add {N journeys and M stays} on days the saved claim already has.` and the "already in your saved claim, so I didn't add it/them again." line are missing in Python; TS joins lines with `\n`, Python with a space; TS "Every day…" requires covered days in the merged claim and uses `uncoveredDays` (returns [] when nothing is covered), Python requires `window and now`.

### 15.9 ocr_mapping.py vs mapOcrToRows
- Mode: TS normalises with `mapTravelMode` to one of the 7 codes (or 'select'); Python keeps the raw OCR text (≤ 40 chars).
- Empty rows: TS drops a main journey without from/to/start/end date and legs without from/to/departure/arrival date; Python keeps the main journey if from/to/startDate is set (ignores endDate) and keeps **every** leg dict (even empty). TS drops stay entries without location and dates; Python keeps all entries.
- Amounts: TS transport `claimedAmount = totalClaimedAmount ?? claimedAmount`, `travelCost = totalBilledAmount = billedAmount`; stays `claimed = claimedAmountWithTax ?? claimedAmount`, `billed = billedAmount`, `tax = cgst+sgst+igst`, `Claimed*GSTAmount` from cgst/sgst/igst, `Bill*GSTAmount` from billCGST/... Python: `billed = billedAmount or claimedAmountWithTax or totalClaimedAmount`, `claimed = billed`, bill GST falls back to cgst/sgst/igst.
- Tax: TS `NOT_APPLICABLE`→NA, `IGST`→IGST, anything else → SGST_CGST; Python returns None for unknown and also accepts `NA`, `N_A`, `NONE`, `CGST_SGST`.
- Accommodation type: TS maps `accommodationType` via `accommodationTypeFields` (own / free / company paid / hotel); Python ignores it.
- `classOfCity`: TS default `'Select'`; Python None (≤ 10 chars).
- Failure: TS failedFiles = per-file `success:false`; Python marks the whole response unreadable only if no per-file result succeeded, and returns "nothing readable found" when no rows.
- Python truncates places to 60 chars, hotel names to 100; TS does not.
- TS `unmatchedFiles` / `attachUnmatched` (bills read but tied to no row go to the first row of their kind) have no Python counterpart (Python reports `UNUSED_BILL` instead).

### 15.10 Minor
- TS `norm` and Python `norm` are identical. TS `samePlace` (tourMatcher) and Python `tour_match.same_place` agree; but Python code elsewhere uses `merge.same_place` (word overlap) where TS uses `samePlace` (see 15.5).
- TS `addDay`/`eachDay` use local-time JS Dates; Python `date` arithmetic is equivalent (no DST in IST).
