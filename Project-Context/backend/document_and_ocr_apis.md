# Tour API — Supporting Documents & OCR APIs

**Scope:** (a) how bills, tickets, nomination, DA, advance-payment and other supporting files are uploaded, linked, stored and returned in the claim, draft, edit and clarification flows; (b) the asynchronous OCR subsystem (`api/Ocr/*`, queue, worker, provider) and how it compares with `Tour_API/docs/OCR_Frontend_Integration.md`.
Paths are relative to `Tour_API/`. **(inferred)** marks conclusions not stated directly in code. Secrets are given by key name only.

**Summary**
- Claim documents are **not a standalone resource**. They travel as `IFormFile` lists inside the claim/draft/edit/clarification multipart forms.
  - Bills attached to a claim row are mapped to that row through an inclusive **index range** (`StartFileIndexCount..EndFileIndexCount`) into one flat file list per section.
  - Every file is written to `wwwroot/UploadDocuments/{Category}/{guid}{ext}` and returned as a public static URL.
  - The server enforces **no type or size limits** on these files. There is no dedicated download or delete endpoint, and the `DocumentsAttached` checklist is never persisted or enforced.
- **OCR is the only properly designed document API:**
  - Per-user (token EmpCode) isolation and server-side validation (.pdf/.jpg/.jpeg/.png, ≤20 MB, ≤20 files).
  - Files are stored outside wwwroot and processed by a DB-backed queue with leases, retries and a claim/release protocol.
  - The provider key stays on the server.

---

## Part A — Supporting-document management

### A.1 Entities
| Entity (table) | Key fields | Links to | Purpose |
|---|---|---|---|
| `UploadDocType` (`TourProgram.DAL/Models/UploadDocType.cs`) | Id, Name, IsBillable, IsActive | – | Master of document types, seeded in `TourProgramDbContext.cs:263-275` |
| `UploadTypeDetail` (`Models/UploadTypeDetail.cs`) | EmpTourPlanId, UploadTypeId, Amount, BaseModel audit fields | EmpTourPlan, UploadDocType | One "bucket" of one type for one tour |
| `UploadDocument` (`Models/UploadDocument.cs`) | UploadTypeDetailsId, Amount, FileName, **PathUrl**, Comment | UploadTypeDetail | One file in a tour-level bucket |
| `TransportationUpload` (`Models/TransportationUpload.cs`) | TransportationDetailsId, FilePath, FileName | TransportationDetail (claim row) | Ticket/bill of one travel row |
| `AccomodationUpload` (`Models/AccomodationUpload.cs`) | AccomodationDetailsId, FilePath, FileName | AccomodationDetail | Hotel bill of one stay row |
| `TransportationUploadHistory` / `AccomodationUploadHistory` | ClarificationId, *DetailHistoryId, FileName, FilePath | ClaimClarification | Snapshot of the files at the time of a clarification |
| `EmpTourPlan` path columns | `ManualApprovalfilePath`, `RequestPdfPath`, `ApprovalPDFPath`, `MergeApprovalPDFPath`, `OldApprovalPDFPath` | – | Single files and generated PDFs |
| Draft tables (`ClaimDraft`, `ClaimWithRequestDraft`, `TourRequestDraft`) | `DraftData` JSON containing `List<DraftFileMetadata>` (`Model/Dtos/Draft/DraftFileMetadata.cs`: FileId, RowIndex, OriginalFileName, TempFilePath, FileSize, FileType, UploadedAt, DisplayName) | – | Temporary draft files |

**UploadDocType seed / `RecordMasterUploadDocType`** (`TourProgram.Utility/Eumus/RecordMasterEnum.cs:217-239`):

