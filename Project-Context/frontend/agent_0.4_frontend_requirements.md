# Tour Claim AI Agent 0.4: frontend requirements (Tour_React)

**Status:** target contract. The agent is being rebuilt to behave exactly like the old in-app Claim Assistant
(ARYAAN); this document is what the React app must do for that. It **replaces** `agent_0.3_frontend_requirements.md`.
**Agent side:** `TourClaim_AI_Agent/Project-Context/agent/old_assistant_parity/` (`00_port_plan.md` for the decisions,
specs A–E for the old behaviour, quoted below as "Spec A §4.2" etc.).
**Old code being replaced:** `src/components/claimChatbot/ClaimChatbot.tsx`, `WithinClaimChatbot.tsx`, `useNarrator.ts`,
`src/lib/claimChatbot/*` (except UI helpers), and the assistant's use of `src/lib/ocr/*`.

---

## 1. What changes

The employee must see **no difference** from the old Claim Assistant: same windows, launchers, greetings, buttons,
questions, reports and wording (the AI model still writes every reply, in English, Hindi or Hinglish). What moves is
*where* the work happens.

| Work | Old assistant | Now |
|---|---|---|
| Deciding what to do, what to ask, which buttons to show | React (`ClaimChatbot.tsx`, `WithinClaimChatbot.tsx`) | **Agent** |
| Calling the AI model (understand, narrate) and the gateway key | Browser (`/chat-api`, `VITE_CHAT_API_KEY`) | **Agent** (the key leaves the browser) |
| Checking, hashing and de-duplicating files; uploading to OCR; polling; MarkAsRead/Release; leases | Browser (`lib/ocr/*`) | **Agent** (same OCR endpoints and the old reference tags) |
| Reading bills into rows, finding the tour, merging, review questions, missing-detail questions, typed edits | Browser (`lib/claimChatbot/*`) | **Agent** |
| Saving a draft from the Raise Claim page (`saveDraftFromAssistant`) | Browser | **Agent** (`SaveRaiseClaimDraft`, same payload) |
| Kept reports ("bills read while you were away") | `localStorage` | **Agent** |
| The chat window, launcher, chips, composer, voice input, file picker, camera | React | React (unchanged look) |
| Anything on an **open claim form**: read rows, write rows, run its checks, save, submit, DA and totals | React form | React form, **when the agent asks** through the bridge (§5) |
| Opening the claim form with the chat's claim ("Fill the form") | React (`draftHandoff`) | React, **when the agent asks** (§5.2) |

---

## 2. Where the assistant appears

Same places and conditions as before. One chat **mode** per place:

| Mode | Where | Shown when (unchanged) | Old component |
|---|---|---|---|
| `raise_page` | Raise Claim page (`RaiseClaim.tsx`) | always (when `VITE_AGENT_ENABLED=true`) | `ClaimAssistantLauncher` → `ClaimChatbot` (no `form`) |
| `raise_form` | Claim form `/raise-claim-approver` (`ApproverPage.tsx`) | `!isExpandedRow && !activeTab && !readOnly && !isClarificationMode && !!assistantTour` | `ClaimAssistantLauncher` → `ClaimChatbot` with `form` |
| `within_form` | `/within-juridiction/new` and `/edit/:draftId` (`WithinJurisdictionFormPage`) | `!isReadOnly && mode !== 'clarification' && !existingTourPlanId` | `WithinClaimAssistantLauncher` → `WithinClaimChatbot` |

`VITE_AGENT_ENABLED=false` keeps the old in-browser assistant everywhere (no change for users until the switch).

---

## 3. Requirements

Priority: **M** = Must, **S** = Should.

### 3.1 FE-CONV: conversations

| ID | Requirement | Pri |
|---|---|---|
| FE-CONV-01 | When a chat is mounted, create a conversation: `POST /v1/workflows` with `{ mode, tourId?, draftId? }` (`tourId` for `raise_form`, `draftId` for `within_form` when editing). Keep its id for the life of the mount. Store it in sessionStorage per mode (and tour/draft), so a page reload continues the same conversation. | M |
| FE-CONV-02 | The chat stays **mounted while the window is closed**, as before (ChatPanel hides it). Send `active: true/false` with every call (§4.1): the agent writes replies and goes through read bills **only while the window is open**, exactly as the old narrator and read effect did. | M |
| FE-CONV-03 | The first time the window opens, call `POST /v1/workflows/{id}/open`. Its reply is the old greeting (Spec A §3, Spec D §9.1). Call it again when the window is reopened after replies were held back (`held: true` in the last `status` event). | M |
| FE-CONV-04 | The header's **Start over** button (ChatPanel) remounts the chat: abandon the conversation (`DELETE /v1/workflows/{id}`) and create a new one, with no question, exactly as before (Spec A §0.1). | M |
| FE-CONV-05 | Only one call per conversation at a time (the old `busy`): while a call is running, the composer and chips are disabled and the typing indicator shows the label from the latest `status.busy` (default "Typing…"). Polls (§3.5) are skipped while a call runs. | M |

