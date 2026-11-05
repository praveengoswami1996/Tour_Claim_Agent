# Tour API — Services & Business Logic (BAL) Catalogue

**Scope:** every repository/service in `TourProgram.BAL` (≈29k LOC), what each public method does, which controller action calls it, which entities it touches, and its side effects (email, PDF, file I/O, outbound HTTP, transactions). It then covers transactions, file storage, notifications, PDFs, outbound HTTP, error handling and code-quality risks, and ends with guidance for the Python agent.
All paths are relative to `Tour_API/`. **(inferred)** marks conclusions not stated directly in code. Secrets are given by key name only.

**Summary**
- The BAL is a set of large "fat repositories" (`ClaimRepository` alone is 8,066 lines) that inject `TourProgramDbContext` directly. `UnitOfWork`/`GenricRepository` are vestigial.
- Most business rules live inside long methods. Examples: claim save, tour overlap, the approval state machine, PDF merge, and SAP sync.
- Side effects are spread across those methods: DinkToPdf/PdfSharpCore PDFs written under `wwwroot/`, uploads under `wwwroot/UploadDocuments/*`, outbound calls to the Organization service (role assignment, hierarchy, cadre, finance users), and WhatsApp via the DMS API. SMTP email is sent in **only two live places** (ShareTourOnMail and the CGM monthly job).
- Files under `wwwroot` are served by `app.UseStaticFiles()`, which runs **before** `UseAuthentication` (`TourProgram.API/Program.cs:93-95`). **Every uploaded bill or generated PDF can be downloaded without auth if you know its URL.** OCR files are the only exception: they are stored outside wwwroot and served through an auth-checked endpoint.

---

## 1. DI registration (what is actually live)

`TourProgram.API/Extensions/ApplicationServicesExtensions.cs:16-51`. Every repository is **Scoped**. The other registrations are:
- `PDFGenerator`: scoped.
- `IConverter` (DinkToPdf `SynchronizedConverter`): singleton (`Program.cs:52`).
- `OcrQueueSignal`: singleton.
- `OcrProcessingWorker`: hosted service.
- Named HttpClient `"Ocr"`.
- `TokenService`: registered.

Not registered: `ProfileRepository` (commented out, `:31`; the whole class and `ProfileController` are commented out), `UnitOfWork`, `JwtTokenInfo`.

---

## 2. Per-class catalogue

Column legend: **Ctrl** = controller.action (route prefix `api/<Controller>`). **Entities** = DbSets touched directly (more through private helpers). **SE** = side effects (Tx = explicit DB transaction).

### 2.1 ClaimRepository (`TourProgram.BAL/Repository/Concrete/ClaimRepository.cs`, 8,066 lines)
Covers: claims for already-approved tours ("Outside Jurisdiction" / RaiseClaim), claim-with-request ("Within Jurisdiction", local/daily), three draft families, supplementary claims, finance approval and the SAP interface.
Dependencies: `IEmpTourPlanRepository`, `ICommanRepository`, `IEmployeeRepository`, … (`:42`).

