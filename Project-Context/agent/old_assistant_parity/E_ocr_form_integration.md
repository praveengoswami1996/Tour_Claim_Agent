# Spec E — Claim Assistant ↔ OCR ↔ claim form ↔ backend (old React assistant)

Source of truth: `D:\CETPA\Tour_React` (read-only). Every path below is relative to that repo unless stated.
Goal: a server-side re-implementation (`D:\CETPA\TourClaim_AI_Agent`) must produce **the same backend calls in the same order with the same payloads**.

Files covered (read in full): `src/lib/ocr/{ocrApi,ocrJobs,ocrClaims,ocrResults,ocrReferences}.ts`, `src/components/common/OcrReadyNotifier.tsx`, `src/lib/claimPayloadBuilder.ts`, `src/pages/RaiseClaim/RaiseClaim.tsx`, the relevant parts of `src/pages/RaiseClaim/ApproverPage.tsx`, `src/app/hooks/useClaimDraft.ts`, `src/lib/claimChatbot/{draftHandoff,keptReports,fileHash,savedClaim,ocrMapping,rowOrder,billOutcome,loadAssistantTour}.ts`, the OCR/draft parts of `src/components/claimChatbot/{ClaimChatbot,ClaimAssistantLauncher}.tsx`, `src/features/RaiseClaim/{raiseClaimDraftSlice,raiseClaimSlice,getRaiseClaim,SubmitFinalDraftSlice}.ts`, `src/services/axiosInstance.ts`, and tests `tests/claimChatbot/{ocrReferences,ocrResults,billDuplicates,claimLink,keptReports}.test.ts`.

> Note: `src/features/RaiseClaim` and `src/features/raiseClaim` are the same folder (Windows, case-insensitive). Imports use both spellings.

---

## 0. Transport conventions (all calls)

* Base URL: `environment.apiUrl` (Tour API). One axios instance, default header `Content-Type: application/json`.
* Request interceptor (`src/services/axiosInstance.ts`):
  * `Authorization: Bearer <OIDC access_token>` (read from sessionStorage `oidc.user:<authority>:<client_id>`).
  * `DeviceType: web` on **every** request.
  * Read-only (impersonated) login (`tokenData.decoded.IsReadOnly === 'True'`): any POST/PUT/PATCH/DELETE is cancelled client-side (`axios.Cancel('READ_ONLY_MODE')`) after the toast **"This is an impersonated login. You are not authorized to perform this action."**
* Response interceptor: HTTP 401 → clear storage, toast "Session expired. You will be redirected to DFC Portal.", redirect after 4 s — **unless** the request had `skipAuthRedirect: true`. All `/Ocr/*` calls set `skipAuthRedirect: true` (`ocrApi.ts:48`); the Claim/TourListing calls do not.
* Every Tour API answer is an envelope `{ statusCode, message, data, totalRecords? , errorDetail? }`. The OCR client treats `body.statusCode >= 400` as an error even on HTTP 200 (`ocrApi.ts:69-72`). The draft thunks check `statusCode === 200` explicitly.
* Multipart posts explicitly set `Content-Type: multipart/form-data` (axios then sends the real boundary).

---

## 1. Backend calls, in order, per scenario

### 1.1 App start (any page) — `OcrReadyNotifier` (mounted once in `src/components/layout/app-layout.tsx:19`)

Starts only when: OIDC authenticated, not loading, access token present, `state.user.empId` known, user not loading, and `state.applications.applications.length > 0` (`OcrReadyNotifier.tsx:41-45`). If the employee id changes, `resetOcrJobs()` clears everything (timer, hidden ids, local files, snapshot).

Then (`OcrWatcher`):
1. `setOcrUser(empId)` – local claims keyed per employee.
2. Subscribe to the shared poller → **GET `/Ocr/GetMyDocuments`** params `IncludeResponse=false`, `PageSize=200` (no `ReferencePrefix`, no `BatchId`, no `IncludeRead`). Response used: `data.summary {pending,completed,failed,total}` (default zeros), `data.documents[]`, top-level `totalRecords`. Each document normalised to `{documentId:Number, batchId:String, reference|null, fileName, index (0 if missing), status, isRead, uploadedDate, processedDate, readDate, errorMessage, queuePosition, response}`.
3. `recoverOcrClaims()` immediately, and again after **75 000 ms** → **POST `/Ocr/Release`** `{ "documentIds": [orphaned…] }` only if orphaned ids exist (see §3.5).

The poller continues per §3.4.

### 1.2 Raise Claim page load (`RaiseClaim.tsx`)

In this order (effects, all fire on mount once `recipientId = user.EmpId ?? user.empId` is known):
1. **GET `/TourListing/GetTourRequest/{empId}`** params `{ toureType: 1 }` (`raiseClaimSlice.ts:78`, param name literally `toureType`; `search` added only when the page search box is used, 400 ms debounce). Response: `response.data` (envelope) → `.data` array stored as `recipientTours.data`. Row fields used: `tourId, source, destination, tourStartDate, tantetiveEndDate, totalDays, status, statusId, isAdvanceTaken, tourType, requestDate`.
2. **GET `/Claim/GetAllRaiseClaimDrafts`** params `{ empId }` (`raiseClaimDraftSlice.ts:196`). Success iff `statusCode === 200` → `data || []`. Row fields used: `id` (= draftId; fallback `draftId`), `empTourPlanId`, `createdDate`, `lastSavedDate`, `lastEditedSection`, `draftName`. Failure toast: server `message` or **"Failed to retrieve drafts"**.
3. GET `/ModuleManagement/GetAllMasterData` on the DMS API (page, not assistant).

**`/TourListing/GetTourRaiseClaimList` is never called by the old assistant.** Claimable tours = rows of GetTourRequest(toureType=1) with `statusId === 2 || (statusId === 9 && isAdvanceTaken)` (`RaiseClaim.tsx:74`, `claimableTours` at :341).

Values passed to the assistant (`RaiseClaim.tsx:882-894`):
* `tours` = claimable rows; `toursLoading = loading || (tourData == null && !toursError)`.
* `draftTourIds = allDrafts.map(d => d.empTourPlanId)`.
* `draftsLoading = !!recipientId && getAllDraftsStatus ∈ {'idle','loading'}`.
* `draftsInfo = allDrafts.map(d => ({ tourId: Number(d.empTourPlanId), lastSaved: d.lastSavedDate ?? d.createdDate }))`.
* `pageInfo = describeRaiseClaimPage(tourData, allDrafts)` (text for the LLM: the 5 tabs with ≤40 tours each, then saved drafts ≤40).
* If `tourClaimAgent.enabled` (config), the new `TourClaimAgentLauncher` is mounted **instead** of the old assistant.

### 1.3 Opening the chat + greeting (Raise Claim page)

1. When the window becomes active: `refreshOcrDocuments()` → **GET `/Ocr/GetMyDocuments`** (`IncludeResponse=false, PageSize=200`; an in-flight call is reused). A 10 000 ms timer lets the greeting go on without it (`ClaimChatbot.tsx:441-446`).
2. Greeting waits for `!toursLoading && !draftsLoading` and (`ocr.loaded` or the 10 s timeout) (`:490-494`).
3. The chat's own documents: `ocr.documents` filtered by `isRaiseClaimChatRef(reference)` (page) / `isClaimChatRef(reference, tourId)` (in-form) and not in `discardedRef` (`:431-433`).
4. Greeting branches (`:495-536`):
   * no claimable tours → fixed text (no calls).
   * kept reports for scope `page` exist → said with the hello (no calls) — §4.1.
   * chat documents exist: if any is finished → greeting is deferred and becomes the read report header (`welcomeRef`); if all are in flight → "still being read" text.
   * else claimable tours with a draft (`draftTourIds`): for the **first 10** of them, in parallel, `loadSaved(tourId)` → `onLoadDraft` → **GET `/Claim/GetRaiseClaimDraft`** params `{ empId, empTourPlanId }` via plain axios (`RaiseClaim.tsx:385`, no toast; 404 → null; result used iff `statusCode === 200 && data`). The returned `data` is parsed with `claimStateFromDraft` (§2.6) and cached in `savedRef` (one read per tour per chat on the page).
5. If the drafts list changes while only the greeting has been said, the chat clears and greets again (re-running step 4).

### 1.4 Upload (chat)

