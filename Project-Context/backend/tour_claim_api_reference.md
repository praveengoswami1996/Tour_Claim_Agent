# Tour & Claim API Reference (for the Python agent)

This reference covers the Tour_API (ASP.NET Core, net6.0) endpoints a Python agent (FastAPI + LangGraph) would call to read and run an employee's tours and claims. All `path:line` citations are relative to `Tour_API/`.

**Repository abbreviations** (all files are in `TourProgram.BAL/Repository/Concrete/`):

| Abbreviation | File |
|---|---|
| **EPR** | `EmpTourPlanRepository.cs` |
| **CR** | `ClaimRepository.cs` |
| **CER** | `ClaimEditRepository.cs` |
| **CCR** | `ClaimClarificationRepository.cs` |
| **TSR** | `TourStatusRepository.cs` |
| **TLR** | `TourListingRepository.cs` |
| **TNR** | `TourNotAvailedRepostitory.cs` |
| **BAR** | `BulkApprovalRepository.cs` |
| **TRR** | `TourRecoveryRepository.cs` |
| **TOV** | `TourProgram.BAL/Helpers/Touroverlapvalidator.cs` |

Controllers are in `TourProgram.API/Controllers/`. Statements marked **(inferred)** are not literally in code. Example IDs such as EmpId 1001 or tourId 5001 are placeholders.

> Companion document: `claim_lifecycle.md`, which covers status IDs, the state machine and transitions.

---

## 0. Cross-cutting behaviour (read first)

### 0.1 Authentication and authorization

**How the token is checked**
- There is one global filter, `SSOTokenValidateFilter`, registered at `TourProgram.API/Program.cs:22-28`.
- It requires `Authorization: Bearer <token>`. The token's `iss` must equal the configured SSO base URL (`ActionFilter/SSOTokenValidateFilter.cs:196-214`).
- The token is then introspected at `{SSO}/connect/introspect1`; `active` must be true (`:216-247`).
- On failure it returns HTTP 401 with body `{message:"User is unauthorized.", statusCode:401, timestamp}` (`:178-194`).
- The introspected user is stored in `HttpContext.Items["CurrentUser"]`, with fields UserId, UnitId, Department, Designation, role, Level (`:250-272`).

**Read-only users**
- If the user's `IsReadOnly` is `"true"` or `"1"` and the request is POST, PUT, DELETE or PATCH, the filter returns **403** "Access denied. User has read-only permissions and cannot perform write operations." (`:112-116, 128-158`).
- **(inferred)** `IsReadOnly` is never populated from introspection (`:250-269`), so this check is effectively inert.

**`[AllowAnonymous]` actions skip the filter entirely** (`:36-41`). Agent-relevant anonymous actions:
- `TourPlan/EditTourRequest`
- `TourPlan/SubmitTourRequestDraft`
- `ClaimEdit/edit`
- `Claim/GetSupplementaryClaimDetails`
- `TourListing/GetTourTrajectory`
- `TourListing/GetTotalPendingTaskCount`
- `TourListing/GetRecipientTourRequestPendingSummary`
- `TourListing/GetRecipientTourRequestByUser`
- The bill-passing summary and list actions
- `TourListing/GetEmployeesOnTour*`

**No role or permission attributes exist.** `[Authorize]` is commented out on every controller in scope (for example `ClaimController.cs:20`, `TourPlanController.cs:15`).

**No ownership checks against the token**
- Only `TourRecoveryController` and `CGMExcessiveTourController` inherit `BaseController`. They read `TokenDetails.EmpCode` from the `UserId` claim (`BaseController.cs:34-46`).
- Every other endpoint takes the actor (`EmpId`, `senderId`, `RecipentId`, `recipientId`) from the route, query or body.
- The server never compares that actor with the token user. **Any authenticated user can act as any employee, approver or finance user.**
- The agent **must** derive `empId` from the token itself and never take it from LLM or user text.

**`DeviceType` header**
- It is read into `BaseController.SourceType` (`BaseController.cs:21`).
- No business logic in scope reads it. Send `DeviceType: web` to mirror the frontend.

### 0.2 Response envelope and HTTP status

The envelope is `ResponeModel` (`TourProgram.Model/Dtos/Response/ResponeModel.cs:10-22`). Its fields, camelCase in JSON (System.Text.Json default, `Program.cs:25-28`):

- `statusCode`
- `message`
- `data`
- `dataLength`
- `pageNumber`
- `pageSize`
- `totalRecords`
- `error`
- `errorDetail`

**The HTTP status is often not the real status.**
- Actions that return `ResponeModel` directly always produce **HTTP 200**, even when the body has `statusCode: 400/404/409/500`.
- These actions map the body status onto the real HTTP status (`StatusCode((int)result.StatusCode, result)`):
  - `Claim/raise-claim-with-request`
  - `Claim/SubmitClaimWithRequestDraft`
  - `Claim/SubmitRaiseClaimDraft`
  - `ClaimEdit/edit`
  - `ClaimClarification/respond-message`
  - every `TourRecovery` action
  - the CGM actions
  - `TourPlan/EditTourRequest`, which maps OK, NotFound and BadRequest only
- **Agent rule: always branch on `body.statusCode`.** Treat `200/201` as success. Do not rely on `error`: many failure paths leave it `false`.
- Model-binding failures return an HTTP 400 `ValidationProblemDetails`, not a `ResponeModel`.
  - These come from `[ApiController]` plus `<Nullable>enable</Nullable>` in TourProgram.Model, which makes non-nullable `string` properties implicitly required.
  - Unhandled exceptions go to `ExeceptionMiddleware` and return HTTP 500 (`TourProgram.Logger/ExeceptionMiddleware.cs:34-44`).

### 0.3 Multipart conventions for claims

- Detail sections are **JSON strings inside form fields**:
  - `TransportaionDetailDtos` (spelled exactly like this)
  - `AccomodationDetailDto`
  - `DailyAllowanceDto`
  - `LeaveAvailedDto`
- They are parsed with `JsonConvert.DeserializeObject(str)` and then `DeserializeObject<List<T>>(x.ToString())`. Both raw and double-encoded arrays are accepted, and property names are case-insensitive (`CR:1798-1799`).
- **File-to-row mapping.** Transport and accommodation rows carry `StartFileIndexCount`/`EndFileIndexCount`, which are inclusive 0-based indexes into the single flat file list for that category (`CR:1849-1863, 1953-1966, 3415-3453`).
  - A row with no files **must send `-1`**. The default 0..0 attaches file #0 to the row.
  - An out-of-range index throws, which gives a 500 and a rollback.
- Tour-level file parts carry no index: additional, advance-payment, accident and nomination files.

### 0.4 Status trail

- The status trail is append-only `EMPClaimProcessDetail` rows (`TourProgram.DAL/Models/EMPClaimProcessDetail.cs:7-25`).
- The **current status is the row with the highest Id** for the tour. `IsActive` is never cleared.
- Full details are in `claim_lifecycle.md`.

### 0.5 Side effects summary

- **Emails and notifications:** **none** of the tour or claim transition endpoints send email, SMS or WhatsApp.
  - The only live notifications are `POST api/Account/ShareTourOnMail` and the monthly CGM job (`NotificationRepository.cs`).
  - The tour-cancel email is commented out (`TNR:219-227`).
- **PDFs:**
  - Request PDF: `EPR GenratePDFUser :903`.
  - Approval PDF: `EPR :1009`, `TSR :791-888`.
  - Re-approval merged PDF: `TSR :893-984`.
  - Finance merged claim PDF: `CR:4021-4080`.
- **External role grant:** `AssignedTourApprovalRole` POSTs to `{Organization}/api/AdminManage/CreateRolesandPermssion` (`EPR:301-323`).
- **Audit:** a hash-chained `AuditLog` row is written for every EF write by interceptors (`Program.cs:39-50`).

---

## 1. Tour request

### 1.1 POST `api/TourPlan/TourRequest`: create a tour request

- **Controller:** `TourPlanController.TourRequest` (`TourPlanController.cs:40-45`). Body `[FromForm] EmpTourPlanRequestDto`. HTTP is always 200.
- **BAL:** `EPR.EmpTourRequest` (`EPR:325-823`).
- **Auth:** SSO filter only. `EmpId` and `ManagerId` come from the body.

**Request** (`TourProgram.Model/Dtos/EmpTourPlanDtos/EmpTourPlanRequestDto.cs:8-128`)

