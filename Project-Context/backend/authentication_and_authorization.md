# Authentication & Authorization — TourProgram API

**Summary.** The API does **not** use ASP.NET JwtBearer or `[Authorize]`. Every MVC action (except those marked `[AllowAnonymous]`) passes a single global filter, `SSOTokenValidateFilter`, which (1) decodes the bearer JWT *without* signature checks, (2) requires its `iss` to equal the configured SSO base URL, and (3) POSTs it to the SSO server's custom `/connect/introspect1` endpoint and accepts it if `active == true`. Identity used by business code comes from **unvalidated JWT claims** (`UserId` = emp code) in `BaseController`, which only 4 controllers inherit; the rest take `empId/recipientId/senderId` from the URL/body and **do not check them against the caller**. There is no login/OTP endpoint, no server-side role enforcement except a few ad-hoc checks (CGM via org service, finance-recovery users via org service, a hard-coded admin emp code). The read-only (impersonation) check exists but can never trigger.

## 1. Token validation path

| Stage | Evidence | Behaviour |
|---|---|---|
| Filter registration | `TourProgram.API/Program.cs:22-28` | `SSOTokenValidateFilter` added globally via `options.Filters.AddService<>()` |
| JwtBearer | `TourProgram.API/Extensions/IdentityServicesExtension.cs:30-43`; call commented at `Program.cs:59` | **Not active.** `UseAuthentication()/UseAuthorization()` (Program.cs:92-93) run with no scheme — no-ops. Key names it would use: `TokenKey`; `ValidateIssuer=false`, `ValidateAudience=false`, `ValidateLifetime=true`, `ClockSkew=0` |
| AllowAnonymous bypass | `SSOTokenValidateFilter.cs:36-41` | Any action with `[AllowAnonymous]` skips everything |
| Header parse | `:44-62` | Requires `Authorization: Bearer <token>`; else 401 |
| Issuer gate `IsOIDCToken` | `:196-214`, `GetSSOBaseUrl` `:284-292` | `jwt.iss` must **exactly equal** `ApiBaseUrlsProd:SSO` (PROD_DFCCIL) / `ApiBaseUrlsDfcuat:SSO` (UAT_DFCCIL) / `CETPA:SSO` (other — key **absent** in appsettings ⇒ null ⇒ every token rejected). Non-matching issuer ⇒ `isValidToken=false` ⇒ 401 (legacy-token branch is commented out `:78-95`) |
| Introspection | `:216-282` | `new HttpClient()`, sync `.Result`, `POST {SSO}/connect/introspect1` form `Token=<jwt>`; **no client id/secret, no scope/audience/client_id check**. Non-2xx ⇒ 401; `active != true` ⇒ 401 |
| Current user object | `:250-272` | Builds `Root1` (`TourProgram.Model/Dtos/Response/ResponeModel.cs:124-146`) from introspection (`username`→UserId/name, `email`, `custom_claims.UnitName/UnitId/Department/Designation/role/Level`) into `HttpContext.Items["CurrentUser"]`. `IsReadOnly`, `DApplications`, `DApplicationNames`, `IsD` are **never set** |
| Read-only gate | `:112-116, 128-158` | If `CurrentUser.IsReadOnly` is "true"/"1" and method is POST/PUT/DELETE/PATCH ⇒ 403. Because `IsReadOnly` is never populated, **this check never fires** |
| Failure body | `:160-193` | `{message, statusCode (401/403), timestamp}` — not the `ResponeModel` envelope |

Expected access token (from frontend context): OIDC from DFCCIL identity server, scope `openid profile api1`. The backend never checks scope.

## 2. How identity reaches business code

### BaseController (`TourProgram.API/Controllers/BaseController.cs:12-58`)
- Constructor reads `Authorization` and **re-decodes the JWT with `ReadJwtToken` (no validation)**.
- Claims → `TokenDetail` (`Model/Dtos/Response/TokenDetail.cs`): `UserId` → `EmpCode` (int; **this is the emp code, not EmpId**), `Designation`, `UnitName`→Unit, `UnitId`→UnitID, `Level`→Lavel, `Department`. `unique_name` read but unused.
- If a bearer token is present but `UserId` is not an int ⇒ `throw new Exception("Invalid EmpCode format in token.")` ⇒ `ExeceptionMiddleware` 500.
- `SourceType` = `DeviceType` header (`:21`). **Never read anywhere else** — DeviceType has no server-side effect.
- No `EmpId`, roles, or email is extracted. There is no `CheckToken` method (Not found in code).
- Only `CGMExcessiveTourController`, `OcrController`, `TourRecoveryController`, `UserExtensionController` inherit it.