| Id | Name | Used for | Storage folder |
|---|---|---|---|
| 1 | Travel | Travel estimate at tour request | `TourTravelEstimate` |
| 2 | Accommodation | Accommodation estimate | `TourAccommodationEstimate` |
| 3 | Nomination | Nomination letter (tour request, edit, claim-with-request) | `Nomination` |
| 4 | Additional | Any additional supporting docs (claim) | `Additional` |
| 5 | AdvancePaymentBill | Proof of advance payment / bills against the advance | `AdvancePaymentBill` |
| 6 | TourNotAviledRefundPaymentBill | Refund proof when a tour is not availed | `TourNotAviledRefundPaymentBill` |
| 7 | DA | DA estimate (tour request) / DA bills (supplementary) | `TourDaEstimate` / `DA` |
| 8 | Local | Local conveyance estimate | `TourLocalEstimate` |
| 9 | Accident | Accident/incident documents (`IsIncidentRelatedClaim`) | `Accident` |

Row-level bills are not stored as UploadDocType rows. They go to `TransportationUpload` (folder `Transportation`) and `AccomodationUpload` (folder `Accomodation`).

### A.2 Upload fields per flow (multipart/form-data)
| Flow / endpoint | DTO | File fields → destination |
|---|---|---|
| Tour request `POST api/TourPlan/TourRequest`, `PUT EditTourRequest/{tourId}` | `EmpTourPlanRequestDto` (`Model/Dtos/EmpTourPlanDtos/EmpTourPlanRequestDto.cs:40-110`) | `ManualApprovalfile` (single) → `EmpTourPlan.ManualApprovalfilePath`. `NominationFiles` → type 3. `TourTravelEstimate.TravelFiles` → 1. `TourAccommodationEstimate.AccommodationFiles` → 2. `TourDAEstimate.DaFiles` → 7. `TourLocalEstimate.LocalFiles` → 8 (`EmpTourPlanRepository.cs:412-583`) |
| Raise claim on approved tour `POST api/Claim/RaiseClaimRequest` and claim-with-request `POST api/Claim/raise-claim-with-request` | `ClaimApprovalDto` (`Model/Dtos/RaiseClaimDtos/ClaimApprovalDto.cs:10-62`) | `TransportationUploadDto[]` → TransportationUpload by range. `AccomodationUploadDto[]` → AccomodationUpload by range. `AdditionalFiles` → 4. `AccidentClaimFile` → 9. `AdvancePaymentBillUpload` → 5 (`ClaimRepository.cs:1656-1968`, `3243-3533`). `NominationFiles` is persisted **only** in raise-claim-with-request (passed to the tour request, `:2970`). **`DaUploadDto` is accepted but never persisted in either raise flow** (only commented code, `:2958`) |
| Supplementary `POST api/Claim/RaiseSupplementaryClaim` | `SupplementaryClaimDto` (`…/SupplementaryClaimDto.cs`) | Per row: `TransportationDetailDtos[i].TransportationUploadDto` and `AccomodationDetailDto[i].AccomodationUploadDto` (lists on each row, `:100/:137`). `DaUploadDto` → 7 (folder `DA`). `AdditionalFiles` → 4 (`ClaimRepository.cs:6852-7024`) |
| Claim edit `POST api/ClaimEdit/edit` | `EditClaimWithRequestDto` (`…/ClaimEdit/EditClaimWithRequestDto.cs:150-200`) | Same section lists plus `NominationFiles`. Deletions use `DeletedTransportationUploadIds`, `DeletedAccommodationUploadIds`, `DeletedAdditionalFileIds`, `DeletedNominationFileIds`, `DeletedAdvancePaymentFileIds` (comma-separated ids). `DaUploadDto` is ignored |
| Clarification response `POST api/ClaimClarification/respond` | `ClaimClarificationResponseModel` (`…/ClaimClarificationResponseModel.cs:110-180`) | `TransportationUploadDto`, `AccomodationUploadDto`, `AdvancePaymentBillUpload`, `AdditionalFiles`, plus `Deleted{Transportation,Accommodation,Additional,AdvancePayment}…Ids` |
| Tour-not-availed refund `POST api/TourNotAvailed/PaymentRefund/{tourId}/{PaidAmount}/{PaymentReferenceNo}` | form `Files` | → type 6, `UploadTypeDetail.Amount = PaidAmount` (`TourNotAvailedRepostitory.cs:33-80`) |
| Finance approval `PUT api/Claim/ApproveRaiseClaimAmount/...` | JSON `ApproveRaiseClaimModel.ScreenshotPath` (base64 PDF or image) | Converted to PDF at `wwwroot/UploadDocuments/{guid}.pdf` (`ClaimController.cs:255-293`) and merged into the approval bundle |
| Drafts `POST api/Claim/SaveRaiseClaimDraft`, `SaveClaimWithRequestDraft`, `SaveClaimDraft`, `api/TourPlan/SaveTourRequestDraft` | form `draftData` (JSON string) + lists | RaiseClaim draft: `additionalFiles, advancePaymentFiles, transportationFiles, accommodationFiles, AccidentClaimFile, nominationFiles`. ClaimWithRequest draft adds `daFiles`. Tour draft: `travelFiles, accommodationFiles, daFiles, localFiles, nominationFiles, manualApprovalFile` (`ClaimController.cs:54-104, 356-408, 479-527`; `TourPlanController.cs:159-170`) |

