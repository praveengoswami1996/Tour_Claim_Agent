# Backend Folder Structure — Tour_API

**Summary.** Solution `TourProgram.APISolution.sln` with 6 net6.0 projects (API, BAL, DAL, Model, Utility, Logger). ~59k lines of hand-written C# excluding migrations; the weight sits in `TourProgram.BAL/Repository/Concrete` (≈27k lines, `ClaimRepository.cs` alone 8,066). DAL holds ~50 EF entity classes in 41 files and 51 migrations (≈64k generated lines). `wwwroot` contains runtime-generated/uploaded files committed to the repo. Line counts below are `wc -l` of the current files; purposes marked **(inferred)** are from names/first lines only. bin/obj excluded.

## Root

```
Tour_API/
├── TourProgram.APISolution.sln         (55)  6 projects
├── .gitignore                          (488)
├── SupplementaryClaimDto.cs            (8)   stray file outside any project (inferred: leftover)
├── docs/OCR_Frontend_Integration.md    (476) OCR integration notes for frontend
├── TourProgram.API/
├── TourProgram.BAL/
├── TourProgram.DAL/
├── TourProgram.Model/
├── TourProgram.Utility/
└── TourProgram.Logger/
```

## TourProgram.API (web host)

| Path | Lines | Purpose |
|---|---|---|
| Program.cs | 100 | Host build, DB/audit wiring, global SSO filter, middleware pipeline |
| TourProgram.API.csproj | 46 | Packages: JwtBearer, EF Core, Serilog, Swashbuckle |
| appsettings.json / appsettings.Development.json | 151 / 8 | Config (keys in backend_architecture.md §9; contains secrets — redacted) |
| Properties/launchSettings.json | 31 | Kestrel 7010/5156, IIS Express |
| .config/dotnet-tools.json | 11 | dotnet local tools (inferred: dotnet-ef) |
| ActionFilter/SSOTokenValidateFilter.cs | 341 | Global `IAuthorizationFilter`: SSO token introspection, read-only check; also `IntrospectionResponse` DTO |
| BackgroundServices/OcrProcessingWorker.cs | 70 | Hosted service processing `OcrDocuments` queue |
| Extensions/ApplicationServicesExtensions.cs | 103 | DI for repositories, OCR, Swagger, CORS |
| Extensions/IdentityServicesExtension.cs | 63 | JwtBearer + Identity registration — **not called** |
| Extensions/AuditUserProvider.cs | 38 | Supplies (name, UserId) from JWT to audit interceptor |
| Model/SwaggerBasicAuthMiddleware.cs | 47 | Basic-auth gate for `/swagger` |
| wwwroot/ | ~428 files | Static: `email-templates/` (1), `Style/` (1 PDF CSS), `Images/` (1), `Developerdocument/` (1 .docx), and runtime data: `UploadDocuments/` (170, incl. subfolders Accomodation, Additional, AdvancePaymentBill, ManualApprovalFile, Nomination, TourAccommodationEstimate, TourNotAviledRefundPaymentBill, TourTravelEstimate, Transportation), `DownloadApproval/` (68 approval PDFs), `DownloadFolder/` (121), `DownloadUserPdf/` (6), `temp-uploads/` (59 draft uploads) |

### Controllers (`TourProgram.API/Controllers`, 19 files, 3,509 lines)