### Other token readers
| Component | Evidence | Status |
|---|---|---|
| `AuditUserProvider` | `API/Extensions/AuditUserProvider.cs:16-36` | Reads `UserId`, `name`/`unique_name` (unvalidated) for audit rows |
| `JwtTokenInfo` | `BAL/Service/JwtTokenInfo.cs:13-48` | Maps `EmpId = token.Issuer`(!), `email`; **never used** |
| `TokenService.CrateToken` | `BAL/Service/TokenService.cs:17-46` | Would mint HS256 JWT (claims Name, PrimarySid, MobilePhone, Email, Role, EmpCode, Designation, Unit, Lavel, Department; 1-year expiry, key `TokenKey`); registered in DI, **never called** |

## 3. Login, OTP, Identity tables

| Item | Evidence | Status |
|---|---|---|
| Login / register / OTP endpoints | `AccountController.cs` (4 actions only) | **Not found in code**. DTOs `LoginDto`, `RegisterDto`, `VerifyOtpCodeModel` exist (`Model/Dtos/UsersDtos`) but are unused |
| `OtpCodeDetails` entity | `DAL/Models/OtpCodeDetails.cs` (OtpCode, Senttime, RequestedByEmpId, IsOtpVerify, VerifyDateTime) | DbSet only (`TourProgramDbContext.cs:51`); no reader/writer. `AppConfig:OtpCode` bound to `ConfigDetails` but never read |
| `LoginHistory` | `DAL/Models/LoginHistory.cs`; `ProfileRepository.cs` and `ProfileController.cs` fully commented out | Dead |
| `ApplicationUser : IdentityUser` (+`EmpId`) | `DAL/Models/ApplicationUser.cs` | Table exists via `IdentityDbContext`; Identity services not registered |
| `UserRole : IdentityRole` (+IsActive, CreatedDate) | `DAL/Models/UserRole.cs` | Read by `AccountRepository.GetUserRolesAsync` (`:297-320`): `AspNetUserRoles.UserId == empCode.ToString()` joined to roles; default `["user"]`; `SuperAdmin` config key (absent in appsettings) ⇒ `["superAdmin"]` |

## 4. Employee & role data sources

| Data | Source | Evidence |
|---|---|---|
| Employee master (name, code, empId, email, mobile, designation, department, unit, PositionGrade, ReDesignatedGrade, Status) | SQL view `vMStEmployeeMasterNG` (`VEmployeeMaster`, keyless) | `TourProgramDbContext.cs:109`; `EmployeeRepository.cs:45-93` (by EmpId / by EmpCode) |
| Grade normalisation | `DAL/Helpers/EmployeeGradeHelper.cs:11-44` (reversed or digit-first ReDesignatedGrade → `E8(Redesignated)` form) | used in 5 places |
| Org hierarchy | Org service `GET /api/Organization/GetOrganizationHierarchy/` | `EmployeeRepository.cs:94-117` |
| CGM of unit | Org service `GET /api/MobileVisibility/GetCadreAllotementsAsync`, `roleAssigned == "CGM"` | `EmployeeRepository.cs:122-161`; `CGMExcessiveTourRepository.cs:392-470` |
| Finance (bill passing / payment / super bill-passing) users per unit | Org service `GET /api/AdminManage/GetAdminTourReimbursement` | `EmployeeRepository.cs:166-227`; `TourRecoveryRepository.cs:46-57` |
| Approver role in org portal | App **pushes** a fixed roleId to org `POST /api/AdminManage/CreateRolesandPermssion` when routing to an approver | `EmpTourPlanRepository.cs:301-322`; `TourStatusRepository.cs:~1000-1020` |
| Token claims (Unit, Level, Designation…) | DFCCIL SSO | BaseController / filter |
| Local `AspNetUserRoles` | DB | `AccountRepository.cs:308-311` |

Org-service calls are made **without any Authorization header** (e.g. `EmployeeRepository.cs:98-102`, `CGMExcessiveTourRepository.cs:419-421`).

## 5. Per-controller authorization table

