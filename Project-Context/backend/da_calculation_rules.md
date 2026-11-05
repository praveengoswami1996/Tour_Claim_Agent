# Daily Allowance (DA) Calculation Rules — as implemented

**Summary.** The .NET backend does **not compute DA**. DA rows (date, in/out time, % admissible, amount) are calculated in the React client and submitted as a JSON string (`DailyAllowanceDto`); the backend stores them verbatim in `DADetail`. The server-side DA logic is limited to: (1) **overlap validation** of DA time windows (`TourOverlapValidator`), (2) rejecting DA on cancellation claims, and (3) at bill-passing, an **arithmetic reconciliation** that the approved total equals the sum of approved line items. The hour-slab table, base-rate lookup (`GradeDetail.DAAmount`), promotion-date rate split, free-lodging 25 % reduction and leave deduction are all **frontend-only**. `OrgClaimLimit` is mapped in EF but never used.

Paths: backend relative to `Tour_API/`; frontend relative to `Tour_React/src/` and labelled **(frontend-only, not enforced by backend)**.

---

## 1. Where DA is computed — trace

| Step | Location | What happens |
|---|---|---|
| Client builds DA rows | `lib/helperFunction.ts:185` `CalculateDAArray` + `components/WithinJuridiction/DAAllowanceBugFree.tsx:223-287` (within jurisdiction) / `pages/RaiseClaim/sections/DailyAllowanceSection.tsx:256-289` (outside jurisdiction) | Splits tour into calendar-day rows, computes hours, slab %, rate, amount. **(frontend-only)** |
| Payload | `lib/claimPayloadBuilder.ts:339-341` | Sends `DASlab` (= grade DA rate), `TotalHoursMinutes`, `PercentageAdmissible`, `Amount` per row |
| Controller | `TourProgram.API/Controllers/ClaimController.cs:34-38` `POST api/Claim/RaiseClaimRequest` (multipart form); also `:42` `raise-claim-with-request`, `:458` `RaiseSupplementaryClaim`; `ClaimEditController.cs:31` `edit`; `ClaimClarificationController.cs:53` `respond` | — |
| BAL | `TourProgram.BAL/Repository/Concrete/ClaimRepository.cs:2004-2043` (`RaiseClaimRequest`); same pattern at `:3587` (`SaveClaimDataWithoutProcessEntry`), `:6962` (supplementary), `ClaimEditRepository.cs:1312,1331`, `ClaimClarificationRepository.cs:1659` | Deserialises `List<DailyAllowanceDto>`, copies `PercentageAdmissible`→`DAPercentageAdmissible`, `Amount`→`DAAmount`, `IsApplicable`, `TourNumber`. **No recomputation, no range check.** |
| Entity | `TourProgram.DAL/Models/DADetail.cs:18-45` | `DateOfWorkIn/Out` (both = row `Date`, `ClaimRepository.cs:2024-2027`), `TimeOfWorkIn/Out`, `DAPercentageAdmissible`, `DAAmount`, `DAApprovedAmount`, `IsApplicable`, `IsSupplementary`, `FinanceRemark` |
| Not persisted | `TourProgram.Model/Dtos/RaiseClaimDtos/ClaimApprovalDto.cs:150-151` | `DASlab` and `TotalHoursMinutes` are accepted in the DTO but **not stored** (no column in `DADetail`). |

`ClaimTourDetails`/`EmpTourPlan.ClaimAmount` (`ClaimRepository.cs:1767`) is also client-supplied; no server check that it equals the sum of rows at submit time.

---

## 2. Rule table