| field | type | req | meaning |
|---|---|---|---|
| EmpId | int | Y | Requester. Becomes CreatedBy and the first SenderId. |
| PurposeOfTour, TypeOfTour, Destination, Source | string | Y | Tour header |
| TantetiveDateOfDeparture / TantetiveEndDate | DateTime | Y | Window used for the overlap check on Normal tours |
| NoOfDays | int | Y | |
| IsRoad / IsRail / IsAir | bool? | N | Travel modes |
| ClassOfCity | string? | N | Defaults to "X" (`EPR:382`) |
| Grade | string | Y | Stored only. No entitlement check. |
| Remark | string? | N | |
| AdvanceRequired | double? | N | Advance amount requested |
| IsAdvanceRequired | bool | N | Selects ClaimType 1 (advance) or 4 |
| IsX / IsY / IsZ | bool? | N | City-class flags |
| IsManualApproval | bool | N | **If true, the tour is auto-approved** (see below) |
| ManualApprovalfile | IFormFile? | N | Stored when IsManualApproval |
| Jurisdiction | string | N | "Outside Jurisdiction" (default) or "Within Jurisdiction" |
| IsDirectClaim | bool? | N | |
| IsClaimWithRequest | bool | N | true is used by the within-jurisdiction flow (§3) |
| ExFactoApproval | bool | N | |
| TourTravelEstimate / TourAccommodationEstimate / TourDAEstimate / TourLocalEstimate | {Files: List&lt;IFormFile&gt;, EstimatedAmount:int?} | N | Estimates. Files are stored as UploadTypeDetail types 1, 2, 7 and 8. Note `Amount=101` is hard-coded (`EPR:504-590`). |
| TentativeTravelPlans | string (JSON array of {Date, From, To, TravelMode, TourDetails?, ClassOfCity?, Remarks?, IsOpenTime, travelStatus}) | Y (non-nullable) | Itinerary. "NA" skips it. |
| ManagerId | int | Y | **Approver (reporting manager), chosen by the client.** Not validated. |
| TourType | TypeofTrip (1 Normal, 2 Daily) | N | |
| NominationFiles | List&lt;IFormFile&gt;? | N | Upload type 3. Sets IsNominationUpload. |
| DailyTourDateValidation | List&lt;{StartDate, EndDate}&gt;? | conditional | **Required if IsClaimWithRequest or TourType==Daily** |

**Validations, in order**
1. A transaction is opened and `TOV.LockEmployeeAsync` takes an `sp_getapplock` on `TourOverlap_{empId}` with a 20 s timeout (`EPR:332-333`, `TOV:49-60`).
   - On timeout, SQL throws "Another request for this employee is in progress. Please retry.", which surfaces as a 500.
2. If IsClaimWithRequest or Daily, and the ranges are empty: body 400, Error=true, `"DailyTourDateValidation Is Required For This Case"` (`EPR:338-347`).
3. Overlap check:
   - Daily or claim-with-request tours: `TOV.FindOverlapAsync` per range.
   - Otherwise: one check over departure to end (`EPR:349-361`).
4. A conflict rolls back and returns body **400 with Error=false**.
   - `message` is one of:
     - `"These dates overlap DA already claimed in another tour. Conflicting Tour ID {id}: {dd MMM yyyy HH:mm} to {…}."`
     - `"These dates overlap another tour request. Conflicting Tour ID …"` (`TOV:13-26`)
   - `data = {TourID, PurposeOfTour, TypeOfTour, Destination, Source, fromDate, toDate}`, with dates formatted "dddd, dd MMMM yyyy HH:mm" (`EPR:801-819`).
5. Self/manual case: if an Approved row already exists, body 400 `"Request is already submitted."` (`EPR:668-673`). This is unreachable on a fresh tour.

**Writes**
- `EmpTourPlan` (`EPR:369-408`).
- `DailyTourDateValidations`.
- Estimate and nomination `UploadTypeDetail` plus `UploadDocument` rows.
- `ClaimTourDetails`.
- `TentativeTravelPlans` (`EPR:592-620`).
- `EMPClaimProcessDetail` with **status 1 PendingApproval**, Sender=EmpId, Recipient=ManagerId, and ClaimType 1 if IsAdvanceRequired else 4 (`EPR:622-644`).

**Side effects**
- Role grant to ManagerId (`EPR:646`).
- Request PDF written to `wwwroot/DownloadUserPdf/` (`EPR:647`).

**Auto-approval branch**
- Condition: **`EmpId == ManagerId` ("MD case") OR `IsManualApproval`** (`EPR:650-791`).
- Adds a status **2** row (Sender = Recipient = ManagerId, Remark "Auto Approval").
- Sets ApprovedBy and ApprovedDate, and generates the approval PDF.
- If IsClaimWithRequest, also adds a status **3** row to the unit finance user (`GetFinanceUserForUnit`). Finance-lookup errors are swallowed into the message: `"Tour request approved successfully. Warning: …"`.

**Success:** body 201 `"Tour created successfuly"`, `data` = new tour Id as a **string** (`EPR:794-798`).

**No email** is sent; `_iNotificationRepository` is injected but unused.

### 1.2 GET `api/TourPlan/GetTourDetails/{tourId}`

- **Controller:** `TourPlanController.cs:24-31`. **BAL:** `EPR.GetTourDetails :48-257`.
- **Response `data`:**
  - `EmployeeDetails` {EmployeeNo, EmployeeName, Designation, PurposeOfTour, Source, Destination, EmployeeDepartment, TentativeStartDate, NoOfDays, Units, Lavel, TourType, TypeofTrip, IsManualApproval, ManualApprovalfilePath, RequestPdfPath, ExFactoApproval}
  - `TentativeTourPlans[]` {Date, From, To, TravelMode, ClassOfCity, Remark, IsOpenTime}
  - `IsAdvanceRequired`, `AdvanceRequired`
  - `TravelEstimate`, `AccommodationEstimate`, `DaEstimate`, `LocalEstimate`
  - `IsNominationUpload`, `NominationFiles`, `ManagerId`
  - `ApproverDetails` {ApprovalDate, ApprovedAmount, ApprovedById, ApprovedBy, Designation, Department, FilePath}
  - `claimprocess` (`EPR:229-245`)
- **Not found:** body 404 `"Tour id is not found."` (`EPR:253-254`).
- No ownership check.

### 1.3 PUT `api/TourPlan/EditTourRequest/{tourId}` (**[AllowAnonymous]**)

- **Controller:** `TourPlanController.cs:46-81`. HTTP is mapped: 200, 404 or 400.
- **BAL:** `EPR.EditEmpTourRequest :1109-1450`. Body: same `EmpTourPlanRequestDto` (multipart).
- **Validations:**
  - Null → `"Invalid request data"` 400.
  - Tour missing → 404 `"Tour request not found"`.
  - **Only guard:** the latest row (by CreatedDate) has status **2** → 400 `"Cannot edit an approved tour request"` (`EPR:1133-1145`). Status 3, 9, 12 and so on are not blocked.
  - Lock, then FindOverlap excluding this tour → 400 with `conflict.Message` and the same `data` shape as §1.1 (`EPR:1149-1172`).
- **Writes:**
  - Tour fields updated.
  - Estimates refreshed (`UpdateEstimateFiles :1451`).
  - TentativeTravelPlans deleted and rebuilt.
  - **Appends a new status 1 row** to `ManagerId` (`EPR:~1400`).
  - Role grant.
  - Request PDF regenerated.
- **Success:** 200 `"Tour request updated successfully"`. Exception: 500 `"Error updating tour request: …"`.
- **Use:** re-submitting a tour after a send-back (13).

### 1.4 POST `api/TourPlan/SendBackToEmployee/{tourId}/{senderId}/{StatusId}?remark=`

- **Controller:** `TourPlanController.cs:117-122`. **BAL:** `EPR:1861-1910`.
- Appends a row with status=`StatusId` (code comment says 13 or 15), Sender=senderId, **Recipient=tour.EmpId**, Remark, and ClaimType 1 or 4.
- Success: 200 `"Request send successfully."`. Not found: 404 `"Invalid tour id."`.
- **No status or role check.** StatusId is any number.

### 1.5 POST `api/TourPlan/SendBackToReportingManager/{tourId}/{managerId}/{stausId}`

- **Controller:** `TourPlanController.cs:123-128`. **BAL:** `EPR:1912-1960`.
- Appends a row with status=`stausId` (comment: "only 1,14"), Sender=tour.EmpId, Recipient=managerId, plus a role grant.
- Messages are the same as §1.4.
- This is how an employee re-sends a sent-back tour to the manager without editing it.

### 1.6 POST `api/TourPlan/TourReApprovalRequest/{tourId}` (amend an approved tour)

- **Controller:** `TourPlanController.cs:129-134`, `[FromForm] EmpTourPlanRequestReApprovalDto`. **BAL:** `EPR:1962-2155`.

**Request** (`EmpTourPlanRequestReApprovalDto.cs`)

| field | type | req | meaning |
|---|---|---|---|
| EmpId | int | Y | Requester |
| PurposeOfTour, TypeOfTour, Destination, Source | string | Y | New tour header |
| TantetiveDateOfDeparture | DateTime | Y | New start |
| NoOfDays | int | Y | New length |
| IsRoad, IsRail, IsAir | bool? | N | Travel modes |
| ClassOfCity, Grade | string | Y | |
| Remark | string? | N | |
| IsX, IsY, IsZ, IsDirectClaim | bool? | N | |
| TentativeTravelPlans | string JSON | Y | New itinerary |
| ManagerId | int | Y | Approver |

**Validations**
- Window = departure to departure + NoOfDays − 1 minute. FindOverlap excludes this tour (`EPR:1967-1977`).
- Conflict → body 400 with `conflict.Message`.
- Tour missing → 404 `"Record not found."`. Null DTO → 400 `"Bad request."`.
- **No status guard.** In particular, nothing checks that the tour is approved.

**Writes**
- Snapshots the old header and approver into `ClaimTourDetailsReApproval`, and the old itinerary into `TentativeTravelPlanReApproval`.
- Replaces `ClaimTourDetails` and the TentativeTravelPlans.
- Appends a **status 14 ReApprovalRequest** row to ManagerId, plus a role grant.
- On the tour: sets `IsReApproval=true`, `OldApprovalPDFPath=ApprovalPDFPath`, and `ApprovalPDFPath=""`.