| Method (line) | Ctrl | What it does | Entities | SE |
|---|---|---|---|---|
| GetPdfForSap (:53) | Claim.GetPdfForSap (header `APIKey` = `ApiKeys:Sap`) | Returns the URL of the merged approval PDF (falls back to ApprovalPDFPath) | EmpTourPlans | Null-deref bug: `empTourPlan` is used at :63 before the null check at :72 |
| GetSapDetails (:363) | Claim.GetSapDetails (AllowAnonymous + APIKey) | Lists claims/advances/recoveries ready for SAP, each with a PDF URL **(inferred)** | EmpTourPlans, TourRecoveries, VEmployeeMasters, ClaimTourDetails | – |
| UpdateSapDetails (:820) | Claim.UpdateSapDetails (AllowAnonymous + APIKey; controller checks dd.MM.yyyy dates) | SAP callback that sets the `IsSap*Acepted`/`Sap*Date` flags on the tour and marks recoveries Recovered | EmpTourPlans, TourRecoveries, TourRecoveryLogs | Can return 206 PartialContent |
| GetRaiseClaimRequest (:1093) | Claim.RaiseClaimRequest/{tourId} GET | Full claim view: transport/accommodation/DA/leave rows plus file lists (`PathUrl = baseUrl + path`) | Upload*, Transportation*, Accomodation*, DADetails, LeaveDetails, ClaimIncidents, TentativeTravelPlans | – |
| RaiseClaimRequest (:1541, 709 lines) | Claim.RaiseClaimRequest POST (multipart) | Raises a claim on an approved tour: parses JSON row arrays, saves rows, writes files by index range, adds a process entry, checks DA overlap | EmpTourPlans, Transportation/Accomodation/DA/Leave, EMPClaimProcessDetails, ClaimTourDetails, ClaimDrafts, ClaimIncidents | **Tx** (:1604), `LockEmployeeAsync` (:1608), `ValidateTourDaAsync` (:2173), file writes (not rolled back) |
| SaveClaimDraft / GetClaimDraft / GetAllClaimDrafts / DeleteClaimDraft (:2250/:2462/:2541/:2575) | Claim.SaveClaimDraft, GetClaimDraft, GetAllClaimDrafts, DeleteClaimDraft | Legacy claim-draft CRUD; files go to `wwwroot/temp-uploads/draft-{id}/{category}` | ClaimDrafts | File I/O |
| RaiseClaimWithRequest (:2758) | Claim.raise-claim-with-request (multipart) | Pre-checks DA JSON for overlap, **creates the tour** via `EmpTourPlanRepository.EmpTourRequest` (its own Tx), then saves the claim in a 2nd Tx | via `SaveClaimDataWithoutProcessEntry` | 2 separate Tx: if claim save fails, the tour stays created (:2849-2858). Returns 409 + `TourConflict` on overlap |
| ApproveRaiseClaimAmount (:3650, 448 lines) | Claim.ApproveRaiseClaimAmount PUT (controller turns a base64 screenshot into `wwwroot/UploadDocuments/{guid}.pdf`, `ClaimController.cs:251-300`) | Finance/approver approves the claimed amount per row and moves the workflow | Transportation/Accomodation/DA, FinanceDetails, UploadDocuments, EmpTourPlans | **Tx** ×2 (sync `BeginTransaction`). **PDF merge** into `wwwroot/DownloadApproval/DFCCIL_{guid}_MergeFile.pdf` → `MergeApprovalPDFPath` (:4021-4079) |
| RemoveRowClaim (:4098) | Claim.RemoveRowClaim PUT | Soft-removes claim rows of a given TourNumber (`ISRemoved`) **(inferred)** | Transportation/Accomodation/DA | – |
| GetAllClaims (:4228) | Claim.GetAllClaims/{senderId} | Paged, filtered claim list | many | – |
| ClaimsDetailsById (:4469) | Claim.ClaimsDetailsById/{tourId} | Claim header/status | EmpTourPlans, EMPClaimProcessDetails | – |
| GetClaimWithRequestDraft(empId, draftId?) (:4545), GetAllClaimWithRequestDrafts (:4633), SaveClaimWithRequestDraft (:4966), GetClaimWithRequestDraft(empId) (:5226, overload), DeleteClaimWithRequestDraft (:5298) | Claim.*ClaimWithRequestDraft* | Draft CRUD for the claim-with-request flow; files in `temp-uploads/claim-request-draft-{id}` | ClaimWithRequestDrafts | File I/O |
| SubmitClaimWithRequestDraft (:5344) | Claim.SubmitClaimWithRequestDraft/{draftId}?empId | Rebuilds `ClaimApprovalDto` from the draft, calls RaiseClaimWithRequest, copies temp files to permanent storage and links them | ClaimWithRequestDrafts, Upload* | File copy, writes `wwwroot/logs` (FileLogger) |
| SubmitRaiseClaimDraft (:5452) | Claim.SubmitRaiseClaimDraft/{draftId}?empId | Same pattern for Outside-Jurisdiction drafts (DraftType 2) → RaiseClaimRequest → `CopyRaiseClaimDraftFilesToPermanentStorage` | ClaimDrafts | Files are copied **after** the Tx has committed |
| RaiseSupplementaryClaim (:6712) | Claim.RaiseSupplementaryClaim (multipart) | One supplementary claim per completed tour, no approval | EmpTourPlans, EMPClaimProcessDetails, rows | **Tx**, lock, `ValidateTourDaAsync(checkOwnRows:false)` |
| GetSupplementaryClaimDetails (:7157) | Claim.GetSupplementaryClaimDetails (AllowAnonymous) | Supplementary rows plus files | rows, uploads | – |
| SaveRaiseClaimDraft / GetRaiseClaimDraft / GetAllRaiseClaimDrafts / DeleteRaiseClaimDraft (:7342/:7637/:7707/:7747) | Claim.*RaiseClaimDraft | Outside-Jurisdiction draft CRUD with `Retained*FileIds` (see document_and_ocr_apis.md) | ClaimDrafts, EmpTourPlans | File I/O |
| FileLogger.Log (:8041) | – | Appends to `wwwroot/logs/log_yyyyMMdd.txt` | – | **Logs are written inside the publicly served wwwroot** |

### 2.2 EmpTourPlanRepository (`…/EmpTourPlanRepository.cs`, 3,299 lines)
Covers: tour request lifecycle, grade master, tour drafts.