| # | Rule | Where enforced | Inputs | Effect | Message |
|---|---|---|---|---|---|
| D1 | DA % by hours on a day: 0:00 → 0 %; >0 and ≤ 6:00 → 30 %; >6:00 and ≤ 12:00 → 70 %; >12:00 → 100 % | `lib/helperFunction.ts:36-48` (`getDAPercentageByHours`) and duplicate inside `CalculateDAArray` **(frontend-only)** | hours, minutes on that calendar day | compute | — |
| D2 | Multi-day split: day 1 = start time→**23:59**; middle days = 24 h (100 %); last day = 00:00→end time | `lib/helperFunction.ts:423, 449-471, 475` **(frontend-only)** | first departure, last arrival (date+time) | compute | — |
| D3 | Single-day tour: one row; end < start is treated as next-day (+24 h); multiple segments on one day are merged before summing | `lib/helperFunction.ts:292, 368-420` **(frontend-only)** | | compute | — |
| D4 | Tour span = earliest departure → latest arrival across all legs | `DAAllowanceBugFree.tsx:226-246`; `DailyAllowanceSection.tsx:259-268` (`tripEnds`) **(frontend-only)** | legs / transport rows | compute | — |
| D5 | Base DA rate = `GradeDetail.DAAmount` for the employee's **ReDesignatedGrade** (`entitlement.find(e => e.lavel === reDesignatedGrade)`) | `DAAllowanceBugFree.tsx:82-105`; `DailyAllowanceSection.tsx:100-120`. Data via `GET api/TourPlan/GetAllGradeData` (`features/adminEntitled/AdminSlice.ts:23` → `EmpTourPlanRepository.cs:1785-1795`). **(frontend-only lookup; rate is reference data)** | grade, `GradeDetail` table | compute | — |
| D6 | Default rate 1250 if not supplied | `GradeDetail.cs:19` `[DefaultValue(1250)]`; `helperFunction.ts:190` `gradeWiseDAAmount = 1250` param default | | default | — |
| D7 | Amount per row = rate × % / 100 | `DAAllowanceBugFree.tsx:263`, `:311`; `DailyAllowanceSection.tsx:273` **(frontend-only)** | | compute | — |
| D8 | **Promotion-date rate split**: if row date < `promotionDate` use previous grade's DA rate, else current grade's rate. "Previous grade" = the position grade with the next-lower `pgOrder` in DMS master data | `DAAllowanceBugFree.tsx:88-105, 171-178`; `DailyAllowanceSection.tsx:100-135`. `promotionDate` from org-hierarchy service `Organization/GetEmployeDetailsWithEmpCode/{empCode}` (`features/user/userSlice.ts:83,126`); `positionGrades` from DMS `ModuleManagement/GetAllMasterData` (`features/masterData/masterDataSlice.ts:57`) **(frontend-only, not enforced by backend)** | promotionDate, current grade, grade order | compute; info banner shown if promotion falls inside tour (`DAAllowanceBugFree.tsx:152-162, 406-418`) | "Promoted Grade Entitlement Effective …" |
| D9 | Company-provided **free** lodging on a date → DA for that date reduced to 25 % | Within: amount × 0.25 when an accommodation entry of type `company_provided_lodging_free` covers the date (`DAAllowanceBugFree.tsx:192-207, 264`). Outside: % × 0.25 when `freeProvidedByCompany === 'Yes'` (`helperFunction.ts:260, 381, 404, 429, 455, 481`) **(frontend-only)** | accommodation rows | compute | UI label "25% of DA admissible" (`Accommodation.tsx:88-89`) |
| D10 | Company lodging **paid** → 100 % DA | UI label only `Accommodation.tsx:94-95` **(frontend-only)** | | — | — |
| D11 | One DA row per calendar date (dedupe) | `DAAllowanceBugFree.tsx:180-190` **(frontend-only)** | | compute | — |
| D12 | User may untick a row (`IsApplicable=false`) → excluded from subtotal; backend stores flag and excludes non-applicable rows from overlap checks | Frontend `DAAllowanceBugFree.tsx:317`; backend `Touroverlapvalidator.cs:114, 138, 208, 232` | `IsApplicable` | compute / skip | — |
| D13 | Manual edit of times recomputes slab and amount; manual `daSlab` value is honoured if numeric | `DAAllowanceBugFree.tsx:291-322`; on draft hydration `daSlab` kept if > applicable rate (`:370`) **(frontend-only; backend accepts any value)** | | compute | — |
| D14 | **Leave between tour**: leave rows with `DADeductable=Yes` deduct the sum of that period's DA (100 % of the day's computed DA) | `pages/RaiseClaim/sections/LeaveSection.tsx:133-155` (`computeLeaveAmount`), overlapping leave rows blocked `:263, 278` **(frontend-only)**. Backend only stores `LeaveDetail.IsDADeductable`, `DADeductableAmount` (`ClaimRepository.cs:2048-2077`) | leave dates, DA rows | compute | "This date range overlaps with another leave entry" |
| D15 | Leave subtracted at approval: `approved = Σtransport + Σhotel + ΣDA − Σleave` | **Backend** `ClaimRepository.cs:3888-3925` | approved line amounts | reject on mismatch | "Claim Cannot Be Approved There is Some Mismatch in Data ." (409) |
| D16 | DA not allowed on a cancellation claim | **Backend** `ClaimRepository.cs:2006-2011` | `IsCancelationClaim`, non-empty `DailyAllowanceDto` | reject (400) | "DA Is Not Allowed in CancelationClaim" |
| D17 | DA rows of the same claim must not overlap each other | **Backend** `Touroverlapvalidator.cs:154-173` (skipped for supplementary, `checkOwnRows:false`, `ClaimRepository.cs:7107-7108`) | DA windows | reject (409, rollback) | "DA is claimed more than once for the same date/time in this claim. Conflicting Tour ID {id}: …" |
| D18 | DA window must not overlap any other active tour of the employee (its DA rows if claimed, else its request dates / daily ranges); tours whose latest status is Rejected / TourNotAvailed / RejectedByFinance are ignored | **Backend** `Touroverlapvalidator.cs:175-197, 202-294`; called at `ClaimRepository.cs:2173` (raise), `:2811` (pre-check JSON) & `:2863` (with-request), `:7107` (supplementary), `ClaimEditRepository.cs:243` (edit) | DA windows, other tours | reject (409, rollback) | "DA from {s} to {e} overlaps DA already claimed in another tour." / "… overlaps another tour request." |
| D19 | DA window parsing: end time "24:00", ≥ 23:59, blank or unparsable = end of day; end ≤ start → full day | **Backend** `Touroverlapvalidator.cs:318-345` | time strings | normalise | — |
| D20 | Employee-level lock to serialise concurrent submissions | **Backend** `Touroverlapvalidator.cs:49-60` (`sp_getapplock`, 20 s) | EmpId | reject | "Another request for this employee is in progress. Please retry." |
| D21 | Bill-passer may set any `DAApprovedAmount` per row; no cap vs claimed DA per row | **Backend** `ClaimRepository.cs:3865-3886` | `ApproveDailyAllowanc[]` | store | — |
| D22 | Total approved ≤ total claimed (`EmpTourPlan.ClaimAmount`) | **Backend** `ClaimRepository.cs:3805-3810` | | reject (406) | "Approved Amount Cannot be Grater than Claimed Amount" |
| D23 | With advance: `approved + advanceApproved == Σlines − leave` | **Backend** `ClaimRepository.cs:3920-3926` | `EmpTourPlan.ApprovedAmount` | reject (409) | same mismatch message |
| D24 | Rounding: client rounds to 2 dp in places (`lib/utils.ts:85-88`); backend compares totals with `Math.Round(x,2)` (`ClaimRepository.cs:3805, 3912, 3920`) | mixed | | — | — |