**Success:** 200 `"Re-approval send successfully."`.

**What comes next:** the approver acts via §6.1 or §6.4. The re-approval PDF is merged with the old one when IsReApproval is set (`TSR:893-984`).

### 1.7 GET `api/TourPlan/GetTourOldApprovalDetails/{tourId}`

- **BAL:** `EPR:2156-2326`. Returns the pre-amendment snapshot.
- Success: 200 `"Re approval details."`.
- Errors: 400 `"This tour have not re approval."`, 404 `"Tour id is not found."`.

### 1.8 Tour-request drafts

These endpoints store drafts in the `TourRequestDraft` entity.

**POST `api/TourPlan/SaveTourRequestDraft`** (`TourPlanController.cs:159-205`; BAL `EPR:2376-2580`)
- Multipart parts:
  - `draftData` (a JSON `TourRequestDraftDto`, `TourProgram.Model/Dtos/Draft/TourRequestDraftDto.cs:10-78`)
  - `travelFiles`, `accommodationFiles`, `daFiles`, `localFiles`, `nominationFiles`
  - `manualApprovalFile` (only the first file is used)
- `TourRequestDraftDto` fields:
  - DraftId?, EmpId, DraftName
  - All tour fields from §1.1 as nullables
  - TravelEstimatedAmount, AccommodationEstimatedAmount, DaEstimatedAmount, LocalEstimatedAmount
  - TentativeTravelPlans (JSON), ManagerId?, TourType (int)
  - DailyTourDateValidationJson
  - `*FileReferences` (server-managed `DraftFileMetadata` {FileId, RowIndex?, OriginalFileName, TempFilePath, FileSize, FileType, UploadedAt, DisplayName})
  - `Existing*FileIds` (keep-lists)
  - LastEditedSection
- Errors:
  - Controller: 400 `"Invalid draft data format."` / `"Invalid request: …"`.
  - BAL: 400 `"EmpId is required."`, 404 `"Draft not found or already submitted."`, 500 `"Error saving draft: …"`.
- Success: 200 `"Draft saved successfully."`, with message `"New draft created"` or `"Draft updated"`.

**GET `api/TourPlan/GetTourRequestDraft/{draftId}?empId=`** (`EPR:2826`)
- Success: 200 `"Draft retrieved successfully."`. Not found: 404 `"Draft not found."`.

**GET `api/TourPlan/GetAllTourRequestDrafts?empId=`** (`EPR:2892`)
- Success: 200 `"Found {n} draft(s)."`.

**DELETE `api/TourPlan/DeleteTourRequestDraft/{draftId}?empId=`** (`EPR:2945`)
- Soft-deletes the draft and removes its temp files.
- Success: 200 `"Draft deleted successfully."`. Not found: 404 `"Draft not found."`.

**POST `api/TourPlan/SubmitTourRequestDraft/{draftId}?empId=`** (**[AllowAnonymous]**; `EPR:2583-2822`)
- Errors: 404 `"Draft not found or already submitted."`, 400 `"Invalid draft data."`.
- Runs `ValidateTourRequestDraftForSubmit` (`EPR:3038-3068`). Failures give 400 `"Draft validation failed: "` followed by a comma-joined list of:
  - `EmpId is required`
  - `PurposeOfTour is required`
  - `TypeOfTour is required`
  - `Destination is required`
  - `Source is required`
  - `TantetiveDateOfDeparture is required`
  - `TantetiveEndDate is required`
  - `NoOfDays is required`
  - `Grade is required`
  - `IsAdvanceRequired is required`
  - `TentativeTravelPlans is required`
  - `ManagerId is required`
- Then converts the draft and calls `EmpTourRequest` (§1.1), moves the files to permanent storage, and marks the draft submitted (`MoveDraftFilesToPermanentAndSaveRecords :3070`).
- Exception: 500 `"Error submitting tour request draft: …"`.
- **This is the only server-side "validate required fields" list in the system.** It is useful for the agent's slot filling.

---

## 2. Listing, history and trajectory (employee-facing)

All endpoints in this section are on `TourListingController`. It does **not** inherit BaseController, so every id comes from the path. HTTP is always 200.

### 2.1 GET `api/TourListing/GetTourRequest/{empId}?touretype={1|2}&search=`

- **Controller:** `TourListingController.cs:25-32`. **BAL:** `TLR:1236-1428`.
- `touretype` is non-nullable. If it is omitted it binds to 0 and the result is empty. **Call once with 1 and once with 2.**
- Returns **all** of the employee's tours in every status, newest first, with these item fields:
  - tourId, empId
  - tourType (the **ClaimType name** of the latest row, not Normal/Daily)
  - Requester: empName, empCode, designation, department, unitName, lavel, reDesignatedGrade, empStatus
  - **Current holder:** recipientId and `recipient*` fields
  - approvedBy and `approver*` fields
  - requestDate, purposeofTour, source, destination, tourStartDate, tantetiveEndDate, totalDays
  - **status, statusId** (latest row)
  - isAdvanceTaken, advanceRequestAmount, paidAmount, approvedAmount, isManualApproval
  - sendBackToRemark (**all** trail remarks joined with ", ")
  - (`TLR:1357-1416`)
- There are **no** canEdit or canRaiseClaim flags; derive them from statusId (see the lifecycle doc).
- Messages: `"{n} tour(s) found."`; `"No tour found for the search."`. An employee with no tours gets statusCode 0 and data null.

### 2.2 GET `api/TourListing/GetTourRaiseClaimList/{empId}`: tours eligible for a claim

- **Controller:** `TourListingController.cs:252-259`. **BAL:** `TLR:1033-1233`.
- **Filter:** `EmpTourPlan.EmpId == empId` **and** latest trail status **== 2 (Tour Approved)** (`TLR:1044-1051`).
  - No end-date check and no TourType check.
  - A LEFT JOIN on FinanceDetails can duplicate rows.
- **Item fields:** tourId, empId, tourType, empName, designation, department, requestDate, purposeofTour, tourStartDate, source, destination, totalDays, status, statusId, and `approverDetails` {approvalDate, approvedAmount, approvedById, approvedBy, designation, department, filePath}.
- Empty list: body 404 with `data: []`.

### 2.3 GET `api/TourListing/GetTourTrajectory/{empTourId}` (**anonymous**)

- **Controller:** `TourListingController.cs:168-204`. **BAL:** `TLR:462-565`. DTO: `TourProgram.Utility/Model/TourTrajectoryDto.cs:10-55`.
- `data = {empTourId, tourPlanTitle, trajectory[]}`.
- Each trajectory item has:
  - id
  - sender id, name, empCode, department, designation, grade, unit
  - the same fields for the recipient
  - claimTypeId, claimTypeName, statusId, statusName, remark, createdDate
- The trajectory is ordered by CreatedDate.
- Body statusCode is never set (it stays 0). Message: `"Tour trajectory retrieved successfully"` or `"Tour plan not found"`.
- **Best source for "where is my claim / who has it / what did finance say".**

### 2.4 GET `api/TourListing/GetTotalPendingTaskCount/{empId}/{Type}` (anonymous)

- **BAL:** `TLR:2515-2637`. `data` is an int.
- It counts tours whose latest row has `RecipientId == empId` and a status in the set for `Type`:

| Type | Statuses counted |
|---|---|
| `TOURAPPROVER` | 1, 6, 14 |
| `TOURBILLPASSING` | 3, 17, 25 |
| `TOURPAYMENTPROCESSING` | 8 (plus FinanceDetails conditions) |
| `USER` | **11, 13**, which is the employee's own to-do count |

- Any other Type: 404 `"Record not found."`.

### 2.5 Other listing endpoints

- **GET `GetMyActionList/{empId}`** (`TLR:2166`): a finance user's FinanceDetails history. It is **not** an employee to-do list.
- **GET `GetEmployeesOnTourByDateRange?empcode=&tourDateFrom=&tourDateTo=`** (anonymous, `TLR:2894`): the employee's tour days in the range.
  - Excludes Daily tours and tours whose latest status is 4, 5, 7 or 10.
  - Validation messages (400): "Tour end date cannot be before start date", "Employee code is required", "Invalid employee code format".