| Method | Ctrl | What | Entities | SE |
|---|---|---|---|---|
| GetTourDetails (:48) | TourPlan.GetTourDetails/{tourId} | Tour plus estimate files per type (Travel/Accommodation/DA/Local/Nomination) | EmpTourPlans, UploadTypeDetails, UploadDocuments, TentativeTravelPlans, ClaimTourDetails | – |
| Update (:259) | – | `NotImplementedException` | – | – |
| EmpTourRequest (:325, 503 lines) | TourPlan.TourRequest (multipart); also called by ClaimRepository.RaiseClaimWithRequest | Creates a tour request with overlap check (`FindOverlapAsync`, also per DailyTourDateValidation), estimate files, process entry, approver role, request PDF; auto-approves for ExFacto/ClaimWithRequest | EmpTourPlans, TentativeTravelPlans, EMPClaimProcessDetails, DailyTourDateValidations, Upload* | **Tx** (:332) + lock. **HTTP** org role assign, **sync `.Result`** (:316). **PDF** (`GenratePDFUser` :654, `GenratePDFApproval` :728) |
| EditEmpTourRequest (:1109) | TourPlan.EditTourRequest/{tourId} PUT (AllowAnonymous!) | Edits a pending request, re-validates overlap, replaces estimate/nomination files, regenerates the PDF | same | **Tx**, lock, HTTP, PDF |
| GetGradeData / GetAllGradeData / CreateGradeData / Edit / GradeDelete / UpdateDeviceAmountReimbursement (:1540/:1785/:1727/:1798/:1773/:1830) | TourPlan.GetGradeDetail, GetAllGradeData (anon), CreateGradeData, PUT {id}, GradeDelete, UpdateGradeReimbursement/{level} | Grade/entitlement master CRUD | GradeDetails | Tx |
| EmpTourApprovedAmount (:1570) | – (no controller) | Sets the approved amount | EmpTourPlans | Tx |
| SendBackToEmployee (:1861) / SendBackToReportingManager (:1912) | TourPlan.SendBackTo* | Workflow send-back with remark | EmpTourPlans (+process) | Tx, HTTP role assign |
| TourReApprovalRequest (:1962) | TourPlan.TourReApprovalRequest/{tourId} (multipart) | Re-approval of an approved tour with changed dates; snapshot into *ReApproval tables | EmpTourPlans, TentativeTravelPlans, ClaimTourDetails | Tx, lock, overlap, HTTP |
| GetTourOldApprovalDetails (:2156) | TourPlan.GetTourOldApprovalDetails | Pre-re-approval snapshot | TentativeTravelPlanReApproval, ClaimTourDetailsReApproval | – |
| GetOwnArrangmentAmount / UpdateOwnArrangmentAmount (:2328/:2347) | TourPlan.* | Own-arrangement lodging rate master | OwnArrangment | – |
| SaveTourRequestDraft / SubmitTourRequestDraft / GetTourRequestDraft / GetAllTourRequestDrafts / DeleteTourRequestDraft (:2376/:2583/:2826/:2892/:2945) | TourPlan.*TourRequestDraft (Submit is AllowAnonymous) | Draft CRUD; on submit, moves temp files to `UploadDocuments/{TourTravelEstimate…}` | TourRequestDrafts, EmpTourPlans | File move/delete |

Private helpers worth knowing:
- `AssignedTourApprovalRole` (:301): POST `{Organization}/api/AdminManage/CreateRolesandPermssion` with a hard-coded roleId GUID.
- `GetAdminTourReimbursementData` (:866): GET `{Organization}/api/AdminManage/GetAdminTourReimbursement`.
- `GenratePDFUser` (:903) → `wwwroot/DownloadUserPdf/{empName}{guid}_GeneratedFileUser.pdf` → `RequestPdfPath`.
- `GenratePDFApproval` (:1009) → `wwwroot/DownloadApproval/{empName}{guid}_GeneratedFile.pdf` → `ApprovalPDFPath`.

### 2.3 TourStatusRepository (`…/TourStatusRepository.cs`, 1,403 lines) — approval state machine

| Method | Ctrl | What | SE |
|---|---|---|---|
| UpdateTourRequestAmount (:61) | TourStatus.Update/TourRequestAmount PUT | Approver amends the advance amount | Tx, FinanceDetails |
| UpdateTourRequestStatus (:187) | TourStatus.Update/TourRequestStatus PUT | Approve/reject/transfer/forward a request. Guards: rejected, in finance, not-availed. On approval: PDF approval or re-approval, role assignment on Transfer/ReApprovalRequest, finance-user routing | Tx (:290). **PDF** (:327/:331). **HTTP** (:337/:373) |
| RejectByFinance (:513) | TourStatus.Update/RejectByFinance | Finance rejection with guards | Tx |
| UpdateReApprovedTourRequestStatus (:684) | **not exposed** by any controller | Re-approval status plus PDF | Tx, PDF |
| UpdateTourReApprovalStatus (:1055) | TourStatus.UpdateTourReApprovalStatus | Approve/reject a re-approval | Tx |
| MoveAllPendingDataFromFinance (:1139) | TourStatus.Update/MoveAllPendingDataFromFinance | Bulk-reassign pending finance items from old to new recipient | EMPClaimProcessDetails |
| TransferApproverPendingTasks (:1261) | CGMExcessiveTour.TransferApproverPendingTasks PUT | CGM/Admin moves an approver's pending tasks | EMPClaimProcessDetails |

Private helpers:
- `GenratePDFReApproval` (:893): merges the new and old approval PDFs into `DownloadApproval/DFCCIL_{guid}_GeneratedReApprovalMergeFile.pdf`.
- `SendCGMExcessiveTourNotification` (:1231): email + WhatsApp to the CGM. **No caller found (dead code).**

### 2.4 ClaimClarificationRepository (`…/ClaimClarificationRepository.cs`, 2,294 lines)

| Method | Ctrl | What | SE |
|---|---|---|---|
| Create (:37) | ClaimClarification POST | Approver/finance raises a clarification request; workflow → clarification status | Tx (sync) |
| GetClarification (:182) | GET {tourid} | List of clarifications for a tour | – |
| GetClarificationThread (:243) | GET thread/{tourPlanId} | Q&A thread | – |
| GetClarificationWithHistory (:314) | GET detail/{clarificationId} | One clarification plus snapshots | – |
| CompareClarificationData (:448) | GET compare/{tourPlanId}/{clarificationId} | Current claim rows vs *History snapshot | *History tables |
| GetClarificationHistory (:563) | GET history/{tourPlanId} | Snapshots including `Transportation/AccomodationUploadHistories` | – |
| RespondToClarificationMessage (:1059) | POST respond-message (JSON) | Text-only reply | Tx |
| RespondToClarification (:1972) | POST respond (multipart) | Employee resubmits a modified claim (rows + files + `Deleted*Ids`) and snapshots history | Tx, file writes, hard deletes |