### 3.2 FE-MSG: what the employee does

| ID | Requirement | Pri |
|---|---|---|
| FE-MSG-01 | **Typed message:** add the user bubble locally (the text, or the first 200 characters + `…` when it is over 1,000 characters, as the old `send`), then `POST /messages` with `{ text, active, form? }` (§4.2). Clean the text as `cleanMessage` did (control and zero-width characters removed, whitespace collapsed) before sending. | M |
| FE-MSG-02 | **Chip tap:** `POST /actions` with `{ chip: <chip id>, active, form? }`. Chips of kind `link` open their `href` in a new tab and send nothing; kind `upload` opens the file picker; kind `camera` opens the camera. | M |
| FE-MSG-03 | **Files** (📎 picker, camera, drag-and-drop, any `upload` chip): `POST /attachments` (multipart `files`, plus `active` and `form` fields). Send all picked files: the agent applies the old checks, duplicate rules and the 20-file limit and answers with the old wording (Spec A §4.2). The picker keeps `accept` = PDF, JPG, PNG. | M |
| FE-MSG-04 | **Try again:** when the last reply was the system notice, the only chip is "Try again"; tapping it calls `POST /retry`. | M |
| FE-MSG-05 | Voice input and read-aloud stay as they are in the old UI (browser features). Closing the window stops speech. | S |

### 3.3 FE-EV: drawing what the agent sends

| ID | Requirement | Pri |
|---|---|---|
| FE-EV-01 | `reply` events are the assistant's bubbles. While streaming, `reply_delta` carries the **whole text so far**; replace the bubble text each time (as `useNarrator` did). Render with the existing `FormattedText` (bold, lists, ✅/⚠️). `tone` `error`/`success` styles the bubble as before. | M |
| FE-EV-02 | `system_notice` is the fixed bubble "The assistant is unavailable right now. Please try again." (system style). | M |
| FE-EV-03 | `user_files` adds one user bubble per accepted file: `📎 <name>` (Spec A §4.2 step 11). | M |
| FE-EV-04 | `chips` **replaces** the whole chip bar with the list given, in order, with `primary` styling and `disabled` as sent. An empty list hides the bar. | M |
| FE-EV-05 | `composer` sets the placeholder and whether typing and attaching are allowed (e.g. "Checking your tours…", "Type your answer…", "Type a detail, a correction or a question…"). | M |
| FE-EV-06 | `status` sets the header status line (e.g. "Thinking…", "Processing 2 bills…", "Tour #1234 · you review and submit", "Upload your bills to begin"), the typing-indicator label (`busy`), and the launcher notice for when the window is closed. | M |
| FE-EV-07 | `bills` lists staged and in-flight files for the pills above the composer ("Processing your bill…" / "Processing N bills…" with names; staged files with ✕ to remove → `POST /actions` with the chip id given on the pill). | M |
| FE-EV-08 | `bridge_call`: carry out the call on the page (§5) and answer it with `POST /bridge/{callId}`. The answer's events continue the same turn; keep the typing indicator on until a response arrives with no further `bridge_call`. | M |
| FE-EV-09 | Unknown event types are ignored (forward compatibility). | S |
| FE-EV-11 | `open_file_picker` opens the composer's file picker, as the old chip did directly. | M |
| FE-EV-12 | Send the `Origin` header (browsers do) so the saved-claim link chips carry this app's address. | M |
| FE-EV-10 | `reply_remove` removes the bubble with that `id` (a streamed reply the model then failed to finish; the old narrator dropped it the same way). `messages_cleared` empties the message list (the old chat re-greeting when the drafts changed). `user_message` adds a user bubble with `text` (a chip whose old `onClick` sent words, e.g. "yes"). `error` shows the system notice with "Try again". | M |

### 3.4 FE-LAU: launchers

