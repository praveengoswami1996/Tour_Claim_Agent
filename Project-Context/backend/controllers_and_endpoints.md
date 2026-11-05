# Tour_API — Controllers & Endpoints Catalogue

Complete list of every HTTP endpoint in `TourProgram.API/Controllers` (19 files, **130 controller actions** + 2 minimal-API routes), traced from source.
Auth is **not** `[Authorize]`-based: every `[Authorize]` is commented out and `AddIdentityServices` is disabled (`TourProgram.API/Program.cs:59`). The only gate is the **global** `SSOTokenValidateFilter` (`Program.cs:22-28`), which introspects the OIDC bearer token and is skipped for any action marked `[AllowAnonymous]` (`ActionFilter/SSOTokenValidateFilter.cs:36-41`).
Most controllers take the acting user's identity from the URL/body (`empId`, `recipientId`, `senderId`), not the token; only `Ocr`, `CGMExcessiveTour`, `TourRecovery`, `UserExtension` derive it from the token via `BaseController`.
All paths below are relative to the API host (`/api/<Controller>/...`). Route token `[controller]` = class name minus `Controller` suffix (for `PDFGeneratordummy` the full class name).

## 0. Cross-cutting auth mechanics

| Mechanism | Behaviour | Evidence |
|---|---|---|
| Global filter `SSOTokenValidateFilter` (IAuthorizationFilter) | No `Authorization` header → 401 JSON `{message,statusCode,timestamp}` (not the `ResponeModel` envelope). Token must be a JWT whose `iss` equals the configured SSO base URL (`DeploymentModes` → `ApiBaseUrlsProd:SSO` / `ApiBaseUrlsDfcuat:SSO` / `CETPA:SSO`), then POSTed to `{SSO}/connect/introspect1`; `active!=true` → 401. On success stores a `Root1` user object in `HttpContext.Items["CurrentUser"]`. | `ActionFilter/SSOTokenValidateFilter.cs:33-120, 196-290` |
| Read-only users | POST/PUT/DELETE/PATCH → 403 if `Root1.IsReadOnly` is "true"/"1". **(inferred)** never fires: `IsReadOnly` is not populated when `Root1` is built from introspection. | `SSOTokenValidateFilter.cs:112-158, 254-272` |
| `[AllowAnonymous]` | Filter returns early → endpoint fully public (26 actions). | `SSOTokenValidateFilter.cs:36-41` |
| `BaseController` | Not an endpoint host (0 actions). Constructor decodes (no signature check) the bearer JWT and builds `TokenDetails` from claims `UserId`(→EmpCode, must be int else throws), `Designation`, `UnitName`, `UnitId`, `Level`, `Department`; reads header `DeviceType` into `SourceType` (unused by actions). | `Controllers/BaseController.cs:13-48` |
| `CheckToken()` / inline check | Subclasses return 401 `ResponeModel` if `TokenDetails==null || EmpCode<=0`. | `OcrController.cs:132-144`, `CGMExcessiveTourController.cs:60`, `TourRecoveryController.cs:125-147`, `UserExtensionController.cs:30` |
| Header API keys | `APIKey` header vs config `ApiKeys:Sap` (Claim SAP endpoints); header `apiKey` vs `APIKEYS:MonthlyNotification` (CGM cron). Values `<redacted>`. **A hard-coded default key literal exists at `ClaimController.cs:24` (`<redacted>`), overwritten from config in ctor.** | `ClaimController.cs:24-28,145,167,185`; `CGMExcessiveTourController.cs:281` |
| Recovery role check | `ITourRecoveryRepository.IsRecoveryUserAsync(EmpCode)` → 403 if false. | `TourRecoveryController.cs:137-145` |
| Hard-coded admin | `GetAllUnitCGMs` allowed only if token EmpCode equals a literal admin code (`<redacted>`). | `CGMExcessiveTourController.cs:335` |
| Minimal APIs (no filter, no auth) | `GET /health` → `{status:"Healthy"}`, `GET /` → "API is running". | `Program.cs:70-74` |
| Envelope | `ResponeModel {StatusCode, Message, Data, DataLength, PageNumber, PageSize, TotalRecords, Error, ErrorDetail}`. Actions returning `ResponeModel` directly always send **HTTP 200** regardless of `StatusCode`; actions returning `IActionResult` map `StatusCode` to HTTP status. Dashboard validation errors use anonymous `{error,message,statusCode}`. | `TourProgram.Model/Dtos/Response/ResponeModel.cs:10-22` |

## 1. Summary

| Controller | Base route | Class-level auth | # endpoints | Domain |
|---|---|---|---|---|
| AccountController | /api/Account | none (global filter) | 4 | Profile, roles, share tour by mail/WhatsApp |
| ActualTravelPlanController | /api/ActualTravelPlan | `[Authorize]` commented | 0 (all commented) | — |
| BaseController | /api/Base | none | 0 | Token parsing base class |
| BulkApprovalController | /api/BulkApproval | `[Authorize]` commented | 4 | Bulk bill-passing / banking approval |
| CGMExcessiveTourController | /api/CGMExcessiveTour | none; inherits BaseController (token checks in body) | 7 | CGM excessive-tour report, approver task transfer |
| ClaimClarificationController | /api/ClaimClarification | `[Authorize]` commented | 8 | Finance clarification Q&A on claims |
| ClaimController | /api/Claim | `[Authorize]` commented | 26 | Raise claim, drafts, approval, SAP integration |
| ClaimEditController | /api/ClaimEdit | `[Authorize]` commented | 5 | Edit claim-with-request before approval |
| DashboardController | /api/Dashboard | none | 14 | User/admin/approver dashboards |
| OcrController | /api/Ocr | none; BaseController + `CheckToken()` | 8 | Async OCR of bills |
| PDFGeneratordummy | /api/PDFGeneratordummy | none | 1 | **Test/dummy** PDF generator |
| ProfileController | — (file fully commented out) | — | 0 | — |
| TentativeTravelPlanController | /api/TentativeTravelPlan | `[Authorize]` commented | 0 (all commented) | — |
| TourListingController | /api/TourListing | `[Authorize]` commented | 16 | Tour/claim lists for employee, approver, bill-passing |
| TourNotAvailedController | /api/TourNotAvailed | `[Authorize]` commented | 2 | Mark tour not availed, advance refund |
| TourPlanController | /api/TourPlan | `[Authorize]` commented | 20 | Tour request, drafts, re-approval, grade master |
| TourRecoveryController | /api/TourRecovery | none; BaseController + recovery-role check | 7 | Finance recovery upload/release/cancel |
| TourStatusController | /api/TourStatus | `[Authorize]` commented | 5 | Approve/reject/amount status transitions |
| UserExtensionController | /api/UserExtension | none; BaseController | 3 | Tour-raise date extension overrides |
| **Total** | | | **130** | |

Legend for tables: **Auth** `G` = global SSO filter only; `Anon` = `[AllowAnonymous]` (public); `G+Tok` = global filter + `TokenDetails` check in body; `G+Rec` = + recovery-role check; `Key` = header API key. **Resp** = `ResponeModel` envelope unless stated; `Data:` gives the type/variable assigned in BAL (anon = anonymous object). BAL = interface.method. Relevance = usefulness to the Python agent.

## 2. Per-controller endpoints