### 2.5 ClaimEditRepository (`…/ClaimEditRepository.cs`, 1,636 lines)

| Method | Ctrl | What | SE |
|---|---|---|---|
| EditClaimWithRequest (:43) | ClaimEdit.edit POST (multipart, **AllowAnonymous**) | Edits a pending claim-with-request. Ownership is checked by comparing body `EmpId` to the tour owner (:69). Writes a `ClaimEditLogs` snapshot | Tx, lock, DA overlap, file writes, hard deletes |
| GetEditHistory (:293) / GetEditLogDetails (:348) / CompareWithHistoricalVersion (:421) | ClaimEdit.history/log/compare | Audit views | ClaimEditLogs |
| CanEditTour (:515) | ClaimEdit.can-edit/{id}?empId | Editability rule check | – |

### 2.6 TourListingRepository (`…/TourListingRepository.cs`, 3,481 lines) — read-only work queues

These methods only read data and have no side effects:
- GetTourRequest (:1236), GetRecipientTourRequest (:1432), GetRecipientTourRequestPendingSummary (:1599, anon), GetRecipientTourRequestByUser (:1761, anon).
- GetBillPassingList (:36), GetBillPassingPendingSummary (:251, anon), GetRaiseClaimBillPassingList (:568, anon), GetClearificationBillPassingList (:805, anon), GetRaiseClaimBillPassingPendingSummary (:3052, anon), GetUserBillPassingDetails (:3223, anon).
- GetTourRaiseClaimList (:1033), GetMyActionList (:2166), GetTotalPendingTaskCount (:2515, anon).
- GetEmployeesOnTourByDate (:2640, anon), GetEmployeesOnTourByDateRange (:2894, anon).
- GetTourTrajectoryAsync (:462, anon).

Each maps 1:1 to the `TourListing.*` action of the same name (`TourListingController.cs:25-296`). They read EmpTourPlans, EMPClaimProcessDetails, ClaimStatusCategorys, ClaimTypes, FinanceDetails, TentativeTravelPlans and VEmployeeMasters. Many are **AllowAnonymous** and take the `recipientId`/`empId` from the route.

### 2.7 DashboardRepository (`…/DashboardRepository.cs`, 1,542 lines) — read-only aggregates

These methods aggregate counts and amounts by status, month and type. They read EmpTourPlans, EMPClaimProcessDetails, rows and ClaimStatusCategorys, and have no side effects:
- GetDashboardDetails (:30) → `Dashboard.GetDashboardDetails/{year}`.
- GetUserDashboard (:163) → `Dashboard.user` POST, `user/{id}`, `user/current/{id}`, `user/{id}/months/{m}`.
- GetAdminDashboard (:460) → `admin` POST/GET, `admin/current`, `admin/months/{m}`.
- GetApproverDashboard (:303), GetApproverDashboardUserWiseSummary (:817), GetApproverDashboardByUser (:1014), GetRecipientDashboardUserWiseSummary (:1165), GetRecipientDashboardByUser (:1385) → `Approver*`/`Recipient*` actions, **all AllowAnonymous**.

### 2.8 Other repositories