---

## 3. Requested rules NOT implemented in backend

| Requested rule | Backend status | Where it actually lives |
|---|---|---|
| DA hour slabs | **Not found in backend code** | Frontend `lib/helperFunction.ts:36-48` |
| Base DA rate by grade | **Not found in backend logic**; only CRUD of `GradeDetail` (`EmpTourPlanRepository.cs:1540-1860`) | Frontend lookup (D5) |
| City-class (X/Y/Z) DA difference | **Not found** — DA uses a single `DAAmount` per grade; X/Y/Z columns are used for **hotel** entitlement only (frontend) | — |
| `OrgClaimLimit` caps | **Not used anywhere** (only `TourProgramDbContext.cs:28` DbSet) | Possibly DB / future |
| Promotion-date DA change | **Not found in backend code.** `EmployeeGradeHelper` (`DAL/Helpers/EmployeeGradeHelper.cs:12-34`) only *normalises the display string* of ReDesignatedGrade (e.g. reversed "8E"→"E8(Redesignated)"); no effective dates. `VEmployeeMaster` has no promotion date (`DAL/Models/VEmployeeMaster.cs:8-21`) | Frontend + org-hierarchy service (`promotionDate`) + DMS `positionGrades.pgOrder` |
| Grade at date-of-travel | **Not determined server-side.** `EmpTourPlan.Grade` is whatever the client sent at request time (`EmpTourPlanRepository.cs:383`); `ClaimRepository.cs:3007-3011` defaults to `"E1"` when unknown | — |
| Free accommodation / food DA reduction | **Not found** (backend stores `IsFreeAccomodation`, `isCompanyLodginPaid` but never reads them for DA). No food-related rule anywhere | Frontend D9 |
| Leave DA deduction amount | **Not computed** server-side; only subtracted at approval (D15) | Frontend D14 |
| Within- vs outside-jurisdiction DA difference | **Not found in backend.** Only difference is in the client: within uses *amount ×0.25* for free lodging; outside uses *% ×0.25*; outside has Leave section, within has none | Frontend |
| Overnight / journey-time rules beyond D2 | **Not found** | — |
| Server recomputation / rejection of client DA | **None** except overlap (D17-D18) and cancellation (D16) | — |