### AccountController (`Controllers/AccountController.cs`, repo `IAccountRepository`)
| Method | Route | Action (line) | Auth | Inputs | Resp | BAL | Agent |
|---|---|---|---|---|---|---|---|
| POST | /api/Account/ShareTourOnMail | ShareTourOnMail (25) | G | body `TourMailDto` | ResponeModel | IAccountRepository.ShareTourOnMail | Low |
| POST | /api/Account/SendWhatsAppMessage | SendWhatsAppMessageAsync (33) | **Anon** | body `TourWhatsappDto` | ResponeModel | IAccountRepository.SendWhatsAppMessageAsync | None |
| GET | /api/Account/profile | GetUserProfile (43) | G | none (reads `Items["CurrentUser"] as Root`) | Data: anon {EmpId, EmpCode, Name, Email, Mobile, Designation, UnitId, Department, Level, Roles, SSOUserInfo} | IAccountRepository.GetEmployeeDetailsAsync, GetUserRolesAsync | High (identity) — see anomaly A7 |
| GET | /api/Account/user-roles/{empCode} | GetUserRoles (130) | G | route `int empCode` | Data: anon {Roles: List<string>} | IAccountRepository.GetUserRolesAsync | High |

### ActualTravelPlanController / TentativeTravelPlanController / ProfileController / BaseController
0 live endpoints. Get/Create actions are inside `/* */` (`ActualTravelPlanController.cs:18-37`, `TentativeTravelPlanController.cs:22-41`); `ProfileController.cs` is entirely `//`-commented (would have been `GET {empId}`, `PUT UpdateLogout/{empId}`).

### BulkApprovalController (`Controllers/BulkApprovalController.cs`, `IBulkApprovalRepository`)
| Method | Route | Action (line) | Auth | Inputs | Resp | BAL | Agent |
|---|---|---|---|---|---|---|---|
| GET | /api/BulkApproval/GetBillPassingBulkApproval/{empId} | GetAdvanceApproval (25) | G | route `int empId` | Data: List<object> lstToursAdvances | GetBillPassingBulkApproval | Med |
| POST | /api/BulkApproval/UploadBillPassingBulkApproval/{empId}/{recipientId} | UploadBillPassingBulkApproval (35) | G | body `List<UploadAdvanceApprovalModel>`, route empId, recipientId | Data: List<object> | UploadBillPassingBulkApproval | Low (bulk write) |
| GET | /api/BulkApproval/GetBankingBulkApproval/{empId} | GetBankingBulkApproval (44) | G | route empId | Data: List<object> | GetBankingBulkApproval | Low |
| POST | /api/BulkApproval/UploadBankingBulkApproval/{empId} | UploadBankingBulkApproval (54) | G | body `List<UploadAdvanceBankingApprovalModel>`, route empId | Data: List<object> | UploadBankingBulkApproval | None |

### CGMExcessiveTourController (`Controllers/CGMExcessiveTourController.cs`, `ICGMExcessiveTourRepository`, `ITourStatusRepository`)
| Method | Route | Action (line) | Auth | Inputs | Resp | BAL | Agent |
|---|---|---|---|---|---|---|---|
| GET | /api/CGMExcessiveTour/GetExcessiveTourReport | GetExcessiveTourReport (44) | G+Tok (CGM/admin check in BAL) | query fromDate, toDate, minimumDays=20, maxGrade="E2", pageNumber, pageSize, searchTerm → builds `ExcessiveTourReportRequestDto` | IActionResult; Data: `ExcessiveTourReportResponseDto` | ICGMExcessiveTourRepository.GetExcessiveTourReport | Med |
| GET | /api/CGMExcessiveTour/CheckAuthorization | CheckAuthorization (126) | G+Tok | none | IActionResult; Data: anon {IsAuthorized, IsAdmin, AuthorizedUnitIds, AuthorizedUnitNames, EmpCode} | CheckCGMAuthorization | Med |
| POST | /api/CGMExcessiveTour/GetExcessiveTourReport | GetExcessiveTourReportPost (191) | G+Tok | body `ExcessiveTourReportRequestDto` | as GET | GetExcessiveTourReport | Med (read-only POST) |
| POST | /api/CGMExcessiveTour/SendMonthlyNotifications | SendMonthlyNotifications (272) | **Anon + Key** (header `apiKey`) | header apiKey | IActionResult; Data: anon summary | SendMonthlyExcessiveTourNotifications | None (cron) |
| GET | /api/CGMExcessiveTour/GetAllUnitCGMs | GetAllUnitCGMs (316) | G+Tok + hard-coded admin EmpCode | none | IActionResult; Data: list of unit-CGM | GetAllUnitCGMs | None (debug) |
| GET | /api/CGMExcessiveTour/GetEmployeeTourSummary | GetEmployeeTourSummary (367) | G (no token check) | query `int employeeId`, `int month`, `int? year` | IActionResult; Data: `EmployeeTourSummaryResponseDto` | GetEmployeeTourSummaryByMonth | Med |
| PUT | /api/CGMExcessiveTour/TransferApproverPendingTasks | TransferApproverPendingTasks (433) | G+Tok (CGM check in BAL) | body `TransferApproverPendingTasksDto` | IActionResult; Data: anon | ITourStatusRepository.TransferApproverPendingTasks | None (admin, high impact) |

### ClaimClarificationController (`Controllers/ClaimClarificationController.cs`, `IClaimClarificationRepository`)
| Method | Route | Action (line) | Auth | Inputs | Resp | BAL | Agent |
|---|---|---|---|---|---|---|---|
| GET | /api/ClaimClarification/{tourid} | Get (28) | G | route `int tourid` | Data: List<object> clarificationlist | GetClarification | High |
| POST | /api/ClaimClarification | Post (38) | G | body `ClaimClarificationRequestModel` | Data: `ClaimClarification` entity | Create | Med (finance raises query) |
| POST | /api/ClaimClarification/respond | RespondToClarification (53) | G | **form** `ClaimClarificationResponseModel` (multipart, files) | Data: `ClaimClarification` record | RespondToClarification | High (employee replies) |
| POST | /api/ClaimClarification/respond-message | RespondToClarificationMessage (61) | G | body `ClaimClarificationMessageResponseModel` | IActionResult; Data: anon | RespondToClarificationMessage | High |
| GET | /api/ClaimClarification/thread/{tourPlanId} | GetClarificationThread (74) | G | route long | Data: anon thread | GetClarificationThread | High |
| GET | /api/ClaimClarification/detail/{clarificationId} | GetClarificationWithHistory (86) | G | route long | Data: anon | GetClarificationWithHistory | Med |
| GET | /api/ClaimClarification/history/{tourPlanId} | GetClarificationHistory (98) | G | route long; query `clarificationId` **ignored** (not passed to BAL) | Data: anon | GetClarificationHistory | Med |
| GET | /api/ClaimClarification/compare/{tourPlanId}/{clarificationId} | CompareClarificationData (111) | G | route long, long | Data: anon comparison | CompareClarificationData | Low |

