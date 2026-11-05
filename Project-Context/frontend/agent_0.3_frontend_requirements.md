# Tour Claim AI Agent 0.3: frontend changes (Tour_React)

> **Superseded** by `agent_0.4_frontend_requirements.md`: the agent is being rebuilt to behave exactly like the old
> Claim Assistant. Do not implement this document.

**Status:** ready to implement. The agent side is done (branch `feature/bill-auto-draft` in `TourClaim_AI_Agent`).
**Source requirements:** `TourClaim_AI_Agent/Project-Context/agent/bill_upload_auto_draft_requirements.md`
(§6 API, §7 React changes). This document turns those into concrete changes in `D:\CETPA\Tour_React`.
**Files in scope:** `src/services/agentClient.ts`, `src/components/tourClaimAgent/AgentChat.tsx`,
`src/components/tourClaimAgent/AgentCards.tsx`, `src/components/tourClaimAgent/TourClaimAgentLauncher.tsx`,
`public/web.config`, new tests under `tests/tourClaimAgent/`.

---

## 1. What changed in the agent, in short

| Before (0.2) | Now (0.3) |
|---|---|
| Bills could only be uploaded after "start a claim for …"; otherwise `409 no_active_claim` | Bills can be uploaded **at any time**. `409 no_active_claim` no longer exists |
| Bills were read only when the employee said "review my claim" | The app **polls** `POST /v1/workflows/{id}/bills/check` while bills are being read. Each message also processes finished bills first |
| Nothing was saved unless the employee said "save draft" | When saving is switched on, the agent **works out the tour** of each bill and **saves it into that tour's draft** (new or existing) by itself |
| No per-file feedback | A **`bill_report`** event: one line per file, with what was read, where it went and why |
| — | A **`bill_status`** event: how many bills are still being read |
| — | `POST /v1/sessions` returns `bills: { pending, unreported }`, so the launcher can say "Reading your bills…" or "Your bills are read" while the chat is closed |
| — | "Read it again" and "Remove" for single bills: `POST …/bills/{documentId}/retry` and `…/cancel` |
| "Start a claim" on a tour with a saved draft sent the employee to the claim form | It now loads the draft, and new bills are added to it |

Draft saving is still **off by default** on the agent (`TCA_ENABLED_WRITE_ENDPOINTS=[]`). While it is off,
bills are matched to a tour but not saved: their report lines say `ready_not_saved`. The UI must handle both.

---

## 2. Requirements

Priority: **M** = Must, **S** = Should, **C** = Could.

### 2.1 FE-API: the agent client (`src/services/agentClient.ts`)

| ID | Requirement | Pri |
|---|---|---|
| FE-API-01 | Add the event types `BillStatusEvent` and `BillReportEvent` (§3.2) to the `AgentEvent` union. | M |
| FE-API-02 | Add `bills: { pending: number; unreported: number }` to `AgentSession`. | M |
| FE-API-03 | Add `checkBills(workflowId)` → `POST /workflows/{id}/bills/check?stream=false`, returning `events`. | M |
| FE-API-04 | Add `retryBill(workflowId, documentId)` → `POST /workflows/{id}/bills/{documentId}/retry?stream=false` and `cancelBill(workflowId, documentId)` → `…/cancel?stream=false`. Both return `events`. | M |
| FE-API-05 | `JourneyLine.origin` and `StayLine.origin` may now be `"saved"` (a row already in the saved draft), besides `plan`, `bill`, `employee`. Type them as `'plan' \| 'bill' \| 'employee' \| 'saved'`. | M |
| FE-API-06 | `toAgentError` already passes through the agent's `{ error, message }`. New code to expect: `404 bill_not_found`. `409 no_active_claim` will never be returned again. | S |

### 2.2 FE-UP: uploading at any time

| ID | Requirement | Pri |
|---|---|---|
| FE-UP-01 | The paperclip and camera in the composer are **always** available, except for read-only sessions (`appReadOnly` or `session.employee.read_only`). Uploading no longer depends on an `upload_request` card having been shown. | M |
| FE-UP-02 | Default upload limits must match the agent: **20 files per request, 20 MB per file**, PDF/JPG/JPEG/PNG. Change `DEFAULT_LIMITS` in `AgentChat.tsx` (today 10 files / 10 MB). An `upload_request` event still overrides them. | M |
| FE-UP-03 | **Split large selections.** When the employee picks more than 20 files, send them in batches of at most 20, one request after the other, in the order picked. Also keep each batch under the server's request size limit (FE-OPS-02): start a new batch when the next file would push the batch over **50 MB**. Show one user bubble for the whole selection. Append each batch's events as they arrive. | M |
| FE-UP-04 | Keep the client-side checks (type, size, empty file) before sending. A file that fails is named in an error bubble and left out; the rest are still uploaded. (Today one bad file stops the whole selection; change that.) | S |
| FE-UP-05 | After an upload, start polling (FE-POLL) using the `bill_status` event in the reply. | M |
| FE-UP-06 | The upload reply may start with a `notice` (files skipped as duplicates: "Skipped, already uploaded: a.pdf."). Show it like any notice. If every file was a duplicate, the reply is just that notice plus `bill_status`. | M |