---

## 4. Worked example (formula only — fully derivable from frontend code; rate R is reference data)

Tour departs D1 18:00, returns D3 10:00, no free lodging, no promotion inside tour, grade rate R (`GradeDetail.DAAmount`).

| Date | Window | Hours | Slab | Amount |
|---|---|---|---|---|
| D1 | 18:00–23:59 | 5:59 | 30 % | 0.30 R |
| D2 | 00:00–23:59 | 24:00 | 100 % | 1.00 R |
| D3 | 00:00–10:00 | 10:00 | 70 % | 0.70 R |
| **Total** | | | | **2.00 R** |

If D2 had company-free lodging: within jurisdiction → D2 = 0.25 R; outside → D2 % = 25 % → 0.25 R (same result, different field stored: `DAPercentageAdmissible` = 100 vs 25). Backend stores whatever is sent.

---

## 5. Observed defects relevant to DA (code facts)

- `GetGradeData(level)` (`EmpTourPlanRepository.cs:1543-1558`): operator precedence makes `IsActive` apply only when `level` is non-null, and the loop reuses one DTO so only the **last** grade is returned. Prefer `GetAllGradeData`.
- Supplementary approval looks up rows with `f.Id == _empTourPlan.Id` instead of `EmpTourPlanId` (`ClaimRepository.cs:3690, 3717, 3744`) → rows are normally not found and the sum check fails **(inferred effect)**.
- Comment in `Touroverlapvalidator.cs:91-92` says the DA check runs at bill-passing / finance approval and in clarification; no such call exists in `ApproveRaiseClaimAmount` or `ClaimClarificationRepository`.

---

## Must remain in .NET
- DA overlap / double-claim validation (`TourOverlapValidator`) with the per-employee DB lock and transaction.
- Persistence of `DADetail` / `LeaveDetail`, approval reconciliation (D15, D22, D23), cancellation-claim DA block.
- (Recommended) a server-side DA recomputation so the agent and UI cannot diverge — currently missing.

## Agent should call (APIs)
| Purpose | Endpoint |
|---|---|
| Grade DA rate + X/Y/Z hotel entitlement | `GET api/TourPlan/GetAllGradeData` (avoid `GetGradeDetail?lavel=` — buggy) |
| Employee grade + promotionDate | org-hierarchy `Organization/GetEmployeDetailsWithEmpCode/{empCode}` (external service) |
| Grade order (`pgOrder`) for previous-grade lookup | DMS `ModuleManagement/GetAllMasterData` (external) |
| Tour/legs to derive DA span | `GET api/Claim/RaiseClaimRequest/{tourId}`, `GET api/TourPlan/GetTourDetails/{tourId}` |
| Submit DA (server validates overlap only) | `POST api/Claim/RaiseClaimRequest`, `POST api/Claim/raise-claim-with-request`, `POST api/ClaimEdit/edit`, `POST api/Claim/RaiseSupplementaryClaim` |

## Gaps (no API to evaluate this rule)
- No endpoint computes DA rows/slabs/amounts; agent must replicate `CalculateDAArray` + `getDAPercentageByHours` exactly (or a new .NET endpoint is needed).
- No endpoint to dry-run DA overlap without submitting (`ValidateDaJsonAsync` exists in BAL but is not exposed).
- No endpoint returning "grade on date X"; promotion split depends on external services and client logic.
- No API for leave-deduction amount.

## Unresolved questions
1. Is the frontend hour-slab table (30/70/100 at 6 h / 12 h) the official policy? Boundary semantics (exactly 6:00 → 30 %, 6:01 → 70 %) need policy confirmation.
2. Should DA vary by city class X/Y/Z? Code uses one rate per grade.
3. Free-lodging reduction: within stores `%=100, amount×0.25`, outside stores `%×0.25` — which representation should the agent submit?
4. Is "previous grade = next-lower pgOrder" correct for multi-step promotions or redesignations?
5. Is `OrgClaimLimit` meant to cap annual claims? It is unused.
6. Should DA be server-validated against tour dates (no check today that DA dates fall inside the tour)?