### ClaimController (`Controllers/ClaimController.cs`, `IClaimRepository`)
| Method | Route | Action (line) | Auth | Inputs | Resp | BAL | Agent |
|---|---|---|---|---|---|---|---|
| POST | /api/Claim/RaiseClaimRequest | RaiseClaimRequest (34) | G | **form** `ClaimApprovalDto` (multipart w/ files) | ResponeModel | RaiseClaimRequest | High (submit claim) |
| POST | /api/Claim/raise-claim-with-request | RaiseClaimWithRequest (42) | G | **form** `ClaimApprovalDto` | IActionResult(ResponeModel) | RaiseClaimWithRequest | High (daily/local tour + claim) |
| POST | /api/Claim/SaveClaimDraft | SaveClaimDraft (54) | G | **form** `draftData` (JSON string → `ClaimDraftDto`) + IFormFile lists additionalFiles, advancePaymentFiles, transportationFiles, accommodationFiles, AccidentClaimFile | ResponeModel | SaveClaimDraft | Med (legacy draft) |
| GET | /api/Claim/GetClaimDraft | GetClaimDraft (105) | G | query `long empId`, `long? empTourPlanId` | Data: `ClaimDraftResponseDto` | GetClaimDraft | Med |
| GET | /api/Claim/GetAllClaimDrafts | GetAllClaimDrafts (115) | G | query empId | Data: drafts list | GetAllClaimDrafts | Med |
| DELETE | /api/Claim/DeleteClaimDraft/{draftId} | DeleteClaimDraft (125) | G | route long draftId, query empId | ResponeModel | DeleteClaimDraft | Low |
| GET | /api/Claim/RaiseClaimRequest/{tourId} | RaiseClaimRequest (132) | G | route `int tourId` | Data: anon tourDetails | GetRaiseClaimRequest | High (claim prefill) |
| GET | /api/Claim/GetPdfForSap/{tourId} | GetPdfForSap (140) | G + Key (`APIKey`) | route tourId, header APIKey | Data: string PDF URL | GetPdfForSap | None (SAP) |
| GET | /api/Claim/GetSapDetails | GetSapDetails (163) | **Anon + Key** | header `APIKey` | Data: List<`sapdata`> | GetSapDetails | None — **mutates** (A3) |
| POST | /api/Claim/UpdateSapDetails | UpdateSapDetails (181) | **Anon + Key** | body `List<sapdataPost>` (dates `dd.MM.yyyy` validated) | Data: List<object> results | UpdateSapDetails | None (SAP payment write-back) |
| PUT | /api/Claim/ApproveRaiseClaimAmount/{tourid}/{empId}/{claimApprovedAmount}/{ApproveTaxRate} | ApproveRaiseClaimAmount (251) | G | body `ApproveRaiseClaimModel` (ScreenshotPath base64 → saved PDF in wwwroot/UploadDocuments); route int, int, double, double | Data: List<object> | ApproveRaiseClaimAmount | Med (approver action; amounts in URL) |
| PUT | /api/Claim/RemoveRowClaim/{tourid}/{empId}/{TourNumber} | RemoveRowClaim (304) | G | route int ×3 | ResponeModel | RemoveRowClaim | Low (destructive) |
| GET | /api/Claim/GetAllClaims/{senderId} | GetAllClaims (312) | G | route senderId; query pageNumber=1, pageSize=10 (null→int.MaxValue), searchTerm, departmentFilter, fromDate, toDate, statusFilter | Data: anon paged list | GetAllClaims | High (claim history) |
| GET | /api/Claim/ClaimsDetailsById/{tourId} | ClaimsDetailsById (333) | G | route tourId | Data: List<object> | ClaimsDetailsById | High |
| POST | /api/Claim/SaveClaimWithRequestDraft | SaveClaimWithRequestDraft (356) | G | **form** `draftData` (→ `ClaimWithRequestDraftDto`) + IFormFile lists transportationFiles, accommodationFiles, daFiles, advancePaymentFiles, additionalFiles, AccidentClaimFile, nominationFiles | ResponeModel | SaveClaimWithRequestDraft | High (agent-safe draft) |
| GET | /api/Claim/GetClaimWithRequestDraft | GetClaimWithRequestDraft (409) | G | query empId, `long? draftId` | Data: `ClaimWithRequestDraftResponseDto` | GetClaimWithRequestDraft | High |
| GET | /api/Claim/GetAllClaimWithRequestDrafts | GetAllClaimWithRequestDrafts (419) | G | query empId | Data: List<object> | GetAllClaimWithRequestDrafts | High |
| DELETE | /api/Claim/DeleteClaimWithRequestDraft/{draftId} | DeleteClaimWithRequestDraft (429) | G | route draftId, query empId | ResponeModel | DeleteClaimWithRequestDraft | Med |
| POST | /api/Claim/SubmitClaimWithRequestDraft/{draftId} | SubmitClaimWithRequestDraft (439) | G | route draftId, query empId | IActionResult | SubmitClaimWithRequestDraft (→ RaiseClaimWithRequest) | High (final submit — needs HITL) |
| POST | /api/Claim/SubmitRaiseClaimDraft/{draftId} | SubmitRaiseClaimDraft (447) | G | route draftId, query empId | IActionResult | SubmitRaiseClaimDraft (→ RaiseClaimRequest) | High (final submit — needs HITL) |
| POST | /api/Claim/RaiseSupplementaryClaim | RaiseSupplementaryClaim (458) | G | **form** `SupplementaryClaimDto` | Data: anon / DA conflict | RaiseSupplementaryClaim | Med |
| GET | /api/Claim/GetSupplementaryClaimDetails/{empTourPlanId} | GetSupplementaryClaimDetails (469) | **Anon** | route long | Data: anon | GetSupplementaryClaimDetails | Med |
| POST | /api/Claim/SaveRaiseClaimDraft | SaveRaiseClaimDraft (479) | G | **form** `draftData` (→ `RaiseClaimDraftDto`) + IFormFile lists additionalFiles, advancePaymentFiles, transportationFiles, accommodationFiles, AccidentClaimFile, nominationFiles | ResponeModel | SaveRaiseClaimDraft | High (agent-safe draft) |
| GET | /api/Claim/GetRaiseClaimDraft | GetRaiseClaimDraft (528) | G | query empId, empTourPlanId (both required) | Data: `RaiseClaimDraftResponseDto` | GetRaiseClaimDraft | High |
| GET | /api/Claim/GetAllRaiseClaimDrafts | GetAllRaiseClaimDrafts (538) | G | query empId | Data: drafts list | GetAllRaiseClaimDrafts | High |
| DELETE | /api/Claim/DeleteRaiseClaimDraft/{draftId} | DeleteRaiseClaimDraft (548) | G | route draftId, query empId | ResponeModel | DeleteRaiseClaimDraft | Med |

### ClaimEditController (`Controllers/ClaimEditController.cs`, `IClaimEditRepository`)
| Method | Route | Action (line) | Auth | Inputs | Resp | BAL | Agent |
|---|---|---|---|---|---|---|---|
| POST | /api/ClaimEdit/edit | EditClaimWithRequest (31) | **Anon** | **form** `EditClaimWithRequestDto` (file fields removed from ModelState) | IActionResult | EditClaimWithRequest | Med (write; public!) |
| GET | /api/ClaimEdit/can-edit/{empTourPlanId} | CanEditTour (56) | G | route long, query `long empId` | Data: anon {canEdit, reason…} | CanEditTour | High |
| GET | /api/ClaimEdit/history/{empTourPlanId} | GetEditHistory (68) | G | route long | Data: List<`ClaimEditHistoryDto`> | GetEditHistory | Med |
| GET | /api/ClaimEdit/log/{editLogId} | GetEditLogDetails (79) | G | route long | Data: anon | GetEditLogDetails | Low |
| GET | /api/ClaimEdit/compare/{empTourPlanId}/{editLogId} | CompareWithHistoricalVersion (92) | G | route long, long | Data: `ClaimEditCompareDto` | CompareWithHistoricalVersion | Low |