| ID | Requirement | Pri |
|---|---|---|
| FE-LAU-01 | Launcher bubbles and notices come from the agent's `status.launcher` (old texts: "Your bill is read. Open me to go through it." / "Your 3 bills are read. Open me to go through them." / "Processing your bill…" / "Processing 3 bills…"; Within: "Your tour plan is read. Open me to create the tours." / "Processing your tour plan…"). With none, show the old resting bubble ("Need help? Ask Aryaan" on hover; Within: "Hi, I'm ARYAAN! Upload your monthly tour plan and I'll create every tour for you."). | M |
| FE-LAU-02 | On page load (before the window is first opened), call `POST /v1/sessions` with the mode to get the launcher state, so bills read while the employee was away show "…read. Open me…" straight away. | M |

### 3.5 FE-POLL: bills being read

| ID | Requirement | Pri |
|---|---|---|
| FE-POLL-01 | While the latest `bills.inFlight` is not empty, call `POST /v1/workflows/{id}/poll` every **20 s** while the tab is visible, every **60 s** after an error, and once straight away when the tab becomes visible again (the old `ocrJobs.ts` intervals). | M |
| FE-POLL-02 | Send `active` with each poll. With the window closed the agent only updates `status` and `bills` (the launcher notice); with it open, the agent goes through finished bills and replies (the old read effect, Spec A §4.6, §7.4). | M |
| FE-POLL-03 | Polls never show the typing indicator and are skipped while another call runs. | M |

### 3.6 FE-RO: read-only and errors

| ID | Requirement | Pri |
|---|---|---|
| FE-RO-01 | Read-only (impersonated) logins: the assistants are not shown in `raise_form` and `within_form` (as before). On `raise_page` the agent refuses uploads with the old message; also hide 📎 and camera. | M |
| FE-RO-02 | HTTP errors from the agent (`401 session_expired`, `404 workflow_not_found`, `503`): `401` → the app's normal sign-in handling; `404` → create a new conversation and call `/open`; others → show the system notice with "Try again". | M |

### 3.7 FE-CLEAN: removing the browser assistant (when `VITE_AGENT_ENABLED=true`)

| ID | Requirement | Pri |
|---|---|---|
| FE-CLEAN-01 | The agent-mode chat must not call the gateway (`/chat-api`), `Ocr/*`, `MarkAsRead`/`Release`, or write the assistant's localStorage keys (`aryaan.billReports.v1.*`, `aryaan.chatDrafts.v1.*`, `aryaan.ocrClaims.v1.*`, `aryaan.ocrTabs.v1`). The agent does all of that. | M |
| FE-CLEAN-02 | `OcrReadyNotifier` (toast on other pages) keeps working: the agent uses the old reference tags (`outside-chat:unassigned`, `claim-chat:tour-<id>`, `within-plan`). | M |
| FE-CLEAN-03 | Keep the old code behind the flag until the agent is signed off on UAT. | M |

---

## 4. API

Base: `/agent-api/v1` (same proxy rule as today). Every call carries the employee's SSO token. Replies are an SSE stream
(`event: agent_event`, `data: <event JSON>`, then `event: done`) so replies appear while the model writes them;
`?stream=false` returns `{ "events": [...] }` and is fine for polls.

### 4.1 Common body fields

| Field | Meaning |
|---|---|
| `active` | `true` when the chat window is open |
| `form` | `raise_form` / `within_form` only: the form's current state as `bridge.read()` returns it (§5.1, §5.3). Sent with every call so the agent always works on the live form (the old `syncFromForm()`). |

### 4.2 Endpoints

| Method | Path | Body | Use |
|---|---|---|---|
| POST | `/sessions` | `{ mode, tourId?, draftId? }` | Launcher state before the window opens (`status`, `bills`) |
| POST | `/workflows` | `{ mode, tourId?, draftId? }` | New conversation → `{ workflow_id }` |
| DELETE | `/workflows/{id}` | — | Start over (header button) |
| POST | `/workflows/{id}/open` | `{ active, form? }` | Greeting / held replies |
| POST | `/workflows/{id}/messages` | `{ text, active, form? }` | Typed message |
| POST | `/workflows/{id}/actions` | `{ chip, active, form? }` | Chip tap |
| POST | `/workflows/{id}/attachments` | multipart: `files[]`, `active`, `form` (JSON) | Bills or tour plan |
| POST | `/workflows/{id}/poll` | `{ active, form? }` | Bills/plan being read |
| POST | `/workflows/{id}/retry` | `{ active, form? }` | "Try again" after the system notice |
| POST | `/workflows/{id}/bridge/{callId}` | `{ ok, result?, error?, form? }` | Answer to a `bridge_call` |

### 4.3 Events