| Class / method | Ctrl | What | Entities | SE |
|---|---|---|---|---|
| **BulkApprovalRepository** GetBillPassingBulkApproval (:31) / GetBankingBulkApproval (:259) | BulkApproval.Get* | Export lists for bill-passing and banking | EmpTourPlans, FinanceDetails, … | – |
| UploadBillPassingBulkApproval (:447) / UploadBankingBulkApproval (:616) | BulkApproval.Upload* (JSON list) | Bulk-apply approval or banking results | EmpTourPlans, FinanceDetails | Tx (sync) |
| **TourNotAvailedRepostitory** TourNotAvailed (:116) | TourNotAvailed POST {tourId}/{recipientId} | Marks the tour cancelled/not availed. Builds the `TourCancelEmailTemplate` HTML (:219), but the **send is commented out** (:221-227) | EmpTourPlans, ClaimTourDetails, FinanceDetails | Tx |
| PaymentRefund (:33) | TourNotAvailed.PaymentRefund/{tourId}/{amt}/{ref} (multipart `Files`) | Records the advance refund and stores bills as type 6 | EmpTourPlans, UploadTypeDetails, UploadDocuments | File write, **no Tx** |
| **TourRecoveryRepository** IsRecoveryUserAsync (:46) | TourRecovery (guard, :137) | Hard-coded admin EmpCode **or** BillPass/PaymentProc/SuperBillPassing user from Org API | – | HTTP |
| UploadRecovery (:89) | TourRecovery.UploadRecovery (JSON rows, `PreviewOnly`) | Validates and imports recovery rows into a batch | TourRecoveries, *Details, *Logs, *Batches | Tx |
| GetRecoveryList (:274) / GetRecoveryDetails (:453) / GetRecoveryBatches (:865) / GetTourRecoveryStatus (:802) | TourRecovery.* | Read | – | – |
| ReleaseRecovery (:584) / CancelRecovery (:695) | TourRecovery.* | Status change with mandatory remark | TourRecoveries, Logs | Tx |
| **CGMExcessiveTourRepository** GetExcessiveTourReport (:87) | CGMExcessiveTour.GetExcessiveTourReport GET/POST | Days on tour per employee (grade < `MaxGrade`, default "E2") for CGM units | EmpTourPlans, … | HTTP cadre |
| CheckCGMAuthorization (:392) / GetAllUnitCGMs (:496) | CGMExcessiveTour.CheckAuthorization / GetAllUnitCGMs | CGM-of-unit check via Org API `MobileVisibility/GetCadreAllotementsAsync` | – | HTTP |
| SendMonthlyExcessiveTourNotifications (:566) | CGMExcessiveTour.SendMonthlyNotifications (anon + header `apiKey` = `APIKEYS:MonthlyNotification`) | Monthly digest to every unit CGM | – | **Email + WhatsApp** |
| GetEmployeeTourSummaryByMonth (:700) | CGMExcessiveTour.GetEmployeeTourSummary | Per-employee monthly tour summary | – | – |
| **NotificationRepository** SendNotification (:30) / SendOtp (:127) | used by AccountRepository.ShareTourOnMail | MailKit SMTP (`MailSettings:*`), HTML body + attachments, `SecureSocketOptions.None` | – | **SMTP** |
| SendEmailToCGM (:162) / SendWhatsAppToCGM (:326) / SendMonthlyEmailToCGM (:503) / SendMonthlyWhatsAppToCGM (:415) | CGM job / dead helper | Inline HTML; WhatsApp via `{Whatsapp}/api/NotificationManage/SendWhatsAppMessageNew`. In UAT the recipient is overridden to a hard-coded test mailbox (:181) | – | SMTP / HTTP |
| **AccountRepository** ShareTourOnMail (:226) | Account.ShareTourOnMail | Emails the tour's approval PDF to any `ReveiverEmail` using `wwwroot/email-templates/TourMailTemplate.html`. **No ownership check** | EmpTourPlans | SMTP, file read |
| SendWhatsAppMessageAsync (:88) | Account.SendWhatsAppMessage (**AllowAnonymous**) | Sends the tour/approval-PDF link over WhatsApp | EmpTourPlans | HTTP DMS |
| GetEmployeeDetailsAsync (:285) / GetUserRolesAsync (:297) | Account.profile, user-roles/{empCode} | Employee master and roles (`SuperAdmin` config key) | Roles, UserRoles | – |
| **EmployeeRepository** GetEmployeeDetails (:45) / GetEmployeeDetailsWithEmpCode (:69) | used everywhere | Reads the `VEmployeeMasters` view | VEmployeeMasters | – |
| GetEmployeeList (:94) / GetCGMForUnit (:122) / GetFinanceUserForUnit (:166) | internal | Org hierarchy / cadre / finance-unit lookups | – | HTTP (IHttpClientFactory) |
| **OcrRepository** (8 methods) | Ocr.* | See document_and_ocr_apis.md | OcrDocuments | File I/O, queue signal |
| **UserExtensionRepository** (`Service/`) SeedUserExtension (:36) / GetUserExtensionByEmpCodes (:124) / GetAllUserExtensions (:188) | UserExtension.Seed / GetByEmpCodes / GetAll | Per-user tour-extension months (`TourExtensionSettings:DefaultExtensionMonths`) | UserExtensions | Tx |
| **TentativeTravelPlanRepository** / **ActualTravelPlanRepository** Create, Get (Update/Delete throw NotImplemented) | TentativeTravelPlan/ActualTravelPlan.Create, {id} | Legacy row CRUD | TentativeTravelPlans / ActualTravelPlans | Tx |
| **CommanRepository** UploadDocumets (:14) | internal | Writes each non-empty file to `wwwroot/UploadDocuments/{folder}/{guid}{ext}` and returns `{filePath (wwwroot stripped), fileName}` | – | Disk |
| **ProfileRepository** | – | Entirely commented out | – | – |
| **GenricRepository<T>** (`Repository/GenricRepository.cs`) | base class of most repositories | Add/GetAll/GetFirstOrDefault/Remove. Its methods are never called; subclasses use `_dbContext` directly | – | – |
| **UnitOfWork** (`Repository/UnitOfWork.cs`) | – | `Save()` → `SaveChanges()`. **Not registered or used** | – | – |
| **TokenService** (`Service/TokenService.cs:19`) | – | Legacy HMAC JWT (`TokenKey`, multi-year expiry). `CrateToken` has **no callers** | – | – |
| **JwtTokenInfo** (`Service/JwtTokenInfo.cs`) | – | Reads claims without validation. Unused | – | – |
| **OcrProcessingService** / **OcrQueueSignal** / **OcrClock** | worker | See document_and_ocr_apis.md | OcrDocuments | HTTP OCR |
| **TourOverlapValidator** (`Helpers/Touroverlapvalidator.cs`) | static helper | `LockEmployeeAsync` (`sp_getapplock` per employee, 20 s, :49-58). `FindOverlapAsync` (request dates vs other tours). `ValidateTourDaAsync` / `ValidateDaJsonAsync` (DA windows must not overlap other tours). Returns `TourConflict{TourId,From,To,Message}` → HTTP 409 | EmpTourPlans, DADetails, DailyTourDateValidations | Raw SQL |