### DashboardController (`Controllers/DashboardController.cs`, `IDashboardRepository`)
All return `IActionResult` (except first); validation errors are anonymous `{error,message,statusCode}`.
| Method | Route | Action (line) | Auth | Inputs | Resp | BAL | Agent |
|---|---|---|---|---|---|---|---|
| GET | /api/Dashboard/GetDashboardDetails/{year} | GetDashboardDetails (22) | G | route int year; query `string? unit`, `int? empId` | ResponeModel; Data: `DashboardResponseDto` | GetDashboardDetails | Med |
| POST | /api/Dashboard/user | GetUserDashboard (32) | G | body `UserDashboardRequestDto` | Data: `UserDashboardResponseDto` | GetUserDashboard | High (read-only POST) |
| POST | /api/Dashboard/admin | GetAdminDashboard (88) | G | body `AdminDashboardRequestDto` | Data: `AdminDashboardResponseDto` | GetAdminDashboard | Low |
| GET | /api/Dashboard/user/{employeeId} | GetUserDashboardSummary (135) | G | route int; query fromDate, toDate (default Jan-1..now) | `UserDashboardResponseDto` | GetUserDashboard | High |
| GET | /api/Dashboard/admin | GetAdminDashboardSummary (192) | G | query fromDate, toDate, department, unit, grade | `AdminDashboardResponseDto` | GetAdminDashboard | Low |
| GET | /api/Dashboard/user/current/{employeeId} | GetCurrentYearUserDashboard (243) | G | route int | `UserDashboardResponseDto` | GetUserDashboard | High |
| GET | /api/Dashboard/admin/current | GetCurrentYearAdminDashboard (286) | G | none | `AdminDashboardResponseDto` | GetAdminDashboard | Low |
| GET | /api/Dashboard/user/{employeeId}/months/{months} | GetUserDashboardLastMonths (321) | G | route int, int (1–24) | `UserDashboardResponseDto` | GetUserDashboard | Med |
| GET | /api/Dashboard/admin/months/{months} | GetAdminDashboardLastMonths (373) | G | route months; query department, unit, grade | `AdminDashboardResponseDto` | GetAdminDashboard | Low |
| GET | /api/Dashboard/Approver/{employeeId} | GetApproverDashboard (425) | **Anon** | route int; query fromDate, toDate | Data: anon | GetApproverDashboard | Med |
| GET | /api/Dashboard/ApproverUserWiseSummary/{employeeId} | GetApproverDashboardUserWiseSummary (485) | **Anon** | route; query fromDate, toDate, pageNumber, pageSize, searchTerm, tourType (1 Normal/2 Daily) | Data: anon paged | GetApproverDashboardUserWiseSummary | Med |
| GET | /api/Dashboard/ApproverUserWiseDetails/{employeeId}/{empId} | GetApproverDashboardByUser (554) | **Anon** | route int, int; query fromDate, toDate, pageNumber, pageSize | Data: anon paged | GetApproverDashboardByUser | Med |
| GET | /api/Dashboard/RecipientUserWiseSummary/{employeeId} | GetRecipientDashboardUserWiseSummary (616) | **Anon** | as ApproverUserWiseSummary | Data: anon paged | GetRecipientDashboardUserWiseSummary | Med |
| GET | /api/Dashboard/RecipientUserWiseDetails/{employeeId}/{empId} | GetRecipientDashboardByUser (684) | **Anon** | as ApproverUserWiseDetails | Data: anon paged | GetRecipientDashboardByUser | Med |

### OcrController (`Controllers/OcrController.cs`, `IOcrRepository`) — all `G+Tok`, identity = token `UserId` claim
| Method | Route | Action (line) | Auth | Inputs | Resp | BAL | Agent |
|---|---|---|---|---|---|---|---|
| POST | /api/Ocr/Upload | Upload (29) | G+Tok | **multipart** `OcrUploadRequestDto` (request limit 200 MB) | IActionResult; Data: anon (DocumentIds) | UploadDocuments | High (receipt extraction) |
| GET | /api/Ocr/GetMyDocuments | GetMyDocuments (43) | G+Tok | query `OcrDocumentFilterDto` | Data: anon list | GetMyDocuments | High |
| GET | /api/Ocr/GetDocument/{documentId} | GetDocument (51) | G+Tok | route long | Data: OCR document DTO (`ToDto`) | GetDocument | High |
| POST | /api/Ocr/MarkAsRead | MarkAsRead (59) | G+Tok | body `OcrMarkReadRequestDto` | Data: anon | MarkAsRead | Med |
| POST | /api/Ocr/Retry/{documentId} | Retry (67) | G+Tok | route long | Data: OCR document DTO | RetryDocument | Med |
| POST | /api/Ocr/Cancel | Cancel (78) | G+Tok | body `OcrCancelRequestDto` | Data: anon | CancelDocuments | Med |
| POST | /api/Ocr/Release | Release (89) | G+Tok | body `OcrReleaseRequestDto` | Data: anon | ReleaseDocuments | Med |
| GET | /api/Ocr/GetFile/{documentId} | GetFile (100) | G+Tok | route long | **file** (`PhysicalFile`, original upload) or 404 ResponeModel | GetStoredFile | Med |

### PDFGeneratordummy (`Controllers/PDFGeneratordummy.cs`) — dummy/test
| Method | Route | Action (line) | Auth | Inputs | Resp | BAL | Agent |
|---|---|---|---|---|---|---|---|
| POST | /api/PDFGeneratordummy/DummyPDFgenerator | DummyPDFgenerator (22) | **Anon** | body `PdfRequest {EmployeeDetails: TourPlanDetails, TentativeTourPlans: List<TentativeTourPlans>}` | **file** application/pdf; overwrites `GeneratedPDFs/GeneratedTourPlans.pdf` on server | none (DinkToPdf `IConverter`, `PdfTemplate.GetHTMLStringdummy`) | None |

