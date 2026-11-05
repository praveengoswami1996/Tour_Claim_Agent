# Bill uploads at any time, automatic tour matching and automatic draft saving: requirements

**Status:** proposed change for the Tour Claim AI Agent (release 0.3)
**Extends:** [functional_requirements.md](../../TourClaim_AI_Agent/Project-Context/agent/functional_requirements.md) §5.5 DOC, §5.11 EXE, §5.12 STA, §5.15 UI
**Reference behaviour:** the in-app Claim Assistant in the React app (`D:\CETPA\Tour_React`), which this agent replaces.
A description of that assistant is in `Tour_React/docs/claim-assistant-how-it-filled-claims.md`.

---

## 1. Why this change

Today the agent only accepts bills inside a claim it has started:

- `POST /v1/workflows/{id}/attachments` returns **409 `no_active_claim`** ("Start a claim first…") when the employee
  uploads before saying "start a claim for my Mumbai tour".
- `start_claim` refuses a tour that already has a saved draft ("continue it in the claim form").
- Bills are only read when the employee says "review my claim", and nothing is saved unless they say "save draft".

The in-app Claim Assistant that the agent replaces works the other way round, and employees are used to it:

1. The employee **drops their bills in first**, without naming a tour.
2. The bills are read in the background.
3. The assistant **works out which approved tour each bill belongs to** from the dates and places on the bill.
4. It **saves the bills straight into that tour's draft**, adding them to a draft that already exists without touching
   what is in it.
5. It reports, one line per file, what was found and where it went. The employee opens the draft in the claim form,
   checks it, gets the daily allowance calculated, and submits.

This document specifies that behaviour for the agent.

### 1.1 Goals

- G-A: Uploading bills never requires starting a claim first.
- G-B: Each bill is matched to the right approved tour automatically, and the employee is asked only when the agent
  cannot tell.
- G-C: Read bills are saved into the tour's outside-jurisdiction draft automatically (when saving is switched on),
  including into a draft that already exists.
- G-D: The employee is told what happened to every file, even if they closed the chat while the bills were being read.
- G-E: Nothing is lost or applied twice: no duplicate rows, no overwritten saved rows or files, and each OCR result is
  used exactly once.

### 1.2 Out of scope

- Within-jurisdiction claims (monthly tour plan). Only **outside-jurisdiction** claims (`Claim/SaveRaiseClaimDraft`).
- Submitting a claim. The agent still only saves drafts; the employee submits from the claim form.
- Daily allowance and entitlement limits: still calculated by the claim form when the draft is opened (DEC-1).
- Editing amounts in the chat. Amounts come only from the bills.

### 1.3 Change to an existing principle (needs sign-off)

FRS **DOC-04** says read values must be confirmed by the employee before they are used. The in-app assistant saves
read bills into the **draft** without asking, because a draft is not a submission: the employee reviews every row in
the claim form before submitting. This document adopts the same rule. Saving stays behind the existing switch
(`TCA_ENABLED_WRITE_ENDPOINTS`) plus a new one (`TCA_AUTO_SAVE_BILLS`, §8), so an operator can keep the old
"review, then say save" behaviour.

---

## 2. The new flow at a glance

```
Employee attaches bills (any time, with or without a claim in progress)
        │
        ▼
POST /v1/workflows/{id}/attachments        → checks, de-duplicates, uploads to Ocr/Upload
        │                                     tag agent:<workflow>:unassigned  (or :tour-<id>, see BIL-03)
        ▼                                     replies: "Got 3 files, reading them now…"  + bill_status event
React polls every 20 s while bills are pending
POST /v1/workflows/{id}/bills/check        → finished results: MarkAsRead, map to rows,
        │                                     match each row to a tour, merge into that tour's draft, save, read back
        ▼
Replies: bill_report (one line per file)  + draft_saved per tour  + choices if a bill could not be placed
```

---

## 3. Functional requirements

Priority as in the FRS: **M** = Must, **S** = Should, **C** = Could.

### 3.1 BIL-UP: Uploading