Legend: **SSO** = global filter (valid introspected token, any user); **Anon** = `[AllowAnonymous]` (no auth at all); **Tok** = uses `TokenDetails.EmpCode` from claims. No controller/action has an active `[Authorize]` (all occurrences are commented, e.g. `ClaimController.cs:20`).

| Controller (route `api/[controller]`) | Base | Anon actions | All other actions | Server-side extra checks |
|---|---|---|---|---|
| Account | ControllerBase | `POST SendWhatsAppMessage` (`:32`) | SSO: `POST ShareTourOnMail`, `GET profile`, `GET user-roles/{empCode}` | `profile` casts `Items["CurrentUser"] as Root` but filter stores `Root1` ⇒ **always** returns `statusCode 401 "User not found in context"` (`:51-57`). `user-roles/{empCode}` returns roles of **any** emp code |
| ActualTravelPlan | ControllerBase | — | SSO: `GET {id}`, `POST Create` | none |
| BulkApproval | ControllerBase | — (all `//[AllowAnonymous]` commented) | SSO: `GET GetBillPassingBulkApproval/{empId}`, `POST UploadBillPassingBulkApproval/{empId}/{recipientId}`, `GET GetBankingBulkApproval/{empId}`, `POST UploadBankingBulkApproval/{empId}` | none — empId from URL |
| CGMExcessiveTour | **BaseController** | `POST SendMonthlyNotifications` (`:273`, requires header `apiKey` = `APIKEYS:MonthlyNotification`) | SSO+Tok: `GET/POST GetExcessiveTourReport`, `GET CheckAuthorization`, `GET GetAllUnitCGMs`, `PUT TransferApproverPendingTasks`; SSO only: `GET GetEmployeeTourSummary` (employeeId query, no caller check) | CGM/Admin gate via org cadre API + hard-coded admin emp code (`CGMExcessiveTourRepository.cs:22, 392-470`); `GetAllUnitCGMs` admin-only by literal emp-code compare (`Controller:335`); Transfer gated by `CheckCGMAuthorization` (`TourStatusRepository` TransferApproverPendingTasks) |
| ClaimClarification | ControllerBase | — | SSO: `GET {tourid}`, `POST`, `POST respond`, `POST respond-message`, `GET thread/{tourPlanId}`, `GET detail/{id}`, `GET history/{tourPlanId}`, `GET compare/{tourPlanId}/{id}` | none |
| Claim | ControllerBase | `GET GetSapDetails` (`:164`, header `APIKey`=`ApiKeys:Sap`), `POST UpdateSapDetails` (`:182`, same key), `GET GetSupplementaryClaimDetails/{empTourPlanId}` (`:470`, **no key**) | SSO: RaiseClaimRequest (POST/GET), raise-claim-with-request, Save/Get/GetAll/Delete ClaimDraft, GetPdfForSap, ApproveRaiseClaimAmount, RemoveRowClaim, GetAllClaims/{senderId}, ClaimsDetailsById, ClaimWithRequestDraft CRUD + Submit, SubmitRaiseClaimDraft, RaiseSupplementaryClaim, RaiseClaimDraft CRUD | drafts filtered by `EmpId == empId` **query param** (e.g. `ClaimRepository.cs:2582`), not by caller |
| ClaimEdit | ControllerBase | `POST edit` (`:32`) — **anonymous write** | SSO: `GET can-edit/{id}?empId`, `history`, `log`, `compare` | none from token |
| Dashboard | ControllerBase | `GET Approver/{employeeId}`, `ApproverUserWiseSummary/{employeeId}`, `ApproverUserWiseDetails/{employeeId}/{empId}`, `RecipientUserWiseSummary/{employeeId}`, `RecipientUserWiseDetails/{employeeId}/{empId}` | SSO: `GetDashboardDetails/{year}`, `POST user`, `POST admin`, `GET user/{employeeId}`, `GET admin`, `user/current/{id}`, `admin/current`, `user/{id}/months/{m}`, `admin/months/{m}` | "admin" endpoints have **no role check** |
| Ocr | **BaseController** | — | SSO+Tok: Upload, GetMyDocuments, GetDocument, MarkAsRead, Retry, Cancel, Release, GetFile | **Ownership enforced**: every query filters `EmpCode == TokenDetails.EmpCode` (`OcrRepository.cs:127, 197, 239, 304`) |
| PDFGeneratordummy | ControllerBase | `POST DummyPDFgenerator` | — | test endpoint exposed |
| Profile | — | — | **commented out** | — |
| TentativeTravelPlan | ControllerBase | — | SSO: `GET {id}`, `POST Create` | none |
| TourListing | ControllerBase | `GetRecipientTourRequestPendingSummary/{recipientId}`, `GetRecipientTourRequestByUser/{recipientId}/{empId}`, `GetBillPassingPendingSummary/{recipientId}`, `GetRaiseClaimBillPassingPendingSummary/{recipientId}`, `GetUserBillPassingDetails/{recipientId}/{empId}`, `GetTourTrajectory/{empTourId}`, `GetRaiseClaimBillPassingList/{recipientId}`, `GetClearificationBillPassingList/{recipientId}`, `GetTotalPendingTaskCount/{empId}/{Type}`, `GetEmployeesOnTour`, `GetEmployeesOnTourByDateRange` | SSO: `GetTourRequest/{empId}`, `GetRecipientTourRequest/{recipientId}`, `GetBillPassingList/{recipientId}`, `GetTourRaiseClaimList/{empId}`, `GetMyActionList/{empId}` | none — ids from URL |
| TourNotAvailed | ControllerBase | — | SSO: `POST {tourId}/{recipientId}`, `POST PaymentRefund/{tourId}/{PaidAmount}/{ref}` | none |
| TourPlan | ControllerBase | `PUT EditTourRequest/{tourId}` (`:46`), `GET GetAllGradeData` (`:83`), `POST SubmitTourRequestDraft/{draftId}?empId` (`:242`) — **anonymous writes** | SSO: GetTourDetails, GetGradeDetail, TourRequest, CreateGradeData, `PUT {id}` (grade edit), GradeDelete, UpdateGradeReimbursement, SendBackToEmployee, SendBackToReportingManager, TourReApprovalRequest, GetTourOldApprovalDetails, Get/UpdateOwnArrangmentAmount, tour-draft CRUD | grade master edits have **no admin check** |
| TourRecovery | **BaseController** | — | SSO+Tok: all 7 actions | `CheckRecoveryAccessAsync` (`Controller:124-147`): caller must be hard-coded admin or a BillPass/PaymentProc/SuperBillPassing user per org API (`TourRecoveryRepository.cs:46-57`), else 403 |
| TourStatus | ControllerBase | — | SSO: `PUT Update/TourRequestStatus`, `Update/RejectByFinance`, `Update/MoveAllPendingDataFromFinance`, `Update/TourRequestAmount`, `UpdateTourReApprovalStatus` | none — actor = body `EmpId`/`RecipientId` |
| UserExtension | **BaseController** | — | SSO+Tok: `POST Seed`, `GET GetByEmpCodes`, `GET GetAll` | Seed only requires a token; caller code used as CreatedBy; **no admin check** |
| Minimal APIs `/health`, `/` | — | always anonymous (outside MVC) | — | — |