### TourListingController (`Controllers/TourListingController.cs`, `ITourListingRepository`)
| Method | Route | Action (line) | Auth | Inputs | Resp | BAL | Agent |
|---|---|---|---|---|---|---|---|
| GET | /api/TourListing/GetTourRequest/{empId} | GetTourRequest (25) | G | route int; query `TypeofTrip touretype`, search | Data: List<object> | GetTourRequest | High (my tours) |
| GET | /api/TourListing/GetRecipientTourRequest/{recipientId} | GetRecipientTourRequest (35) | G | route; query touretype, pageNumber, pageSize, searchTerm, department, startDate, endDate | Data: anon paged | GetRecipientTourRequest | Med (approver inbox) |
| GET | /api/TourListing/GetRecipientTourRequestPendingSummary/{recipientId} | (61) | **Anon** | route; query touretype, pageNumber, pageSize, searchTerm, RequestType | Data: anon | GetRecipientTourRequestPendingSummary | Med |
| GET | /api/TourListing/GetRecipientTourRequestByUser/{recipientId}/{empId} | (81) | **Anon** | route ×2; query touretype, pageNumber, pageSize, RequestType | Data: anon | GetRecipientTourRequestByUser | Med |
| GET | /api/TourListing/GetBillPassingList/{recipientId} | (98) | G | route | Data: List<object> | GetBillPassingList | Low |
| GET | /api/TourListing/GetBillPassingPendingSummary/{recipientId} | (107) | **Anon** | route; query pageNumber, pageSize, searchTerm | Data: anon | GetBillPassingPendingSummary | Low |
| GET | /api/TourListing/GetRaiseClaimBillPassingPendingSummary/{recipientId} | (126) | **Anon** | route; query pageNumber, pageSize, searchTerm, `TypeofTrip? tourtype`, department | Data: anon | GetRaiseClaimBillPassingPendingSummary | Low |
| GET | /api/TourListing/GetUserBillPassingDetails/{recipientId}/{empId} | (146) | **Anon** | route ×2; query pageNumber, pageSize, tourType | Data: anon | GetUserBillPassingDetails | Low |
| GET | /api/TourListing/GetTourTrajectory/{empTourId} | GetTourTrajectory (168) | **Anon** | route long | IActionResult; Data: trajectory object (StatusCode left 0) | GetTourTrajectoryAsync | High (where is my claim) |
| GET | /api/TourListing/GetRaiseClaimBillPassingList/{recipientId} | (206) | **Anon** | route; query pageNumber, pageSize, searchTerm, tourtype, department, startDate, endDate, `List<long> statusIds` | Data: anon paged | GetRaiseClaimBillPassingList | Low |
| GET | /api/TourListing/GetClearificationBillPassingList/{recipientId} | (226) | **Anon** | route; query pageNumber, pageSize, searchTerm, tourtype, statusIds, claimTypeIds | Data: anon paged | GetClearificationBillPassingList | Med |
| GET | /api/TourListing/GetTourRaiseClaimList/{empId} | (252) | G | route | Data: List<object> | GetTourRaiseClaimList | High (claimable tours) |
| GET | /api/TourListing/GetMyActionList/{empId} | (260) | G | route | Data: List<object> | GetMyActionList | High |
| GET | /api/TourListing/GetTotalPendingTaskCount/{empId}/{Type} | (269) | **Anon** | route int, string | Data: int count | GetTotalPendingTaskCount | Med |
| GET | /api/TourListing/GetEmployeesOnTour | GetEmployeesOnTourByDate (277) | **Anon** | query date, pageNumber, pageSize=100, searchTerm | Data: List<`EmployeeOnTourDto`> / anon | GetEmployeesOnTourByDate | Low (org-wide PII) |
| GET | /api/TourListing/GetEmployeesOnTourByDateRange | (290) | **Anon** | query empcode, tourDateFrom, tourDateTo | Data: List<`EmployeeTourByDateDto`> | GetEmployeesOnTourByDateRange | Low |

### TourNotAvailedController (`Controllers/TourNotAvailedController.cs`, `ITourNotAvailedRepostitory`)
| Method | Route | Action (line) | Auth | Inputs | Resp | BAL | Agent |
|---|---|---|---|---|---|---|---|
| POST | /api/TourNotAvailed/{tourId}/{recipientId} | Post (22) | G | route int, int | ResponeModel (Data null) | TourNotAvailed | Med (cancels tour; HITL) |
| POST | /api/TourNotAvailed/PaymentRefund/{tourId}/{PaidAmount}/{PaymentReferenceNo} | PostPaymentRefund (29) | G | route int, double, string; **form** `List<IFormFile> Files` | ResponeModel | PaymentRefund | Low |

### TourPlanController (`Controllers/TourPlanController.cs`, `IEmpTourPlanRepository`)
| Method | Route | Action (line) | Auth | Inputs | Resp | BAL | Agent |
|---|---|---|---|---|---|---|---|
| GET | /api/TourPlan/GetTourDetails/{tourId} | Get (24) | G | route int | Data: anon tourDetail | GetTourDetails | High |
| GET | /api/TourPlan/GetGradeDetail | GetGradeDetail (33) | G | query `string lavel` | Data: grade detail | GetGradeData | High (entitlements) |
| POST | /api/TourPlan/TourRequest | TourRequest (40) | G | **form** `EmpTourPlanRequestDto` | Data: anon | EmpTourRequest | High (submit tour — HITL) |
| PUT | /api/TourPlan/EditTourRequest/{tourId} | EditTourRequest (47) | **Anon** | route long; **form** `EmpTourPlanRequestDto` | IActionResult | EditEmpTourRequest | Med (public write!) |
| GET | /api/TourPlan/GetAllGradeData | Get (84) | **Anon** | none | Data: List<`GradeDetail`> | GetAllGradeData | High (policy lookup) |
| POST | /api/TourPlan/CreateGradeData | CreateGradeData (91) | G | body `GradeDetailDto` | Data: GradeDetail | CreateGradeData | None (admin) |
| PUT | /api/TourPlan/{id} | Edit (98) | G | route id (**unused**); body `GradeDetailDto` | Data: GradeDetailDto | Edit | None (admin) |
| DELETE | /api/TourPlan/GradeDelete/{id} | GradeDelete (104) | G | route int | Data: GradeDetail | GradeDelete | None (admin, destructive) |
| PUT | /api/TourPlan/UpdateGradeReimbursement/{level} | UpdateDeviceAmountReimbursement (111) | G | route string; body `DeviceAmountReimbursementDto` | Data: dto | UpdateDeviceAmountReimbursement | None (admin) |
| POST | /api/TourPlan/SendBackToEmployee/{tourId}/{senderId}/{StatusId} | SendBackToEmployee (117) | G | route int ×3; query `remark` | ResponeModel | SendBackToEmployee | Low (approver) |
| POST | /api/TourPlan/SendBackToReportingManager/{tourId}/{managerId}/{stausId} | SendBackToReportingManager (123) | G | route int ×3 | ResponeModel | SendBackToReportingManager | Low |
| POST | /api/TourPlan/TourReApprovalRequest/{tourId} | TourReApprovalRequest (129) | G | **form** `EmpTourPlanRequestReApprovalDto`; route int | Data: anon | TourReApprovalRequest | Med |
| GET | /api/TourPlan/GetTourOldApprovalDetails/{tourId} | (135) | G | route int | Data: anon | GetTourOldApprovalDetails | Med |
| GET | /api/TourPlan/GetOwnArrangmentAmount | (143) | G | none | Data: `OwnArrangment` entity | GetOwnArrangmentAmount | High (policy) |
| PUT | /api/TourPlan/UpdateOwnArrangmentAmount/{id}/{amount} | (150) | G | route int, int | ResponeModel | UpdateOwnArrangmentAmount | None (admin; amount in URL) |
| POST | /api/TourPlan/SaveTourRequestDraft | (159) | G | **form** `draftData` (→ `TourRequestDraftDto`) + IFormFile lists travelFiles, accommodationFiles, daFiles, localFiles, nominationFiles, manualApprovalFile (first only) | ResponeModel | SaveTourRequestDraft | High (agent-safe draft) |
| GET | /api/TourPlan/GetTourRequestDraft/{draftId} | (210) | G | route long; query empId | Data: `TourRequestDraftResponseDto` | GetTourRequestDraft | High |
| GET | /api/TourPlan/GetAllTourRequestDrafts | (220) | G | query empId | Data: List<`TourRequestDraftListItemDto`> | GetAllTourRequestDrafts | High |
| DELETE | /api/TourPlan/DeleteTourRequestDraft/{draftId} | (230) | G | route long; query empId | ResponeModel | DeleteTourRequestDraft | Med |
| POST | /api/TourPlan/SubmitTourRequestDraft/{draftId} | (241) | **Anon** | route long; query empId | ResponeModel | SubmitTourRequestDraft (→ EmpTourRequest) | High (HITL; public!) |