```ts
type AgentEvent =
  | { type: 'reply_start'; id: string; tone?: 'error' | 'success' }
  | { type: 'reply_delta'; id: string; text: string }            // whole text so far
  | { type: 'reply'; id: string; text: string; tone?: 'error' | 'success' } // final text
  | { type: 'system_notice'; text: string }                       // "The assistant is unavailable right now. Please try again."
  | { type: 'user_files'; names: string[] }                       // add "📎 name" user bubbles
  | { type: 'chips'; items: Chip[] }
  | { type: 'composer'; placeholder: string; canType: boolean; canAttach: boolean; highlightUpload?: boolean } // highlight 📎 as the old composer did
  | { type: 'status'; header: string; busy?: string | null; launcher?: string | null; held?: boolean; greeting?: string | null; draftsChanged?: boolean } // greeting: the launcher's resting bubble text
  | { type: 'bills'; staged: FilePill[]; inFlight: FilePill[] }
  | { type: 'bridge_call'; id: string; method: BridgeMethod; args?: unknown }
  | { type: 'reply_remove'; id: string }                         // take out a reply that started streaming, then failed
  | { type: 'messages_cleared' }                                  // clear the chat's messages (the old re-greeting)
  | { type: 'user_message'; text: string }                        // add a user bubble (a chip that "says" words)
  | { type: 'error'; message: string }
  | { type: 'open_file_picker' };                                 // open the 📎 file picker (the old "Yes, I'll upload more")                           // a failure after the stream started: show the system notice

interface Chip {
  id: string;                     // send back in POST /actions
  label: string;                  // exact old label, e.g. "Yes, that's right", "📎 Upload other bills", "↩ Undo last change"
  kind: 'action' | 'link' | 'upload' | 'camera';
  href?: string;                  // kind 'link': opens in a new tab (the old saved-claim link chips)
  primary?: boolean;
  disabled?: boolean;
}
interface FilePill { name: string; documentId?: number; removeChip?: string }
type BridgeMethod = 'read' | 'write' | 'check' | 'info' | 'saveDraft' | 'submit' | 'openForm';
```

A turn's events arrive in this order: optional `user_files`, `status` (busy), then `reply_start`/`reply_delta`*/`reply`
(or `system_notice`), or a `bridge_call` (the turn waits for its answer), then the final `chips`, `composer`, `status`
and `bills`.

---

## 5. The bridge: what React does on the agent's behalf

The agent only asks; React does exactly what the old bridge objects did, with the form's own code. Every answer
(`POST /bridge/{callId}`) includes the form's state **after** the call (`form`).

### 5.1 `raise_form` (claim form, old `ClaimFormBridge`, `ApproverPage.tsx:2154-2185`, Spec E §5)

| Method | React does | Answer `result` |
|---|---|---|
| `read` | Nothing (the state is sent anyway) | — |
| `write` `{ state, replace? }` | As the old `write(state, {replace})`: set transport/stay/leave rows and `includeAccommodation`/`includeLeave`; `RetainedTransportationFileIds` / `RetainedAccomodationFileIds` = every `fileId` still on the rows (`[]` with `replace`); `hasUserModifiedDA = false`. Files in rows that have a `documentId` and no `fileId` are new bills: fetch them with `GET /Ocr/GetFile/{documentId}` and attach them as `File` objects before the next save. | — |
| `saveDraft` | `forceSave()` **after the write has rendered** (fixes the old timing quirk, Spec E §1.11) | `true` / `false` |
| `check` | `collectSubmitProblems()` | list of `{ kind, section, row, message }` |
| `submit` | `setIsDeclared(true)`; `handleSubmit(true)` | `{ submitted: boolean, message?: string }` |

**Form state shape** (`form`): `{ transportationRows, accommodationRows, leaveRows, includeAccommodation, includeLeave }`
exactly as the form holds them (stored order). Rows are the form's `TransportationRow` / `AccommodationRow` /
`LeaveRow` objects; for files send `{ name, size, type, fileId?, isRetained?, tempFilePath?, documentId? }`, never
the bytes. Keep each new bill file's `documentId` on its row until the form has saved it (the agent uses it to know which
bill results the form still needs).

### 5.2 `raise_page` (Raise Claim page)

| Method | React does | Answer `result` |
|---|---|---|
| `openForm` `{ tourId }` (no `state`) | Open a saved claim in the form, as the old `onOpenClaim` (`startClaim(row, false)`) | `{ opened: boolean }` |
| `openForm` `{ tourId, state }` | The old "Fill the form" hand-off (`fillFormFromAssistant`, Spec E §1.9): fetch every new bill file in `state` (`documentId` without `fileId`) with `GET /Ocr/GetFile/{documentId}` into `File` objects, then `setAssistantDraft({ tourId, state })` and `startClaim(row, true)`. The form applies it as before (rows replaced, retained ids, DA recalculated, toast "Claim Assistant filled your form. Please review it before submitting.") | `{ opened: boolean }` (answer before navigating) |