**Row ↔ file mapping (index ranges):**
- `TransportaionDetailDtos` / `AccomodationDetailDto` are **JSON strings** (arrays) inside the form. Each row has `StartFileIndexCount` and `EndFileIndexCount` (`ClaimApprovalDto.cs:79-80, 121-122`).
- The server takes `files[Start..End]` (inclusive) from the flat section list: `ClaimRepository.cs:1854-1864` (transport), `:1958-1968` (accommodation), and the same at `:3420/:3523`.
- `Start = -1` means the row has no files.
- In the raise flows the indices are **not bounds-checked**: a bad range throws and becomes a 500. Edit and clarification check `i < Count` (`ClaimEditRepository.cs:1076-1085`, `ClaimClarificationRepository.cs:1385-1394`).
- Drafts convert the ranges to `DraftFileMetadata.RowIndex` (`ClaimRepository.cs:7854-7950`) and use it when files are moved to permanent storage at submit.

**Draft file retention (`Retained*FileIds`):**
- Applies to `RaiseClaimDraftDto` (`Model/Dtos/Draft/RaiseClaimDraftDto.cs:52-64`): `RetainedAdditionalFileIds`, `RetainedAdvancePaymentFileIds`, `RetainedTransportationFileIds`, `RetainedAccomodationFileIds`, `RetainedAccidentFileIds`, `RetainedNominationFileIds`. The values are `DraftFileMetadata.FileId` (GUID "N").
- Rules (`ClaimRepository.cs:7564-7605`):
  - The list is `null` → keep all existing files.
  - The list is given → keep only the listed files and delete the rest from temp storage.
  - New files in the form are appended.
- Temp files are written under `wwwroot/temp-uploads/draft-{id}/…` or `claim-request-draft-{id}/…`. On submit they are copied or moved to `UploadDocuments/{Category}/` and linked, then the temp folder is cleaned (`:5452-5530`, `:6510-6560`).

**Deletion (`Deleted*Ids`):**
- `ParseDeletedIds` → `RemoveRange` hard-deletes the DB rows by id **without checking that they belong to the tour being edited** (`ClaimEditRepository.cs:902-953`; ClaimClarification has the same code, `:2062`, `:2233`).
- The physical files are not deleted.

### A.3 Validation actually enforced on the server
| Check | Claim/draft uploads | OCR upload |
|---|---|---|
| Extension whitelist | **None.** The original extension is kept (`CommanRepository.cs:24`) | `.pdf .jpg .jpeg .png` (`OcrDtos.cs:19`, `OcrRepository.cs:516`) |
| Per-file size | **None** (only `Length > 0` skips empty files, `CommanRepository.cs:21`) | `Ocr:MaxFileSizeMB` = 20 |
| Request size | No `[RequestSizeLimit]`, so the framework default applies (Kestrel ≈30 MB / IIS default) **(inferred)** | 200 MB (`OcrController.cs:19, 31-32`) |
| Count | None | `Ocr:MaxFilesPerUpload` = 20 |
| Content sniffing | None (only the screenshot `%PDF` check, `ClaimController.cs:272`) | None (the type comes from the extension) |
| Required docs per expense | **None** (see A.5) | n/a |