### TourRecoveryController (`Controllers/TourRecoveryController.cs`, `ITourRecoveryRepository`)
| Method | Route | Action (line) | Auth | Inputs | Resp | BAL | Agent |
|---|---|---|---|---|---|---|---|
| POST | /api/TourRecovery/UploadRecovery | UploadRecovery (25) | G+Rec | body `RecoveryUploadRequestDto` (PreviewOnly flag) | Data: anon {batchId?, summary, rows} | UploadRecovery | None |
| GET | /api/TourRecovery/GetRecoveryList | (49) | G+Rec | query `RecoveryListFilterDto` | Data: anon paged | GetRecoveryList | None |
| GET | /api/TourRecovery/GetRecoveryDetails/{recoveryId} | (57) | G+Rec | route long | Data: anon | GetRecoveryDetails | None |
| POST | /api/TourRecovery/ReleaseRecovery | (65) | G+Rec | body `RecoveryActionDto` | Data: List<object> | ReleaseRecovery | None |
| POST | /api/TourRecovery/CancelRecovery | (73) | G+Rec | body `RecoveryActionDto` | Data: List<object> | CancelRecovery | None |
| GET | /api/TourRecovery/GetRecoveryBatches | (81) | G+Rec | none | Data: batch list | GetRecoveryBatches | None |
| GET | /api/TourRecovery/GetTourRecoveryStatus/{tourId} | (92) | G+Tok | route long | Data: anon | GetTourRecoveryStatus | Med |

### TourStatusController (`Controllers/TourStatusController.cs`, `ITourStatusRepository`)
| Method | Route | Action (line) | Auth | Inputs | Resp | BAL | Agent |
|---|---|---|---|---|---|---|---|
| PUT | /api/TourStatus/Update/TourRequestStatus | UpdateTourRequestStatus (23) | G | **form** `TourRequestStatusModel`; query `remark` | ResponeModel | UpdateTourRequestStatus | Low (approve/reject — approver HITL only) |
| PUT | /api/TourStatus/Update/RejectByFinance | RejectByFinance (29) | G | **JSON body** `TourRequestStatusModel` (inferred `[FromBody]`); query remark | ResponeModel | RejectByFinance | None |
| PUT | /api/TourStatus/Update/MoveAllPendingDataFromFinance | (39) | G | body `MoveRoleChangePendingStatus` | Data: anon | MoveAllPendingDataFromFinance | None (bulk reassignment) |
| PUT | /api/TourStatus/Update/TourRequestAmount | UpdateTourRequestAmount (45) | G | **form** `TourRequestAmountStatusModel` | Data: "" | UpdateTourRequestAmount | Low |
| PUT | /api/TourStatus/UpdateTourReApprovalStatus | (51) | G | **form** `TourRequestStatusModel` | ResponeModel | UpdateTourReApprovalStatus | Low |

### UserExtensionController (`Controllers/UserExtensionController.cs`, `IUserExtensionRepository` in `BAL/Service/UserExtensionRepository.cs`)
| Method | Route | Action (line) | Auth | Inputs | Resp | BAL | Agent |
|---|---|---|---|---|---|---|---|
| POST | /api/UserExtension/Seed | Seed (28) | G+Tok | body `List<SeedUserExtensionRequestDto>` | ResponeModel | SeedUserExtension | None (admin) |
| GET | /api/UserExtension/GetByEmpCodes | GetByEmpCodes (46) | G | query `List<int> empCodes` (repeated) | ResponeModel | GetUserExtensionByEmpCodes | Low |
| GET | /api/UserExtension/GetAll | GetAll (56) | G | none | ResponeModel | GetAllUserExtensions | None |

## 3. Write / state-changing endpoints

Destructiveness: 🔴 irreversible business/financial effect or bulk/admin; 🟠 workflow transition on a single record; 🟡 draft/reversible/user-scoped.

| Sev | Endpoint | Effect |
|---|---|---|
| 🔴 | PUT /api/Claim/ApproveRaiseClaimAmount/{tourid}/{empId}/{amount}/{taxRate} | Approves claim amount; writes PDF to wwwroot |
| 🔴 | POST /api/Claim/UpdateSapDetails (Anon+Key) | SAP payment write-back |
| 🔴 | GET /api/Claim/GetSapDetails (Anon+Key) | Stamps `SapPulledDate`/`SapRfNo` on recoveries (`BAL ClaimRepository.cs:661-670`) |
| 🔴 | PUT /api/TourStatus/Update/MoveAllPendingDataFromFinance | Bulk-moves finance pending items |
| 🔴 | PUT /api/CGMExcessiveTour/TransferApproverPendingTasks | Bulk-transfers an approver's pending tasks |
| 🔴 | POST /api/BulkApproval/UploadBillPassingBulkApproval/…, UploadBankingBulkApproval/… | Bulk approvals |
| 🔴 | POST /api/TourRecovery/UploadRecovery, ReleaseRecovery, CancelRecovery | Salary recovery records |
| 🔴 | POST/PUT/DELETE /api/TourPlan/CreateGradeData, {id}, GradeDelete/{id}, UpdateGradeReimbursement/{level}, UpdateOwnArrangmentAmount/{id}/{amount} | Entitlement master data |
| 🔴 | POST /api/UserExtension/Seed | Sets tour-raise extension overrides |
| 🔴 | POST /api/CGMExcessiveTour/SendMonthlyNotifications (Anon+Key); POST /api/Account/SendWhatsAppMessage (**Anon**); POST /api/Account/ShareTourOnMail | Outbound messages |
| 🟠 | PUT /api/TourStatus/Update/TourRequestStatus, Update/RejectByFinance, Update/TourRequestAmount, UpdateTourReApprovalStatus | Approve/reject/amount transitions |
| 🟠 | POST /api/TourPlan/TourRequest, SubmitTourRequestDraft/{id} (**Anon**), TourReApprovalRequest/{id}; PUT EditTourRequest/{id} (**Anon**) | Create/modify tour request |
| 🟠 | POST /api/TourPlan/SendBackToEmployee/…, SendBackToReportingManager/… | Return to sender |
| 🟠 | POST /api/Claim/RaiseClaimRequest, raise-claim-with-request, SubmitRaiseClaimDraft/{id}, SubmitClaimWithRequestDraft/{id}, RaiseSupplementaryClaim | Submit claims |
| 🟠 | PUT /api/Claim/RemoveRowClaim/{tourid}/{empId}/{TourNumber} | Removes a claim row |
| 🟠 | POST /api/ClaimEdit/edit (**Anon**) | Edits submitted claim (snapshot kept) |
| 🟠 | POST /api/ClaimClarification, /respond, /respond-message | Clarification records + claim data changes |
| 🟠 | POST /api/TourNotAvailed/{tourId}/{recipientId}; PaymentRefund/… | Marks tour not availed; refund record |
| 🟡 | POST /api/TourPlan/SaveTourRequestDraft; DELETE DeleteTourRequestDraft/{id} | Tour drafts |
| 🟡 | POST /api/Claim/SaveClaimDraft, SaveClaimWithRequestDraft, SaveRaiseClaimDraft; DELETE DeleteClaimDraft, DeleteClaimWithRequestDraft, DeleteRaiseClaimDraft | Claim drafts |
| 🟡 | POST /api/Ocr/Upload, MarkAsRead, Retry/{id}, Cancel, Release | OCR queue state (token-scoped) |
| 🟡 | POST /api/PDFGeneratordummy/DummyPDFgenerator (**Anon**) | Overwrites a server file |