---

## 3. Transactions & UnitOfWork
- `UnitOfWork`/`IUnitOfWork` are dead (not registered, no consumers). Each repository opens transactions itself through `_dbContext.Database.BeginTransaction[Async]` (≈35 sites, see the method tables). It mixes sync `BeginTransaction()`/`Commit()` (ClaimRepository :3677/:3811, BulkApproval, TourNotAvailed, EmpTourPlan) with async.
- **The overlap lock pattern**: Tx → `LockEmployeeAsync` → save rows → `ValidateTourDaAsync` → Commit/Rollback. It is used by EmpTourRequest, EditEmpTourRequest, TourReApprovalRequest, RaiseClaimRequest, RaiseClaimWithRequest, RaiseSupplementaryClaim and EditClaimWithRequest.
- **Non-atomic flows:**
  - RaiseClaimWithRequest creates the tour and the claim in two Txs (`ClaimRepository.cs:2826-2858`).
  - Draft submits mark `IsSubmitted`, call raise, then copy files *after* commit (:5452-5530).
  - PaymentRefund has no Tx.
  - File writes are never undone on rollback, which leaves orphan files.
- **Audit:** the DAL adds `AuditSaveChangesInterceptor` + `AuditTransactionInterceptor`, which write a hash-chained `AuditLog` and record writes only after commit (`Program.cs:40-51`).

## 4. File storage
| Area | Path (under content root) | Naming | Written by |
|---|---|---|---|
| Claim/tour bills | `wwwroot/UploadDocuments/{Transportation\|Accomodation\|Additional\|Accident\|AdvancePaymentBill\|DA\|Nomination\|TourTravelEstimate\|TourAccommodationEstimate\|TourDaEstimate\|TourLocalEstimate\|ManualApprovalFile\|TourNotAviledRefundPaymentBill}/` | `{Guid}{original ext}` | `CommanRepository.cs:23-36`, draft move (`EmpTourPlanRepository.cs:3084-3109`, `ClaimRepository.cs:6526-6538`) |
| Draft temp files | `wwwroot/temp-uploads/{draft-\|claim-request-draft-\|tour-draft-}{draftId}/{category}/` | GUID | `ClaimRepository.cs:4715, 4783, 7808`; `EmpTourPlanRepository.cs:3253` |
| ClaimWithRequest permanent (alt) | `wwwroot/uploads/{empTourPlanId}/{category}/` | – | `ClaimRepository.cs:2631` (`MoveDraftFilesToPermanent`) |
| Approval screenshot | `wwwroot/UploadDocuments/{guid}.pdf` | – | `ClaimController.cs:263-293` |
| Generated PDFs | `wwwroot/DownloadUserPdf/`, `wwwroot/DownloadApproval/` | `{empName}{guid}_Generated*.pdf`, `DFCCIL_{guid}_*Merge*.pdf` (the name contains the employee name) | §6 |
| App logs | `wwwroot/logs/log_yyyyMMdd.txt` | – | `ClaimRepository.cs:8045` |
| OCR | `{Ocr:StoragePath}/{empCode}/{guid}{ext}` (default `OcrUploads`, **outside wwwroot**) | – | `OcrRepository.cs:52-67` |
| Misc | `TourProgram.API/GeneratedPDFs/` (one static sample), `wwwroot/email-templates/`, `wwwroot/Style/PdfStyleSheet.css` | – | – |

**Serving:**
- Path columns (`UploadDocument.PathUrl`, `TransportationUpload.FilePath`, `ApprovalPDFPath`, …) store paths relative to wwwroot. Read APIs return `baseUrl + path`, e.g. `ClaimRepository.cs:1405`.
- The browser fetches them through `app.UseStaticFiles()` (`Program.cs:93`), which runs before authentication. **There is no auth or ownership check on any wwwroot file.** The only protection is that the GUID file names are hard to guess. That covers bills, approval PDFs and `wwwroot/logs`.
- The one auth-checked file endpoint is `GET api/Ocr/GetFile/{id}`.
- No storage config key exists except `Ocr:StoragePath`; the paths are relative to the working directory.

## 5. Email / notification
| When | Code | Channel |
|---|---|---|
| User shares a tour | `AccountRepository.ShareTourOnMail` (:226) → `NotificationRepository.SendNotification` | SMTP (MailKit, `MailSettings:Server/Port/SenderEmail/UserName/Password`), with the approval PDF attached |
| Tour WhatsApp share | `AccountRepository.SendWhatsAppMessageAsync` (:88) | DMS API `ApiBaseUrls{Prod\|Dfcuat}:Whatsapp` |
| Monthly CGM digest (external scheduler calls the endpoint) | `CGMExcessiveTourRepository.SendMonthlyExcessiveTourNotifications` (:566) | SMTP + WhatsApp |
| Tour not availed | `TourCancelEmailTemplate.GetHTMLString` is built (`TourNotAvailedRepostitory.cs:219`) but **the send is commented out** | none |
| Per-approval CGM alert | `TourStatusRepository.SendCGMExcessiveTourNotification` (:1231) | **dead code** |