### 2.3 FE-POLL: polling for finished bills

| ID | Requirement | Pri |
|---|---|---|
| FE-POLL-01 | Track `pendingBills` = the `pending` value of the most recent `bill_status` event (from any reply: upload, message, check, retry, cancel), or `session.bills.pending` at start. | M |
| FE-POLL-02 | While `pendingBills > 0`, call `checkBills` every **20 s**. After a failed call, wait **60 s** before the next one. Stop when `pendingBills` reaches 0. These are the same intervals as the old assistant's `ocrJobs.ts`. | M |
| FE-POLL-03 | Poll only while the browser tab is visible (`document.visibilityState === 'visible'`). When the tab becomes visible again and `pendingBills > 0`, check once straight away, then continue every 20 s. | M |
| FE-POLL-04 | Polling continues while the chat **window is closed** (the launcher is still mounted). Events that arrive are appended to the chat as usual, so they are there when the employee opens it (and kept in sessionStorage like other items). | M |
| FE-POLL-05 | At most one `checkBills` call at a time. Skip a tick while a user-triggered call (message, upload, retry, cancel) is in flight; the agent processes finished bills in those calls too. | M |
| FE-POLL-06 | A poll must not show the typing dots or block the composer. It is a background call: do not use `run()`'s `busy` flag. | M |
| FE-POLL-07 | `checkBills` needs a workflow. Poll only when there is a `workflowId`. On a `404 workflow_not_found`, stop polling and clear the stored workflow id, as `run()` already does. On `401`, stop polling (the existing sign-in handling applies). | M |
| FE-POLL-08 | A check that finds nothing new returns only `[{ type: 'bill_status', … }]`. Do not draw anything for it. | M |

### 2.4 FE-SES: session start and the launcher

| ID | Requirement | Pri |
|---|---|---|
| FE-SES-01 | Call `startSession()` **when the launcher mounts** (on the Raise Claim page), not only when the window first opens. Today `AgentChat` starts it on first open; move that into `TourClaimAgentLauncher`, or keep `AgentChat` mounted while closed, so `bills` is known before the employee opens the chat. | M |
| FE-SES-02 | Launcher text while closed: if `pendingBills > 0`, "Reading your bills…" (with the count when more than one: "Reading 3 bills…"); else if there are unseen bill results (FE-SES-03), "Your bills are read. Open me to see where they went."; else the normal greeting. | M |
| FE-SES-03 | **Unseen results.** When `session.bills.unreported > 0` at start, and there is a stored `workflowId`, call `checkBills` once straight away to fetch the report. Without a stored workflow, create one with `newWorkflow()` first. A `bill_report` that arrives while the window is closed sets an "unseen" flag that drives FE-SES-02 and a dot on the launcher; opening the window clears it. | M |
| FE-SES-04 | Chat header status (the `onStatusChange` text) shows "Reading 2 bills…" while `pendingBills > 0` and nothing else is in progress. | S |
| FE-SES-05 | Launcher greeting (`TourClaimAgentLauncher`): "Hi! Drop your tickets and hotel bills here and I'll put them in the right claim, or ask me about your tours." | S |

### 2.5 FE-REP: the bill report card

