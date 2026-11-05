# Expense Eligibility Rules — Transport, Accommodation, Advances, Recovery, Overlap

**Summary.** The backend stores transport and accommodation lines exactly as submitted by the client (`TransportaionDetailDtos`, `AccomodationDetailDto` JSON strings). It does **not** enforce entitlement by grade, travel class, city-class hotel caps, per-km rates, claimed ≤ billed, GST percentages or invoice requirements — those are **frontend-only** (or absent). Backend-enforced rules are: tour/DA date-overlap, duplicate-submission guards, edit/supplementary eligibility by status, approval reconciliation and caps, and the full **TourRecovery** upload validation. **Sponsored tours: not found anywhere** (backend or frontend).

Paths: backend relative to `Tour_API/`; frontend relative to `Tour_React/src/`, labelled **(frontend-only, not enforced by backend)**.

---

## 1. Transportation

Trace: `ClaimController.cs:34` → `ClaimRepository.RaiseClaimRequest` `ClaimRepository.cs:1796-1895` → `DAL/Models/TransportationDetail.cs`.

| # | Rule | Where enforced | Inputs | Effect | Message |
|---|---|---|---|---|---|
| T1 | Fields stored verbatim: dates/times, Source, Destination, `ModeOfTravel` (free string), Billed/Claimed, SGST/CGST/IGST (claimed & billed), `TaxStructure`, `DistanceINKm`, `PresonalVehicalType`, `InvoiceNumber/Date`, `TourNumber`, `Remark` | Backend `ClaimRepository.cs:1808-1838` (no validation) | JSON | store | — |
| T2 | Modes (within jurisdiction): Rail, Public Transport, Public Transport Beyond Entitled, Company Provided Vehicle, Personal Vehicle | `components/WithinJuridiction/TourForm.tsx:231-237` **(frontend-only)** | | UI | — |
| T3 | Modes (outside jurisdiction): Air (only if an approved tentative plan contained Air), Rail, Personal Vehicle, Public Transport, Official Entitled / Beyond / Dedicated vehicle | `pages/RaiseClaim/sections/TransportationSection.tsx:501-504, 1097-1127` **(frontend-only)** | tentative plan modes | UI filter | — |
| T4 | "Public Transport Beyond Entitled" needs CGM approval | `TourForm.tsx:756-761` (banner); approval routing in `pages/tour/TentativeTourPlan.tsx:472-545` **(frontend-only)** | mode, grade | warn / route | "This claim requires CGM approval" |
| T5 | Personal vehicle requires vehicle type (Two/Four Wheeler) and distance > 0 before "End Tour" | `TourForm.tsx:371-373, 410-430, 438-449` **(frontend-only)** | | block UI | "End Tour is disabled until the personal-vehicle details are entered" |
| T6 | Per-km / mileage rate for personal vehicle or taxi | **Not found** (backend or frontend). `DistanceINKm` stored only | — | — | — |
| T7 | Class-of-travel entitlement by grade (AC class, air class) | **Not found in backend code.** Frontend only uses grade to choose the *approver* (E3/E5/E8 thresholds in `TentativeTourPlan.tsx:312-339, 520-541`) | — | — | — |
| T8 | Claimed ≤ travel cost (billed base) | `pages/RaiseClaim/sections/TransportationAmountSection.tsx:292, 309, 332, 359` **(frontend-only)** | | cap | "Claimed amount cannot exceed travel cost (₹…)" |
| T9 | GST: structure ∈ {IGST, SGST_CGST, NA}; IGST ≤ 18 %, CGST/SGST ≤ 9 % each of base | `TransportationAmountSection.tsx:98, 113, 257-262, 572-574` **(frontend-only)**; backend stores `TaxStructure` string unchecked | | cap | — |
| T10 | Journey legs must not overlap each other; arrival after departure | `TransportationSection.tsx:660-670` **(frontend-only)** | | block | "Please fix the journey times marked in red: they overlap with another journey" |
| T11 | Journey date within allowed window | `TransportationSection.tsx:720` **(frontend-only)** | | block | "Date must be between … and …" |
| T12 | Mode classification used server-side only for tour-request mapping (Road/Car/Bus, Rail/Train, Air/Flight substring) | Backend `ClaimRepository.cs:3013-3050` (RaiseClaimWithRequest) | `ModeOfTravel` | sets `IsRoad/IsRail/IsAir` | — |