## 6. Specific answers

| Question | Answer |
|---|---|
| `[Authorize]` anywhere active? | **No.** All commented. |
| `Roles=` / `IsInRole` / policies? | **None** (grep: no matches). Roles list from `GetUserRolesAsync` is only returned to the client, never enforced. |
| Role-like string checks server-side | `roleAssigned == "CGM"` from org cadre API (`CGMExcessiveTourRepository.cs:431`, `EmployeeRepository.cs:144`); finance-user membership (`TourRecoveryRepository.cs:54-56`); hard-coded admin emp code (`CGMExcessiveTourRepository.cs:22`, `TourRecoveryRepository.cs:20`, `CGMExcessiveTourController.cs:335`); `SuperAdmin` config compare (`AccountRepository.cs:301`). |
| IsReadOnly / impersonation enforced? | Code exists (`SSOTokenValidateFilter.cs:112-158`) but `IsReadOnly` is never populated from introspection (`:250-270`) ⇒ **effectively not enforced**. Also bypassed for all `[AllowAnonymous]` writes. |
| Ownership checks on EmpId / RecipientId / SenderId | **Generally absent.** Examples: `TourStatusRepository.UpdateTourRequestStatus` (`TourStatusRepository.cs:187+`) uses body `EmpId` as `SenderId`/`ApprovedBy`/`UpdatedBy` (≈`:268, 298, 317-320`) and body `RecipientId` as next approver — no comparison to token; `TourListing/GetTourRequest/{empId}` returns any employee's tours; `Claim/DeleteClaimDraft/{draftId}?empId` matches draft to the *supplied* empId (`ClaimRepository.cs:2582`); `Account/user-roles/{empCode}` any code. **Present only in**: OCR (token EmpCode), TourRecovery (finance/admin gate), CGM endpoints (CGM/admin gate). |
| DeviceType header | Read into `BaseController.SourceType` (`:21`); not used anywhere ⇒ no effect. |
| Delegate / switch-user | **Not found in code.** `Root1` has `DApplications`, `DApplicationNames`, `IsD` (possibly delegation flags — **inferred**) but they are never set or read. The org-service "TransferApproverPendingTasks" (CGM-gated) and "MoveAllPendingDataFromFinance" (ungated) move pending work between people. |
| EmpCode vs EmpId | Token `UserId` = **emp code**; most endpoints expect **EmpId** (`vMStEmployeeMasterNG.empId`). Client must translate (frontend does so; server endpoint `/api/Account/profile` that would return both is broken). |