| ID | Requirement | Pri |
|---|---|---|
| BIL-01 | `POST /v1/workflows/{id}/attachments` accepts bills **whether or not a claim is in progress**. The `NoActiveClaim` error and the 409 `no_active_claim` response are removed. | M |
| BIL-02 | Keep the current checks: PDF/JPG/JPEG/PNG only, content must match the extension (magic bytes), not empty, ≤ `max_upload_bytes` (20 MB) per file, ≤ `max_upload_files` (20) per upload. Read-only sessions are refused (403 `read_only`). | M |
| BIL-03 | **Reference tag.** With no claim in progress, upload with `agent:<workflow_id>:unassigned`. With a claim in progress (a workspace exists), keep `agent:<workflow_id>:tour-<id>`. Every agent tag starts with `agent:` so the web app's own screens never pick these files up. | M |
| BIL-04 | **Duplicates by content.** Compute SHA-256 of each file. Skip (do not upload) a file whose hash this employee has already uploaded through the agent, in any workflow, unless its earlier upload ended `unreadable` or was given back unused. Also skip repeats inside the same upload. Report skipped files as `duplicate`. | M |
| BIL-05 | Record every uploaded file in a new **employee-level upload store** (§5.1): document id, workflow id, employee code, file name, size, hash, reference, status, and later the tour, outcome and whether the outcome was reported. | M |
| BIL-06 | The attachments reply returns immediately after upload (it does not wait for OCR): a `text` event ("Got 3 files: a.pdf, b.jpg, c.pdf. I'm reading them now; this usually takes a minute or two. I'll tell you which tour they're for and save them into your drafts."), any `notice` for skipped files, and a `bill_status` event (§6.1). | M |
| BIL-07 | Files over the per-upload limit are not silently dropped: the whole request is refused with 400 `attachment_rejected` naming the limit, as today. *(The in-app assistant holds extras back for the next upload; the React client will split large selections instead, see §7.)* | S |

### 3.2 BIL-RD: Reading bills (OCR) and claiming results