| File | Lines | Base | Purpose |
|---|---|---|---|
| AccountController.cs | 153 | ControllerBase | Share tour by mail/WhatsApp, profile, user roles |
| ActualTravelPlanController.cs | 40 | ControllerBase | CRUD for actual travel legs |
| BaseController.cs | 62 | ControllerBase | Parses JWT claims into `TokenDetails`, reads `DeviceType` |
| BulkApprovalController.cs | 62 | ControllerBase | Bill-passing / banking bulk approval lists & uploads |
| CGMExcessiveTourController.cs | 475 | BaseController | CGM excessive-tour report, CGM list, employee tour summary, approver task transfer, monthly cron notification |
| ClaimClarificationController.cs | 120 | ControllerBase | Finance clarification raise/respond/thread/history/compare |
| ClaimController.cs | 555 | ControllerBase | Raise claim, claim-with-request, drafts, supplementary claim, approve amounts, SAP in/out |
| ClaimEditController.cs | 97 | ControllerBase | Edit submitted claim, can-edit, edit history/compare |
| DashboardController.cs | 744 | ControllerBase | User/admin/approver/recipient dashboards |
| OcrController.cs | 164 | BaseController | OCR upload, list, get, mark-read, retry, cancel, release, file download |
| PDFGeneratordummy.cs | 89 | ControllerBase | Test PDF generator endpoint (AllowAnonymous) |
| ProfileController.cs | 39 | — | **Fully commented out** (last-login/logout) |
| TentativeTravelPlanController.cs | 46 | ControllerBase | CRUD for tentative travel plan |
| TourListingController.cs | 301 | ControllerBase | Employee/approver/finance listings, pending counts, trajectory, employees-on-tour |
| TourNotAvailedController.cs | 41 | ControllerBase | Mark tour not availed, refund payment upload |
| TourPlanController.cs | 251 | ControllerBase | Tour request create/edit/re-approval, send-back, grade master, own-arrangement amount, tour drafts |
| TourRecoveryController.cs | 149 | BaseController | Finance recovery upload/list/release/cancel/batches |
| TourStatusController.cs | 59 | ControllerBase | Approve/reject/status transitions, finance reject, amount update, finance role transfer |
| UserExtensionController.cs | 62 | BaseController | Tour-raise extension overrides (seed/get) |

## TourProgram.BAL (business logic)

| Path | Lines | Purpose |
|---|---|---|
| TourProgram.BAL.csproj | 22 | MailKit, Identity 2.1, Http, Newtonsoft, PdfSharpCore, Jwt |
| Helpers/Touroverlapvalidator.cs | 360 | Detects overlapping tours/claims; `sp_getapplock` per employee |
| Repository/IGenricRepository.cs, GenricRepository.cs | 20 / 67 | Generic EF repository base |
| Repository/IUnitOfWork.cs, UnitOfWork.cs | 10 / 25 | Unit of work (`Save()` only) — unused, not in DI |
| Service/JwtTokenInfo.cs | 93 | Reads `email`/issuer from JWT — unused |
| Service/TokenService.cs | 51 | Issues HMAC JWT (1-year) — registered, never called |
| Service/OcrProcessingService.cs | 227 | Calls OCR provider for one document, saves response/retries |
| Service/OcrQueueSignal.cs | 34 | Singleton wake-up signal for worker |
| Service/OcrClock.cs | 11 | IST clock for OCR timestamps |
| Service/UserExtensionRepository.cs | 221 | Tour-raise extension (`UserExtensions`) logic (repository placed in Service folder) |

### Repository/Concrete (19 files, 27,179 lines) and Interface (20 files, 678 lines)

| Repository | Lines | Interface lines | Purpose |
|---|---|---|---|
| AccountRepository | 354 | 16 | Mail/WhatsApp sharing, employee details by code, roles from `AspNetUserRoles` |
| ActualTravelPlanRepository | 71 | 19 | Actual travel plan CRUD |
| BulkApprovalRepository | 744 | 15 | Bulk bill-passing / banking approval |
| CGMExcessiveTourRepository | 957 | 38 | CGM authorization (org cadre API), excessive-tour reports, monthly mails |
| ClaimClarificationRepository | 2,294 | 22 | Clarification workflow and history |
| ClaimEditRepository | 1,636 | 51 | Claim edit with history snapshots |
| ClaimRepository | 8,066 | 108 | Claims, drafts, supplementary, approvals, SAP export/import |
| CommanRepository | 45 | 11 | Generic file upload to wwwroot |
| DashboardRepository | 1,542 | 48 | Dashboard aggregates |
| EmployeeRepository | 228 | 23 | `vMStEmployeeMasterNG` lookups; org hierarchy, CGM, finance user via org API |
| EmpTourPlanRepository | 3,299 | 54 | Tour request lifecycle, grades, drafts, PDFs, role push |
| NotificationRepository | 606 | 46 | SMTP mail (MailKit) |
| OcrRepository | 588 | 28 | OCR document CRUD scoped to caller's EmpCode |
| ProfileRepository | 87 | 13 | **Commented out** |
| TentativeTravelPlanRepository | 76 | 13 | Tentative plan CRUD |
| TourListingRepository | 3,481 | 105 | All listings/pending summaries |
| TourNotAvailedRepostitory | 309 | 13 | Not-availed + refund |
| TourRecoveryRepository | 1,393 | 16 | Recovery upload/release/cancel; finance-user check |
| TourStatusRepository | 1,403 | 21 | Status transitions, finance transfer, approver task transfer |
| (Interface) IUserExtensionRepository | — | 18 | for Service/UserExtensionRepository |