There are no emails on submit, approve, reject or clarification, and the backend has no in-app notification store **(inferred from the absence of callers)**.

## 6. PDF generation
| Document | Engine | Trigger | Output field |
|---|---|---|---|
| Tour request PDF (user copy) | DinkToPdf (wkhtmltopdf) + `PdfTemplate.GetHTMLStringdummy` (`Utility/PdfUtility/PDFGenerator.cs`) | EmpTourRequest / EditEmpTourRequest (`EmpTourPlanRepository.cs:654, 1428`) | `EmpTourPlan.RequestPdfPath` |
| Tour approval PDF | DinkToPdf + `PdfTemplate.GetHTMLString` | Auto-approve in EmpTourRequest (:728). Approve in `TourStatusRepository.UpdateTourRequestStatus` (:331) | `ApprovalPDFPath` |
| Re-approval PDF | DinkToPdf + PdfSharpCore merge with the old approval | `TourStatusRepository` :327, :755 | `ApprovalPDFPath` (merged) |
| Claim approval bundle | PdfSharpCore: approval PDF + screenshot + bills (images scaled to A4) | `ClaimRepository.ApproveRaiseClaimAmount` (:4021-4079) | `MergeApprovalPDFPath` (sent to SAP by GetPdfForSap) |
| Dev test | `PDFGeneratordummy.DummyPDFgenerator` (AllowAnonymous) | – | – |

Notes:
- iTextSharp is referenced but unused (its usings are commented out).
- **Bug:** the merge adds `TransportQuery` twice and never adds accommodation bills (`ClaimRepository.cs:4064`).
- The merge only includes `.pdf/.jpg/.jpeg/.png/.tiff/.psd`. Other types are skipped silently (:4168).

## 7. External HTTP calls (key names only)
| Target | Config key | Endpoint | Caller |
|---|---|---|---|
| Organization svc | `ApiBaseUrlsProd:Organization` / `ApiBaseUrlsDfcuat:Organization` / `ApiBaseUrlsCetpauat:Organization`, plus static `DomainDetails.apiBaseUrlOrganization` | `/api/AdminManage/CreateRolesandPermssion` (POST), `/api/AdminManage/GetAdminTourReimbursement`, `/api/Organization/GetOrganizationHierarchy/`, `/api/MobileVisibility/GetCadreAllotementsAsync` | EmpTourPlan, TourStatus, Employee, CGM, TourRecovery repos |
| SSO introspection | `ApiBaseUrls*:SSO` | token introspection (sync `.Result`) | `ActionFilter/SSOTokenValidateFilter.cs` (global filter) |
| WhatsApp (DMS) | `ApiBaseUrls{Prod\|Dfcuat}:Whatsapp` | `/api/NotificationManage/SendWhatsAppMessageNew` | Account, Notification |
| OCR provider | `Ocr:BaseUrl`, `Ocr:ExtractPath`, `Ocr:ApiKey` (`x-api-key`) | POST multipart `files` | OcrProcessingService |
| Inbound from SAP | `ApiKeys:Sap` (header `APIKey`) | GetSapDetails / UpdateSapDetails / GetPdfForSap | ClaimController |
| Inbound scheduler | `APIKEYS:MonthlyNotification` (header `apiKey`) | SendMonthlyNotifications | CGMExcessiveTourController |

Upastithi appears only as a CORS origin. **No Upastithi API call was found in the BAL.** The `AttendenceId` field on transport rows suggests the frontend links attendance **(inferred)**.

## 8. Error-handling patterns
- **Envelope:** `ResponeModel {statusCode, message, data, dataLength, pageNumber, pageSize, totalRecords, error, errorDetail}` (`TourProgram.Model/Dtos/Response/ResponeModel.cs:10`).
- **HTTP status vs envelope:**
  - Most actions return `Task<ResponeModel>`, so the HTTP status is **always 200** and the real status is in `statusCode` (Claim, TourPlan, TourListing, TourStatus, Clarification, BulkApproval, …).
  - Ocr, TourRecovery, Dashboard, CGMExcessiveTour, ClaimEdit.edit and raise-claim-with-request map it with `StatusCode((int)r.StatusCode, r)`.
  - **The agent must always check `statusCode` and `error` in the body.**
- **Repository catch blocks:** they usually return `statusCode 500` with `message = "Error: " + ex.Message`, which leaks internal details. Some swallow errors (`catch {}`, ≈15), and 18 use `Console.WriteLine`.
- **Unhandled exceptions:** `TourProgram.Logger/ExeceptionMiddleware.cs` returns `{statuCode, message, details}`. Note the misspelled `statuCode` and that `details` is the inner exception in every environment. It logs through `ILogger`.
- **Auth failures:** the filter returns `{message:"User is unauthorized.", statusCode:401, timestamp}`, and 403 for read-only users on POST/PUT/DELETE/PATCH.
- **Serilog:** packages and an `appsettings` `Serilog` section exist, but **`UseSerilog`/`ReadFrom.Configuration` is never called**, so the file sink is inactive **(inferred)**. Logging goes to the default `ILogger` plus the ad-hoc `FileLogger`.
- **Conflict semantics:** 409 with `data = TourConflict` (overlap). 409 on OCR Retry concurrency. `sp_getapplock` timeout throws SQL error 50001, which surfaces as 500.