| ID | Requirement | Pri |
|---|---|---|
| BIL-10 | A new endpoint **`POST /v1/workflows/{id}/bills/check`** (§6.2) does all processing of finished bills. It is called by the React client while bills are pending, and also runs at the start of every `POST /messages` turn (BIL-14). | M |
| BIL-11 | **Why processing is triggered by the client:** the agent never stores the employee's token (IDN-08), so it cannot call the Tour API in the background. All OCR polling and draft saving happens inside a request that carries the token. | M (constraint) |
| BIL-12 | `bills/check` lists the employee's agent documents with `GET Ocr/GetMyDocuments?ReferencePrefix=agent:&IncludeRead=true&IncludeResponse=true`. It processes **every** of this employee's agent uploads that are finished and not yet processed, whichever workflow they were uploaded from, and reports them in the calling workflow. A bill uploaded in a conversation the employee has since left is still processed and reported. | M |
| BIL-13 | **Exactly once.** For finished documents (`Completed` or `Failed`), call `POST Ocr/MarkAsRead` and process only the ids returned in `marked`. Ids not returned were taken by another client; record them as `used_elsewhere` and do not apply them. | M |
| BIL-14 | Each `POST /messages` turn first runs the same processing (silently if nothing finished), so "what did you find?" or "review my claim" always includes fresh results. | M |
| BIL-15 | `Failed` documents get outcome `unreadable` with the OCR error message. The report offers "Read it again" (`POST Ocr/Retry/{id}`, already on the allow-list), which puts the bill back to pending. | S |
| BIL-16 | A bill still `Pending`/`Processing` after **15 minutes** is reported once as `slow`, with the choice to keep waiting or remove it (`POST Ocr/Cancel`). | C |
| BIL-17 | Results that were taken but could **not** be applied (save failed, the tour's draft could not be read, the employee chose "not now") are handed back with `POST Ocr/Release`, so a later `bills/check` tries them again. Their store status goes back to `completed_unprocessed`. | M |
| BIL-18 | Treat everything read from a bill as untrusted data (DOC-08). | M |

### 3.3 BIL-MAP: Turning a result into rows

| ID | Requirement | Pri |
|---|---|---|
| BIL-20 | Use the existing `drafting/ocr_mapping.py` (`extract_bill`) to turn each result into journey and stay rows, each carrying its bill's document id. | M |
| BIL-21 | A date with no year is never guessed; it stays empty and becomes a finding. | M |
| BIL-22 | A file that was read but produced no journey or stay gets outcome `nothing_found`. | M |
| BIL-23 | **Stay bill problems** (same rules as the in-app assistant, `claimProgress.ts › stayBillProblem`). A stay row is **not added** (outcome `problem` with the reason) when: check-out is before check-in (same day: check-out time before check-in time); or its dates fall outside the tour's dates (plan start − 1 day to plan end + 1 day); or, when the tour has journey dates, check-in is more than one day before the first journey, or check-out more than one day after the last journey (the last rule only when there are two or more journeys). | M |
| BIL-24 | **Air rule.** When the matched tour's approved plan has no air leg, a journey read as air is added with an **empty mode** and a warning finding "One bill shows air travel, but this tour has no air leg, so I left the travel mode empty." | M |

### 3.4 BIL-MATCH: Which tour does each bill belong to?

Candidates are the employee's **claimable** tours (`TourListing/GetTourRaiseClaimList/{empId}`), so ownership and
"approved, not yet claimed" are both given. The rules below are those of the in-app `tourMatcher.ts`.

| ID | Requirement | Pri |
|---|---|---|
| BIL-30 | **Tour window:** from the tour's start date to its end date. If there is no end date, use start + (number of days − 1). A tour with no start date has no window. | M |
| BIL-31 | **Places** are normalised (lower case, everything but letters, digits and spaces removed, spaces collapsed). Two places match when both have ≥ 3 characters and one contains the other ("new delhi" ~ "delhi"). | M |
| BIL-32 | **Scoring a set of rows against a tour.** Collect the rows' dates (journey departure and arrival, stay check-in and check-out) and places (journey from/to, stay location). `date_fit` is the share of those dates inside the window. A date one day before the start or one day after the end also counts, but only if one of the places matches the tour's destination (or there are no places). `route_hits` (0–2) is how many of the tour's source and destination match one of the places. `score = date_fit × 2 + route_hits`. | M |
| BIL-33 | **Judging one row on its own.** Score that row against every candidate tour, best first. If the row has no dates and no places → **neutral**. If the best score is 0, or every tour that has a window gets `date_fit` 0 → **no tour** (outcome `no_tour`; mention the tour it looks like if one has some date fit). If the top two scores differ by less than 0.5 → **neutral**. Otherwise → **assigned** to the best tour. | M |
| BIL-34 | **Neutral rows** go with the single tour the other rows of the same check went to. If there was no other tour, they go to the tour of the claim in progress in this workflow, or to the only claimable tour. Otherwise they are **unsure** (BIL-36). | M |
| BIL-35 | **A claim in progress does not narrow the matching.** A bill that clearly belongs to a different claimable tour is saved into that tour's draft, and the report says so ("this ticket is for Tour 1234, Delhi → Pune, so I saved it there"). | M |
| BIL-36 | **Unsure bills are asked about, not dropped.** *(Improvement on the in-app assistant, which only reported them.)* Send a `choices` event "Which tour are these bills for?" listing up to 5 candidate tours, best score first, plus an option "None of these". The answer goes through the existing `pending_choice` mechanism with a new goal `assign_bills`. Choosing a tour saves the bills into it (§3.5). "None of these" releases them and marks them `no_tour`. Until answered they stay `awaiting_tour` in the store. | M |
| BIL-37 | **No claimable tours at all:** every bill is `no_tour`, and the reply says "You have no approved tours waiting for a claim, so I couldn't save these bills." The results are released. | M |

### 3.5 BIL-SAVE: Saving into the tour's draft

| ID | Requirement | Pri |
|---|---|---|
| BIL-40 | For each tour that received rows in this check, **read the tour's current draft from the server**: `GET Claim/GetRaiseClaimDraft?empId=&empTourPlanId=`. Always read it fresh; never trust a copy in workflow state. | M |
| BIL-41 | **No draft yet:** build the claim from the approved plan as `start_claim` does today (`Claim/RaiseClaimRequest/{tourId}` → `ClaimWorkspace.from_prefill`). Then merge the new rows (BIL-43) and save a new draft (`DraftId = null`). | M |
| BIL-42 | **Draft exists:** parse it into a workspace (§5.2). Keep the saved journeys and stays exactly as they are, **in the same order and position**, together with their saved files. The server ties a saved draft's files to its rows by position (`rowIndex`), so saved rows must never move, change or be removed. | M |
| BIL-43 | **Merging new rows.** A new journey whose key (source, destination, departure date, departure time, normalised as BIL-31; dates as ISO; times as `HH:mm`) equals a saved or already-added journey is **already saved**: it is not added again (outcome `already_saved`). The same applies to stays, keyed by location, check-in date and check-out date. A plan-seeded journey (`Origin.PLAN`) with no bill is matched and filled using the existing `drafting/merge.py` rules (both places match, date within one day) instead of being added again. New rows are added **after** the saved rows, sorted by date. | M |
| BIL-44 | **Payload for an existing draft.** Build with `drafting/payload.py` plus: `DraftId` = the existing draft id; `RetainedTransportationFileIds` and `RetainedAccomodationFileIds` = every saved file id still in the claim, which is all of them since saved rows never change; `StartFileIndexCount`/`EndFileIndexCount` count **only the newly attached files**, numbered from 0 across the rows in row order (a saved row with no new files gets −1/−1); the multipart parts `transportationFiles` / `accommodationFiles` carry only the new files, downloaded with `GET Ocr/GetFile/{id}`, in that order. This mirrors the React `buildClaimPayload` + `collectNewFiles`. Note that today's agent sends empty retained lists when re-saving its own draft. That must change: retained lists are always the saved files. | M |
| BIL-45 | **A draft that exists but cannot be read is never written over.** If the draft read fails (other than "no draft"), do not save. Outcome `save_failed`; release the results (BIL-17) so they are retried next time. | M |
| BIL-46 | **Re-check before writing** (FRS STA-05): the tour must still be in the claimable list. If not, outcome `tour_not_claimable`, release the results. | M |
| BIL-47 | **Save and confirm:** `POST Claim/SaveRaiseClaimDraft`, then read the draft back. Success means the read-back draft id matches and the number of journeys and stays is the expected count. An unclear result (timeout, or a read-back mismatch) is reconciled by reading back once more and is never retried blindly (EXE-12). | M |
| BIL-48 | **On success:** every row that went in gets outcome `saved` with the tour and draft id. The upload store is updated. A `draft_saved` event is sent for the tour (§6.4). | M |
| BIL-49 | **Saving switched off** (`Claim/SaveRaiseClaimDraft` not in `TCA_ENABLED_WRITE_ENDPOINTS`, or `TCA_AUTO_SAVE_BILLS=false`): match and merge exactly the same, but keep the result in the workflow's claim workspace instead of writing. Outcome `ready_not_saved`, with the reason "Saving drafts from the chat is switched off; enter these details in the claim form." Results are **not** released, because they are applied in the workspace. | M |
| BIL-50 | **Read-only sessions** never save, since they can't upload in the first place (BIL-02). | M |
| BIL-51 | Before saving, run the existing checks (`drafting/analysis.py`). **Blocking findings do not stop an automatic draft save**, since a draft may be incomplete; they are listed in the report and in a `claim_summary` for the tour. Only the explicit "save draft" command keeps today's rule of refusing while blocking findings exist. | M |
| BIL-52 | Each tour's save is independent: one tour failing does not stop the others. | M |
| BIL-53 | Saving into the tour of the claim in progress also updates the workflow's claim workspace, so "review my claim" shows the saved state. | M |

### 3.6 BIL-REP: Telling the employee

| ID | Requirement | Pri |
|---|---|---|
| BIL-60 | After each check that processed at least one file, send **one `bill_report` event** (§6.3) with one line per file, plus a short `text` summary such as "I read 4 bills. 3 are saved in your draft for Tour 1234 (Noida → Mumbai, 05–08 Oct). 1 couldn't be read." | M |
| BIL-61 | **File outcomes** (strongest wins when a file has several rows): `saved`, `already_saved`, `ready_not_saved`, `save_failed`, `tour_not_claimable`, `no_tour`, `awaiting_tour`, `problem`, `nothing_found`, `unreadable`, `used_elsewhere`, `duplicate`, `rejected`. Each line names the tour where relevant and what was read: "ticket Noida → Mumbai, 05 Oct" or "hotel stay in Mumbai, 05–08 Oct". | M |
| BIL-62 | **Day coverage for a saved tour** (from the in-app `savedClaimText`): "Your saved claim already had 05 and 06 Oct. These bills add 07–08 Oct. Every day of the tour is now covered." Or "Still no bills for 07 Oct." | S |
| BIL-63 | **Reports are never lost.** Each processed file's outcome is stored with `reported = false` until it has been returned in a response. If the employee closed the chat, the next `bills/check`, `POST /messages` or `POST /v1/sessions` returns any unreported outcomes first. Outcomes older than **14 days** that were never reported are dropped. | M |
| BIL-64 | `POST /v1/sessions` additionally returns `bills: { pending: <count>, unreported: <count> }`, so the launcher can say "Processing 2 bills…" or "Your bills are read. Open me to see where they went." | M |
| BIL-65 | All reply text stays deterministic (fixed templates in code), as in the rest of the agent. | M |

### 3.7 BIL-CONV: Conversation behaviour

| ID | Requirement | Pri |
|---|---|---|
| BIL-70 | **"Start a claim for …" on a tour with a saved draft** no longer sends the employee away. It loads the draft into the workspace (§5.2) and shows its `claim_summary` and an `upload_request`. New bills are then merged and saved as in §3.5. | M |
| BIL-71 | The greeting and help text change from "Start a claim, then upload" to "Upload your tickets and hotel bills and I'll find the tour and save them into its draft. Or ask about your tours." | M |
| BIL-72 | New intents in `llm/intent.py` and `llm/rules.py`: `bill_status` ("are my bills read?", "what happened to my bills"), answered from the upload store; and `assign_bills`, used only through `pending_choice` (BIL-36). | S |
| BIL-73 | "Cancel this claim" keeps today's behaviour for the claim in progress. It never deletes or changes anything already saved into a draft. | M |
| BIL-74 | Uploaded files must never be lost: everything uploaded is either reported with an outcome or still pending. Nothing disappears silently. | M |

---

## 4. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | `bills/check` returns in under 5 s when nothing finished, and in under 30 s for 20 finished files (dominated by `GetFile` and `SaveRaiseClaimDraft`). |
| NFR-2 | **Concurrency.** Two `bills/check` calls for the same employee (two tabs) must not process the same document twice: `MarkAsRead` decides, and per-employee processing is serialised with a lock (an `asyncio.Lock` keyed by employee code is enough for one instance). |
| NFR-3 | Logs never contain tokens, file contents or read amounts. Log document ids, tour ids, outcomes and endpoint status codes. |
| NFR-4 | Every new Tour API call goes through the allow-list. No new endpoints are needed: all those used are already listed. |

---

## 5. Data model changes

### 5.1 New table `agent_uploads` (SQLite now, PostgreSQL later)

| Column | Type | Notes |
|---|---|---|
| `document_id` | int, PK | OCR document id |
| `emp_code` | text, indexed | owner; every query filters by it |
| `workflow_id` | text | where it was uploaded (reports go to the calling workflow) |
| `file_name`, `file_size`, `sha256` | | `sha256` indexed per `emp_code` for BIL-04 |
| `reference` | text | `agent:<wf>:unassigned` or `agent:<wf>:tour-<id>` |
| `status` | text | `pending` · `completed_unprocessed` · `awaiting_tour` · `processed` · `released` · `cancelled` |
| `tour_id` | int, null | tour it was saved into or assigned to |
| `outcome`, `outcome_detail` | text | BIL-61 |
| `reported` | bool | BIL-63 |
| `created_at`, `processed_at` | float | |

`WorkflowRegistry` (or a new `UploadStore`) owns this table. The bills inside `ClaimWorkspace.bills` stay as they are,
for the claim in progress.

### 5.2 Reading a saved draft into a `ClaimWorkspace`

Extend `ExistingDraft` (today it holds only `draftId` and `lastSavedDate`) so it parses the whole draft. The React
reference is `savedClaim.ts › claimStateFromDraft`.

- `draftData` may be nested or flat; field names are case-insensitive.
- Journeys: `TransportaionDetailDtos` (or `TransportationDetailDtos`). Stays: `AccomodationDetailDto` (or
  `AccommodationDetailDto`). Leave: `LeaveAvailedDto`. Each may be a JSON **string** or an array.
- Files: `transportationFileReferences` and `accomodationFileReferences`, each item with `fileId`,
  `originalFileName`/`displayName`, `fileSize` and `rowIndex` (the row it belongs to).
- New row field `saved_files: list[SavedFile]` (`file_id`, `name`, `size`, `row_index`) and a new `Origin.SAVED`.
  Saved rows keep their money, tax and invoice fields exactly as read, and are written back unchanged.
- Leave rows and any fields the agent does not model (remarks, DA, `DocumentsAttached`) must be **carried through
  unchanged** into the next save, so saving from the agent never wipes data entered in the claim form. Keep the raw
  draft JSON for anything not modelled and merge it back when building the payload.

### 5.3 Workflow state

- `pending_choice` gains goal `assign_bills` with the document ids and rows waiting for a tour.
- `claim` (the workspace) stays optional; uploads no longer need it.

---

## 6. API changes

All replies keep the `{ "events": [...] }` shape with `?stream=false`, as today.

### 6.1 `POST /v1/workflows/{id}/attachments` (changed)

- No longer returns 409 `no_active_claim`.
- Events: `text`, optional `notice` (duplicates or rejected files), and a new **`bill_status`** event:

```json
{ "type": "bill_status", "pending": 3,
  "files": [ { "document_id": 101, "file_name": "ticket.pdf", "status": "Pending" } ] }
```

### 6.2 `POST /v1/workflows/{id}/bills/check` (new)

- Runs BIL-12…BIL-53 and returns the events, followed by a final `bill_status` with the remaining `pending` count.
- Returns just the `bill_status` when nothing finished.
- The client polls it every **20 s** while `pending > 0` and the tab is visible, and every **60 s** after an error;
  it stops at 0. These are the same intervals as the in-app `ocrJobs.ts`.

### 6.3 New event `bill_report`

```json
{ "type": "bill_report",
  "title": "What I found in your bills",
  "files": [
    { "file_name": "ticket.pdf", "outcome": "saved", "tour_id": 1234,
      "found": "ticket Noida → Mumbai, 05 Oct 2026", "detail": null },
    { "file_name": "hotel.jpg", "outcome": "problem", "tour_id": 1234,
      "found": "hotel stay in Mumbai, 09–10 Oct 2026",
      "detail": "its dates are outside your tour dates (05–08 Oct 2026)" },
    { "file_name": "scan.png", "outcome": "unreadable", "tour_id": null, "found": null,
      "detail": "could not be read", "retry_document_id": 103 }
  ],
  "tours": [ { "tour_id": 1234, "route": "Noida → Mumbai", "dates": "05–08 Oct 2026",
               "draft_id": 778, "coverage": "Every day of the tour is now covered." } ] }
```

### 6.4 Existing events reused

- `draft_saved`: one per tour saved in the check; `open_in_form` as today, including `totalDays`.
- `claim_summary`: for the tour of the claim in progress, or when there are blocking findings.
- `choices`: for BIL-36.

### 6.5 `POST /v1/sessions` (changed)

Adds `"bills": { "pending": 2, "unreported": 1 }`.

### 6.6 New error codes

None. `no_active_claim` is retired; keep the exception class only if other code still uses it.

---

## 7. Changes on the React side (for information)

These will be made in `Tour_React` once the agent ships 0.3:

- Upload works at any time. The paperclip and camera are always on (except for read-only users).
- Poll `bills/check` while `bill_status.pending > 0`. Show "Reading your bills…" in the header and on the launcher
  while closed, and "Your bills are read" when `unreported > 0`.
- Draw `bill_report` as a card: one line per file with an outcome icon, the tour, and "Open draft" / "Read it again"
  buttons.
- Split a selection of more than 20 files into several uploads.

---

## 8. Configuration

| Setting | Default | Meaning |
|---|---|---|
| `TCA_ENABLED_WRITE_ENDPOINTS` | `[]` | Existing. Must include `"Claim/SaveRaiseClaimDraft"` for any saving. |
| `TCA_AUTO_SAVE_BILLS` | `true` | New. When saving is enabled, save read bills into drafts automatically (§3.5). `false` keeps them in the workspace until "save draft". |
| `TCA_BILL_REPORT_KEEP_DAYS` | `14` | New. BIL-63. |
| `TCA_BILL_SLOW_MINUTES` | `15` | New. BIL-16. |

---

## 9. Acceptance scenarios

Use the fake backend in `tests/fakes.py`; extend it with OCR documents, drafts with file references, and failure switches.

1. **Upload with no claim, one tour fits.** Employee has claimable tours A (Noida → Mumbai, 05–08 Oct) and B
   (Delhi → Pune, 20–22 Oct). They upload a train ticket Noida → Mumbai 05 Oct and a Mumbai hotel bill 05–08 Oct. The
   attachments reply says reading has started (no 409). After OCR finishes, `bills/check` saves both into a **new**
   draft for A and returns `bill_report` (2 × `saved`, tour A) and `draft_saved` for A.
2. **Mixed upload.** The same upload also has a ticket Delhi → Pune 20 Oct. It is saved into B's draft, and the report
   lists two tours.
3. **Existing draft.** Tour A already has a draft with the outbound journey and its file. Uploading the return ticket
   adds one journey **after** the saved one. The saved file is in `RetainedTransportationFileIds`, the new file is
   index 0, and leave rows and remarks in the draft are unchanged after the save.
4. **Already saved.** Uploading the outbound ticket again (different file name, same content) is skipped as
   `duplicate`. A different scan of the same journey is read and reported `already_saved`; the draft is not changed.
5. **Can't tell.** Two tours to Mumbai in the same week; a taxi bill with only "Mumbai" and no date → `choices`
   "Which tour are these bills for?". Picking 2 saves into that tour; "None of these" releases the result and marks it
   `no_tour`.
6. **Fits no tour.** A ticket dated three months before any claimable tour → `no_tour`, result released.
7. **Draft can't be read.** `GetRaiseClaimDraft` fails with 500 for A → nothing saved, `save_failed`, result released.
   The next check retries and succeeds.
8. **Another device took it.** `MarkAsRead` returns no ids for a document → `used_elsewhere`, nothing applied.
9. **Saving off.** `TCA_ENABLED_WRITE_ENDPOINTS=[]` → bills matched and merged into the workspace, `ready_not_saved`,
   and no `SaveRaiseClaimDraft` call is made.
10. **Closed the chat.** Upload, then no further calls until OCR is done. The next `POST /v1/sessions` reports
    `unreported: 2`, and the next `bills/check` returns the report once. A second check returns no report.
11. **Read-only token.** Upload refused with 403 `read_only`; no OCR call.
12. **Air not in plan.** The plan has train only; an air ticket is saved with an empty mode and the air warning.
13. **Bad stay.** A hotel bill whose check-out is before its check-in is not added; outcome `problem` with the reason.
14. **Two tabs.** Two concurrent `bills/check` calls → each document is processed once.

---

## 10. Implementation notes

- Reuse: `ocr_mapping.extract_bill`, `merge.merge_bill`/`same_place`, `analysis.analyse`, `payload.build_draft_request`
  (extend it for retained files and new-file indexing), `OcrApiClient`, `TourApiClient.get_outside_draft` (make it
  return the full draft).
- New modules suggested: `drafting/tour_match.py` (§3.4), `drafting/saved_draft.py` (§5.2, parse and write back),
  `drafting/bill_pipeline.py` (BIL-12…53 as one function given a context, testable without HTTP), and
  `state/uploads.py` (§5.1).
- In the graph: add a `process_bills` step that runs before `understand` on every message (BIL-14), and a node for the
  `assign_bills` choice. `register_bills` is replaced by the upload store.
- Keep amounts as `Decimal` end to end; never compute DA.
- Tests first for `tour_match` (port the cases from `Tour_React/tests/claimChatbot/tourMatch.test.ts`) and for the
  saved-draft round trip (parse → add rows → payload → same saved rows and file ids; cases in
  `Tour_React/tests/claimChatbot/savedClaim.test.ts`).
- Update `README.md` (flow, endpoints, events, settings), and in `functional_requirements.md` mark DOC-04 as amended by
  §1.3 and add the BIL ids to the traceability table.

## 11. Open points to confirm on UAT

1. `SaveRaiseClaimDraft` with `DraftId` set plus `Retained…FileIds`: confirm that saved files stay linked to their
   rows by `rowIndex`, and that new files are linked by `StartFileIndexCount`/`EndFileIndexCount` counted over new
   files only. This is how the React app does it today.
2. The exact file-reference field names in `GetRaiseClaimDraft` (`transportationFileReferences`,
   `accomodationFileReferences`, `rowIndex`).
3. `GetMyDocuments` with `ReferencePrefix=agent:` returns documents from every workflow of the employee.
4. Whether the claim form opens a draft saved this way correctly, including tax fields and file links (README item 4).