The only implicit type rule: the approval merge includes only `.pdf/.jpg/.jpeg/.png/.tiff/.psd` and silently skips anything else (`ClaimRepository.cs:4168-4174`). If the frontend enforces any limits, they are not mirrored here.

### A.4 Storage & retrieval
- **Write:** `CommanRepository.UploadDocumets` writes `wwwroot/UploadDocuments/{folder}/{Guid}{ext}` and stores `"/UploadDocuments/{folder}/{guid}.ext"` in `PathUrl`/`FilePath` (`CommanRepository.cs:23-36`).
- **Read:** files are listed inside the aggregate GET responses as `{PathUrl: baseUrl + path, FileName, id}`. Examples: `GET api/Claim/RaiseClaimRequest/{tourId}` (`ClaimRepository.cs:1400-1440`), `api/TourPlan/GetTourDetails/{id}` (`EmpTourPlanRepository.cs:111-181`), `GetSupplementaryClaimDetails`, `ClaimClarification/history`, and draft GETs (TempFilePath).
- **Download:** a plain static GET on the URL. `UseStaticFiles` runs before authentication (`TourProgram.API/Program.cs:93-94`), so **there is no auth and no ownership check**. A random GUID name is the only protection.
- **There is no dedicated document endpoint** (no list, download, delete or replace by id). The only way to delete is `Deleted*Ids` inside edit or clarification.
- Retrieval reads only the **first** `UploadTypeDetail` of each type per tour (`…FirstOrDefault()`, e.g. `ClaimRepository.cs:1400`). The raise flows create a new `UploadTypeDetail` on every call (`:3245-3253`), so documents in later buckets may not show **(inferred risk)**.

### A.5 Required documents per expense
- `DocumentsAttached { IsApprovedTourProgramme, IsTickets_BoardingPass_Bills, IsHotelBills }` (`ClaimApprovalDto.cs:168-173`) is accepted on ClaimApprovalDto and round-tripped through draft JSON (`ClaimRepository.cs:5950, 6614-6619`). It is **never written to an entity and never validated** (no reads elsewhere in the BAL/DAL).
- Nothing in code requires a ticket per transport row, a hotel bill per non-own-arrangement stay, nomination for any tour type, and so on. `IsBillable` on `UploadDocType` is seeded `true` for all types and is unused.
- A comment says local tours "usually don't require nomination" (`ClaimRepository.cs:2970`), but no rule enforces it.
- **Rules about required documents must therefore come from policy or the frontend; the backend does not enforce them.**

---

## Part B — OCR subsystem

### B.1 Components
| Component | File | Role |
|---|---|---|
| `OcrController` | `TourProgram.API/Controllers/OcrController.cs` | 8 endpoints. Each checks `TokenDetails.EmpCode > 0` (401 otherwise, `:132-144`) and maps `ResponeModel.StatusCode` to the HTTP status (`:146-162`) |
| `OcrRepository` | `TourProgram.BAL/Repository/Concrete/OcrRepository.cs` | Validate, store and enqueue. List, get, read/release/cancel/retry. File lookup |
| `OcrProcessingService` | `TourProgram.BAL/Service/OcrProcessingService.cs` | Claims the next row (SQL lease), calls the provider, saves the result or error, handles retry |
| `OcrProcessingWorker` | `TourProgram.API/BackgroundServices/OcrProcessingWorker.cs` | `BackgroundService` with `MaxParallel` loops. Each loop gets a new DI scope, claims and processes |
| `OcrQueueSignal` | `TourProgram.BAL/Service/OcrQueueSignal.cs` | Singleton bounded `Channel<bool>(1000, DropWrite)` that wakes the worker on upload or retry |
| `OcrClock` | `TourProgram.BAL/Service/OcrClock.cs:9` | `UtcNow + 330 min`, so all OCR dates are IST without an offset |
| `OcrDocument` entity | `TourProgram.DAL/Models/OcrDocument.cs`; config `TourProgramDbContext.cs:245-252` | One row per file. `IsRead` is a **concurrency token**. Indexes (EmpCode, IsRead), (StatusId, NextAttemptDate), BatchId |
| Settings | `OcrSettings` (`Model/Dtos/Ocr/OcrDtos.cs:6-20`), section `Ocr` | `BaseUrl`, `ExtractPath` (/extract), `ApiKey` (redacted), `TimeoutSeconds` 600, `MaxParallel` 2, `MaxAttempts` 3, `RetryDelaySeconds` 30, `PollSeconds` 10, `MaxFileSizeMB` 20, `MaxFilesPerUpload` 20, `StoragePath` "OcrUploads", `AllowedExtensions` (code default) |