## TourProgram.DAL (data)

| Path | Lines | Purpose |
|---|---|---|
| TourProgram.DAL.csproj | 28 | Identity EF, EF SqlServer |
| TourProgramDbContext.cs | 360 | `IdentityDbContext<ApplicationUser, UserRole, string>`, DbSets, AuditLog mapping, view mapping |
| Audit/ (5 files) | 685 | AuditSaveChanges/Transaction/SessionContext interceptors, AuditChain, AuditChainVerifier |
| Helpers/EmployeeGradeHelper.cs | 46 | Normalizes `ReDesignatedGrade` vs `PositionGrade` (e.g. reversed "8E" → "E8(Redesignated)") |
| Scripts/ (4 .sql) | 311 | AuditLog table, DB triggers, immutability |
| Migrations/ | 102 files, ≈64k lines | 51 migrations from `20250512114853_initial migration` to `20261030072903_AddOcrFileIndex`, plus model snapshot |

### Models (41 files, 1,619 lines; ~50 classes) — purposes (inferred from names)

| File (lines) | Classes | Purpose |
|---|---|---|
| BaseModel (25) | BaseModel | Id, CreatedBy/Date, UpdatedBy/Date, IsActive |
| EmpTourPlan (122) | EmpTourPlan | Tour request header (core aggregate) |
| EMPClaimProcessDetail (28) | EMPClaimProcessDetail | Workflow step rows (Sender/Recipient/Status/ClaimType) |
| ClaimStatusCategory (21), ClaimType (15) | masters | Status & claim-type masters |
| TentativeTravelPlan (32), TentativeTravelPlanReApproval (25), ActualTravelPlan (28) | | Planned/actual legs |
| ClaimTourDetails (42), ClaimTourDetailsReApproval (33), DailyTourDateValidation (26) | | Claim per-tour details, daily-trip date ranges |
| TransportationDetail (108), TransportationUpload (29), AccomodationDetail (111), AccomodationUpload (32), DADetail (65), LeaveDetail (52) | + *History classes | Claim line items + edit history |
| FinanceDetail (42), OrgClaimLimit (18), GradeDetail (26), OwnArrangment (14) | | Finance data, limits, grade entitlements |
| ClaimClarification (30), ClaimIncident (25), ClaimEditLog (98) | | Clarification, incidents, edit log |
| ClaimDraft (40), ClaimWithRequestDraft (38), TourRequestDraft (32) | | Drafts (JSON + files) |
| UploadDocument (26), UploadDocType (20), UploadTypeDetail (22) | | Uploaded documents & types |
| TourRecovery (122) | TourRecoveryBatch, TourRecovery, TourRecoveryDetail, TourRecoveryLog | Recovery (salary deduction) |
| OcrDocument (48) | OcrDocument | OCR queue/result |
| AuditLog (27) | AuditLog | Hash-chained audit |
| ApplicationUser (18), UserRole (15) | Identity user/role | ASP.NET Identity tables (`AspNetUsers`, `AspNetRoles`, `AspNetUserRoles`) |
| Employee (41), EmpProfile (50) | | Local employee/profile tables (no BAL usage of `Employees` DbSet found) |
| VEmployeeMaster (22) | keyless view | `vMStEmployeeMasterNG` employee master |
| UserExtension (19) | | Tour-raise extension override |
| OtpCodeDetails (16), LoginHistory (16) | | OTP & login history — **no active code uses them** |