Read-only POSTs (not state-changing): `POST /api/Dashboard/user`, `POST /api/Dashboard/admin`, `POST /api/CGMExcessiveTour/GetExcessiveTourReport`.

## 4. Anomalies

| # | Anomaly | Evidence |
|---|---|---|
| A1 | **26 `[AllowAnonymous]` actions**; 23 have no protection at all. Public **writes**: `PUT TourPlan/EditTourRequest/{tourId}`, `POST TourPlan/SubmitTourRequestDraft/{draftId}`, `POST ClaimEdit/edit`, `POST Account/SendWhatsAppMessage`, `POST PDFGeneratordummy/DummyPDFgenerator`. Public reads exposing org data by arbitrary id: 11 TourListing lists, 5 Dashboard approver/recipient views, `Claim/GetSupplementaryClaimDetails`, `TourPlan/GetAllGradeData`, `TourListing/GetEmployeesOnTour*`. | §2 tables, `[AllowAnonymous]` lines |
| A2 | No IDOR protection in most controllers: identity is caller-supplied (`empId`, `recipientId`, `senderId`, `employeeId`, `managerId`) in route/query; the token's user is never compared. | e.g. `ClaimController.cs:312`, `TourListingController.cs:25`, `TourPlanController.cs:117` |
| A3 | GET that mutates: `GET /api/Claim/GetSapDetails` saves `SapPulledDate`/`SapRfNo`. | `BAL/Repository/Concrete/ClaimRepository.cs:661-670` |
| A4 | Amounts / tax rate / payment reference in URL: `ApproveRaiseClaimAmount/{tourid}/{empId}/{claimApprovedAmount}/{ApproveTaxRate}`, `UpdateOwnArrangmentAmount/{id}/{amount}`, `TourNotAvailed/PaymentRefund/{tourId}/{PaidAmount}/{PaymentReferenceNo}`. | `ClaimController.cs:251`, `TourPlanController.cs:150`, `TourNotAvailedController.cs:29` |
| A5 | Dummy/test/debug surfaces: `PDFGeneratordummy` (anon, writes fixed file); `CGMExcessiveTour/GetAllUnitCGMs` ("for debugging"); empty controllers ActualTravelPlan, TentativeTravelPlan, Base; Profile fully commented. | `PDFGeneratordummy.cs:22-47`, `CGMExcessiveTourController.cs:313-316` |
| A6 | Hard-coded secrets/identities in code: default SAP API key literal; admin EmpCode literal. (values `<redacted>`) | `ClaimController.cs:24`, `CGMExcessiveTourController.cs:335` |
| A7 | `GET /api/Account/profile` casts `Items["CurrentUser"] as Root`, but the filter stores a `Root1` (unrelated class) → **(inferred)** always returns `StatusCode=401 "User not found in context"` inside HTTP 200. | `AccountController.cs:51`, `SSOTokenValidateFilter.cs:272`, `ResponeModel.cs:24,124` |
| A8 | `PUT /api/TourPlan/{id}` — catch-all single-segment PUT; `id` ignored (grade id comes from body). | `TourPlanController.cs:98-103` |
| A9 | Overlapping verb/route names: `GET` vs `POST Claim/RaiseClaimRequest` (prefill vs submit); `GET` vs `POST CGMExcessiveTour/GetExcessiveTourReport`; `GET Dashboard/user/{id}` vs `POST Dashboard/user`; `GET ClaimClarification/{tourid}` is a catch-all for unknown literal GETs. Three parallel claim-draft families: `ClaimDraft` (legacy), `RaiseClaimDraft`, `ClaimWithRequestDraft`. | `ClaimController.cs:34,132`, etc. |
| A10 | Inconsistent route casing/style: PascalCase (most), kebab-case (`raise-claim-with-request`, `can-edit`, `respond-message`, `user-roles`), lowercase (`profile`, `edit`, `respond`, `user`, `admin`, `thread`). Typos baked into routes/params: `GetClearificationBillPassingList`, `GetOwnArrangmentAmount`, `stausId`, `lavel`, `touretype` vs `tourtype` vs `tourType`, `ITourNotAvailedRepostitory`. | §2 |
| A11 | Ignored/unused params: `ClaimClarification/history` `clarificationId`; `Dashboard/GetDashboardDetails` computes `baseUrl` but doesn't use it. | `ClaimClarificationController.cs:99-101`, `DashboardController.cs:25-27` |
| A12 | HTTP status inconsistency: `ResponeModel`-returning actions always HTTP 200 (check `statusCode`/`error` in body); `IActionResult` actions map it; filter 401/403 bodies are a different shape; `GetTourTrajectory` never sets `StatusCode` (0). | `TourListingController.cs:180-193` |
| A13 | Auth filter calls an introspection path `/connect/introspect1` synchronously (`.Result`) per request; read-only flag never populated. | `SSOTokenValidateFilter.cs:222-229, 254-272` |
| A14 | Exception messages leaked in responses (`$"An error occurred: {ex.Message}"`). | e.g. `OcrController.cs:126,158`, `ClaimController.cs:97` |

## 5. Frontend cross-check (Tour_React → this API)