`OcrDocument` columns: Id, EmpCode, BatchId (Guid), FileIndex, Reference(200), FileName(300), StoredFilePath(500, relative to the content root), ContentType(100), FileSize, StatusId, AttemptCount, ProcessingStartedDate, NextAttemptDate, ResponseJson (nvarchar(max)), ErrorMessage(2000), ProcessedDate, IsRead, ReadDate, plus BaseModel (CreatedBy/Date, UpdatedBy/Date, IsActive).

Statuses (`RecordMasterOcrStatus`, `RecordMasterEnum.cs:86-98`): **1 Pending, 2 Processing, 3 Completed, 4 Failed**. "Cancelled" is not a status; it is `IsActive = false`.

### B.2 Endpoints
All routes are under `api/Ocr`. Every call needs `Authorization: Bearer <SSO token>`, validated by the global `SSOTokenValidateFilter`. The EmpCode comes from the token claim `UserId` (`BaseController.cs:18-60`, claim at :35). Read-only users get 403 on POST. Responses use the `ResponeModel` envelope and the HTTP status mirrors `statusCode`.

| Method & route | Request | Success `data` | Validations / errors |
|---|---|---|---|
| `POST Upload` (multipart, limit 200 MB) | `Files` (repeat), `Reference?` (≤200) | `{batchId, documents:[{documentId, index, fileName, status:"Pending"}]}`, `dataLength` | 400: no files / >20 files / bad ext, empty or >20 MB (all-or-nothing; `errorDetail:[{fileName, reason}]`) / Reference >200. On a DB failure the written files are deleted and the exception becomes 500 (`OcrRepository.cs:35-122`) |
| `GET GetMyDocuments` | query `IncludeRead`(false), `BatchId`, `Reference` (exact), `ReferencePrefix`, `IncludeResponse`(true), `PageNumber`(1), `PageSize`(50, clamped 1..200) | `{summary:{pending(=Pending+Processing), completed, failed, total}, documents:[OcrDocumentDto]}` plus paging fields | Only the caller's rows with `IsActive`. Newest first (`:124-192`) |
| `GET GetDocument/{documentId}` | – | `OcrDocumentDto` | 404 if missing, another user's, or cancelled (`:194-210`) |
| `GET GetFile/{documentId}` | – | **Raw file** (`PhysicalFile`). Content-Type is derived from the extension (pdf/jpeg/png, otherwise octet-stream); the download name is the original name | 404 JSON if missing, another user's, cancelled, or not on disk (`OcrController.cs:100-130`, `OcrRepository.cs:477-501`) |
| `POST MarkAsRead` | `{documentIds:[…]}` (≤500) | `{marked, alreadyRead, stillPending, notFound}` | Only Completed or Failed rows can be marked. Optimistic concurrency on IsRead means exactly one caller wins (`:295-368`) |
| `POST Release` | `{documentIds}` (≤500) | `{released, notRead, notFound}` | Reverse of MarkAsRead (`:230-293`) |
| `POST Cancel` | `{documentIds}` (≤500) | `{cancelled, notFound}` | Any status. Sets `IsActive = false`. Up to 3 concurrency retries (`:422-475`) |
| `POST Retry/{documentId}` | – | `OcrDocumentDto` (Pending) | 404 if not found. 400 if not Failed. **409** on a concurrency clash. Resets attempts, error and response, sets unread, and signals the queue (`:370-416`) |