## TourProgram.Model (DTOs) — `Dtos/` (2 root files 239 lines + 22 folders)

| Folder | Files / lines | Purpose |
|---|---|---|
| (root) DashboardDtos.cs, TourMailDto.cs | 2 / 239 | Dashboard requests, mail/WhatsApp share DTOs |
| ActualTravelPlanDtos | 1 / 20 | |
| BulkApproval | 5 / 150 | Bulk approval/banking models |
| CGMExcessiveTour | 1 / 203 | Report request/response, cadre wrapper, transfer DTO |
| ClaimClarification | 2 / 218 | Clarification request/response |
| ClaimEdit | 1 / 238 | Edit claim DTO |
| Comman | 2 / 23 | ConfigDetails (OtpCode), upload response |
| Dashboard | 1 / 78 | Dashboard response |
| Draft | 5 / 442 | Draft DTOs + file metadata |
| Employee | 2 / 39 | EmployeeBasicDetails, EmployeeDetails |
| EmpTourPlanDtos | 7 / 306 | Tour request, re-approval, approver details, grade |
| Finance | 1 / 31 | Org `GetAdminTourReimbursement` response (BillPassId, PaymentProcId, SuperBillPassingManagerId…) |
| JwtInfo | 1 / 14 | JwtTokenInfoModel (unused) |
| LoginHistory | 1 / 10 | (unused) |
| Notification | 2 / 37 | MailRequestModel, MailSettingsModel |
| Ocr | 1 / 76 | OCR DTOs + OcrSettings |
| RaiseClaimDtos | 7 / 390 | Claim approval, supplementary, amount approvals |
| Response | 2 / 165 | **ResponeModel** envelope, Root/Root1 user-info classes, TokenDetail |
| TentativeTravelPlanDtos | 1 / 20 | |
| TourListingDtos | 2 / 56 | Employees on tour |
| TourRecovery | 1 / 106 | Recovery DTOs |
| TourStatus | 4 / 93 | Status update models (TourId, EmpId, RecipientId, StatusId) |
| UsersDtos | 6 / 119 | Login/Register/User/VerifyOtp (unused), UserExtension DTOs |

## TourProgram.Utility

| Path | Lines | Purpose |
|---|---|---|
| Eumus/RecordMasterEnum.cs | 266 | `DomainDetails` (per-mode base URLs), enums: claim types, status categories, recovery/OCR status, `ApplicatonTimeOut`, `TypeofTrip` |
| Model/ (3 files) | 117 | TentativeTourPlans, TourPlanDetails, TourTrajectoryDto |
| PdfUtility/PDFGenerator.cs, PdfTemplate.cs, TourCancelEmailTemplate.cs | 147 / 1,348 / 373 | HTML→PDF (DinkToPdf) templates, cancel e-mail template |

## TourProgram.Logger

| Path | Lines | Purpose |
|---|---|---|
| ExeceptionMiddleware.cs | 48 | Global exception → 500 JSON |
| AppExecption.cs | 22 | Error payload `{StatuCode, Message, Details}` |

## Must remain in .NET
Everything under BAL/DAL (workflow, PDFs, audit, SAP, OCR worker).

## Reusable by the Python agent
Controllers listed above are the integration surface; DTO folders define request bodies (see other analysts' API docs).

## Unresolved questions
1. Are `Employee`/`EmpProfile` tables still populated, given employee data is read from `vMStEmployeeMasterNG`?
2. Should committed `wwwroot` runtime data (uploads, approval PDFs, temp drafts) be in source control? Contains personal documents (not reproduced here).
3. Purpose of root-level `SupplementaryClaimDto.cs` (outside any project).