## 7. Recommendation for the Python agent

**Use token pass-through (on-behalf-of the logged-in user). Do not use a service account.** Basis:
1. The only accepted credential is an SSO-issued JWT whose `iss` equals the backend's configured SSO URL and which `/connect/introspect1` reports `active`. The agent must forward the user's exact access token (`Authorization: Bearer …`); also send `DeviceType: web` for parity (no server effect).
2. Endpoints on `BaseController` (OCR, TourRecovery, CGM, UserExtension) need an integer `UserId` claim; a client-credentials token would cause a 500 there and would be audited with an empty user (`AuditUserProvider.cs:24-28`).
3. Because the server trusts `empId/recipientId/senderId` from the request, a service-account token would let the agent act as **anyone** — unacceptable. With pass-through the residual risk is the same, so the **agent itself must enforce**: derive EmpCode from the token's `UserId`, resolve EmpId once, and refuse to send any other `empId/senderId` unless the user is acting as the approver/recipient of that specific item (verify via listing endpoints first).
4. Treat the session as read-only by default; require explicit user confirmation before any POST/PUT/DELETE, since the server's read-only guard is non-functional.
5. Never call `[AllowAnonymous]` APIKey endpoints (SAP, monthly notification) from the agent.

Risks to flag: token expiry mid-conversation (no refresh path in backend — agent must obtain refreshed token from the frontend/IdP); per-request synchronous introspection latency; agent prompt-injection could trigger writes against other employees' records given absent ownership checks; anonymous write endpoints (`ClaimEdit/edit`, `TourPlan/EditTourRequest`, `TourPlan/SubmitTourRequestDraft`) are reachable without any token; error details leak via exception middleware.

## Must remain in .NET
- Token introspection & any authorization fixes (ownership checks, role enforcement, read-only enforcement) — must be added server-side; the agent cannot be the only guard.

## Reusable by the Python agent
- SSO introspection semantics (agent may pre-validate the user token against the same `/connect/introspect1` to fail fast — **inferred** option).
- `vMStEmployeeMasterNG`-backed lookups via existing endpoints; `/api/Account/user-roles/{empCode}` for role hints (informational only).

## Gaps (no existing API)
- Working "who am I" endpoint returning EmpId + EmpCode + roles (`/api/Account/profile` broken by `Root`/`Root1` mismatch).
- Server-side ownership/role checks for status transitions, listings, drafts, dashboards.
- Service/agent identity concept (e.g. `act`/delegation claim) — not supported.
- Delegation / switch-user — not implemented server-side.

## Unresolved questions
1. Does the DFCCIL SSO access token actually carry `UserId` (emp code), `UnitId`, `Level`, `Designation`, `Department` claims? The code assumes so; not verifiable from this repo.
2. What does the custom `/connect/introspect1` endpoint check (signature, expiry, audience, client)? It is called without client credentials.
3. Is `IsReadOnly` supposed to come from `custom_claims`? If so the mapping is missing.
4. Are the `[AllowAnonymous]` write endpoints intentional (mobile/WhatsApp links?) or left over from debugging?
5. What do `DApplications` / `IsD` represent (delegation?) and where should they be enforced?
6. Is the `SuperAdmin` config key set in deployed environments (absent in repo appsettings)?