`OcrDocumentDto` (`OcrDtos.cs:60-75`): `documentId, batchId, index, reference, fileName, status, queuePosition` (Pending only; count of all users' Pending+Processing rows with a lower Id), `isRead, uploadedDate, processedDate, readDate, errorMessage, response` (the provider JSON parsed as an object, or the raw string if it is not JSON).

### B.3 Queue mechanics
1. **Enqueue:** the Upload handler writes the files to `{CWD}/{StoragePath}/{empCode}/{guidN}{ext}` (`FileMode.CreateNew`), inserts rows with `StatusId = 1`, then calls `_signal.Notify(n)`.
2. **Claim:** an atomic SQL CTE (`OcrProcessingService.cs:30-40`) runs `SELECT TOP 1 … WITH (UPDLOCK, READPAST, ROWLOCK)`. It picks an active row that is either Pending with a due `NextAttemptDate`, or Processing with an expired lease (`ProcessingStartedDate < now − (TimeoutSeconds + 120 s)`), ordered by Id. It sets `StatusId = Processing`, `ProcessingStartedDate = now` and `AttemptCount + 1`. This is safe across loops **and across servers**.
3. **Process** (`:79-148`):
   - Skip the row if it is cancelled or no longer Processing.
   - `AttemptCount > MaxAttempts` (repeated crashes) → Failed.
   - File missing → Failed.
   - Otherwise POST to the provider:
     - 2xx with `meta.per_file_results[].success == false` → **Failed**, and the response is kept.
     - 2xx otherwise → **Completed** (`ResponseJson` = body).
     - 408/429/5xx, timeout or network error → `RetryOrFail`: back to Pending with `NextAttemptDate = now + RetryDelaySeconds × AttemptCount`, or Failed once `AttemptCount ≥ MaxAttempts`.
     - Other 4xx → Failed with `"OCR API {code}: {body}"` (truncated to 2,000 chars).
   - On app shutdown the row returns to Pending without consuming an attempt.
4. **Worker loop** (`OcrProcessingWorker.cs:296-328`): when a row was processed it loops immediately. Otherwise it waits for the signal or `PollSeconds`.
5. **Concurrency:**
   - `MaxParallel` = 2 loops **per app instance**.
   - The provider timeout is 600 s, so one big file can hold a slot for up to 10 minutes.
   - Cancel during Processing does not abort the HTTP call. The result is still saved on the inactive row and never shown.

### B.4 External OCR provider contract (as used)
- `POST {Ocr:BaseUrl}{Ocr:ExtractPath}`, header `x-api-key: {Ocr:ApiKey}`. Multipart with **one file per call** in field `files` (`:150-169`). The Content-Type is the client-supplied one from the upload.
- **Response:** stored verbatim. The only field the backend reads is `meta.per_file_results[].success/error` (`:172-195`). The documented shape (not validated in code):
  - `meta {processed, succeeded, failed, per_file_results[{filename, success, bill_type, error}]}`
  - `tourDetails {startDate, from, to, tourLegs[]}`
  - `accommodationDetails {totalAmount, entries[]}`
  - optional `sourceFileName`
  - (`docs/OCR_Frontend_Integration.md:222-225, 251-256`). **The backend has no typed model for this**, so the exact field list is defined by the provider.

### B.5 Isolation, retention, security
- **Per-user isolation:** every query filters `d.EmpCode == empCode && d.IsActive` with the EmpCode taken from the token (`OcrRepository.cs:127, 197, 239, 304, 373, 431, 480`). A user **cannot** read, mark or cancel another user's document; they get 404 or `notFound`. The token signature and introspection are enforced by the global filter. `BaseController` only decodes the claims.
- `queuePosition` reveals how many files exist across all users (counts only).
- **Retention:** none. Files, rows and cancelled rows are kept indefinitely, and no cleanup job exists.
- Duplicate files are not detected.
- Files are stored outside wwwroot, so `UseStaticFiles` cannot serve them.

### B.6 Doc vs code discrepancies (`docs/OCR_Frontend_Integration.md`)
| Doc says | Code | Note |
|---|---|---|
| "retries by itself up to 3 times" (:475) | `MaxAttempts` = 3 **total attempts** (2 retries); `RetryOrFail` fails at `AttemptCount ≥ 3` (`OcrProcessingService.cs:202`) | wording |
| Transient = "timeout, network error or 5xx" (:475) | Also **408 and 429** (`:197-198`) | doc omits these |
| "server runs 2 files at a time" / "2 files in parallel for the whole server" (:249, :472) | 2 per app instance (`MaxParallel` loops); the claim SQL supports several instances | fine on a single VM |
| Limits: 20 files × 20 MB | The request cap is **200 MB** (`OcrController.cs:19`), so 20 × 20 MB = 400 MB would be rejected by the framework before validation | gap |
| Section numbering 1-4, 6, 7 | No section 5 | cosmetic |
| `GetFile` returns 404 for cancelled | Yes (`IsActive` filter) | ✔ |
| 401 body `{message, statusCode, timestamp}` | From the SSO filter. The controller's own 401 (EmpCode ≤ 0) uses the `ResponeModel` envelope | two shapes |
| Content-Type `application/pdf\|image/jpeg\|image/png` | Plus `application/octet-stream` fallback | ✔ |
| Retry from any Failed | Retry also clears `IsRead` and `ResponseJson` | ✔ (documented) |
| Not documented | Results of files cancelled while Processing are still saved; `Reference` max 200 is checked **after** extension validation; the ContentType sent to the provider is client-controlled | – |

### B.7 How the Python agent should use OCR
- **Call the existing endpoints with the end-user's bearer token** (pass-through). Do not call the provider directly. This keeps `Ocr:ApiKey` on the server, keeps per-user isolation and the shared queue/backpressure, and makes results visible in the web UI.
- Suggested flow:
  1. `POST /api/Ocr/Upload` with `Reference = "agent:<session>:tour-<id>"`.
  2. Poll `GetMyDocuments?BatchId=…&IncludeResponse=false` every 15-30 s until `summary.pending == 0`, or let the user resume later via `ReferencePrefix=agent:`.
  3. `MarkAsRead([id])`, and act only on ids in `marked`.
  4. `GetDocument/{id}` to read `response`.
  5. Map to claim rows.
  6. On abort, call `Release`.
  7. To attach the bill to a claim, fetch `GetFile/{id}` and re-upload it in the claim/draft multipart, with `Start/EndFileIndexCount` pointing at it.
- **Prefer saving a draft** (`SaveRaiseClaimDraft` / `SaveClaimWithRequestDraft`) over a direct submit so a human confirms the OCR-derived amounts.
- Treat OCR output as **unvalidated suggestions**. The backend does not validate or normalise it (dates, amounts, GST). The agent must validate against entitlement endpoints and let the server's overlap rules (409) arbitrate.
- Do not run a separate OCR in the agent unless the provider is down. If you must, note that results would not show up in the user's OCR list.

### B.8 Gaps
- No server-side type or size validation for claim/draft uploads, and no AV scan.
- All claim documents and generated PDFs are publicly downloadable from wwwroot without auth.
- No document API (list, download, delete or replace by id). `Deleted*Ids` is not scoped to the tour, and physical files are never deleted. Orphan files are left on rollback.
- `DocumentsAttached` is not persisted, there are no required-document rules, and `DaUploadDto` is ignored in raise and edit.
- The OCR → claim link is not stored: nothing records which `OcrDocument` produced which claim row.
- OCR has no retention, no dedup, no typed result schema, no per-user quota, and the 200 MB request cap is inconsistent with the doc.

### B.9 Unresolved questions
1. What schema and version does the OCR provider guarantee, including the full field list of `tourDetails`/`accommodationDetails` and the `bill_type` values?
2. Is any document mandatory per expense type (tickets for Air/Rail, hotel bills unless own arrangement, nomination for outside-jurisdiction)? Today this is only frontend or policy.
3. Is unauthenticated `wwwroot/UploadDocuments` access acceptable, e.g. is it protected by a reverse proxy?
4. Should the agent's OCR uploads use a reserved `Reference` prefix, and should results be persisted as a link to the draft or claim?
5. Should `DaUploadDto` (DA bills) be stored in the raise flows, or is the field obsolete?
6. What are the intended retention and cleanup policies for `OcrUploads/` and `wwwroot/temp-uploads/`?