## 9. Code-quality risks
| Risk | Evidence |
|---|---|
| God methods | RaiseClaimRequest 709 lines. CopyDraftFilesToPermanentStorage 529. EmpTourRequest 503. 20+ methods over 250 lines |
| Duplicated logic | `UploadTansportationFile`/`UploadBillFiles` are copied into 5 repositories. `MergePdfFile`/`MergerImageToPdf` exist in both ClaimRepository and TourStatusRepository. `GetAdminTourReimbursementData` has 3 copies. Three separate draft systems |
| Copy-paste bugs | `AccomodationDoc = TransportQuery.ToList()` (`ClaimRepository.cs:4064`). Null-deref in GetPdfForSap (:63). Only the first `UploadTypeDetail` per type is read (`:1400`), so later batches may be hidden **(inferred)** |
| Raw SQL | `sp_getapplock` (`Touroverlapvalidator.cs:56`, parameterised). OCR claim CTE (`OcrProcessingService.cs:30-40`, parameterised). No string-concatenated SQL found |
| Sync-over-async | `client.PostAsync(...).Result` (`EmpTourPlanRepository.cs:316`). SSO introspection `.Result` on every request. Sync `SaveChanges()` (29 sites) and sync `BeginTransaction` |
| Magic numbers | Hard-coded admin EmpCode (`TourRecoveryRepository.cs:20`, `CGMExcessiveTourRepository.cs:22`; value redacted). Role GUID (`TourStatusRepository.cs:1000`). `DraftType == 2`. `MaxGrade` "E2". `portalId = 1`. UAT test-mailbox overrides |
| Trusting client identity | Most methods take `EmpId`/`senderId`/`recipientId` from the body, route or query instead of the token (e.g. `SubmitRaiseClaimDraft(draftId, empId)`, `EditClaimWithRequest` ownership = body EmpId). Many listing/dashboard/edit endpoints are AllowAnonymous. `Deleted*Ids` delete any upload row by id without scoping it to the tour (`ClaimEditRepository.cs:902-953`) |
| Files | No server-side type or size validation for claim uploads. Original extension is preserved. Public static serving. No cleanup of orphaned/temp files. Logs inside wwwroot |
| Security/config | Secrets live in `appsettings.json` (connection strings, SMTP, OCR, SAP, TokenKey, OtpCode). SMTP without TLS. Swagger is exposed in all environments (basic-auth middleware) |
| Time | Server-local `DateTime.Now` everywhere. OCR uses `UtcNow+5:30` (`OcrClock`) |

---

## 10. Logic that must remain in .NET
- Claim/tour persistence and the **approval state machine**: TourStatus, ClaimRepository.ApproveRaiseClaimAmount, BulkApproval, Clarification, Recovery and the SAP interface.
- **Overlap/DA conflict rules and the per-employee lock** (`TourOverlapValidator`). These must run inside the save transaction.
- **Entitlement math** (grade/DA/own-arrangement masters) and the amounts finance approves.
- PDF generation and merge (wkhtmltopdf native libraries, approval PDFs that SAP consumes). Role assignment in the Org service. The audit-log chain.
- OCR queueing and the provider key, which must never reach the agent.

## 11. Logic the agent can reuse via existing APIs
| Need | Endpoint(s) |
|---|---|
| Read tours, queues and status | `TourListing/GetTourRequest/{empId}`, `GetTourRaiseClaimList/{empId}`, `GetMyActionList/{empId}`, `TourPlan/GetTourDetails/{id}`, `Claim/RaiseClaimRequest/{tourId}` (GET), `Claim/ClaimsDetailsById/{id}` |
| Entitlements | `TourPlan/GetGradeDetail?lavel=`, `GetAllGradeData`, `GetOwnArrangmentAmount` |
| Draft-first claim building (safest for an agent) | `Claim/SaveRaiseClaimDraft` / `SaveClaimWithRequestDraft` → user reviews in the UI → `Submit*Draft` |
| Direct submit | `Claim/RaiseClaimRequest`, `raise-claim-with-request` (multipart). Check `statusCode` (409 = overlap) |
| Clarifications | `ClaimClarification/thread/{id}`, `respond`, `respond-message` |
| OCR | `api/Ocr/*` with the user's bearer token (see document_and_ocr_apis.md) |
| Edit / history | `ClaimEdit/can-edit`, `history`, `compare` |

## 12. Unresolved questions
1. Should approval and status changes trigger email/WhatsApp? Today none do; the TourNotAvailed send is commented out.
2. Is the `wwwroot` public file exposure intentional, e.g. a reverse proxy that adds auth? Nothing in code protects it.
3. Which of the three draft systems (ClaimDraft type 1, ClaimDraft type 2 "RaiseClaim", ClaimWithRequestDraft, plus TourRequestDraft) is canonical for the current frontend?
4. Is `UpdateReApprovedTourRequestStatus` intentionally unexposed?
5. Is the accommodation-bill omission in the claim merge PDF (`ClaimRepository.cs:4064`) a known defect affecting SAP?
6. Which scheduler calls `SendMonthlyNotifications`?
7. Is Serilog meant to be active?
8. Will endpoints keep trusting body/route `empId`, or move to the token `UserId`? This matters for any agent acting on a user's behalf.