| Frontend path | Exists? | Notes |
|---|---|---|
| /TourPlan/TourRequest | ✅ POST form | |
| /TourPlan/GetTourDetails/{id} | ✅ GET | |
| /TourPlan/SaveTourRequestDraft | ✅ POST multipart | |
| /TourPlan/GetAllTourRequestDrafts | ✅ GET `?empId` | |
| /TourPlan/SubmitTourRequestDraft/{id} | ✅ POST `?empId` | Anonymous |
| /TourPlan/EditTourRequest/{tourId} | ✅ PUT form | Anonymous |
| /TourPlan/TourReApprovalRequest/{tourId} | ✅ POST form | |
| /TourPlan/GetOwnArrangmentAmount | ✅ GET | |
| /TourPlan/SendBackToEmployee/{tourId}/{senderId}/{statusId} | ✅ POST, `?remark=` | |
| /TourStatus/Update/TourRequestStatus | ✅ PUT form | |
| /TourStatus/Update/TourRequestAmount | ✅ PUT form | |
| /TourStatus/Update/ApproveByFinance | ❌ **Not found in code** (no action, no BAL method) | used `Tour_React/src/features/WithIn/RejectFinanceSlice.ts:136` |
| /TourStatus/Update/RejectByFinance | ✅ PUT **JSON body** | |
| /TourStatus/Update/SeekClarificationByFinance | ❌ **Not found in code** | `RejectFinanceSlice.ts:72,133` |
| /TourStatus/UpdateTourReApprovalStatus | ✅ PUT form | |
| /TourListing/* (GetTourRequest, GetRecipientTourRequest, GetRecipientTourRequestPendingSummary, GetRecipientTourRequestByUser, GetBillPassingList, GetRaiseClaimBillPassingList, GetRaiseClaimBillPassingPendingSummary, GetClearificationBillPassingList, GetUserBillPassingDetails, GetTourTrajectory) | ✅ | |
| /TourListing/GetRecipientTourRequests/{id} (plural) | ❌ only singular `GetRecipientTourRequest` exists | `features/ApprovalTour/Approvtourprogram.ts:6` |
| /TourListing/RejectClaimRequest (POST) | ❌ Not found in code | `features/Approveclaimed/Approveclaim.ts:274` |
| /Claim/RaiseClaimRequest (GET {tourId}, POST) | ✅ both | |
| /Claim/SaveRaiseClaimDraft, GetRaiseClaimDraft, GetAllRaiseClaimDrafts, SubmitRaiseClaimDraft/{draftId}, DeleteRaiseClaimDraft/{id} | ✅ | |
| /Claim/SaveClaimWithRequestDraft, SubmitClaimWithRequestDraft/{id} | ✅ | |
| /Claim/raise-claim-with-request | ✅ POST form | |
| /Claim/ApproveRaiseClaimAmount/{tourId}/{empId}/{amount}/{taxRate} | ✅ PUT | |
| /Claim/RemoveRowClaim/{tourId}/{EmpId}/{TourNumber} | ✅ PUT | |
| /Claim/GetAllClaims/{x} | ✅ GET | |
| /ClaimEdit/can-edit/{planId}, /ClaimEdit/edit | ✅ | not seen in current frontend src grep |
| /ClaimClarification (POST), /ClaimClarification/{tourId} (GET), /ClaimClarification/respond | ✅ | |
| /ClaimClarification/RespondClarification | ❌ Not found in code (backend has `respond` and `respond-message`) | `features/getClarification/GetClarification.ts:114` |
| /TourNotAvailed/{tourId}/{recipientId} | ✅ POST | |
| /Ocr/* (Upload, GetMyDocuments, GetDocument, MarkAsRead, Retry, Cancel, Release, GetFile) | ✅ all 8 | |
| /Dashboard/* (Approver, RecipientUserWiseSummary, RecipientUserWiseDetails) | ✅ | |
| /CGMExcessiveTour/* (GetExcessiveTourReport, GetEmployeeTourSummary, TransferApproverPendingTasks) | ✅ | |
| /UserExtension/* (Seed, GetByEmpCodes, GetAll) | ✅ | |
| /ModuleManagement/GetAllMasterData | ❌ not here — called via `axiosDmsInstance` (DMS service, `environment.dmsApiUrl`) | `features/masterData/masterDataSlice.ts:57` |
| /User/GetAllRoles, /User/AddNewRole, /User/AddUserRoleMapping | ❌ not in this API, yet called on default `axiosInstance` (`environment.apiUrl`) | `features/roleManagement/roleSlice.ts:25` |
| /Account/GetStationMasterList (POST) | ❌ Not found in code | `components/WithinJuridiction/WithinFromToSelector.tsx:50` |
| Org hierarchy | not here — `axiosOrganisationInstance` (`environment.orgHierarchy`) | `services/axiosInstance.ts:87` |

## Reusable by the Python agent

- **Read (safe tools):** `TourListing/GetTourRequest/{empId}`, `GetTourRaiseClaimList/{empId}`, `GetMyActionList/{empId}`, `GetTourTrajectory/{id}`; `TourPlan/GetTourDetails/{id}`, `GetGradeDetail?lavel=`, `GetAllGradeData`, `GetOwnArrangmentAmount`; `Claim/RaiseClaimRequest/{tourId}` (claim prefill), `ClaimsDetailsById/{tourId}`, `GetAllClaims/{senderId}`; `ClaimClarification/{tourId}`, `thread/{id}`; `ClaimEdit/can-edit/{id}?empId=`; `Dashboard/user/{employeeId}`; `TourRecovery/GetTourRecoveryStatus/{tourId}`; `Account/user-roles/{empCode}`.
- **Draft-only writes (agent may perform, user confirms later):** `TourPlan/SaveTourRequestDraft`, `Claim/SaveRaiseClaimDraft`, `Claim/SaveClaimWithRequestDraft` (multipart: `draftData` JSON string + file lists) and their Get/GetAll/Delete counterparts.
- **Submit (require explicit human confirmation):** `TourPlan/SubmitTourRequestDraft/{id}`, `Claim/SubmitRaiseClaimDraft/{id}`, `Claim/SubmitClaimWithRequestDraft/{id}`, `ClaimClarification/respond`, `TourNotAvailed/{tourId}/{recipientId}`.
- **OCR pipeline:** `Ocr/Upload` → poll `GetMyDocuments`/`GetDocument/{id}` → `MarkAsRead`/`Release`; identity comes from the token (needs `UserId` claim).
- Always send `Authorization: Bearer <OIDC token>` (even to anonymous endpoints, harmless) and `DeviceType: web`; check body `statusCode`/`error`, not only the HTTP status.
- Agent must **never** call: SAP endpoints, bulk/admin/master-data endpoints, finance status transitions, recovery, `PDFGeneratordummy`, notifications.

## Gaps

- Response `Data` shapes are mostly anonymous objects / `List<object>` built in BAL — no typed contract (field-level detail owned by the DTO analyst).
- No server-side binding of acting user to `empId`/`recipientId` for 15 of 19 controllers → the agent must itself enforce "own data only" from the token's identity.
- No endpoint to resolve the current user's `empId` from token works reliably (`Account/profile` likely broken, A7); `TokenDetails.EmpCode` (from `UserId` claim) ≠ `empId` used by most routes **(inferred)**.
- No finance approve / seek-clarification endpoints despite frontend usage.
- No versioning, no OpenAPI auth scheme; Swagger is behind `SwaggerBasicAuthMiddleware` in all environments (`Program.cs:79-90`).

## Unresolved questions

1. Where do `/TourStatus/Update/ApproveByFinance`, `/Update/SeekClarificationByFinance`, `/TourListing/RejectClaimRequest`, `/TourListing/GetRecipientTourRequests`, `/ClaimClarification/RespondClarification`, `/Account/GetStationMasterList`, `/User/*` live — another service behind the same gateway, removed code, or dead frontend paths?
2. Is the `[AllowAnonymous]` on write endpoints (`EditTourRequest`, `SubmitTourRequestDraft`, `ClaimEdit/edit`, `SendWhatsAppMessage`) intentional (e.g. gateway-level auth) or leftover from testing?
3. Does `GET /api/Account/profile` actually work in deployed builds (Root vs Root1 cast)? If not, how does the frontend obtain `empId`?
4. Mapping between token claim `UserId` (EmpCode) and the `empId` used in routes — which BAL/DB lookup is authoritative?
5. Is `{SSO}/connect/introspect1` a real custom endpoint on the SSO server?
6. Which of the three claim-draft families (`ClaimDraft`, `RaiseClaimDraft`, `ClaimWithRequestDraft`) is current for each tour type? `SaveClaimDraft`/`GetClaimDraft` appear legacy **(inferred)**.
7. Is there an upstream gateway that adds auth/rate-limiting in front of this API?