| ID | Requirement | Pri |
|---|---|---|
| FE-REP-01 | New `BillReportCard` in `AgentCards.tsx`, drawn for every `bill_report` event (wide row, like `ClaimSummaryCard`). Title from the event (`"What I found in your bills"`). | M |
| FE-REP-02 | One line per entry in `files`: an outcome icon and short label (table in §2.5.1), the file name, `found` ("ticket Noida → Mumbai, 05 Oct 2026") when present, "Tour {tour_id}" when present, and `detail` in smaller text when present. Lines in the order received. | M |
| FE-REP-03 | **Read it again:** a line with `retry_document_id` shows a "Read it again" button that calls `retryBill`. Append the reply's events (a short text and a `bill_status`, which restarts polling). Disable the button after it is pressed, and on lines of older report cards once that file has been retried. | M |
| FE-REP-04 | **Remove:** a line with `cancel_document_id` (outcome `slow`) shows a "Remove" button that calls `cancelBill`. Same handling as FE-REP-03. | S |
| FE-REP-05 | A `tours` section under the lines, one block per entry: "Tour {tour_id} · {route} · {dates}", then `coverage` ("These bills add 05–08 Oct 2026. Every day of the tour is now covered.") when present. When `draft_id` is not null, show "Saved in draft {draft_id}". The "Open draft" button is drawn by the `draft_saved` card that follows in the same reply; do not duplicate it here. | M |
| FE-REP-06 | Outcome values the UI does not know (future ones) are shown with a neutral icon and the raw `detail`. | S |
| FE-REP-07 | Errors from `retryBill`/`cancelBill`: `404 bill_not_found` → error bubble "That bill was not found."; `403 read_only` → existing read-only message. | S |

#### 2.5.1 Outcome labels

| `outcome` | Icon / tone | Label shown | Button |
|---|---|---|---|
| `saved` | green check | Saved in your draft | |
| `already_saved` | grey check | Already in your draft | |
| `ready_not_saved` | amber | Matched; please enter it in the claim form | |
| `save_failed` | amber clock | Not saved yet; I'll try again | |
| `tour_not_claimable` | amber | That tour is no longer open for a claim | |
| `no_tour` | grey | Doesn't match any tour waiting for a claim | |
| `awaiting_tour` | blue question | Which tour? (see the question below) | |
| `problem` | red | Not added | |
| `nothing_found` | grey | No journey or hotel stay found | |
| `unreadable` | red | Couldn't be read | Read it again |
| `used_elsewhere` | grey | Already used elsewhere | |
| `slow` | amber clock | Still being read; taking longer than usual | Remove |

`detail` always gives the reason in full ("its dates (09–10 Oct 2026) are outside your tour dates (05–08 Oct 2026)"),
so labels can stay short.

### 2.6 FE-EV: other events

| ID | Requirement | Pri |
|---|---|---|
| FE-EV-01 | `bill_status` is **not drawn** as a message. It only updates `pendingBills` (FE-POLL-01). It may come at the end of upload, message, check, retry and cancel replies. | M |
| FE-EV-02 | `choices` is unchanged. Bill questions ("Which tour are these bills for?") end with an option **"None of these"** whose `tour_id` is `0`; sending its number works as for any option. Such a question stays open on the agent while the employee does other things, so it is fine that its chips are only live on the newest `choices` card. The agent asks again later if needed. | M |
| FE-EV-03 | `draft_saved` may now come **several times in one reply** (one per tour saved). Each gets its own card. Change the card text from "Draft saved for tour #X" to "Saved in your draft for tour #X", since bills are often added to an existing draft. | M |
| FE-EV-04 | `claim_summary`: rows with `origin: "saved"` are already in the saved draft. Mark them with a small "Saved" tag. A new finding code `AIR_NOT_IN_PLAN` is a warning like the others (no special handling). Show the Save button only when `save_enabled && can_save` (unchanged). | S |
| FE-EV-05 | A `claim_summary` may now arrive inside a bill check reply (for the claim in progress, or when a saved draft has blocking problems). Draw it as usual. | M |

### 2.7 FE-CONV: wording in the chat

| ID | Requirement | Pri |
|---|---|---|
| FE-CONV-01 | Greeting in `AgentChat` (`greeting`): "Hello {name}. Upload your tickets and hotel bills at any time and I'll find the tour and save them into its draft. You can also ask about your tours." Keep the action-needed and view-only sentences. | M |
| FE-CONV-02 | Composer placeholder: 'Ask, or add your tickets and hotel bills with 📎'. | S |
| FE-CONV-03 | `STARTERS`: "Show my tours", "Which tours can I claim?", "Are my bills read?". | S |
| FE-CONV-04 | Resume notice (`resume()`): "Continuing “{title}”." Drop the "Ask “Review my claim”" hint, since a resumed conversation may have no claim. | C |
| FE-CONV-05 | The user bubble for an upload stays as it is ("📎 3 bills: a.pdf, b.jpg, c.pdf"). | — |

### 2.8 FE-OPS: deployment