- **Approver and finance queues** (all filter on the latest row's RecipientId taken from the path):

| Endpoint | Statuses / filter |
|---|---|
| `GetRecipientTourRequest/{recipientId}` | 1, 6, 14 |
| `GetRecipientTourRequestPendingSummary` | 1, 6, 14; or 17 when `RequestType=clarification` |
| `GetBillPassingList` | 3 with advance |
| `GetRaiseClaimBillPassingList` | ClaimType 2/3/7; status 3 by default, or the caller's `statusIds` |
| `GetClearificationBillPassingList` | 11 or 17 |

  (`TLR:36-883, 1432-1799, 3052-3257`)

### 2.6 GET `api/Claim/ClaimsDetailsById/{tourId}` and GET `api/Claim/GetAllClaims/{senderId}`

**ClaimsDetailsById** (`CR:4469-4537`)
- For each trail row of the tour with status 12, 5 or 4, returns `{TourType, TourTypeId, StatusId, statusName, history}`.
- `history` is the §4.1 payload.
- Empty when no such status exists; duplicated when several exist.

**GetAllClaims** (`CR:4228-4468`)
- A finance view: tours whose latest row has `SenderId == senderId` and status 12 or 4.
- Query parameters: `pageNumber, pageSize, searchTerm, departmentFilter, fromDate, toDate, statusFilter`, where `statusFilter` is `ProcessCompleted` or `RejectedByFianance`.

---

## 3. Within-jurisdiction: raise claim together with the request (daily/local tours)

### 3.1 POST `api/Claim/raise-claim-with-request`

- **Controller:** `ClaimController.cs:42-48`. HTTP is mapped.
- **BAL:** `CR.RaiseClaimWithRequest :2758-2909` calls `MapClaimToTourRequest :2911`, which calls `EPR.EmpTourRequest` (§1.1); then `SaveClaimDataWithoutProcessEntry :3190-3649`.

**Request:** multipart `ClaimApprovalDto` (`TourProgram.Model/Dtos/RaiseClaimDtos/ClaimApprovalDto.cs:10-62`)

| field | type | req | meaning (in this flow) |
|---|---|---|---|
| EmpTourPlanId | long | – | **Ignored.** The tour is created. |
| EmpId | long | Y | Employee |
| RecipentId | long | Y | **Reporting manager / approver** (becomes ManagerId, `CR:2924`). `== EmpId` triggers auto-approval. |
| PurposeOfTour, Source, Destination | string | Y | |
| TantetiveDateOfDeparture | DateTime | Y | |
| NoOfDays | int | Y | End-date fallback |
| TransportaionDetailDtos | string JSON | Y | Legs (table below). ModeOfTravel text sets IsRoad/IsRail/IsAir ("Road/Car/Bus", "Rail/Train", "Air/Flight"). |
| TransportationUploadDto | files | N | Indexed by row |
| AccomodationDetailDto | string JSON | N | |
| AccomodationUploadDto | files | N | Indexed by row |
| DailyAllowanceDto | string JSON | N | DA rows (pre-checked for overlap) |
| LeaveAvailedDto | string JSON | N | |
| DailyTourDateValidation | list {StartDate, EndDate} (form keys `DailyTourDateValidation[0].StartDate`) | **effectively Y** | Presence windows; required |
| ClaimAmount | double | N | Stored verbatim. **Not reconciled.** |
| PaidAmount | double? | N | Stored |
| IsPaidViaCreditCard | bool | N | Stored |
| CreditCardPaidAmount | double? | N | **Not persisted** in this flow |
| AdvancePaymentBillUpload / AdditionalFiles / AccidentClaimFile / NominationFiles | files | N | Upload types 5 / 4 / 9 / 3 |
| DaUploadDto | files | N | **Ignored** by the direct endpoint (the draft path keeps them) |
| IsIncidentRelatedClaim + ClaimIncidentDTO {DateOfIncident, location?, Description?, incidentType (1 = accident)} | | N | The DTO is required when the flag is true; otherwise a 500 |
| IsLeaveAvailedwithbetweentour, PaymentReferenceNo, DocumentsAttached {IsApprovedTourProgramme?, IsTickets_BoardingPass_Bills?, IsHotelBills?}, IsCancelationClaim | | (DocumentsAttached is implicitly Y) | Not used |

**TransportaionDetailDto** (`ClaimApprovalDto.cs:66-102`)

| field | type | meaning |
|---|---|---|
| Id | long | 0 = new (used by edit/respond) |
| DateOfTravelSource, TimeOfTravelSource ("HH:mm") | DateTime, string | Departure |
| Source, ModeOfTravel, Destination | string | |
| DateOfTravelDestination, TimeOfTravelDestiNation | DateTime, string | Arrival |
| BilledAmount, ClaimedAmount | double | |
| StartFileIndexCount, EndFileIndexCount | int | Bill slice (−1 = none) |
| travelStatus | enum start=1, continuetion=2, end=3, old=4 | |
| SGST/CGST/IGSTAmount, BillSGST/BillCGST/BillIGSTAmount, TaxStructure? | | GST |
| DistanceINKm, PresonalVehicalType? | | Own-vehicle legs |
| InvoiceNumber?, InvoiceDate? | | |
| ISRemoved, RemovedByEmpcode?, TourNumber (default 1), AttendenceId, Remark? | | |

**AccomodationDetailDto** (`:104-142`)
- Id, Location (not stored), ClassOfCity, IsOwnArrangement, IsFreeAccomodation, isCompanyLodginPaid, HotelName?, HotelCity
- DateOfCheckIn / TimeOfCheckIn / DateOfCheckOut / TimeOfCheckOut
- BilledAmount, ClaimedAmount, Start/EndFileIndexCount
- CGST/SGST/IGST and Bill* versions, TaxStructure?, InvoiceNumber?, InvoiceDate?, Remark?, ISRemoved, TourNumber
- **Note:** CGST and SGST are swapped when stored (`CR:3498-3499`).

**DailyAllowanceDto** (`:144-157`)
- Id, Date (stored as DateOfWorkIn = DateOfWorkOut), SourceTime, EndTime, DASlab (not stored), TotalHoursMinutes (not stored), PercentageAdmissible (int), Amount, IsApplicable (default true), TourNumber.

**LeaveAvailedDto** (`:159-166`): Id, LeaveStartDate, LeaveEndDate, DADeductable, Amount.

**Validations, in order**
1. Null DTO → 400 `"Bad request."`.
2. JSON parse of the four arrays. Malformed JSON → 500 `"Error: {ex}"` (`CR:2772-2809, 2901`).
3. **DA pre-check before save:** `TOV.ValidateDaJsonAsync` (`CR:2811-2820`). Conflict → **409**, Error=true, `data=TourConflict{TourId, PurposeOfTour, TypeOfTour, Source, Destination, From, To, Reason, Message}`.
   - Possible messages:
     - `"DA is claimed more than once for the same date/time in this claim. Conflicting Tour ID 0: …"`
     - `"DA from {S} to {E} overlaps DA already claimed in another tour. Conflicting Tour ID {id}: …"`
     - `"DA from {S} to {E} overlaps another tour request. Conflicting Tour ID {id}: …"`
4. `EmpTourRequest` (transaction A). Any non-201 result is returned as-is, for example 400 `"DailyTourDateValidation Is Required For This Case"` or the date-overlap 400 (§1.1).
5. Transaction B: lock, then save the claim rows.
   - A failure gives 500 `"Error saving claim data. Tour request was created but claim details failed."` (`CR:2849-2859`).
   - **The tour from transaction A stays committed** (an orphan in status 1).
6. DA post-check `TOV.ValidateTourDaAsync` → 409 (same shape). Transaction B rolls back; the tour remains.
7. Other errors: 500 `"Database error occurred while saving claim data."` / `"Error saving claim data: …"`.

**Success:** **201** `"Tour request with claim data created successfully. Pending manager approval."`, `data={EmpTourPlanId}` (`CR:2874-2880`).

**EmpTourPlan values set:**
- `TypeOfTour="Local"`, `Jurisdiction="Within Jurisdiction"`, `TourType=Daily`
- `IsClaimWithRequest=true`, `IsDirectClaim=true`, `IsAdvanceRequired=false`, `ExFactoApproval=true`, `ClassOfCity="X"`
- Grade comes from the Employee API, else `"E1"`.

**Trail:** status 1 to RecipentId. On approval (§6.1) the server adds status 3 to the unit finance user automatically.

**No grade, limit or DA-rate validation** exists anywhere in this flow.

### 3.2 Within-jurisdiction drafts

These are stored in `ClaimWithRequestDraft`.

**POST `api/Claim/SaveClaimWithRequestDraft`** (`ClaimController.cs:356-404`; `CR:4966-5219`)
- Multipart parts: `draftData` (JSON `ClaimWithRequestDraftDto`, `TourProgram.Model/Dtos/Draft/ClaimWithRequestDraftDto.cs:14-68`), `transportationFiles`, `accommodationFiles`, `daFiles`, `advancePaymentFiles`, `additionalFiles`, `AccidentClaimFile`, `nominationFiles`.
- `ClaimWithRequestDraftDto` fields:
  - DraftId?, EmpId, RecipentId?
  - The header fields
  - ClaimAmount/PaidAmount as decimal?
  - The JSON arrays as strings
  - DocumentsAttachedJson
  - **DailyTourDateValidationJson** (needed for submit)
  - IsPaidViaCreditCard, CreditCardPaidAmount
  - `*FileReferences`
  - `Existing{Transportation, Accommodation, Da, AdvancePayment, Additional, Accident, Nomination}FileIds` (JSON array or CSV)
  - LastEditedSection, IsIncidentRelatedClaim, ClaimIncidentDTO
- **File-retention gotcha:** transport and accommodation files are **deleted unless their ids are resent** in `Existing*FileIds` on every save (`CR:5051, 5084`). The other categories keep everything when the keep-list is blank.
- Success: 200 `"Draft saved successfully."`, `data={DraftId, LastSavedDate, FileSummary{…counts…, TotalFiles, TotalSize}, Message}`.
- Errors: 404 `"Draft not found or already submitted."`, 500 `"Error saving draft: …"`.

**GET `api/Claim/GetClaimWithRequestDraft?empId=&draftId=`** (`CR:4545-4628`)
- Without draftId, returns the latest draft.
- Success: 200 `"Draft retrieved successfully."`, `data={DraftId, LastSavedDate, LastEditedSection, DraftData, FileSummary}`.
- Not found: 404 (Error=false) `"No draft found."`.

**GET `api/Claim/GetAllClaimWithRequestDrafts?empId=`** (`CR:4633-4698`)
- Success: 200 `"Drafts retrieved successfully."`, `[{DraftId, LastSavedDate, LastEditedSection, Source, Destination, PurposeOfTour, TantetiveDateOfDeparture, NoOfDays, ClaimAmount, TotalFiles}]`.
- Empty: 404 `"No drafts found."`.

**DELETE `api/Claim/DeleteClaimWithRequestDraft/{draftId}?empId=`** (`CR:5298-5338`)
- Soft-deletes the draft. Success: 200 `"Draft deleted successfully."`. Not found: 404 `"Draft not found."`.

**POST `api/Claim/SubmitClaimWithRequestDraft/{draftId}?empId=`** (HTTP mapped; `CR:5344-5449`)
- Draft not found: 404 `"Draft not found or already submitted."`.
- Converts the draft with defaults **RecipentId→0, departure→Now, NoOfDays→1** (`CR:6575-6646`). **The agent must make sure these fields are filled before submitting.**
- Calls §3.1, so all of its validations and messages apply.
- Copies the files: DA files become upload type 7; accident files are **duplicated**.
- Success: 201 `"Draft submitted successfully."`, `data={EmpTourPlanId}`.
- Exception: 500 `"Error submitting draft: …"`. The tour may already exist at that point.

---

## 4. Outside-jurisdiction: raise a claim on an approved tour

### 4.1 GET `api/Claim/RaiseClaimRequest/{tourId}`: prefill and claim view

- **Controller:** `ClaimController.cs:132-138`. **BAL:** `CR.GetRaiseClaimRequest :1093-1538`. Read-only.
- **`data` contents** (`CR:1456-1524`):
  - `empTourDetails` {employeeNo, employeeName, designation, purposeOfTour, source, destination, employeeDepartment, tentativeStartDate, noOfDays, units, unitId, lavel, employeestatus, reDesignatedGrade}
  - `tourApprovelDetails.approverDetails` {approvalDate, approvedAmount, approvedById, approvedBy, designation, department, filePath}
  - `tentativeTourPlanList[]` {date, from, to, travelMode, classOfCity, remark}
  - `transportationList[]`: the transport fields plus approvedAmount, claimTaxRate, billTaxRate, approveTaxRate, `bills[{filePath, fileName, id}]`, isRemoved, isSupplementary, financeRemark
  - `accommodationList[]`: the same pattern
  - `dailyAllowancList[]` {daDetailId, date, sourceTime, endTime, dASlab, percentageAdmissible, amount, approvedAmount, isApplicable, tourNumber, isRemoved, isSupplementary, financeRemark}
  - `leaveList[]`
  - Amounts and flags: claimAmount, claimApprovedAmount, isAdvanceRequired, advanceRequired, isPaidViaCreditCard, creditCardPaidAmount
  - Advance bill-passing approver and SAP refs; payment-processing approver and SAP refs
  - SAP acceptance flags (IsSapAdvanceApprovalAcepted, IsSapCliamApprovalAcepted, IsSapSupplementaryAccepted, IsAdvanceAdjustmentAcepted, plus their dates)
  - isAdditionalUpload, isIncidentRelatedClaim, isCancelationClaim, claimIncident
  - **File lists:** `additionalFiles`, `advanceDocs`, `nominationFiles`, `accidentFiles` [{pathUrl, fileName, id}]
  - `trDetails` (includes typeofTrip and **tourType = Jurisdiction**)
  - refundedAmount, refundedPaymentReferenceNo, refundedPaymentDate
- **Not found:** 404 `"Tour id is not found."`.
- An NRE (unhandled 500) occurs if the ClaimTourDetails row or the employee is missing (`CR:1147, 1155`).
- **Primary input for the agent's "missing documents" analysis:** look for rows with empty `bills[]`, and for empty `nominationFiles`/`advanceDocs`.

### 4.2 POST `api/Claim/RaiseClaimRequest`: final submit (outside jurisdiction)

- **Controller:** `ClaimController.cs:34-40`. HTTP is always 200. **BAL:** `CR.RaiseClaimRequest :1541-2244`.
- **Request:** the same `ClaimApprovalDto` as §3.1, with these differences:
  - **EmpTourPlanId**: an existing tour; it must have a ClaimTourDetails row.
  - **RecipentId**: the **finance (bill-passing) user**, chosen by the client. There is **no server lookup**.
  - DailyTourDateValidation is not used.
  - `PaymentReferenceNo` together with `PaidAmount` triggers the advance-refund update after commit.
  - `IsCancelationClaim=true` forbids DA rows.
  - NominationFiles and DaUploadDto are ignored on the direct POST.

**Validations, in order**
1. Null DTO → 400 `"Bad request."`.
2. Any trail row on the tour newer than 30 s → **409** `"Claim request is already being processed. Please wait a moment before trying again."` (`CR:1558-1577`).
3. Transport rows created in the last 5 min → **409** `"Claim details already exist for this tour plan. Duplicate submission prevented."` (`CR:1583-1595`).
4. Transaction plus employee lock (on timeout: 500 `"An error occurred while processing the claim request."`).
5. ClaimTourDetails missing → 404 `"Tour id is invalid."`. EmpTourPlan missing → 404 `"Tour plan not found."` (`CR:1614-1648`).
6. Upload files: additional (4), accident (9), advance payment (5).
7. Transport, accommodation, DA and leave rows are inserted, with per-row bill slices.
8. `IsCancelationClaim` with DA rows → 400 `"DA Is Not Allowed in CancelationClaim"` (`CR:2006-2011`).
9. Process row inserted: **status 3**, Sender=EmpId, Recipient=RecipentId, ClaimType **2** if IsAdvanceRequired else **3** (`CR:2082-2103`).
10. Any matching draft (type 1 or 2) is consumed (`CR:2108-2168, 7964-8033`).
11. `TOV.ValidateTourDaAsync` → **409** with `"{Reason} Conflicting Tour ID …"`, `data=TourConflict`, rollback (`CR:2173-2182`).
12. Commit → **201 `"Claim requested successfully."`**, data null.
13. After commit, if PaymentReferenceNo is set: `TNR.PaymentRefund` writes PaidAmount, PaymentReferenceNo and PaymentDate. Errors there are swallowed.

**Exception mapping**
- 409 `"Concurrent modification detected. Please try again."`
- 500 `"Database error occurred while processing claim."`
- 500 `"An error occurred while processing the claim request."`

**Missing guards**
- **No guard on the current status.** A claim can be raised on an unapproved, rejected or already-claimed tour.
- **No jurisdiction check.** No ownership check.
- **No amount, grade or entitlement validation.**
- The agent must pre-check that the latest status is 2 (as in §2.2) and that the tour belongs to the employee.

### 4.3 Outside-jurisdiction drafts

These are stored in `ClaimDraft` with DraftType=2.

**POST `api/Claim/SaveRaiseClaimDraft`** (`ClaimController.cs:479-523`; `CR:7342-7561`)
- Multipart parts: `draftData` (JSON `RaiseClaimDraftDto`, `TourProgram.Model/Dtos/Draft/RaiseClaimDraftDto.cs:16-72`), `additionalFiles`, `advancePaymentFiles`, `transportationFiles`, `accommodationFiles`, `AccidentClaimFile`, `nominationFiles`.
- `RaiseClaimDraftDto` fields:
  - DraftId?, EmpId, **EmpTourPlanId (>0 required)**, RecipentId?
  - Header fields, ClaimAmount?, PaidAmount?, PaymentReferenceNo, IsPaidViaCreditCard, CreditCardPaidAmount?
  - The 4 JSON arrays
  - IsLeaveAvailedwithbetweentour?, DocumentsAttached, DailyTourDateValidation[]
  - `*FileReferences`
  - `Retained{Additional, AdvancePayment, Transportation, Accomodation, Accident, Nomination}FileIds` (null = keep all; a list = keep only those)
  - LastEditedSection, IsIncidentRelatedClaim, IsCancelationClaim, ClaimIncidentDTO
- Messages:
  - 400 `"EmpTourPlanId is required for Outside Jurisdiction draft."`
  - 404 `"Tour plan not found or inactive."`
  - 404 `"Draft not found or already submitted."`
  - 200 `"Draft saved successfully."` with `data={draftId, empTourPlanId, lastSavedDate, fileSummary{…}, message}`
  - 500 `"Error saving draft: …"`

**GET `api/Claim/GetRaiseClaimDraft?empId=&empTourPlanId=`** (`CR:7637-7702`)
- Success: 200 `"Draft retrieved successfully."`, `{draftId, empTourPlanId, lastSavedDate, lastEditedSection, draftData, fileSummary}`.
- Not found: 404 `"No draft found for this tour plan."`.

**GET `api/Claim/GetAllRaiseClaimDrafts?empId=`** (`CR:7707-7742`)
- Success: 200 `"Found {n} draft(s)."`, `[{id, empTourPlanId, lastSavedDate, lastEditedSection, createdDate}]`.

**DELETE `api/Claim/DeleteRaiseClaimDraft/{draftId}?empId=`** (`CR:7747-7789`)
- Success: 200 `"Draft deleted successfully."`. Not found: 404 `"Draft not found."`.

**POST `api/Claim/SubmitRaiseClaimDraft/{draftId}?empId=`** (HTTP mapped; `CR:5452-5539`)
- Not found: 404 `"Draft not found or already submitted."`. Invalid data: 400 `"Invalid draft data."`.
- Marks the draft submitted, converts it (defaults **RecipentId→0**, departure→Now, NoOfDays→1, ClaimAmount→0), and calls §4.2.
- A non-201 result unmarks the draft and is returned as-is.
- On 201 it copies the files: transport and accommodation files map by `RowIndex` onto the tour's active rows ordered by Id; nomination files become type 3 and set IsNominationUpload.
- Success: **201 `"Claim submitted successfully from draft."`**. Exception: 500 `"Error submitting draft: …"`, possibly after the claim was already committed.

**Legacy draft endpoints:** `SaveClaimDraft`, `GetClaimDraft`, `GetAllClaimDrafts`, `DeleteClaimDraft` (`CR:2250-2615`).
- They ignore DraftType, and new-draft creation likely fails because DraftData is NOT NULL **(inferred)**.
- **The agent should not use them.**

---

## 5. Claim edit (claims raised with a request) and clarification

### 5.1 GET `api/ClaimEdit/can-edit/{empTourPlanId}?empId=`

- **Controller:** `ClaimEditController.cs:56-60`. **BAL:** `CER:515-539` using `CanEditTourInternal :548-600`.
- `data={CanEdit, Reason}`. Body is 200, and 500 on an exception.
- **Reasons for false:**
  - `"Tour plan not found."`
  - `"You can only edit your own tour requests."` (empId ≠ owner)
  - `"Tour has already been approved and cannot be edited."` (ApprovedDate set)
  - `"Claim has been approved and cannot be edited."`
  - `"Tour is in a final status and cannot be edited."` (latest status ∈ {3, 8, 9, 12})
- Allowed: message `"Tour can be edited."`.

### 5.2 POST `api/ClaimEdit/edit` (multipart, **[AllowAnonymous]**, HTTP mapped)

- **Controller:** `ClaimEditController.cs:31-48`. **BAL:** `CER:43-284`. DTO: `TourProgram.Model/Dtos/ClaimEdit/EditClaimWithRequestDto.cs:123-200`.

**Request fields**

| field | type | req | meaning |
|---|---|---|---|
| EmpTourPlanId | long | Y | |
| EmpId | long | Y | Must equal the owner |
| RecipentId | long? | N | If >0, **overwrites the latest trail row's RecipientId in place** |
| PurposeOfTour, Source, Destination | string | Y | |
| TantetiveDateOfDeparture | DateTime | Y | |
| TantetiveEndDate | DateTime | (send it) | Omitted → 0001-01-01 is written |
| NoOfDays | int | Y | |
| ClaimAmount | double | (send it) | Written verbatim. Omitted → 0. |
| EditReason | string? | N | |
| TransportaionDetailDtos / AccomodationDetailDto / DailyAllowanceDto / LeaveAvailedDto | JSON strings | N | **Full replacement set by Id:** rows not sent are hard-deleted; Id=0 inserts; Id>0 updates |
| TransportationUploadDto / AccomodationUploadDto | files | N | Indexed |
| AdditionalFiles / NominationFiles / AdvancePaymentBillUpload | files | N | Appended (types 4 / 3 / 5) |
| DaUploadDto, PaymentReferenceNo | | N | Ignored |
| Deleted{TransportationUpload, AccommodationUpload, AdditionalFile, NominationFile, AdvancePaymentFile}Ids | CSV string | N | Hard-delete upload rows (not scoped to the tour) |

**Validations**
- 400 `"Invalid request. EmpTourPlanId is required."`
- 404 `"Tour plan not found."`
- 403 `"You can only edit your own tour requests."`
- 400 with any can-edit reason (§5.1)
- Lock; then after the writes `TOV.ValidateTourDaAsync` → **409** with `"Dates overlap with another tour. Conflicting Tour ID …"` / DA reason and `data=TourConflict`
- 500 `"Error updating tour: …"`

**Writes**
- A `ClaimEditLog` snapshot is written first (version+1).
- EmpTourPlan fields, ClaimTourDetails, the detail rows, and the uploads.
- TentativeTravelPlans are rebuilt from the transport rows (ClassOfCity "Z").
- **No new status row.**

**Success:** 200 `"Tour request updated successfully."`, `data={EmpTourPlanId, EditLogId, EditVersion, EditedAt, EditedSections[]}`.

### 5.3 Edit history endpoints

**GET `api/ClaimEdit/history/{empTourPlanId}`** (`CER:293-343`)
- Returns `[ClaimEditHistoryDto{EditLogId, EmpTourPlanId, EditedByEmpId, EditedByName, EditedAt, EditReason, EditedSections, EditVersion}]`.
- Messages: `"Edit history retrieved successfully."` / `"No edit history found."`.

**GET `api/ClaimEdit/log/{editLogId}`** (`CER:348-416`)
- Returns the log plus `Snapshots{EmpTourPlan, ClaimTourDetails, TransportationDetails, AccommodationDetails, DADetails, LeaveDetails, TentativeTravelPlans, UploadedFiles}`.
- Not found: 404 `"Edit log not found."`.

**GET `api/ClaimEdit/compare/{empTourPlanId}/{editLogId}`** (`CER:421-510`)
- Returns the `*Before` snapshots alongside the `*Current` entities. **No diff is computed.**

### 5.4 POST `api/ClaimClarification`: raise a clarification (finance or approver; JSON body)

- **Controller:** `ClaimClarificationController.cs:38-43`. **BAL:** `CCR:37-179`.
- **Request** (`ClaimClarificationRequestModel.cs:7-21`):
  - `TourPlanId` (long)
  - `TransportationClarification?`, `AccomodationClarification?`, `DAClarification?`, `LeaveClarification?` (the question text per section)
  - `SenderId` (int, the asker)
  - `ReciverId` (int, the responder, normally the employee)
- **Writes:**
  - A ClaimClarification row (`IsResponse=false`, `ParentClarificationId` = the latest row).
  - A trail row: **11** with ClaimType 1/2/4/3, or **23** with ClaimType 7 when IsSupplementaryRaised.
  - Recipient is ReciverId.
- Success: body 201 `"Clarification request created successfully."`, `data` = the entity.
- Not found: 404 `"Record not found, tour id is invalid."`.
- **No status, role or duplicate guard.**

### 5.5 GET clarification endpoints

**GET `api/ClaimClarification/{tourid}`** (`CCR:182-241`)
- Returns all questions and answers: `[{Id, TransportationClarification, AccomodationClarification, LeaveClarification, DAClarification, CreatedDate, SenderId, SenderName, ReciverId, ReciverName}]`.
- Success message: `"Clarifications details."`. Empty: 404 `"Record not found."`.

**GET `thread/{tourPlanId}`** (`CCR:243-311`)
- Returns `{TourPlanId, TotalClarifications, Questions, Responses, Thread[{Id, ParentClarificationId, Type:"Question"|"Response", Date, SenderId, ReciverId, Content{Transportation, Accommodation, DailyAllowance, Leave}}]}`.
- **This is the best way to find the open question:** the latest Question row with no later Response.

**Other read endpoints**
- **`detail/{clarificationId}`** (`CCR:314`): one clarification plus its history.
- **`history/{tourPlanId}`** (`CCR:563`): the full history. The `clarificationId` query parameter is ignored.
- **`compare/{tourPlanId}/{clarificationId}`** (`CCR:448`): `clarificationId` must be the **question** Id.

### 5.6 POST `api/ClaimClarification/respond`: employee answers and modifies the claim (multipart)

- **Controller:** `ClaimClarificationController.cs:53-58`. HTTP is always 200. **BAL:** `CCR:1972-2206`.

**Request fields** (`ClaimClarificationResponseModel.cs:95-180`)

| field | type | req | meaning |
|---|---|---|---|
| ClarificationId | long | Y | The **question** row Id |
| TourPlanId | long | Y | |
| TransportationClarificationResponse / AccomodationClarificationResponse / DAClarificationResponse / LeaveClarificationResponse | string? | N | Answer texts |
| SenderId | int | Y | The employee |
| RecipientId | int | Y | Should be the asker (the question's SenderId). **Not enforced.** |
| PurposeOfTour, Source, Destination, TantetiveDateOfDeparture, NoOfDays | | Y | Written to ClaimTourDetails only |
| TantetiveEndDate, IsLeaveAvailedwithbetweentour | | | Not used |
| TransportaionDetailDtos | string JSON | **Y** | Full set by Id |
| AccomodationDetailDto / DailyAllowanceDto / LeaveAvailedDto | string JSON? | N | DA rows are **all replaced** |
| ClaimAmount | double | (send it) | Written to EmpTourPlan.ClaimAmount. Omitted → 0. |
| TransportationUploadDto / AccomodationUploadDto | files | N | Indexed by row |
| AdvancePaymentBillUpload / AdditionalFiles | files | N | Types 5 / 4 |
| Deleted{TransportationUpload, AccommodationUpload, AdditionalFile, AdvancePaymentFile}Ids | CSV | N | No nomination files here |

**Validations**
- 400 `"Bad request"`.
- 404 `"Clarification request not found or already responded."`, which fires only if the question row does not exist. **Repeat answers are not blocked.**
- 404 `"Tour plan not found."`.
- 500 `"Database error occurred while processing clarification response."`.

**Writes**
- History snapshot (`*History` tables keyed by the question Id).
- Response row.
- Detail rows synced.
- Trail row **17**, ClaimType 2 or 3, Recipient=RecipientId. **This is also used for supplementary claims (it never writes 24).**

**Success:** 200 `"Clarification response submitted successfully and claim updated."`.

**Not done:** no DA-overlap check and no lock.

### 5.7 POST `api/ClaimClarification/respond-message`: text-only answer (JSON, HTTP mapped)

- **BAL:** `CCR:1059-1219`.
- **Request:** `{ClarificationId, TourPlanId, SenderId, RecipientId, the 4 *ClarificationResponse strings}`.
- **Writes:** a response row, and a trail row **17** (ClaimType 1/2/4/3) or **24** (ClaimType 7 if supplementary).
- Success: 200 `"Clarification response submitted successfully."`, `data={ClarificationResponse, ProcessDetail}`.

---

## 6. Status changes (approver, finance)

These are **not** employee actions, but the agent must interpret them.

**None of these endpoints check the caller's role or the current status** (see `claim_lifecycle.md` §4).

### 6.1 PUT `api/TourStatus/Update/TourRequestStatus?remark=`: approve / reject / transfer / send-back

- **Controller:** `TourStatusController.cs:23-28`, `[FromForm] TourRequestStatusModel {TourId:int, EmpId:int (actor), RecipientId:int (next holder), StatusId:int}` (`TourProgram.Model/Dtos/TourStatus/TourRequestStatusModel.cs:8-19`).
- **BAL:** `TSR:187-405`.

**Validations**
- 404 `"Tour id is not valid."`.
- For StatusId 15 only:
  - 400 `"You cannot amend a rejected tour request."`
  - 400 `"You cannot amend a tour that is already in finance processing."`
  - 400 `"You cannot amend a tour that has been marked as not availed."`
  - 400 `"You cannot amend this tour as claim processing has already started."`
- A duplicate tuple without IsReApproval → 400 `"Request is already submitted."`.

**Writes**
- Trail row (ClaimType 1 or 4).
- `ApprovedBy=EmpId` and `ApprovedDate=now` **for every status, including reject**.
- Approval PDF regenerated: re-approval merge if IsReApproval.
- Role grant on 6 or 14.
- **When StatusId==2 and the tour is a claim-with-request:** an extra row **3** to the unit finance user, with ClaimType 3 (or 2 with advance) (`TSR:340-381`).

**Messages**
- 201 `"Status updated successfully."`
- 201 `"Tour request approved successfully. Claim has been forwarded to finance for processing."`
- 201 `"bad request."` for a non-2 status on a claim-with-request tour

### 6.2 PUT `api/TourStatus/Update/RejectByFinance?remark=` (JSON TourRequestStatusModel)

- **BAL:** `TSR:513-683`.
- Trail row with the client status (4, or 25 for "Finance Rejection Processing"). ClaimType 3, 2, or 7 for supplementary.
- Errors:
  - Advance tour whose latest ClaimType is 2 → 400 `"Advance Tour claim Cannot be Rejected"`.
  - Duplicate → 400 `"Request is already submitted."`.
  - Tour not found → 404 `"Invalid Tour ID"`.

### 6.3 PUT `api/Claim/ApproveRaiseClaimAmount/{tourid}/{empId}/{claimApprovedAmount}/{ApproveTaxRate}` (finance approves the claim)

- **Controller:** `ClaimController.cs:251-302`. **BAL:** `CR:3650-4096`.

**Body** (`ApproveRaiseClaimModel`)
- `ApproveTransportation[{transportationId, transportationAmount, ApproveTaxRate?, FinanceRemark?}]`
- `ApproveAccommodation[…]`
- `ApproveDailyAllowanc[{dailyAllowancId, dailyAllowancAmount, FinanceRemark?}]`
- `ApproveLeave[{leaveId, leaveAmount, FinanceRemark?}]`
- `ScreenshotPath` (base64 image or PDF)

**Validations**
- 404 `"Tour id is invalid."`.
- 406 `"Approved Amount Cannot be Grater than Claimed Amount"`.
- The row-sum check: amount (+ the advance ApprovedAmount) must equal T+A+DA−leave over the **rows sent**. Otherwise 409 `"Claim Cannot Be Approved There is Some Mismatch in Data ."`.
- No row matched → 400 `"Bad request."`.

**Writes**
- Per-row approved amounts.
- `FinanceDetail` (ClaimType 2 or 3). The advance row gets IsLiquidationRequired.
- EmpTourPlan: `ClaimApprovedAmount`, `ClaimApprovedBy`, `ClaimApprovedDate`.
- Trail row **12 Process Completed** to the employee. The code is labelled "By pass finance".
- Merged claim PDF.

**Supplementary branch**
- Messages: 406 `"Supplementary Approved Amount Cannot be Greater than Supplementary Requested Amount"` and 409 `"Supplementary Claim Cannot Be Approved There is Some Mismatch in Data ."`.
- On success, writes status **21**.

**Success:** 200 `"Record updated succesfully."` (supplementary: `"Supplementary claim approved successfully."`).

### 6.4 PUT `api/TourStatus/UpdateTourReApprovalStatus` (form TourRequestStatusModel)

- **BAL:** `TSR:1055-1133`.
- Appends a row with the client status (ClaimType 1 or 4). No PDF and no EmpTourPlan change.
- Success: 201 `"Status updated successfuly."`. Duplicate: 400 `"Request is already submitted."`.

### 6.5 PUT `api/TourStatus/Update/TourRequestAmount` (form {EmpId, TourId, ApprovedAmount:int})

- **BAL:** `TSR:61-182`. This is finance approving the **advance**, or the supplementary amount.
- Writes `EmpTourPlan.ApprovedAmount` (or `SupplementaryApprovedAmount`), a `FinanceDetail` row, and trail row **9** to the employee (ClaimType 1/4, or 7).
- Same amount already present → 400 `"Request is already submitted."`.
- Success: 201 `"Request updated successfuly."`.

### 6.6 Finance bulk and role-transfer endpoints

**Bulk SAP stamping** (`BAR`)
- `GET api/BulkApproval/GetBillPassingBulkApproval/{empId}`
- `POST UploadBillPassingBulkApproval/{empId}/{recipientId}` (JSON `[{id: FinanceDetailsId, employeeId, employeeCode, sapReference, sapDate, IsLiquidation}]`): writes status **8**, or **7**/**12** on liquidation (`BAR:447-614`).
- `GET GetBankingBulkApproval/{empId}`
- `POST UploadBankingBulkApproval/{empId}` (`[{id, employeeId, employeeCode, SAPReferenceBanking?, SAPDateBanking?, PreAdvanceUTRNumber?}]`): writes status **9** (advance, no claim yet) or **12** (`BAR:616-742`).

**Role transfers**
- `PUT api/TourStatus/Update/MoveAllPendingDataFromFinance` (`{OldRecepient, NewRecepientId}`): moves rows in 3, 17 or 24. The old row is overwritten to **19** (`TSR:1139-1224`).
- `PUT api/CGMExcessiveTour/TransferApproverPendingTasks` (`{OldApproverEmpCode, NewApproverEmpCode}`, token CGM/admin only): moves rows in 1, 6 or 14. The old row is overwritten to **26** (`TSR:1261-1402`).

---

## 7. Supplementary claim

### 7.1 POST `api/Claim/RaiseSupplementaryClaim` (multipart `SupplementaryClaimDto`)

- **BAL:** `CR:6712-7150`. DTO: `TourProgram.Model/Dtos/RaiseClaimDtos/SupplementaryClaimDto.cs`.

**Fields**
- `EmpTourPlanId`
- `EmpId` (must be the owner)
- `SupplementaryRequestAmount`
- `Remark`
- `TransportationDetailDtos[i].*` and `AccomodationDetailDto[i].*`: **indexed form fields**, not JSON, each with its own upload list
- `DailyAllowanceDto` (a JSON string)
- `DaUploadDto`, `AdditionalFiles`

**Validations, in order**
1. 400 `"Bad request."`
2. 404 `"Tour plan not found."`
3. **The latest status must be 12 or 9:** otherwise 400 `"Supplementary claim can only be raised on a completed tour (ProcessCompleted or PaymentTransferred)."`
4. 409 `"A supplementary claim has already been raised for this tour. Only one supplementary is allowed per tour."`
5. 403 `"You are not authorized to raise supplementary claim on this tour."` (EmpId ≠ owner)
6. 409 `"Supplementary claim is already being processed. Please wait a moment before trying again."`
7. Lock; rows inserted with IsSupplementary.
8. 500 `"Finance user not found for the employee's unit."`
9. Trail row **20** (ClaimType 7) to the unit finance user (**the server does this lookup**).
10. DA overlap → 409.

**Success:** **201** `"Supplementary claim raised successfully."`, `data={EmpTourPlanId, SupplementaryRequestAmount, CalculatedAmount}`.

**This is the best-guarded write endpoint.**

### 7.2 GET `api/Claim/GetSupplementaryClaimDetails/{empTourPlanId}` (anonymous)

- **BAL:** `CR:7157-7330`.
- Returns the supplementary header plus `supplementaryTransportation[]`, `supplementaryAccommodation[]` and `supplementaryDA[]`.
- Not found: 404 `"Tour plan not found."` or `"No supplementary claim has been raised for this tour."`.

---

## 8. Tour not availed (cancel) and advance refund

### 8.1 POST `api/TourNotAvailed/{tourId}/{recipientId}`

- **Controller:** `TourNotAvailedController.cs:22-28`. **BAL:** `TNR:116-307`.
- **Non-advance tour:** sets `IsCanceledWithoutAdvance` and writes trail row **7** (self to self, ClaimType 4). Returns 200 `"Tour is cancelled successfully."`.
- **Advance tour with `ApprovedAmount != 0`:** sets `IsCanceledWithAdvance` and writes row **10** to `recipientId` (finance; ClaimType 6). Returns the same message.
- **Advance tour otherwise:** 400 `"This tour is not cancelled due to advance taken. And Advance is Not Approved from finance "` (trailing space is in the code).
- Tour missing: 404 `"Tour id is invalid."`.
- **No status or ownership guard.** The email is commented out.

### 8.2 POST `api/TourNotAvailed/PaymentRefund/{tourId}/{PaidAmount}/{PaymentReferenceNo}` (+ form `Files`)

- **BAL:** `TNR:33-94`.
- Sets `PaidAmount`, `PaymentReferenceNo`, `PaymentDate`; the files are stored as upload type 6.
- **No status row is written.**
- Success: 200 `"Payment updated successfully.."`.

---

## 9. Recovery (finance-only; the employee can read the status)

All recovery endpoints are on `TourRecoveryController` (BaseController + token).

**Access check**
- A missing token EmpCode → 401 `"Invalid or missing authentication token"`.
- Not a recovery user → 403 `"You are not allowed to manage tour recoveries."`.
- A recovery user is the hard-coded admin, or an employee listed as BillPass, PaymentProc or SuperBillPassing in the Organization API (`TRR:46-83`).

**Endpoints**
- `POST UploadRecovery` (`RecoveryUploadRequestDto {FileName?, Remark?, PreviewOnly=true, Rows[]}`): validates the overlap evidence and saves the valid rows as Pending (1) with `SapRfNo="AR-R-{id}"` (`TRR:89-269`).
- `GET GetRecoveryList`, `GET GetRecoveryDetails/{id}`, `GET GetRecoveryBatches`.
- `POST ReleaseRecovery` / `POST CancelRecovery` (`{RecoveryIds[], Remark}`): 1→2 and 1/2→4. Results are per row; HTTP 200, 206 or 400 (`TRR:584-797`).
- **`GET GetTourRecoveryStatus/{tourId}`**: available to any authenticated user. Returns `{tourId, hasRecovery, recoveredAmount, inProcessAmount, recoveries[{recoveryId, sapRfNo, recoverFromTour, conflictingTour, isRecoverFromThisTour, statusId, status, recoveryAmount, reason, …}]}` (`TRR:802-863`).
- The 2→3 (Recovered) transition happens only through the SAP `UpdateSapDetails` call with an `AR-R-` RFNO (`CR:893-950`).

---

## 10. Agent cookbook (recommended call sequences)

1. **"Show my tours":** `GetTourRequest/{me}?touretype=1` and `?touretype=2`. For details: `GetTourTrajectory/{id}` and `RaiseClaimRequest/{id}` (GET).
2. **"Raise a claim on approved tour X" (outside jurisdiction):**
   - Confirm X is in `GetTourRaiseClaimList/{me}`.
   - GET `RaiseClaimRequest/{X}` for the prefill.
   - Build the rows and pre-check them (bill index −1, DA dates inside the tour, ClaimAmount = sum).
   - `SaveRaiseClaimDraft`, then show the summary and confirm, then `SubmitRaiseClaimDraft/{draftId}?empId=me`. **Ensure RecipentId is set in the draft.**
   - Handle 409 (overlap or duplicate).
3. **"Raise a daily/local claim" (within jurisdiction):** `SaveClaimWithRequestDraft` (include DailyTourDateValidationJson, RecipentId = reporting manager, and resend `Existing*FileIds`), confirm, then `SubmitClaimWithRequestDraft`.
4. **"Answer finance's question":**
   - `GetTotalPendingTaskCount/{me}/USER`, then `ClaimClarification/thread/{tour}` to get the open question (its Id and SenderId).
   - For a text answer: `respond-message`.
   - For a data change: GET `RaiseClaimRequest/{tour}` for the current rows (with their Ids), modify them, then `respond` with **all** rows, the recomputed ClaimAmount, and `RecipientId` = the question's SenderId.
5. **"Fix a sent-back tour" (13):** `EditTourRequest/{id}`, which re-creates status 1. Or `SendBackToReportingManager/{id}/{mgr}/1` if nothing changed.
6. **"Edit my within-jurisdiction claim before approval":** `can-edit`, then `ClaimEdit/edit`.
7. **"Cancel tour":** `TourNotAvailed/{id}/{financeId}`. For advance tours this needs the finance recipient; **(inferred)** the frontend supplies it.

---

## Must remain in .NET

- **Overlap and DA conflict detection:** `TOV`, together with the `sp_getapplock` per-employee lock inside the write transaction. The agent may pre-check for user experience but must not replace it.
- **Duplicate-submission windows:** 30 s and 5 min (`CR:1558-1595`). The one-supplementary rule and the 12/9 precondition (`CR:6739-6763`).
- **Multi-table transactional writes:** claim rows, uploads, file storage under `wwwroot`, file-index mapping, history and edit-log snapshots, and the hash-chained AuditLog interceptors.
- **ClaimTypeId derivation and status-row insertion:** ClaimTypeId from IsAdvanceRequired, ClaimAmount and IsSupplementaryRaised; status rows 1/2/3/11/14/17/20/23/24.
- **Finance-user resolution:** `GetFinanceUserForUnit` (Organization API). Approver role grants (`AssignedTourApprovalRole`).
- **PDFs:** generation of the request, approval and merged PDFs.
- **Finance approval consistency:** the row-sum check, FinanceDetail and liquidation logic. The SAP contract (RFNO scheme, API-key endpoints); the agent must never hold the SAP key.
- **Recovery evidence engine** and its state guards.

## Reusable by the Python agent

- **Read models:**
  - `GetTourRequest`, `GetTourRaiseClaimList`, `GetTourTrajectory`, `GetTotalPendingTaskCount/USER`
  - GET `RaiseClaimRequest/{id}` (complete claim, rows, bills, approver and SAP state)
  - `ClaimClarification/thread`, `GetSupplementaryClaimDetails`, `GetTourRecoveryStatus`
  - `ClaimEdit/can-edit`
  - All `Get*Draft` endpoints
- **Write paths with draft-then-submit, which suit confirm-before-execute:**
  - Tour drafts (`SaveTourRequestDraft` → `SubmitTourRequestDraft`)
  - Outside-jurisdiction drafts (`SaveRaiseClaimDraft` → `SubmitRaiseClaimDraft`)
  - Within-jurisdiction drafts (`SaveClaimWithRequestDraft` → `SubmitClaimWithRequestDraft`)
- **Error contracts:** the overlap 409 `data=TourConflict` gives a structured conflicting tour that the agent can explain. `ValidateTourRequestDraftForSubmit` gives a required-field list.

## Gaps (agent capabilities with no API)

- **No dry-run validation.** There is no endpoint that runs the overlap, DA and duplicate checks without saving. The only way is to submit; drafts skip the validation.
- **No "missing documents" API.** The agent must infer it from GET `RaiseClaimRequest` (`bills[]` empty per row, `nominationFiles`/`advanceDocs` empty, `DocumentsAttached` not stored). **No mandatory-document policy exists in code.**
- **No DA preview or calculation.** DA `Amount` and `PercentageAdmissible` come from the client. The server never computes DA, slabs or hours. `GetGradeDetail?lavel=` and `GetAllGradeData` (`EPR:1540, 1785`) expose grade data. **(inferred)** These may hold limits; another analyst should verify.
- **No policy, entitlement or limit lookup** used for validation. Grade is stored only, and no amount caps are enforced on claims.
- **No "my pending actions" list for employees.** There is only a count (`GetTotalPendingTaskCount/USER`); the agent must filter `GetTourRequest` by statusId ∈ {11, 13, 16, 23} and recipientId == me.
- **No reporting-manager or finance-user lookup endpoint** for the employee, even though `RecipentId` must be supplied for the raise-claim, cancel and re-approval calls. `ProfileController` is commented out.
- **No server-side ClaimAmount computation.** Edit and respond write whatever is sent.
- **No token-bound identity.** The agent must enforce ownership itself.

## Unresolved questions

1. Who populates `RecipentId` for an outside-jurisdiction claim (the bill-passing user)? The frontend presumably resolves it via the Organization API's `GetAdminTourReimbursement`. Is there a backend endpoint for it? None was found in scope.
2. Is `ApproveRaiseClaimAmount` writing 12 directly (bypassing statuses 3 and 8) the intended production behaviour? Statuses 8 and 9 are still produced by the bulk and SAP endpoints.
3. Do any production clients call `UpdateTourReApprovalStatus` versus `Update/TourRequestStatus` for 14/16 decisions? Which `StatusId` values does the UI send to `RejectByFinance` (4 versus 25)?
4. Status 18 ("Pending with unit Finance") exists in the frontend list but is neither seeded nor written anywhere. Is it obsolete?
5. Are the frontend's form keys for supplementary rows (`TransportationDetailDtos[0].Source`, …) as inferred from model binding?
6. Is `IsReadOnly` ever set by the SSO server? The filter reads it but introspection never maps it.