## 2. Accommodation / hotel

Trace: `ClaimRepository.cs:1901-1999` → `DAL/Models/AccomodationDetail.cs`.

| # | Rule | Where enforced | Inputs | Effect | Message |
|---|---|---|---|---|---|
| A1 | Stored verbatim: check-in/out date+time, HotelName, HotelCity, `ClassOfCity`, `IsFreeAccomodation`, `isCompanyLodginPaid`, `IsOwnArrangement`, Billed/Claimed, GST, invoice | Backend `ClaimRepository.cs:1912-1943` (no validation) | | store | — |
| A2 | **Bug:** claimed CGST/SGST are swapped when saved (`CGSTAmount = item.SGSTAmount; SGSTAmount = item.CGSTAmount`) | Backend `ClaimRepository.cs:1933-1934, 3498-3499`; `ClaimClarificationRepository.cs:1489-1490, 1525-1526` | | data defect | — |
| A3 | Accommodation types: Own Arrangement, Hotel, Company Lodging – Free, Company Lodging – Paid | `components/WithinJuridiction/Accommodation.tsx:21, 71-96` **(frontend-only)** | | UI | — |
| A4 | City class from city name via static list; unknown city → `Z` | `lib/utils.ts:105-114` (`constant/cityList.ts`) **(frontend-only)** | city | compute | — |
| A5 | Hotel entitlement/day = `GradeDetail.X|Y|Z` for city class; total = Σ per night (min 1 day), with **promotion-date split** (date < promotionDate → previous grade's X/Y/Z) | Within `Accommodation.tsx:1011-1085`; outside `pages/RaiseClaim/sections/AccommodationSection.tsx:2041-2088` **(frontend-only)** | grade, city class, dates, promotionDate | compute | Banner "Promoted Grade Entitlement Effective" (`Accommodation.tsx:1509-1520`) |
| A6 | Billed capped at entitlement (within); claimed capped at entitlement (outside) | `Accommodation.tsx:1300-1306`; `AccommodationSection.tsx:744-760, 810-812, 888-889` **(frontend-only)** | | cap | "Billed Amount capped at Entitled Limit (₹…)" / "Claimed Amount cannot exceed Entitled Limit (₹…)" |
| A7 | Claimed ≤ billed | `Accommodation.tsx:1310-1313, 1427-1431`; `AccommodationSection.tsx:916` **(frontend-only)** | | cap / block | "Claimed Amount cannot exceed Billed Amount (₹…)" |
| A8 | GST caps IGST 18 %, CGST/SGST 9 % | `Accommodation.tsx:1317-1330`; `AccommodationSection.tsx:943-1012` **(frontend-only)** | | cap | "IGST cannot exceed 18% of Billed Amount (₹…)" |
| A9 | **Own arrangement = 25 % of entitled hotel amount** (billed = claimed = 0.25 × A5 total, no GST) | `Accommodation.tsx:1180-1197`; `AccommodationSection.tsx:2130, 2233, 2404` **(frontend-only)** | | compute | "25% of entitled amount" |
| A10 | `OwnArrangment.OwnArrangmentAmount` (single global value) | Backend CRUD only: `GET api/TourPlan/GetOwnArrangmentAmount` / `PUT UpdateOwnArrangmentAmount/{id}/{amount}` (`TourPlanController.cs:143-150` → `EmpTourPlanRepository.cs:2328-2365`). Fetched by `RaiseClaim.tsx:122` but **not used in any calculation (inferred)** | | reference | "Record not found." |
| A11 | Invoice form required only for Hotel and Company Lodging – Paid | `Accommodation.tsx:214` **(frontend-only)** | type | UI | — |
| A12 | Free company lodging reduces DA to 25 % | see `da_calculation_rules.md` D9 **(frontend-only)** | | | |
| A13 | Hotel name required for Hotel; city class required when location set | `Accommodation.tsx:377, 1538-1544`; `AccommodationSection.tsx:2094-2105` **(frontend-only)** | | block | missing-field list |
| A14 | Upload size ≤ 1 MB per file (within accommodation) | `Accommodation.tsx:1458` **(frontend-only)** | | block | "File … exceeds the 1MB limit and was not added." |
| A15 | Check-in/out vs tour dates, max nights | **Not found in backend** | | | |

## 3. Tour date overlap & date windows

| # | Rule | Where enforced | Inputs | Effect | Message |
|---|---|---|---|---|---|
| O1 | New tour request must not overlap another active tour (its DA windows if claim raised; daily ranges for Daily tours; else request dates). Ignored if latest status Rejected / TourNotAvailed / RejectedByFinance | **Backend** `Touroverlapvalidator.cs:69-88, 202-294`; called `EmpTourPlanRepository.cs:351, 358` (create), `:1153` (edit), `:1974` (re-approval) | EmpId, start/end | reject | `TourConflict.Message`: "These dates overlap another tour request. Conflicting Tour ID {id}: {from} to {to}." or "…overlap DA already claimed in another tour…" (`Touroverlapvalidator.cs:24-25, 356-358`) |
| O2 | Daily tours and claim-with-request must supply `DailyTourDateValidation` ranges; each range overlap-checked and stored | **Backend** `EmpTourPlanRepository.cs:338-355, 435-454`; entity `DAL/Models/DailyTourDateValidation.cs` | ranges | reject (400) | "DailyTourDateValidation Is Required For This Case" |
| O3 | Request end at 00:00 means end of that day; zero window → full start day | **Backend** `Touroverlapvalidator.cs:304-313` | | normalise | — |
| O4 | DA double-claim checks | see `da_calculation_rules.md` D17-D18 | | reject | |
| O5 | Legacy `ValidateDate` / `ValidateDateReApproval` (with two hard-coded exempt tour IDs) | `EmpTourPlanRepository.cs:1607-1725` — **dead code, not called** | | — | — |
| O6 | Ex-facto/auto tour start date ≥ today − 3 months, extendable back to `UserExtension.TourExtensionTillDate` if earlier; future tours ≤ today + 3 months | `pages/tour/TourRequest.tsx:507-535` **(frontend-only)**. Backend only serves data: `GET api/UserExtension/GetByEmpCodes` (`UserExtensionController.cs:46` → `BAL/Service/UserExtensionRepository.cs:130-185`; no record → `now + DefaultExtensionMonths` (config, fallback 3, `:24, 142-159`), `IsCustomExtension=false`); `POST Seed`, `GET GetAll` | empCode | UI date limit | — |

## 4. Submission integrity, duplicates, documents

| # | Rule | Where enforced | Effect | Message |
|---|---|---|---|---|
| S1 | Same tour claim re-submitted within 30 s | **Backend** `ClaimRepository.cs:1558-1575` | 409 | "Claim request is already being processed. Please wait a moment before trying again." |
| S2 | Transport rows for the tour created in last 5 min | **Backend** `ClaimRepository.cs:1583-1595` | 409 | "Claim details already exist for this tour plan. Duplicate submission prevented." |
| S3 | Duplicate **bill/invoice** detection (same invoice number across claims) | **Not found in backend.** Only the claim chatbot groups uploads as "duplicate" (`lib/claimChatbot/billSummary.ts:141-160`) **(frontend-only)** | — | — |
| S4 | Documents: per-row files for transport/accommodation (index ranges), optional Additional, Accident, AdvancePaymentBill, Nomination files; `DocumentsAttached` flags (approved tour programme, tickets/boarding pass, hotel bills) | Backend stores; **no mandatory-document check** (`ClaimRepository.cs:1653-1764, 1849-1888, 1952-1997`; `ClaimApprovalDto.cs:32-51, 168-172`) | store | — |
| S5 | Incident-related claim stores `ClaimIncident` (date, location, description, type) | Backend `ClaimRepository.cs:1772-1784` | store | — |

## 5. Approval (bill passing) caps

`PUT api/Claim/ApproveRaiseClaimAmount/{tourid}/{empId}/{claimApprovedAmount}/{ApproveTaxRate}` (`ClaimController.cs:251-300`) → `ClaimRepository.cs:3650-4096`.

| # | Rule | Location | Effect | Message |
|---|---|---|---|---|
| P1 | Approved total ≤ `EmpTourPlan.ClaimAmount` | `:3805-3810` | 406 | "Approved Amount Cannot be Grater than Claimed Amount" |
| P2 | No advance: approved = Σtransport + Σhotel + ΣDA − Σleave (2 dp) | `:3912-3918` | 409 | "Claim Cannot Be Approved There is Some Mismatch in Data ." |
| P3 | With advance: approved + advance approved = same sum | `:3920-3926` | 409 | same |
| P4 | Per-line approved amount not capped vs claimed line | `:3815-3907` | — | — |
| P5 | When approved = 0 with advance, stored `ClaimApprovedAmount = advanceApproved − PaidAmount`; existing advance `FinanceDetail` marked `IsLiquidationRequired` | `:3929-3975` | compute | — |
| P6 | Success → status ProcessCompleted ("By pass finance") and merged approval PDF (accommodation docs query bug: re-uses transport list, `:4064`) | `:3995-4078` | — | "Record updated succesfully." |

## 6. Advances

| Rule | Where | Notes |
|---|---|---|
| Advance flag `IsAdvanceRequired`, estimates (travel/DA/local/accommodation) stored at request | Backend `EmpTourPlanRepository.cs:384-402` | no server cap |
| Advance approval stores `ApprovedAmount` as sent | Backend `EmpTourPlanRepository.cs:1570-1590` | "Employee tour advance amount approved"; no limit |
| "75% of estimated expenditure" | `pages/tour/TourRequest.tsx:2180, 2195` — **label only, no calculation** **(frontend-only)** | policy unconfirmed |
| Claim type = TourRequestAdvanceClaim vs TourRequestClaim | Backend `ClaimRepository.cs:2093-2100` | |

## 7. Supplementary claims

`POST api/Claim/RaiseSupplementaryClaim` → `ClaimRepository.cs:6712-7150`.

| Rule | Location | Message |
|---|---|---|
| Only on tours whose latest status is ProcessCompleted or PaymentTransferred | `:6740-6751` | "Supplementary claim can only be raised on a completed tour (ProcessCompleted or PaymentTransferred)." |
| One supplementary per tour | `:6757-6762` | "A supplementary claim has already been raised for this tour. Only one supplementary is allowed per tour." |
| Only tour owner | `:6768-6773` | "You are not authorized to raise supplementary claim on this tour." |
| Recent duplicate (status 20) guard | `:6778-6791` | "Supplementary claim is already being processed…" |
| Finance user for unit must exist (external org API) | `:7063-7082` | "Finance user not found for the employee's unit." |
| DA overlap vs other tours (own-row check skipped) | `:7107-7116` | TourConflict message |
| Approval ≤ `SupplementaryRequestAmount`; sum of supplementary lines must equal approved | `:3667-3793` | "Supplementary Approved Amount Cannot be Greater than Supplementary Requested Amount" / "Supplementary Claim Cannot Be Approved There is Some Mismatch in Data ." — **bug**: rows looked up by `f.Id == _empTourPlan.Id` (`:3690, 3717, 3744`) |

## 8. Edit / clarification eligibility

| Rule | Location | Message |
|---|---|---|
| Edit only own tour; not if `ApprovedDate` or `ClaimApprovedDate` set; not if latest status ∈ {ProcessCompleted, FinanceProcessing, BillPassingProcessing, PaymentTransferred} | `ClaimEditRepository.cs:547-598` (`GET api/ClaimEdit/can-edit/{id}`) | "You can only edit your own tour requests." / "Tour has already been approved and cannot be edited." / "Claim has been approved and cannot be edited." / "Tour is in a final status and cannot be edited." |
| Edit re-runs DA overlap | `ClaimEditRepository.cs:243-250` | TourConflict message |
| Clarification response overwrites lines (history kept) — **no overlap re-check** | `ClaimClarificationRepository.cs:1972-2190` | — |

## 9. Tour recovery (overlap-based money recovery)

`api/TourRecovery/*` (`TourRecoveryController.cs:25-92`) → `TourRecoveryRepository.cs`. Finance uploads rows; backend validates.

| Rule | Location | Message |
|---|---|---|
| Access: hard-coded admin EmpCode or unit Bill-pass / Payment-proc / Super-bill-passing manager (org API) | `:46-57`; controller `:137` | — |
| ≤ 5000 rows per upload; re-upload throttle | `:23, 104-107, 160-163` | "A single upload can have at most 5000 rows…" / "This file was just uploaded…" |
| Row checks: emp code, valid tour IDs (AR-C-n or n), tours differ, reason, amount > 0, components ≥ 0, overlap dates valid & ordered | `:1080-1108` | e.g. "Recovery amount must be greater than 0." |
| Both tours exist, same employee, emp code matches; both **paid in SAP**; recover-from tour has approved amount | `:1119-1141` | "Tour AR-C-{id} claim is not paid in SAP yet. Nothing to recover." |
| Backend re-derives overlap evidence: transport legs clashing in time or matching within 30 min; common DA dates; common hotel nights (per-night = approved / nights) | `:1210-1297` | "No overlap found between … no clashing transport time, no common DA date and no common hotel night." |
| File overlap period must intersect actual overlap | `:1166-1172` | "Overlap period in file … does not match the actual overlap …" |
| Recovery ≤ recover-from tour's approved claim; cumulative recoveries ≤ approved + ₹1 tolerance | `:21, 1177-1178, 1196-1204` | "Recovery … is more than the amount paid in AR-C-…" |
| No duplicate tour pair (in file or DB) | `:1184-1195` | "Recovery for this tour pair already exists: AR-R-…" |
| Detail lines type ∈ {DA, Hotel, Transport} | `:1299-1338` | "Detail line {i}: type '…' must be DA, Hotel or Transport." |
| Release only Pending, still SAP-paid, amount > 0; remark required. Cancel blocked if recovered or already pulled by SAP | `:584-693, 695-795` | "Only Pending recoveries can be released…" / "Already sent to SAP on …" |

## 10. Excessive tours (CGM report — reporting only, not a gate)

`GET/POST api/CGMExcessiveTour/GetExcessiveTourReport` → `CGMExcessiveTourRepository.cs:95-372`.

| Rule | Location |
|---|---|
| Defaults: `MinimumDays = 20`, `MaxGrade = "E2"`, period = current month | `:105-126` |
| Employees with grade strictly below MaxGrade per `GradeHierarchy` (N1…N7 < E0…E9 < DR < CV < MD) | `:24-46, 144-160` |
| Tours by departure date in range, latest status ∈ {Approved, FinanceProcessing, ClarificationProcessing, ProcessCompleted, ClarificationResponsed, PaymentTransferred, PendingApproval} | `:48-58, 184-230` |
| Flag if Σ `NoOfDays` ≥ MinimumDays | `:265, 306-307` |
| Access: unit CGM or admin | `:95-101` ("…Only CGM of unit or Admin can access this data.") |

## 11. Jurisdiction & sponsored tours

| Topic | Finding |
|---|---|
| Within vs outside jurisdiction | Backend only stores `EmpTourPlan.Jurisdiction` (default "Outside Jurisdiction", `DAL/Models/EmpTourPlan.cs:51`); RaiseClaimWithRequest forces "Within Jurisdiction" (`ClaimRepository.cs:2935`). **No backend eligibility differences.** Frontend: different forms (within: no leave section, modes per T2, own-arrangement/free-lodging handling per A3/D9). |
| **Sponsored tours** (sponsor / IsSponsored / SponsoredBy / funded) | **Not found in backend code and not found in frontend.** No field, flag, enum or rule exists. Would need DB/reference data or a new feature. |
| `OrgClaimLimit` (Lavel, Limit, ApplicableDate/Year) | Entity only (`DAL/Models/OrgClaimLimit.cs`), **never queried**. |
| Device reimbursement (Laptop/Tablet/HardDisk amounts in `GradeDetail`) | CRUD only (`EmpTourPlanRepository.cs:1830-1857`); not tour-related. |

---

## Must remain in .NET
- Tour/DA overlap validation with employee lock (`TourOverlapValidator`), duplicate-submission guards.
- Edit / supplementary eligibility by status & ownership; approval reconciliation and caps (P1–P3).
- TourRecovery validation, evidence building, SAP release/cancel workflow.
- Persistence of all lines, files and status transitions.

## Agent should call (APIs)
| Purpose | Endpoint |
|---|---|
| Hotel entitlement (X/Y/Z) + DA rate per grade | `GET api/TourPlan/GetAllGradeData` |
| Own-arrangement global amount (reference) | `GET api/TourPlan/GetOwnArrangmentAmount` |
| Tour extension window | `GET api/UserExtension/GetByEmpCodes?empCodes=` |
| Can a claim be edited | `GET api/ClaimEdit/can-edit/{empTourPlanId}` |
| Tour + tentative plans (allowed modes, e.g. Air) | `GET api/Claim/RaiseClaimRequest/{tourId}`, `GET api/TourPlan/GetTourDetails/{tourId}` |
| Supplementary details | `GET api/Claim/GetSupplementaryClaimDetails/{empTourPlanId}` |
| Recovery status for a tour | `GET api/TourRecovery/GetTourRecoveryStatus/{tourId}` |
| Submit (server runs overlap + duplicate checks) | `POST api/TourPlan/TourRequest`, `POST api/Claim/RaiseClaimRequest`, `POST api/Claim/raise-claim-with-request`, `POST api/Claim/RaiseSupplementaryClaim`, `POST api/ClaimEdit/edit` |

## Gaps (no API to evaluate this rule)
- Hotel entitlement computation (city class × nights × promotion split), own-arrangement 25 %, claimed ≤ billed, GST caps — client-only; no validation endpoint.
- City-class lookup (static frontend `cityList`) — no backend API.
- Travel-mode / class entitlement by grade — not implemented anywhere.
- Per-km / mileage rates — not implemented.
- Duplicate invoice detection across claims — not implemented server-side.
- Mandatory-document checks — not implemented.
- Overlap dry-run (no-submit) — BAL methods exist but not exposed.
- Sponsored-tour eligibility — no data model.

## Unresolved questions
1. Official hotel entitlement source: are `GradeDetail.X/Y/Z` per-night caps? (stored as strings.)
2. Own arrangement: 25 % of entitlement (frontend) vs the global `OwnArrangmentAmount` table — which is policy?
3. Is "Public Transport Beyond Entitled → CGM approval" the only class-of-travel control? Any AC/air class matrix by grade?
4. Personal vehicle reimbursement: is there a per-km rate (two/four wheeler)? Only distance is captured.
5. Should claimed ≤ billed and GST caps be enforced server-side? (Currently bypassable via direct API calls.)
6. Advance limit: is "75 % of estimated expenditure" policy? No calculation exists.
7. Sponsored tours: what defines them and where would the flag live?
8. Confirm the CGST/SGST swap (A2) and supplementary-approval lookup bug before relying on stored values.