Saving drafts from the Raise Claim page is done by the agent itself; no bridge call. After the agent saves, it sends a
`status` event and the page should refresh its draft list (`GetAllRaiseClaimDrafts`) when it sees
`{ type: 'status', ... }` with `draftsChanged: true` (S).

### 5.3 `within_form` (Within Jurisdiction form, old `WithinFormBridge`, Spec D §3)

| Method | React does | Answer `result` |
|---|---|---|
| `read` | Nothing (the state is sent anyway) | — |
| `write` `{ tours }` | As the old `write(tours)`: set `tourRequestsRef.current.tours` and state, bump `tourFormResetKeys` for every tour (the form then recomputes totals, overlaps, re-sorts ended tours and generates DA as before) | — |
| `check` | The old `check()` list (not-ended tours, zero DA, `validateTour` errors, approver, overlaps), in the same order | `string[]` |
| `info` | `{ selfApprove, approverChosen }` | object |
| `saveDraft` | `saveDraft(fakeClickEvent)` (toast "Draft saved successfully!") | — |
| `submit` | Accept the declarations, wait 100 ms, `submitAllTours()` | `{ submitted: boolean, message?: string }` |

**Form state shape** (`form`): `{ tours: TourData[] }` — `tours` exactly as the form holds them (Spec D §1),
including each tour's `id`, `tourEnd`, `purpose`, the main journey and every leg (`startDate`, `startTime`, `endDate`,
`endTime`, `departureDate`, `arrivalDate`, `from`, `to`, `modeOfTravel`, `personalVehicleType`, `distance`), the
amounts the form stored on them (`amountDetails`, accommodation `invoice` amounts) and the DA entries it generated
(`daAllowanceDetails.entries[].amount` / `daApplicable`). The chat's totals text adds up these stored values, as the
old chat did; the form's own totals are not needed.

**Dates:** `JSON.stringify` turns the form's `Date` objects into ISO text, and the agent sends them back the same way.
On `write`, turn `tourDetails.startDate` / `endDate` (and leg dates held as `Date`) back into `Date` objects
(`new Date(x)`), so the form holds exactly what the old chat put there.

---

## 6. Acceptance scenarios

Each must look and read as with the old assistant (side by side with `VITE_AGENT_ENABLED=false`):

| # | Mode | Scenario | Expected |
|---|---|---|---|
| 1 | raise_page | Open with no saved drafts | Reply exactly "Upload your tour tickets/bills/receipts" |
| 2 | raise_page | Open with saved drafts | "You have N saved drafts…" with one link chip per draft, "📎 Upload other bills", "Not now" |
| 3 | raise_page | Attach 3 bills, 1 a duplicate, 1 a .docx | `📎` bubbles for the new one; "Your bill is being read…"; pills "Processing your bill…"; later one report with New / Duplicate / Not relevant groups; draft saved for the right tour |
| 4 | raise_page | Attach bills, close the window before they are read | Launcher "Your bills are read. Open me to go through them."; opening shows the report |
| 5 | raise_page | Close the browser, come back the next day | The kept report comes first in the greeting |
| 6 | raise_page | Type "left Noida 5 Oct by air, back 8th by train" | Tour question with tour chips; then missing details one at a time; review questions; "Fill the form" opens the form with the rows and files |
| 7 | raise_form | Attach a bill inside the claim form | Rows added to the form, the form saves, "Do you have more tickets or bills to upload for this claim?" |
| 8 | within_form | Upload a monthly tour plan | Tours created on the form, gaps asked one at a time, "end tours" ends them and DA appears |
| 9 | within_form | "submit" with everything complete | Totals and the 7 declarations; "Yes" submits through the form |
| 10 | any | Gateway down | "The assistant is unavailable right now. Please try again." with "Try again" |
| 11 | any | Page reload mid-conversation | Same conversation continues (sessionStorage id) |

---

## 7. Notes for the implementer

- Reuse the old UI pieces unchanged: `ChatPanel`, `ChatParts` (bubbles, chips, typing dots), `Composer`, `Brand`
  (launchers), `FormattedText`, `ChatTheme`. The new component is a thin renderer of the events above plus the bridge.
- The bridge must use the form's real functions (not copies), so DA, totals, overlap re-sorting and validation stay
  exactly the form's.
- Do not send file bytes in `form`; new files reach the agent only through `/attachments`, and reach the form only
  through `GetFile` when the agent writes rows that reference them.
- The agent sends no toasts; toasts come from the form functions themselves, as before.