`addBills(files)` (`ClaimChatbot.tsx:804-863`), no backend call yet:
1. `checkFiles` (intents.ts): only `.pdf/.jpg/.jpeg/.png` with type `''|application/pdf|image/jpeg|image/png`, size > 0 and ≤ 20 MB (`MAX_FILE_BYTES = 20*1024*1024`); HEIC, office/zip files and others are rejected with a reason; rejected are reported with the next read.
2. SHA-256 of each accepted file (`fileHash.ts`, hex, `null` if `crypto.subtle` missing or unreadable).
3. Duplicate if hash ∈ known.read (hashes of bills read in this chat **and still in its claim**: `readHashesRef` filtered by `readFilesRef`), ∈ known.staged (hashes of staged bills + hashes of this session's in-flight uploads `sentHashesRef`), or repeated in this upload (`billOutcome.uploadVerdict`). `null` hash → always new.
4. Same-name files get renamed `name (2).ext`, `name (3).ext`… among the staged bills.
5. At most **`MAX_FILES_PER_READ = 20`** staged; extra ones are refused with a message listing ≤10 names.
6. Each staged file is echoed as a user message `📎 <name>`.

Then automatically (effect `:1698-1707`, once per fileKey `name|size`) `uploadBills()` (`:1307-1340`):
* Offline (`navigator.onLine === false`) → message, no call.
* **POST `/Ocr/Upload`** multipart: one part **`Files`** per staged file (in staged order, filename = staged name), then **`Reference`** = `outside-chat:unassigned` (Raise Claim page) or `claim-chat:tour-<tourId>` (assistant inside a claim form). All-or-nothing on the server.
* Response used: `data.batchId`, `data.documents[]` (each: `documentId`, `batchId` (fallback upload batchId), `reference` (fallback sent ref), `index` (fallback position), `status`; `isRead` forced false).
* After success: `localFiles[documentId] = files[d.index]` (the bytes stay in the browser), docs prepended to the poller snapshot, poller rescheduled; `sentHashesRef[documentId] = hash`.
* Error with `errorDetail[]` → `OcrApiError.invalid = [{fileName, reason}]` → message listing them; otherwise the error message.

### 1.5 Polling — §3.4. Only `GET /Ocr/GetMyDocuments` (`IncludeResponse=false`).

### 1.6 Taking results (chat)

Trigger (`ClaimChatbot.tsx:1395-1408`): chat active, not busy, narrator not writing, not already taking, tours loaded and non-empty, greeting done; inside a form also the tour must be picked; no yes/no question, no "start over?" question, no add/replace question, and step ∉ `confirmTour, pickTour, bulkDrafts, splitTours, submit`. **All finished batches** (`batchesOf(myDocs).filter(done)`) are merged into one take (`local` = all batches local).

`takeResults(batch)` (`:1343-1391`):
1. **POST `/Ocr/MarkAsRead`** `{ "documentIds": [all ids of the merged batches] }`. Response used: `data.marked[]`, `alreadyRead[]`, `stillPending[]`, `notFound[]`. Ids in `marked` are recorded in localStorage claims (§3.5); `marked ∪ alreadyRead ∪ notFound` are hidden from the list locally. If `marked` is empty → stop (if the greeting was waiting for these, greet again normally).
2. `readTakenDocuments(docs ∩ marked, { withFiles: true, takenNames: names of every file already in the chat's rows })` (`ocrJobs.ts:436-459`):
   * order: by first appearance of each batchId, then by `index`;
   * **GET `/Ocr/GetDocument/{documentId}`** (in parallel) for each doc with `status === 'Completed'` (full result incl. `response`); others reuse the light doc;
   * file bytes: the local `File` if uploaded in this session, else **GET `/Ocr/GetFile/{documentId}`** (`responseType: blob`; File named `doc.fileName`, type = blob type or by extension `.pdf→application/pdf, .png→image/png, .jpg/.jpeg→image/jpeg`); a failed download → `file = null` (not an error);
   * names made unique with `uniquelyNamed(files, takenNames)` (`ticket (2).pdf`…);
   * per file `FileResult = { fileName: uniqueName, failed: status !== 'Completed', response: doc.response ?? null }`.
3. Every taken id is put in `takenRef` with its File (or null). Staged bills = taken files with a hash (session hash or recomputed).
4. `lost` = files with no bytes that did not fail → note "⚠️ The server no longer has …".
5. `combineOcrResults(...)` (§3.1) → `processRead` → `autoSaveRead` (page, §1.7) or `formRead` (in-form, §1.10).
6. On any exception in 2–5: all `marked` ids → `discardedRef` (not taken again in this chat) and **POST `/Ocr/Release`** `{documentIds: marked}`, then the error text.
7. Finally `settleTaken()` (§3.5): every taken id whose File is not in any unsaved place of the chat is **settled** — local claim dropped, **no backend call** (the server already has it as read). This includes unreadable bills, bills for no tour, duplicates and bills already in a draft.

`failedDocs` = taken docs with `failed` → chip "↻ Read the unreadable bill(s) again" → for each **POST `/Ocr/Retry/{documentId}`** (no body; HTTP 409 → tried exactly once more), then unhide + refresh.

"⏹ Stop processing" (shown while any chat doc is in flight) → **POST `/Ocr/Cancel`** `{documentIds: in-flight ids}`; ids → `discardedRef`, `sentHashes` removed.

### 1.7 Auto-save into a tour's draft (Raise Claim page) — `autoSaveRead` (`ClaimChatbot.tsx:1435-1556`)

No question is asked; everything read goes straight into drafts.
1. `mapOcrToRows(combined, stagedBills)` (§2.7) → rows, `failedFiles`, `unmatchedFiles`.
2. Stay rows with a bill problem (`stayBillProblem(row, undefined, [])`, e.g. check-out before check-in) are dropped (fate `problem`).
3. `triageRows(mapped, allClaimableTours)` → `groups[{tour, journeys, stays}]`, `neutral`, `dropped` (fate `noTour`). Neutral rows go to the single group, else (no group) to the chat's picked tour, else the only claimable tour; otherwise fate `unsure`.
4. For each group, **sequentially**:
   * **If the group's tour is the chat's picked tour**: `mergeOcrRows(stateRef, rows)`; nothing new → fate `alreadySaved`; else commit and `saveToDraft(tour)` (§1.8) → fate `saved` / `saveFailed`.
   * **Otherwise**: `hasDraft = draftTourIds ∋ tour || savedHere ∋ tour`; `saved = loadSaved(tourId, fresh=true)` → **GET `/Claim/GetRaiseClaimDraft`** `{empId, empTourPlanId}` (always, even with no known draft).
     * `hasDraft && !saved` → fate `saveFailed`, rows given back (never overwrite an unreadable draft).
     * `compareWithSaved(saved, rows)` by `journeyKey`/`stayKey`; already-held rows → fate `alreadySaved`; nothing added → just offer the claim.
     * state = `saved ? addToSaved(saved, added) : mergeOcrRows(initialClaimState(), added).state`.
     * `onSaveDraft(tour, {tourId, state}, { overwrite: !!saved })` → `saveDraftFromAssistant` (§1.8, §2).
     * `'saved'` → fate `saved`, `savedHere += tour`, localStorage `aryaan.chatDrafts.v1.<empId>[tourId] = ISO now`, the cached saved draft is dropped (re-read next time). Else fate `saveFailed`, rows given back.
5. **POST `/Ocr/Release`** for the taken ids whose files are in not-saved rows (`giveBackFilesOf(notSaved)`; those ids also go to `discardedRef`).
6. Files whose fate is `saved`/`alreadySaved` are remembered (`readFilesRef`, `readHashesRef`) so attaching them again is a duplicate.
7. The report is said and kept (§4.1) with `tourIds` = saved tours ∪ tours with an existing draft; their "Open claim" link buttons use `claimLink(tour)`.

### 1.8 `saveDraftFromAssistant` (`RaiseClaim.tsx:397-445`) — the only draft writer on the Raise Claim page

```
known = allDrafts.find(d => d.empTourPlanId === tourId)        // page's GetAllRaiseClaimDrafts list
if known:   if !overwrite → return 'exists'; draftId = known.id ?? null
else:       GET /Claim/GetRaiseClaimDraft {empId, empTourPlanId}   (plain axios; 404 = none; other error → 'failed')
            if statusCode===200 && data: if !overwrite → 'exists'; draftId = data.data.draftId ?? null
draftPayload = buildClaimPayload({...see §2})
{transportationFiles, accommodationFiles} = collectNewFiles(rows)
POST /Claim/SaveRaiseClaimDraft  (thunk saveDraft)              // multipart, §2.4
GET  /Claim/GetAllRaiseClaimDrafts {empId}   (dispatched, not awaited)
return 'saved'      // any exception → 'failed'
```

`saveToDraft(row)` in the chat (`ClaimChatbot.tsx:756-772`) — used for the chat's picked tour and "Save as draft": calls the above with `state = asStored(chatState)` and `overwrite = savedRef.has(tourId)` (a saved draft was read in this chat). After `'saved'`: mark saved-in-chat, then `loadSaved(tourId, fresh=true)` → **GET `/Claim/GetRaiseClaimDraft`** again so the new files now carry server `fileId`s (not re-uploaded); the chat state is replaced by the read-back draft and its stored row order is reset to the draft's.

Bulk drafts (`saveBulk`, `:1042-1092`) do the same per tour sequentially (`overwrite: !!savedMatch`), then Release for tours not saved, then `settleTaken()`.

Thunk `saveDraft` (`raiseClaimDraftSlice.ts:125-154`): success iff `response.data.statusCode === 200` → `data` (`draftId, empTourPlanId, lastSavedDate, fileSummary{...}, message`); non-200 → reject with `message ?? 'Unexpected error'` (no toast); HTTP error → **toast.error(server message || "Failed to save draft")** (visible on the page even when the chat saved) and reject.

### 1.9 Fill-the-form handoff (Raise Claim page → claim form)

`doFill` (`ClaimChatbot.tsx:1793-1808`) → `onFillForm(row, {tourId, state: asStored(chatState)})` → `fillFormFromAssistant` (`RaiseClaim.tsx:367-375`):
1. `setAssistantDraft(draft)` — in-memory module variable (`draftHandoff.ts`), lost on reload.
2. `startClaim(row, true)` (`RaiseClaim.tsx:311-339`):
   * **GET `/Claim/GetRaiseClaimDraft`** `{empId, empTourPlanId}` via thunk (statusCode 404 or HTTP 404 → null; other error → toast server message / "Failed to retrieve draft", and the page toasts **"Failed to fetch claim data."**, returns false).
   * **GET `/TourPlan/GetOwnArrangmentAmount`** (dispatched, not awaited).
   * navigate `/raise-claim-approver` with state `{ tourId, draftId: existingDraft?.id, statusId, isDraft: true, totalDays, fromAssistant: true }`.
   * If not opened → `takeAssistantDraft(tourId)` discards the hand-off.
3. If opened, the chat settles every taken result whose file is in the handed state (no backend call).

On the claim form (`ApproverPage.tsx`), relevant calls on mount: **GET `/Claim/RaiseClaimRequest/{tourId}`** (`getRaiseClaimData`), **GET `/TourListing/GetTourTrajectory/{tourId}`**, **GET `/ClaimClarification/{tourId}`**, and (useClaimDraft, when draft saving is enabled: not read-only, not clarification, user and tour known, claim data loaded) **GET `/Claim/GetRaiseClaimDraft`** `{empId, empTourPlanId}`.
Hand-off application (`:1931-1941`): waits until claim data is loaded and, when a saved draft exists, until it has been resumed; then `takeAssistantDraft(tourId)` (only for the same tour, once) → `applyAssistantDraft` (`:1903-1927`):
* journeys (if any non-empty): rows replaced; `RetainedTransportationFileIds` = every `fileId` in the rows' `uploadedFiles`;
* stays (if included and any non-empty): rows replaced, accommodation on, `RetainedAccomodationFileIds` = `fileId`s in `billFiles`;
* leave (if included and any non-empty): rows replaced, leave on;
* `hasUserModifiedDA = false` (the form recalculates DA);
* toast **"Claim Assistant filled your form. Please review it before submitting."**
The form's own auto-save then writes the draft (§1.11).

### 1.10 Assistant inside the claim form (`formRead`, `ClaimChatbot.tsx:1563-1638`)

Same upload/poll/take as above with Reference `claim-chat:tour-<id>` and kept-report scope `tour-<id>`. After a take:
1. map rows; drop stays with a bill problem (with the tour's rules); triage against the tours with this tour preferred; dates outside the tour → `noTour`.
2. `syncFromForm()` (bridge `read`); rows already in the form (same `journeyKey`/`stayKey`) → `alreadySaved`.
3. `mergeOcrRows(current, new)`; if something was added: air mode cleared when the tour has no air leg; `commit(next)` → bridge `write(asStored(next))`; then bridge `saveDraft()` = `forceSave()` (§1.11) → SaveRaiseClaimDraft + GET GetRaiseClaimDraft; if it did not return `false`, the taken files in the claim are settled.
4. Report + "Do you have more tickets or bills to upload for this claim?"; step `moreFiles`.

The in-form assistant never calls `saveDraftFromAssistant` (`assistantSaveDraft` always returns `'failed'`, `ApproverPage.tsx:2186`), never opens/fills another form (`assistantFillForm` = `applyAssistantDraft` in place; `assistantOpenClaim` = true), and its `onLoadDraft` returns the draft already in the store (no call).

### 1.11 In-form save (form's `useClaimDraft`, `src/app/hooks/useClaimDraft.ts`)

`forceSave()` (bridge `saveDraft`, also first change, 60 000 ms debounce after any change, unmount, `beforeunload`):
1. **POST `/Claim/SaveRaiseClaimDraft`** — `draftData` = `buildClaimPayload` with the **form's** full state (§2.5), files from `collectNewFiles`.
2. **GET `/Claim/GetRaiseClaimDraft`** `{ empId: user.empId, empTourPlanId }` → `draftData.transportationFileReferences` / `accomodationFileReferences` are matched to rows by `(rowIndex, originalFileName === file.name)`; matched new files become `{fileId, tempFilePath, isRetained: true, file: undefined}`.
Resolves `true` on success, `false` when disabled or on error. The form's own "Save Draft" behaves the same.

> Observation: `forceSave` uses the closure of the last render. The chat calls it right after `bridge.write()` (`formRead` line 1609, `finishRead` line 1129) without waiting for a re-render, so that save probably carries the form state from before the write; the new rows reach the server with the next save (60 s debounce, unmount or beforeunload). The same applies to the form's own `applyOcrBatch` (`ApproverPage.tsx:905`). A faithful port should decide whether to reproduce the timing; the final draft content is the same.

### 1.12 In-form submit (bridge `submit`)

`assistantForm.submit` → `setIsDeclared(true)`; `handleSubmit(true)` (`ApproverPage.tsx:1512-1667`):
1. `collectSubmitProblems()`; if any → toast the first message and return (no throw: the chat still says "I've sent it for submission…").
2. (A FormData for `RaiseClaimRequest`-style submit is built but **only sent in clarification mode**, which the assistant never runs in.)
3. **POST `/Claim/SaveRaiseClaimDraft`** + **GET `/Claim/GetRaiseClaimDraft`** (`forceSave`).
4. **POST `/Claim/SubmitRaiseClaimDraft/{currentDraftId}`** params `{ empId: user.empId }`, body `null` (`SubmitFinalDraftSlice.ts:10`). `currentDraftId` = `state.claim.draftData.draftId` as captured when `handleSubmit` was created. Toast success "Claim draft submitted successfully!"; failure toast server message / "Failed to submit claim draft. Please try again.".
5. statusCode 200/201 or any `message` → `onActionSuccess()` or navigate `/raise-claim`.

**POST `/Claim/RaiseClaimRequest` is never called on the assistant path.** `GET /Claim/RaiseClaimRequest/{tourId}` is used to load tours (`loadAssistantTour.ts`: in parallel with **GET `/TourPlan/GetTourDetails/{tourId}`**; the plan's `data.tentativeTourPlans` override the claim's `tentativeTourPlans`/`tentativeTourPlanList`; tour = `{tourId, source, destination (empTourDetails ?? employeeDetails), startDate = trDetails.tentativeStartDate ?? details.tentativeStartDate, endDate = last plan date, purpose = details.purposeOfTour}`; rules via `tourRulesFrom`). On the page the load is cached per tour (`assistantTourCache`); a failure toasts "Failed to load the tour. Please try again." unless `quiet`.

---

## 2. The SaveRaiseClaimDraft payload produced by the assistant (Raise Claim page)

### 2.1 Builder call (`RaiseClaim.tsx:420-434`)

> **Port note:** this section describes the old code. The port deliberately sends `RetainedTransportationFileIds` /
> `RetainedAccomodationFileIds` when saving into an existing draft (deviation 2 in `01_deviations.md`).

```
buildClaimPayload({
  transportationRows: state.transportationRows,   // stored order (asStored), incl. blank placeholder rows
  accommodationRows:  state.accommodationRows,
  daRowsState: [],                // DA left to the form
  leaveRows: state.leaveRows,
  currentTab: 'Transportation',
  formData: {},                   // → no RecipentId, PurposeOfTour, ClaimAmount, PaidAmount, PaymentReferenceNo, DocumentsAttached
  includeLeave: state.includeLeave,
  includeAccommodation: state.includeAccommodation,
  empId: recipientId,             // user.EmpId ?? user.empId
  empTourPlanId: tourId,
  draftId,                        // null for a new draft
  retainedTransportationFileIds: overwriting ? [fileIds of uploadedFiles] : null,
  retainedAccommodationFileIds:  overwriting ? [fileIds of billFiles] : null,
  // hasResumedDraft NOT passed → false
})
```

**Because `hasResumedDraft` is false, `RetainedTransportationFileIds` / `RetainedAccomodationFileIds` are never put in the payload** (`claimPayloadBuilder.ts:429-438`), even when overwriting. The computed lists are discarded. (The backend's behaviour for an overwrite without retained ids decides whether saved files survive; the Python port must match whatever the old one sends: i.e. omit both keys.)

### 2.2 Top-level `draftData` (after `cleanObjectForPayload`, which drops `null`, `undefined`, `''` keys recursively and keeps `0`/`false`)

| Key | Value | Present when |
|---|---|---|
| `DraftId` | `known.id` (GetAllRaiseClaimDrafts row) or `GetRaiseClaimDraft.data.draftId` | only when overwriting an existing draft (null is dropped) |
| `EmpId` | `user.EmpId ?? user.empId` | always |
| `EmpTourPlanId` | tour id | always |
| `LastEditedSection` | `"Transportation"` | always |
| `Source` | `tripEnds(rows).first.source` | non-empty |
| `Destination` | `tripEnds(rows).last.destination` | non-empty |
| `TantetiveDateOfDeparture` | `tripEnds(rows).first.DateOfTravelSource` (raw row string, e.g. `"2026-10-01"`) | non-empty |
| `IsPaidViaCreditCard` | `false` | always |
| `TransportaionDetailDtos` | JSON **string** of the cleaned transport DTO array | array non-empty (always: state always has ≥1 transport row, possibly a blank one) |
| `AccomodationDetailDto` | JSON string of cleaned stay DTO array | `includeAccommodation && length > 0` |
| `LeaveAvailedDto` | JSON string of leave DTO array (**not** cleaned) | `includeLeave && leaveRows.length > 0` |
| `IsLeaveAvailedwithbetweentour` | `true` | with `LeaveAvailedDto` |
| `RecipentId`, `PurposeOfTour`, `ClaimAmount`, `PaidAmount`, `PaymentReferenceNo`, `NoOfDays`, `DailyAllowanceDto`, `CreditCardPaidAmount`, `DocumentsAttached`, `RetainedTransportationFileIds`, `RetainedAccomodationFileIds`, `IsIncidentRelatedClaim`, `IsCancelationClaim` | — | **never** from the assistant |

`tripEnds(rows)` (`rowOrder.ts:102-112`): rows with a departure or arrival date; first = earliest `"YYYY-MM-DD HH:mm"` departure (fallback arrival), last = latest arrival (fallback departure; ties → later row). With no dated rows: `rows[0]` and `rows[last]`.

Key order in the JSON (matters only cosmetically): `DraftId, EmpId, EmpTourPlanId, LastEditedSection, Source, Destination, TantetiveDateOfDeparture, IsPaidViaCreditCard, TransportaionDetailDtos, AccomodationDetailDto, LeaveAvailedDto, IsLeaveAvailedwithbetweentour`.

### 2.3 Row DTOs (`claimPayloadBuilder.ts:254-373`)

Transport (`TransportaionDetailDtos`), one per row **in stored order** (saved rows first in their saved places, new rows after them in date order), cleaned:

| Field | Value |
|---|---|
| `Id` | row index `idx` (0-based) — saved rows are **not** given their saved Id |
| `DateOfTravelSource` | `toDateOnlyStr(row.DateOfTravelSource)` (string → part before `T`; Date → local `YYYY-MM-DD`; `''` → dropped) |
| `TimeOfTravelSource` | first 5 chars of time, dropped if empty |
| `Source` | `row.source` (dropped if `''`) |
| `ModeOfTravel` | `row.ModeOfTravel` — `'select'` is **sent** for an unknown mode; values `air, rail, personal-vehicle, public-transport, official-entitled, official-beyond, official-dedicated` |
| `DateOfTravelDestination`, `TimeOfTravelDestiNation`, `Destination` | as above |
| `DistanceINKm` | `parseFloat(distanceKm) || 0` |
| `BilledAmount` | `parseFloat(travelCost) || 0` (OCR `billedAmount`) |
| `ClaimedAmount` | `parseFloat(claimedAmount) || 0` (OCR `totalClaimedAmount ?? claimedAmount`) |
| `SGSTAmount`,`CGSTAmount`,`IGSTAmount` | `0` if TaxStructure `NA`, else OCR `sgst/cgst/igst` (claimed tax) |
| `BillSGSTAmount`,`BillCGSTAmount`,`BillIGSTAmount` | `0` if `NA`, else OCR `billSGST/billCGST/billIGST` |
| `TaxStructure` | `row.taxStructure ?? 'SGST_CGST'`; OCR mapping: `NOT_APPLICABLE→NA`, `IGST→IGST`, anything else→`SGST_CGST` |
| `InvoiceNumber` | `invoiceNo` (dropped if `''`) |
| `InvoiceDate` | `toDateOnlyStr(invoiceDate)` (OCR value is a local `Date`) |
| `StartFileIndexCount`,`EndFileIndexCount` | §2.4 |
| `Remark` | `row.remarks` (dropped if `''`; saved rows keep their saved Remark) |

Not sent for transport: `TourNumber`, `isPaidUsingCard`, `financeRemark`, `approvedAmount`.

Stay (`AccomodationDetailDto`), only when `includeAccommodation`:

| Field | Value |
|---|---|
| `Id` | `idx` |
| `Location`, `HotelCity` | `row.location` |
| `ClassOfCity` | `row.classOfCity` (OCR `cityClass` or `'Select'` – `'Select'` is sent) |
| `IsOwnArrangement` | `row.ownArrangement === 'Yes'` |
| `IsFreeAccomodation` | `row.freeProvidedByCompany === 'Yes'` |
| `isCompanyLodginPaid` | `row.selectedType === 'company_paid'` |
| `HotelName` | `hotelName || ''` → dropped if empty |
| `DateOfCheckIn`/`DateOfCheckOut` | `toDateOnlyStr(...) || ''` → dropped if empty |
| `TimeOfCheckIn`/`TimeOfCheckOut` | first 5 chars, dropped if empty |
| `BilledAmount` | `parseFloat(billed) || 0` (OCR `invoice.billedAmount`) |
| `ClaimedAmount` | `parseFloat(claimed) || 0` (OCR `claimedAmountWithTax ?? claimedAmount`) |
| `BillSGST/CGST/IGSTAmount` | `0` if NA else OCR `billSGST/billCGST/billIGST` |
| `CGSTAmount`,`SGSTAmount`,`IGSTAmount` | `0` if NA else `ClaimedCGST/SGST/IGSTAmount` (= OCR `cgst/sgst/igst`) |
| `TaxStructure` | `taxStructure ?? taxType ?? 'SGST_CGST'` |
| `InvoiceNumber` | dropped if empty |
| `InvoiceDate` | raw `invoiceDate` string (`YYYY-MM-DD` from OCR), dropped if empty |
| `StartFileIndexCount`,`EndFileIndexCount` | §2.4 |
| `Remark` | `row.remarks` (dropped if empty) |

OCR stay type → flags (`ocrMapping.accommodationTypeFields`): contains `own` → own_arrangement (`ownArrangement:'Yes'`); `free` → company_free (`freeProvidedByCompany:'Yes'`); `paid` or `company` → company_paid; else `hotel`.

Leave (`LeaveAvailedDto`, not cleaned): `{ Id: 0, LeaveStartDate: row.startDate, LeaveEndDate: row.endDate, DADeductable: String(daDeductable).toLowerCase()==='yes', Amount: Number(amount||0) }` for every leave row.

Daily allowance: never (daRowsState = []).

**Placeholder row.** `initialClaimState()` has one blank transport row. When a draft is created from bills containing only stays, the transport list keeps that blank row, so `TransportaionDetailDtos` = `[{"Id":0,"ModeOfTravel":"select","DistanceINKm":0,"BilledAmount":0,"ClaimedAmount":0,"SGSTAmount":0,"CGSTAmount":0,"IGSTAmount":0,"BillSGSTAmount":0,"BillCGSTAmount":0,"BillIGSTAmount":0,"TaxStructure":"SGST_CGST","StartFileIndexCount":-1,"EndFileIndexCount":-1}]`. A saved draft with no journeys likewise keeps a blank row.

**Saved rows re-serialised.** Saved rows go through `claimStateFromDraft` (§2.6) and back through the builder, so they are rebuilt, not passed through. Losses on overwrite (faithful-port caveats):
* stay `InvoiceDate` is not read back → dropped;
* stay `ownArrangement`/`freeProvidedByCompany` stay `'No'` (only `selectedType` is set) → `IsOwnArrangement:false`, `IsFreeAccomodation:false` for saved own-arrangement/free stays (`isCompanyLodginPaid` survives);
* `Id` becomes the row index; `TourNumber`, `isPaidUsingCard`, DA, NoOfDays, ClaimAmount, RecipentId, PurposeOfTour, DocumentsAttached are not sent;
* times/dates normalised to `HH:mm` / `YYYY-MM-DD`.

### 2.4 Files: ranges, order, part names

* A file is **new** iff it has no `fileId`, is not `isRetained === true`, and is (or wraps as `.file`) a `File` (`isNewFile`, `:160-166`). Saved files (from `claimStateFromDraft`) have `fileId` + `isRetained:true` → never re-uploaded, never counted.
* Ranges (`computeFileIndexRanges`): per category, walk rows in stored order; a row with k new files gets `[running, running+k-1]`, else `-1/-1`. Indices are 0-based into that category's own file list. Transport and stay counters are independent.
* Multipart order (`saveDraft` thunk): `draftData` (JSON string) first; then each new transport file as part **`transportationFiles`**; then each new stay file as **`accommodationFiles`** (collected regardless of `includeAccommodation`); filename = the `File.name` (the unique name, e.g. `ticket (2).pdf`), content type = `File.type` (GetFile: blob type or by extension). No `additionalFiles`, `advancePaymentFiles`, `nominationFiles` parts.
* A row normally holds one bill (the OCR's `sourceFileName` match). Bills with nothing found (`unmatchedFiles`) are **not** attached on the Raise Claim page path (fate `nothingFound`).

### 2.5 The form's payload (in-form save / after hand-off) — for comparison

`useClaimDraft` → `buildClaimPayload` with: rows passed through `cleanArrayForPayload` first; `daRowsState` = the form's DA rows; `formData = clean({ recipientId: isBillPassReq ? superBillPassingManagerId : billPassId, purposeOfTour: claim.data.empTourDetails.purposeOfTour, claimAmount: totalAll, paidAmount: isAdvance && netClaim<0 ? |netClaim| : 0, paymentReferenceNo: '', documents })`; `currentTab` = current section name (default `'Transportation'`); `draftId` = hook state (from the initial GET / last save); `hasResumedDraft` = form state; retained ids = form state (`keptFileIds(rows)` after an assistant write; `[]` after an OCR "replace"; `null` → `[]` for transport / omitted for stays when `hasResumedDraft`); `myEntitlement` (DA slab); credit-card fields. So the form adds `RecipentId`, `PurposeOfTour`, `ClaimAmount` (truthy only), `PaidAmount` (truthy only), `NoOfDays`, `DailyAllowanceDto` (`{Id, Date, SourceTime, EndTime, DASlab, TotalHoursMinutes, PercentageAdmissible, Amount, IsApplicable, TourNumber:1}`), `DocumentsAttached` (only if tickets or hotel ticked: `{IsApprovedTourProgramme: tickets, IsTickets_BoardingPass_Bills: tickets, IsHotelBills: hotel}`), `CreditCardPaidAmount`, retained ids.

### 2.6 Reading a saved draft (`savedClaim.claimStateFromDraft`)

Input: `GetRaiseClaimDraft.data` (`{draftId, empTourPlanId, lastSavedDate, lastEditedSection, draftData, fileSummary}`); uses `draftData` (any key casing). Reads `TransportaionDetailDtos` (or `TransportationDetailDtos`), `AccomodationDetailDto` (or `AccommodationDetailDto`), `LeaveAvailedDto` (JSON strings or arrays), `transportationFileReferences`, `accomodationFileReferences` (`[{fileId, originalFileName|displayName, fileSize, rowIndex, tempFilePath}]`; files attach to the row whose index = `rowIndex`). DA is ignored. Journey ids `transport-draft-<i>`, stays `stay-draft-<i>`. `includeAccommodation = stays.length > 0`; `includeLeave` = any leave with a start or end date.

### 2.7 OCR result → rows (`ocrMapping.mapOcrToRows`)

* Main journey from `tourDetails` if it has from/to/start/end date; then each `tourLegs[i]` with content. Dates via `toIsoDate` (ISO, `dd/mm/yyyy`, `dd-mm-yy`, "5th October 2026"; no year → empty), times via `toTime` (`HH:mm`). Mode via `mapTravelMode` (bus/taxi/cab/auto/public → public-transport; personal/own; air/flight; rail/train; entitled/beyond/dedicated; else `select`).
* Stays from `accommodationDetails.entries` with location or a date.
* The bill file is attached to the row whose `sourceFileName` equals its (unique) name.
* `failedFiles` = `meta.per_file_results` with `success:false`; `unmatchedFiles` = read files tied to no row.

---

## 3. OCR plumbing

### 3.1 Joining per-file results (`ocrResults.combineOcrResults`)

The server reads each file separately; the mapping expects one `/extract`-style result for all files. For each file, in upload order:
* failed (server status ≠ Completed), or OCR-failed (`meta.per_file_results` non-empty and all `success:false`), or no response → `per_file_results.push({filename, success:false})`, contributes nothing;
* else `per_file_results.push({filename, success:true, bill_type: response.meta.per_file_results[0].bill_type})`;
* every `amountDetails`, leg `amountDetails`, stay and stay `invoice` gets `sourceFileName = fileName` (overriding the OCR's, which is often empty);
* the first file's `tourDetails` (with from/to/startDate/endDate) is the main journey; each later file's `tourDetails` becomes a leg `{departureDate:startDate, departureTime:startTime, arrivalDate:endDate, arrivalTime:endTime, from, to, modeOfTravel, distance, amountDetails}`; every file's own `tourLegs` are appended as legs;
* stays are concatenated;
* no main journey but some legs → the first leg leads, the rest stay legs;
* result `{ tourDetails?, accommodationDetails: { entries }, meta: { per_file_results } }` (no `totalAmount`).
`uniquelyNamed(files, taken)`: second `ticket.pdf` → `ticket (2).pdf`; names already in the claim count as taken.

### 3.2 Reference tags and who picks up what (`ocrReferences.ts`)

| Sent from | Reference | Picked up by |
|---|---|---|
| Claim Assistant, Raise Claim page | `outside-chat:unassigned` (`RAISE_CLAIM_CHAT_REF`) | the page chat: `isRaiseClaimChatRef` = starts with `outside-chat:` and is **not** `outside-chat:tour-<n>` |
| Claim Assistant inside claim form of tour N | `claim-chat:tour-N` | that form's chat: `isClaimChatRef(ref, N)` = `claim-chat:tour-N` or legacy `outside-chat:tour-N` |
| Claim form "Process & Auto-Fill" | `claim-form:tour-N` | that form's own OCR fill only (`reference === claimFormRef(tourId)`) |
| Within-jurisdiction plan | `within-plan` | Within Jurisdiction page |
`claimTourOf(ref)` → N for `claim-chat:tour-N`, `outside-chat:tour-N`, `claim-form:tour-N`; else null. Tests: `tests/claimChatbot/ocrReferences.test.ts`.

### 3.3 Statuses

`Pending | Processing | Completed | Failed`; in flight = Pending or Processing. A batch (same `batchId`, or `doc-<id>` if none; files sorted by `index`; batches sorted by smallest documentId) is `done` when no file is in flight; `local` when every file's bytes are in this session. `queueAhead` = min `queuePosition` of Pending docs. The list holds "not read yet" documents (in flight + untaken results).

### 3.4 Polling (`ocrJobs.ts`)

* One shared poller for the whole app; started by the first subscriber (OcrReadyNotifier).
* `POLL_MS = 20 000` while any listed document is in flight and the tab is visible; nothing scheduled when nothing is in flight.
* After a failed check: `RETRY_MS = 60 000`.
* Tab hidden → no timer; visible again → immediate refresh.
* No call without an access token. An in-flight call is shared.
* Ids taken/cancelled locally are hidden until the server stops listing them; `unhide` refreshes immediately.
* Always `IncludeResponse=false`, `PageSize=200`; full results only through `GetDocument/{id}`.

### 3.5 Taking, settling, giving back; heartbeat/lease (`ocrClaims.ts`)

* Tab id: `<Date.now base36>-<6 random base36>`, per page load.
* `localStorage['aryaan.ocrTabs.v1']` = `{ tabId: lastBeatMs }`; beat on first take/recover and every **15 000 ms**; entries older than **10 min** are pruned on each beat.
* `localStorage['aryaan.ocrClaims.v1.<empId|anon>']` = `{ documentId: { tab, at } }`, written for every id in `marked`.
* **take** = MarkAsRead; only `marked` are applied here.
* **settle** (applied: in a saved draft, handed to the form, or not needed) = drop local claims + forget local bytes; **no server call**.
* **give back** = POST `/Ocr/Release`; on success drop claims + unhide (→ refresh); on failure keep the claims (recovered on the next load).
* **discard** ("stop", "discard") = POST `/Ocr/Cancel` then drop claims, forget bytes, hide; in the chat a failed discard falls back to settle.
* **pagehide** (not bfcache-persisted): ids claimed by this tab → `fetch(apiUrl + '/Ocr/Release', {method:'POST', keepalive:true, headers:{'Content-Type':'application/json', Authorization:'Bearer …', DeviceType:'web'}, body: {"documentIds":[…]}})` if online; claims dropped when sent; tab removed from `ocrTabs`.
* **recover** (app start and +75 s): ids claimed by another tab whose heartbeat is missing or older than **60 000 ms** (`STALE_MS`) → POST `/Ocr/Release`; on success drop + unhide.
* Chat unmount ("Start over", leaving the page): every still-taken id → Release.
* Retry: POST `/Ocr/Retry/{id}` (409 once more). Cancel: POST `/Ocr/Cancel`.

### 3.6 The form's own OCR fill (reference `claim-form:tour-N`, `ApproverPage.tsx:610-959`)

Not the assistant, but it shares the queue: upload with `checkFiles` + ≤20 files, `tagOcrDocuments({id: 'transport'|'accommodation'})` in `localStorage['aryaan.ocrTags.v1']`. When a batch is done (not in expanded/read-only/clarification mode, claim loaded): older done batches are cancelled; the newest is filled at once if local, else a dialog asks (Fill / Not now / Discard). Fill = MarkAsRead → GetDocument/GetFile → `fillFormFromOcr` (**replaces** journeys and stays, retained ids set to `[]`) → `forceSave()` → settle + untag; failure → Release.

---

## 4. Local persistence

### 4.1 Kept reports (`keptReports.ts`)

* Key `aryaan.billReports.v1.<empId|anon>` (`empId` = `state.user.empId`), value: JSON array of `{ id: "<ms>-<6 base36>", scope: "page" | "tour-<id>", text, tone?: 'error'|'success', tourIds: number[], at: ms }`.
* Kept for **14 days** (`REPORT_KEEP_MS = 14*24*60*60*1000`); older, malformed or non-text entries are ignored; empty list → key removed; blocked storage never throws.
* Every bill report (`sayReport`) is stored before it is said and removed once it is on screen (narrator callback).
* On the next open in the same scope, all kept reports are said in one message after the hello (tone: error if any error, success if all success), then dropped; on the page their `tourIds` become the "Open claim" buttons (`savedTours`). Inside a form the step becomes `moreFiles`.

### 4.2 Other keys
* `aryaan.chatDrafts.v1.<empId|anon>` = `{ tourId: ISO time }` — drafts saved by the chat (for "from our previous conversation"); entries removed when the draft disappears from the page's draft list.
* `aryaan.ocrTabs.v1`, `aryaan.ocrClaims.v1.<emp>`, `aryaan.ocrTags.v1` — §3.5/§3.6.

### 4.3 Duplicates by hash
* Hash: SHA-256 of bytes, lowercase hex (64 chars); identical bytes under different names are the same bill (test `billDuplicates.test.ts`).
* Stored **only in memory, per chat instance** (lost on reload/start over): `sentHashesRef` (documentId → hash, this session's uploads), `readHashesRef` (hash → {key, name}) for bills that ended `saved`/`alreadySaved`, `readFilesRef` (`name|size` keys). Not on the server, not in localStorage.
* Scope: one chat (page chat or one form's chat). A bill read in an earlier session is not recognised by hash; the saved-draft comparison by `journeyKey`/`stayKey` (route + date + time / location + dates) then reports it as already in the saved claim.

### 4.4 OcrReadyNotifier on other pages
For documents not in flight and not yet announced (once per id per page load): if the current page is not the one that handles them (`/raise-claim-approver*` for claim refs, `/raise-claim` or `/raise-claim/*` for the page chat, `/within-juridiction*` for within-plan), a toast (10 s, icon 📄) with text by the **first** such document's reference and an "Open" button navigating to `/raise-claim` (claim and page chat) or `/within-juridiction`. First look after app open uses "…you sent earlier…" wording.

### 4.5 Launcher (window closed)
`ClaimAssistantLauncher.tsx` notice: read results waiting → "Your bill is read…/Your N bills are read…"; else in flight → "Processing your bill…/Processing N bills…". Header status: `Thinking…`, `Processing 1 bill…`, `Tour #N · guiding your claim` (in form), `Tour #N · you review and submit`, `No approved tours to claim for`, `Upload your bills to begin`.

---

## 5. The in-form bridge (`ClaimFormBridge`, `ApproverPage.tsx:2154-2185`)

Mounted only when: not an expanded table row, no `activeTab` (i.e. the standalone `/raise-claim-approver` page), not read-only, not clarification, and the tour's details are loaded (`showAssistant`, `:2190`). Tours = `[{tourId, source, destination, tourStartDate: trDetails.tentativeStartDate ?? details.tentativeStartDate, statusId, totalDays}]`.

| Method | Behaviour |
|---|---|
| `read()` | `{ transportationRows, accommodationRows, leaveRows, includeAccommodation, includeLeave }` as the form has them (stored order). |
| `write(state, {replace?})` | `replace` → retained ids `[]` for both. Non-empty transport rows → set rows; unless replace, `RetainedTransportationFileIds = keptFileIds(rows)` (every `fileId` still on a row). Same for stays/`RetainedAccomodationFileIds`. Non-empty leave rows set. `includeAccommodation`/`includeLeave` set from state. `hasUserModifiedDA = false` (DA recalculated). |
| `submit()` | `setIsDeclared(true)`; `handleSubmit(true)` (§1.12). |
| `saveDraft()` | `forceSave()` → `true` / `false` (§1.11). |
| `check()` | `collectSubmitProblems()` — list `{kind, section, row, message}` (messages verbatim in §6.4). |

Differences of in-form mode: reference `claim-chat:tour-N`; report scope `tour-N`; read bills go into the open form (never into other tours' drafts; bills outside the tour → `noTour`), the form saves itself; no "fill the form", no "what's left?", no bulk/split; questions about missing details are not asked (only "more bills?"), the review runs in the form; greeting waits for `loadTour`; the chat works on `syncFromForm()` before every action; "save draft" uses `forceSave`; submit runs the form's submit.

---

## 6. User-visible strings (verbatim, file:line)

### 6.1 `src/lib/ocr/ocrApi.ts`
* 52 `You are not signed in yet.`
* 56, 64 `This login is read-only, so bills cannot be sent.`
* 58 `The server is not reachable right now. Please check your connection and try again.`
* 63 `Your session has expired. Please sign in again.`
* 65 server `message` if non-empty, else the fallback.
* 105, 111 `The bills could not be sent.`
* 133, 140 `The bill list could not be read.`
* 148, 150 `The bill could not be read.`
* 164 `The bill file is no longer available.`
* 174, 177 `The results could not be taken.`
* 187, 189 `The results could not be given back.`
* 217, 219 `The reading could not be stopped.`
* 228, 238, 241 `The bill could not be read again.`

### 6.2 `src/lib/ocr/ocrJobs.ts`
* 60 `The bill list could not be read.` (snapshot error fallback)
(`ocrClaims.ts`, `ocrResults.ts`, `ocrReferences.ts`, `draftHandoff.ts`, `keptReports.ts`, `fileHash.ts`: no user-visible strings.)

### 6.3 `src/components/common/OcrReadyNotifier.tsx`
* 22 `` `Open the claim for tour #${tour} from Raise Claim to go through them.` ``
* 23 `Open the Claim Assistant on Raise Claim to go through them.`
* 24 `Open your Within Jurisdiction claim form and the Claim Assistant creates the tours.`
* 94 first look: `` `${n === 1 ? 'A bill' : `${n} bills`} you sent earlier ${n === 1 ? 'has' : 'have'} been read.` `` ; later: `` `${n === 1 ? 'Your bill has' : `${n} of your bills have`} been read.` `` followed by a space and the line-22/23/24 text.
* 104 button `Open`; 108 `duration: 10_000, icon: '📄'`.

### 6.4 `src/pages/RaiseClaim/RaiseClaim.tsx` (assistant-related)
* 314, 489 `User not found`
* 334 `Failed to fetch claim data.`
* 358 `Failed to load the tour. Please try again.`
* 510 `Failed to load draft.`
* 86/90/94/98/99 page description for the LLM: tab labels (74-78) `Raise Claim (approved tours waiting for a claim)`, `Clarification (finance asked a question)`, `Pending With Finance (claim submitted)`, `Availed (claim completed)`, `Not Availed`; `` `  • Tour #${r.tourId}: ${src} → ${dst}, starts ${day}${, N day(s)}${, status "…"}` ``; `  • …and N more`; `Saved drafts (claims started but not submitted): N`; `` `  • Tour #${empTourPlanId}${: draftName}` ``; `` `Tabs on the Raise Claim page, with the tours in each (${n} tours in all):` ``.
* 853 `You don't have any approved tours to claim for right now.`; 855 `Once a tour request is approved, it will show up in the Raise Claim section and I (Aryaan) can help you file its claim. Please come back then.`
* (non-assistant page strings: 277-278 delete-draft toasts, 617/620 not-availed toasts, 901-918 / 927-935 dialogs.)

### 6.5 `src/features/RaiseClaim/*` toasts
* `raiseClaimDraftSlice.ts:150` `Failed to save draft` (or server message) — toast; 183 `Failed to retrieve draft`; 206 `Failed to retrieve drafts`; reject reasons 148/178/204 `Unexpected error`, 227/229 `Failed to delete draft`.
* `SubmitFinalDraftSlice.ts:16` `Claim draft submitted successfully!`; 19 `Failed to submit claim draft. Please try again.`
* `axiosInstance.ts` `This is an impersonated login. You are not authorized to perform this action.`; `Session expired. You will be redirected to DFC Portal.`

### 6.6 `src/pages/RaiseClaim/ApproverPage.tsx` (assistant / OCR related)
* 838 `` `Please remove these first (nothing was sent):\n${problems.join('\n')}` ``
* 842 `` `Up to ${MAX_FILES_PER_READ} bills can be sent at a time. Please remove ${n - 20}.` ``
* 857 `` `${n} bill${s} sent for reading. This takes a few minutes; you can leave this page or close the browser. The form is filled as soon as they are read.` ``
* 862 `` `Nothing was sent. Please remove:\n${fileName}: ${reason}…` ``; 863 `The bills could not be sent. Please try again.`
* 875 `Stopped reading the bills.`; 877 `The reading could not be stopped. Please try again.`
* 884 `` `Filling the form from ${n} read bill${s}...` ``
* 909 `` `Warning: Could not extract data from ${names}` ``; 911 `` `Extraction Complete! (${n} accommodation(s) skipped due to out-of-bound dates.)` ``; 913 `AI Extraction Complete! Form updated automatically.`
* 915 `` `These bill files are no longer on the server, so please attach them again: ${lost}` ``
* 916 `The form was filled, but the draft could not be saved yet. It is saved again when you leave this page.`
* 921 `` `${message || 'The read bills could not be filled in.'} They are kept; the form is filled on your next visit.` ``
* 1891 `Failed to fetch claim data.`
* 1926 `Claim Assistant filled your form. Please review it before submitting.`
* 2255 `OCR Form Fill (Upload Tickets/Bills/Receipts)`; 2261 `` `Processing ${n} bill${s}… You can leave this page; the form is filled when they are done.` ``; 2264 `Stop`
* 2274 `Your bills have been read`; 2276-2277 `` `${n} bill${s} you sent for OCR Form Fill earlier ${is|are} ready. Filling the form replaces the journeys and stays it has now.` ``; 2282 `Discard them`; 2285 `Not now`; 2288 `Fill the form`; 2598 `Sending...`; 2603 `` `Process & Auto-Fill (${n})` ``
* Submit checks (1450-1508, also the bridge's `check()`): `Departure Date is missing for ${legName}`, `Departure Time is missing for …`, `From Location is missing for …`, `Mode of Travel is missing for …`, `Arrival Date is missing for …`, `Arrival Time is missing for …`, `To Location is missing for …` (legName = `Main Tour` for the first journey by date, else `Tour Leg ${i}`); `Accommodation Type is missing for Accommodation ${n}`, `Hotel Name is missing for Accommodation ${n}`, `Check-In Date is missing in Accommodation ${n}`, `Check-In Time is missing in Accommodation ${n}`, `Check-Out Date is missing in Accommodation ${n}`, `Check-Out Time is missing in Accommodation ${n}`, `Location is missing in Accommodation ${n}`, `City Class could not be determined for Accommodation ${n}`, `Invoice Number is required for Accommodation ${n}`, `Claimed amount (₹${base}) cannot exceed billed amount (₹${billed}) in Accommodation ${n}. Please review highlighted fields.`, `Claimed base amount (₹${base}) exceeds your entitled limit (₹${limit}) in Accommodation ${n}. Please click the red fields to auto-adjust.`, `No Bill Passing manager found.`
* 1524 `Please answer all clarification questions.`; 1666 `Failed to process submission. Please try again.`

### 6.7 `src/components/claimChatbot/ClaimAssistantLauncher.tsx`
* 62 `` `Your ${n === 1 ? 'bill is' : `${n} bills are`} read. Open me to go through ${n === 1 ? 'it' : 'them'}.` ``
* 64 `` `Processing ${n === 1 ? 'your bill' : `${n} bills`}…` ``
* 68 `Thinking…`; 70 `` `Processing ${n === 1 ? '1 bill' : `${n} bills`}…` ``; 73 `` `Tour #${id} · guiding your claim` ``; 74 `` `Tour #${id} · you review and submit` ``; 76 `No approved tours to claim for`; 77 `Upload your bills to begin`

### 6.8 `src/components/claimChatbot/ClaimChatbot.tsx` — OCR / draft strings (said exactly via `sayExactly`, or given to the LLM via `tell`)
* 485 hello: `` `Hello${name ? `, ${name}` : ''}! 👋\nI'm Aryaan, your Claim Assistant.` ``
* 496 `` `${hello} You have no approved tours waiting for a claim yet. Once one is approved, I can help with it.` ``
* 512 `` `Hi${name ? ` ${name}` : ''}, ${n === 1 ? 'the bill you uploaded is' : `the ${n} bills you uploaded are`} still being read. I'll show what I find and save it to your drafts as soon as ${n === 1 ? "it's" : "they're"} done. You can close this window meanwhile.` ``
* 518 `Say word for word: "Upload your tour tickets/bills/receipts"` (LLM instruction)
* 522 busy `Looking at your saved drafts…`; 532 `` `${hello} You have **${n} saved draft${s}**. You can continue one, or upload new bills.` ``
* 779 tell: `` `The bills are now in a claim for ${tourLabel}, but it is not saved yet. In one short line, say it is not saved as a draft yet.` ``
* 785 `` `Shall I save the new data into your saved claim for ${tourLabel}? Nothing is submitted.` `` (buttons 801 `Yes, save it`, 802 `Not now`); 787/946/971 busy `Saving your draft…`; 790 `Saved to your claim. It stays in the Raise Claim tab until you submit it.`; 791 `I couldn't save it just now. Say "save draft" to try again.`; 798 `Okay, nothing is saved. Say "save draft" whenever you want to keep it.`
* 807 `There is no approved tour to claim for right now.`; 813 `Let's sort out the drafts first, then you can upload more bills.`; 814 `Please choose the tour first, then you can upload more bills.`
* 852-853 `` `I can read up to ${20} bills at a time, so I'm reading ${fresh ? `the first ${k}` : 'none of these yet'} now. Please attach the other ${m} after this, in groups of up to 20:\n${• names, max 10}` `` (+ `• and N more`)
* 862 user echo `` `📎 ${name}` ``
* 869 `One bill shows air travel, but this tour has no air leg, so I left the travel mode empty.`
* 950 `Your draft is saved. Nothing has been submitted yet, so you can carry on or come back later.`; 952/981 `I couldn't save the draft just now. Please try again.`; 963 `There's nothing to save yet. Add your tickets or bills, or tell me about the trip first.`; 967 `Which tour is this for?`; 975 `` `Draft saved for ${tourLabel}. It stays in the Raise Claim tab until you submit it.` ``; 976 `Would you like to open the claim now?` / `Okay, we can keep working here.` / `Yes, open it` / `No, keep working here`; 978 `This tour has a saved draft I couldn't read just now, so I left it as it is.`; 979 `Would you like to open that claim now?` / `Okay, I left it as it is.` / `Yes, open it` / `No, not now`
* 989 busy `Opening the claim…`; 992 `I couldn't open the claim just now. Please try again.`
* 1051 busy `Saving your drafts…`; 1067 `` `✅ ${tourLabel}: ${added ? 'added to your saved claim' : 'draft saved'}` ``; 1069 `` `↩ ${tourLabel}: already has a draft, left as it is` ``; 1070 `` `⚠️ ${tourLabel}: couldn't save, please try again` ``; 1079 `Not submitted. Would you like to move ahead with one of these claims now?`; 1081 `Nothing new was saved. Would you like to move ahead with one of the existing claims?`; 1082 `No draft was saved.`; 1085 `Would you like to upload the bills again?`
* 1210 `Okay, I haven't added them. Attach them again whenever you want.`
* 1291 busy `Stopping…`; 1298 `Stopped reading. Nothing was added. Attach the bills again whenever you want me to read them.`; 1300 `` `${message || "I couldn't stop the reading just now."} Please try again.` ``
* 1311 `You seem to be offline, so I can't send the bills. Your files are still here, so please try again once you are back online.`
* 1314 busy `` `Processing ${n} bill${s}…` ``
* 1327-1328 `` `${n === 1 ? 'Your bill is' : `Your ${n} bills are`} being read. This takes a few minutes, and you can close this window or the browser meanwhile. ` `` + (`I'll add what I find to this claim.` in form | `I'll show what I find and save it to your drafts.` on the page)
* 1333 tell: `` `The bills were not sent: the server turned away ${this file|these files}, and then it takes none of them:\n• ${fileName}: ${reason}\n\nRemove ${it|them} and try again.` ``; 1336 `` `${message || "I couldn't send the bills just now."} Your files are still here, so please try again.` ``
* 1346 busy `` `Going through ${n} read bill${s}…` ``
* 1371 `` `⚠️ The server no longer has ${names}, so please attach ${it|them} again on the claim form.` ``
* 1385 `` `${message || "I couldn't go through the read bills just now."} They are kept on the server; I'll go through them the next time you open the assistant.` ``
* 1417 busy `Processing them again…`; 1422 `` `${That bill is|Those bills are} being processed again. I'll go through ${it|them} when ${it's|they're} done.` ``; 1424 `` `${message || "I couldn't send them again just now."} Please try again, or attach a clearer copy.` ``
* 1475 busy `Saving to your drafts…`
* 1605 `⚠️ This tour has no air travel, so the travel mode of the flight was left empty.`
* 1634 report suffix `\n\nDo you have more tickets or bills to upload for this claim?`
* 1759 busy `Looking at your claim…`; 1765 `I couldn't load this tour just now. Please close and reopen me in a moment.`; 1776 `` `${hello} Let's ${finish|do} your claim for ${claimFor}.` ``; 1779 `` `${hello} Let's do your claim for ${claimFor}. Please upload your tickets and bills.` ``; 1781 `` `${hello} Let's finish your claim for ${claimFor}.` ``
* 1794 busy `Opening your claim form…`; 1801 tell `The claim form was filled with the claim and opened. Ask the employee to review each section, attach anything missing (amounts and bill files go on the form), then submit.`; 1802/1804 `I couldn't open the form just now. Your claim is still here, so please try again.`
* 1918 `I've sent it for submission. If the form shows an error, fix it there and just tell me to submit again.`; 1920 `I couldn't submit just now. Please check the form for an error message, or try again.`
* 1929 `Your form is already up to date as we go, so there's nothing more to fill.`
* Chips: 2424 `Read again`; 2459 `⏹ Stop processing`; 2460 `` `↻ Read the unreadable bill${s} again` ``; 186 `Save as draft`; 2442 `Fill the form`; 2437 `Yes, submit` / 2438 `Not yet`.
* Read-report header (`billSummary.readHeader`, 219-228): `your bill has been read:` / `your N bills have been read:` / `the bill you uploaded earlier has been read:` / `the N bills you uploaded earlier have been read:`, prefixed `Hi <name>, ` when the greeting was deferred, else capitalised. (Report body: `billSummary.readSummaryText` — see the chat spec.)

---

## 7. Differences: React `buildClaimPayload` (assistant path) vs Python `drafting/payload.py::build_draft_request`

| # | Item | Old React assistant | Python builder |
|---|---|---|---|
| 1 | `RetainedTransportationFileIds` / `RetainedAccomodationFileIds` | **never sent** (computed but dropped: `hasResumedDraft` not passed) | always sent (lists of saved file ids) when a saved draft exists |
| 2 | Saved draft carry-through | none: saved rows are rebuilt from `ClaimFormState`; DA, NoOfDays, ClaimAmount, RecipentId, PurposeOfTour, DocumentsAttached, other saved keys are **not** sent | carries every saved key through (`_carry_through`), keeps raw saved rows |
| 3 | `RecipentId` | key absent | `"RecipentId": null` |
| 4 | `PurposeOfTour` | absent | `ws.purpose` |
| 5 | `Source` / `Destination` / `TantetiveDateOfDeparture` | from the claim rows (`tripEnds`: first departure / last arrival), omitted if empty; date = row's `YYYY-MM-DD` | from the approved tour header (`ws.source`, `ws.destination`, `ws.tour_start`) |
| 6 | `NoOfDays` | absent | tour length in days |
| 7 | `ClaimAmount` | absent | sum of claimed (or saved total + new) |
| 8 | `DailyAllowanceDto` | absent | `"[]"` (or carried saved DA) |
| 9 | `LeaveAvailedDto` / `IsLeaveAvailedwithbetweentour` | only if the state includes leave (from a saved draft): rebuilt `{Id:0, LeaveStartDate, LeaveEndDate, DADeductable, Amount}`, flag `true`; otherwise both absent | `"[]"` and `false` (or carried saved text) |
| 10 | `DocumentsAttached` | absent | `{IsApprovedTourProgramme:null, IsTickets_BoardingPass_Bills, IsHotelBills}` (merged with saved) |
| 11 | `LastEditedSection` | `"Transportation"` | `null` |
| 12 | `IsIncidentRelatedClaim`, `IsCancelationClaim` | absent | `false`, `false` |
| 13 | `DraftId` | absent for a new draft (null stripped) | `"DraftId": null` sent |
| 14 | `AccomodationDetailDto` when no stays | key absent | `"[]"` |
| 15 | Null/empty handling | every `null`/`undefined`/`''` key removed at all levels (rows and top level) | explicit `null` and `""` values (`Remark: null`, `InvoiceNumber: null`, `TimeOfTravelSource: ""`, …) |
| 16 | Row `Id` | row index (0,1,2…) for all rows, saved ones included | `0` for new rows; saved rows keep their saved `Id` |
| 17 | `TourNumber` on transport/stay rows | not sent | `1` |
| 18 | `isPaidUsingCard` on rows | not sent | carried for saved rows |
| 19 | Claimed tax (`SGSTAmount`/`CGSTAmount`/`IGSTAmount`) | OCR claimed tax (`cgst/sgst/igst`), regardless of claimed vs billed | bill tax only when claimed == billed, else 0 |
| 20 | `NA` tax structure | all six tax amounts forced to 0 | bill tax amounts still sent |
| 21 | Default `TaxStructure` | `SGST_CGST` (anything not `IGST`/`NOT_APPLICABLE`) | `NA` when unknown |
| 22 | Unknown `ModeOfTravel` | `"select"` | `""` |
| 23 | `ClassOfCity` unknown | `"Select"` | `""` |
| 24 | `isCompanyLodginPaid` | `selectedType === 'company_paid'` (OCR type contains "paid"/"company") | always `false` |
| 25 | `HotelName`, `InvoiceNumber`, dates, times empty | key removed | `null` / `""` |
| 26 | Stay `InvoiceDate` | OCR `YYYY-MM-DD`; **lost on overwrite** of saved stays | ISO date; saved rows keep theirs |
| 27 | Saved own-arrangement / free stays on overwrite | rewritten as `IsOwnArrangement:false`, `IsFreeAccomodation:false` (bug in `claimStateFromDraft`) | saved raw values kept |
| 28 | Placeholder transport row | a stays-only draft sends one blank transport row (`ModeOfTravel:"select"`, zeros, `TaxStructure:"SGST_CGST"`, -1/-1) | no placeholder; `"[]"` |
| 29 | Saved planned leg filled by a bill | n/a (bills only add rows after saved rows, matched by journeyKey) | merged into the saved row keeping `Id/TourNumber/Remark` |
| 30 | Row order | stored order: saved rows in saved places, new rows after them sorted by date | workspace order |
| 31 | Claimed amount source | transport `totalClaimedAmount ?? claimedAmount`; stay `claimedAmountWithTax ?? claimedAmount` | `row.money.claimed` (check the mapper matches) |
| 32 | Files | multipart parts `transportationFiles` / `accommodationFiles` with the unique display name (`x (2).pdf`) and the local bytes (GetFile only for earlier sessions) | same part names; bytes always via `GET Ocr/GetFile`; name `bill.file_name` or `bill-<id>` |
| 33 | `IsPaidViaCreditCard` | `false` | `false` (same) |

Other integration differences seen in the Python clients:
* Tours: React uses `GET TourListing/GetTourRequest/{empId}?toureType=1` and filters `statusId==2 || (statusId==9 && isAdvanceTaken)`; Python has `GetTourRaiseClaimList` and sends the param as `touretype`.
* OCR list: React sends only `IncludeResponse=false&PageSize=200` (no `ReferencePrefix`, no `IncludeRead`) and filters by reference client-side; Python sends `ReferencePrefix`, `IncludeRead=true`.
* After a save React re-reads the draft (`GetRaiseClaimDraft`) once and refreshes `GetAllRaiseClaimDrafts`; Python reads back up to twice and compares counts.
* React never deletes or submits from the page assistant; submit only exists in-form via `SubmitRaiseClaimDraft/{draftId}?empId=` after a form save.