| ID | Requirement | Pri |
|---|---|---|
| FE-OPS-01 | `public/web.config` has no `/agent-api` rule yet (only the dev proxy in `vite.config.ts`). Add a rewrite rule `^agent-api/(.*)` → `<agent base URL>/{R:1}`, like the `chat-api` rule, for UAT and production. The agent URL per environment comes from the deployment team. | M |
| FE-OPS-02 | Raise the request size limit for `/agent-api` (`<requestLimits maxAllowedContentLength>`) to at least **55 MB**, to fit one upload batch (FE-UP-03, 50 MB) plus form overhead. IIS's default is about 30 MB. | M |
| FE-OPS-03 | No change to `VITE_AGENT_ENABLED`: the agent stays behind that flag and replaces the old Claim Assistant only when it is `true`. | — |

---

## 3. API reference

All calls carry `Authorization: Bearer <SSO token>` (already done by the `agentApi` interceptor) and use
`?stream=false`, which returns `{ "events": AgentEvent[] }`.

### 3.1 Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/v1/sessions` | — | `AgentSession` (now with `bills`) |
| POST | `/v1/workflows/{id}/attachments` | multipart, field `files`, ≤ 20 files | `text`, optional `notice`, `bill_status` |
| POST | `/v1/workflows/{id}/bills/check` | — | Optional `text` summary, `bill_report`, `draft_saved` (per saved tour), `claim_summary`, `choices`, then always a final `bill_status` |
| POST | `/v1/workflows/{id}/bills/{documentId}/retry` | — | `text`, `bill_status` |
| POST | `/v1/workflows/{id}/bills/{documentId}/cancel` | — | `text`, `bill_status` |
| POST | `/v1/workflows/{id}/messages` | `{ text }` | As before; bill events (if any finished) come **first**, then the reply |

Errors (`{ error, message }`): `400 attachment_rejected`, `401 invalid_token` / `session_expired`,
`403 read_only` / `identity_mismatch`, `404 workflow_not_found` / `bill_not_found`, `502/503 backend_error`.

### 3.2 New and changed types

```ts
export interface BillStatusEvent {
  type: 'bill_status';
  pending: number; // bills of this employee still being read (all conversations)
  files: { document_id: number; file_name: string; status: string }[];
}

export type BillOutcome =
  | 'saved' | 'already_saved' | 'ready_not_saved' | 'save_failed' | 'tour_not_claimable' | 'no_tour'
  | 'awaiting_tour' | 'problem' | 'nothing_found' | 'unreadable' | 'used_elsewhere' | 'slow';

export interface BillReportLine {
  file_name: string;
  outcome: BillOutcome | string;
  tour_id?: number | null;
  found?: string | null; // "ticket Noida → Mumbai, 05 Oct 2026" / "hotel stay in Mumbai, 05–08 Oct 2026"
  detail?: string | null; // the reason, in full
  retry_document_id?: number | null; // "Read it again"
  cancel_document_id?: number | null; // "Remove"
}

export interface BillReportEvent {
  type: 'bill_report';
  title: string;
  files: BillReportLine[];
  tours: { tour_id: number; route: string; dates: string; draft_id?: number | null; coverage?: string | null }[];
}

export interface AgentSession {
  employee: { name: string; designation?: string | null; unit?: string | null; read_only: boolean };
  pending: { action_needed_count: number };
  bills: { pending: number; unreported: number };
  workflows: WorkflowInfo[];
}
```

### 3.3 Example: a check that saved two bills

```json
{ "events": [
  { "type": "text", "text": "I read 2 bills. 2 are saved in your draft for Tour 601 (Noida → Mumbai, 05–08 Oct 2026)." },
  { "type": "bill_report", "title": "What I found in your bills",
    "files": [
      { "file_name": "train.pdf", "outcome": "saved", "tour_id": 601, "found": "ticket Noida → Mumbai, 05 Oct 2026", "detail": null, "retry_document_id": null, "cancel_document_id": null },
      { "file_name": "hotel.pdf", "outcome": "saved", "tour_id": 601, "found": "hotel stay in Mumbai, 05–08 Oct 2026", "detail": null, "retry_document_id": null, "cancel_document_id": null }
    ],
    "tours": [ { "tour_id": 601, "route": "Noida → Mumbai", "dates": "05–08 Oct 2026", "draft_id": 9001,
                 "coverage": "These bills add 05–08 Oct 2026. Every day of the tour is now covered." } ] },
  { "type": "draft_saved", "tour_id": 601, "draft_id": 9001, "open_in_form": "/raise-claim-approver?tourId=601&isDraft=true&statusId=2&totalDays=4" },
  { "type": "bill_status", "pending": 0, "files": [] }
] }
```

### 3.4 Example: a bill the agent can't place

```json
{ "events": [
  { "type": "text", "text": "I read 1 bill. For 1 I need you to tell me the tour." },
  { "type": "bill_report", "title": "What I found in your bills",
    "files": [ { "file_name": "taxi.pdf", "outcome": "awaiting_tour", "tour_id": null, "found": "ticket Mumbai → Mumbai Airport", "detail": "I couldn't tell which tour it is for" } ],
    "tours": [] },
  { "type": "choices", "prompt": "Which tour are these bills for?",
    "options": [ { "number": 1, "tour_id": 601, "label": "Tour 601 (Noida → Mumbai, 05–08 Oct 2026)" },
                 { "number": 2, "tour_id": 603, "label": "Tour 603 (Noida → Mumbai, 06–09 Oct 2026)" },
                 { "number": 3, "tour_id": 0, "label": "None of these" } ] },
  { "type": "text", "text": "Which tour are these bills for? (taxi.pdf) Reply with its number." },
  { "type": "bill_status", "pending": 0, "files": [] }
] }
```

---

## 4. Acceptance scenarios

Run against the agent on UAT (or locally with the agent's fake backend where noted). "Saving on" means the agent
has `TCA_ENABLED_WRITE_ENDPOINTS=["Claim/SaveRaiseClaimDraft"]`.

| # | Scenario | Expected in the UI |
|---|---|---|
| 1 | Open the chat and attach a ticket and a hotel bill without typing anything (saving on) | Upload goes through (no "start a claim first"). "Got 2 files … reading them now". Header: "Reading 2 bills…". Within about 20–40 s of OCR finishing, a summary text, a report card with 2 × "Saved in your draft", the tour block with coverage, and an "Open draft" card. Polling stops |
| 2 | Same, with a third bill for another tour | The report lists both tours; two draft cards |
| 3 | Attach 25 files | Two requests (20 + 5), one user bubble, both replies appended |
| 4 | Attach a file already uploaded earlier | Notice "Skipped, already uploaded: …"; no extra polling if nothing new |
| 5 | A bill that matches two tours equally | Report line "Which tour?", then the question with tour chips and "None of these". Picking a tour shows a new report with "Saved in your draft" |
| 6 | An unreadable scan | Line "Couldn't be read" with "Read it again"; pressing it shows "I'm reading … again." and polling restarts |
| 7 | Upload, close the chat window, wait for OCR | Launcher changes from "Reading your bills…" to "Your bills are read. Open me…" with a dot; opening shows the report |
| 8 | Upload, reload the page before OCR finishes | After reload: session `bills.pending` > 0, polling resumes, the report arrives in the same chat |
| 9 | Upload, close the browser, come back the next day after OCR finished | Session says `unreported` > 0, the launcher says the bills are read, and the report appears once. It doesn't appear again on the next check |
| 10 | Switch to another tab while bills are pending | No calls while hidden; one check straight away on return |
| 11 | Agent unreachable during polling | Next try after 60 s; no error bubble per failed poll (one quiet status in the header is enough) |
| 12 | Saving off (agent default) | Lines say "Matched; please enter it in the claim form"; no draft card |
| 13 | View-only session | No paperclip or camera; no polling |
| 14 | "Start a claim for my Mumbai tour" on a tour with a saved draft | Summary card shows its rows tagged "Saved", plus the upload card |

---

## 5. Tests to add (`tests/tourClaimAgent/`, node:test like `tests/claimChatbot/`)

Keep the logic in small pure functions so it can be tested without rendering:

1. `uploadBatches(files, { maxFiles: 20, maxBytes: 50 MB })`: order kept, 25 files → 20 + 5, size limit
   starts a new batch, one file over the limit goes alone.
2. `nextPollDelay({ pending, lastCallFailed, visible })`: 20 s, 60 s after a failure, none at 0 or when hidden.
3. `pendingFromEvents(events, previous)`: takes the last `bill_status.pending`, keeps `previous` when none.
4. `outcomeView(outcome)`: label, tone and button for every outcome in §2.5.1, and a neutral fallback.
5. `launcherText({ pending, unseen })`: the three texts of FE-SES-02.

## 6. Out of scope

- Any change to how bills are matched or saved: that is the agent's job.
- Removing the old Claim Assistant: it stays behind `VITE_AGENT_ENABLED`.
- Showing amounts that the agent doesn't send (the report has no amounts; `claim_summary` still does).
