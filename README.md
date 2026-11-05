# Tour Claim AI Agent

A standalone, goal-oriented assistant that helps DFCCIL employees with their tour claims.
It talks to the existing .NET Tour API **as the signed-in employee** and never writes to the
database directly. The .NET backend remains the source of truth for data, calculations,
validations and authorization.

Built with Python 3.12, FastAPI, LangGraph, Pydantic and HTTPX. The LLM provider is pluggable
(rule-based, any OpenAI-compatible endpoint, or the DFCCIL ARYAAN AI gateway). Requirements are in
`Project-Context/agent/functional_requirements.md` and
`Project-Context/agent/bill_upload_auto_draft_requirements.md`.

## What works in this release (0.3)

### Tours and status (read-only)

| Ask | What the agent does |
|---|---|
| "Show my tours" | Lists your tours with status and who currently holds each one |
| "What is pending for me?" | Tours that need your action (clarification, sent back) and approved tours not yet claimed |
| "Which tours can I claim?" | Approved tours ready for a claim |
| "Where is my Mumbai claim?" / "Status of tour 1234" | Current status in plain words, who holds it, and the last steps of its history. Asks you to choose if several tours match |

### Bills at any time, saved into the right draft (new in 0.3)

This copies how the in-app Claim Assistant handled bills, which this agent replaces.

1. **Upload bills whenever you like** (`POST /v1/workflows/{id}/attachments`), with or without a
   claim in progress. PDF/JPG/PNG only, checked by type, size and content. A file whose content you
   already uploaded through the agent (in any conversation) is skipped as a duplicate, unless the
   earlier copy couldn't be read or was handed back unused. Files go to the Tour system's existing
   OCR queue tagged `agent:<workflow>:unassigned` (or `agent:<workflow>:tour-<id>` during a claim).
   The reply comes straight away, with a `bill_status` event.
2. **The app polls `POST /v1/workflows/{id}/bills/check`** every 20 s while `bill_status.pending > 0`.
   The same processing also runs at the start of every message. It handles every finished bill of
   the employee, from any conversation:
   - claims the result with `MarkAsRead` (a result another client took is reported as
     `used_elsewhere` and never applied);
   - turns it into journeys and stays;
   - **works out the tour** from the dates and places, among the employee's approved tours waiting
     for a claim (same rules as the in-app `tourMatcher.ts`). A bill that clearly belongs to another
     tour goes there, even during a claim. When it can't tell, it asks "Which tour are these bills
     for?" with up to 5 tours and "None of these";
   - **saves into that tour's draft**, read fresh from the server. A new draft starts from the
     approved plan. In an existing draft the saved rows, their files, leave, daily allowance,
     remarks and anything else entered in the claim form are kept exactly as they are; new rows go
     after the saved ones. A journey or stay the draft already holds is not added again. A planned
     leg saved with nothing on it is filled in place;
   - re-checks that the tour is still claimable, saves, and confirms by reading the draft back.
     If the draft can't be read, or the save can't be confirmed, nothing is overwritten and the
     result is handed back to try again later.
3. **A `bill_report` event** lists one line per file: what was read ("ticket Noida → Mumbai, 05 Oct
   2026"), where it went, and why if it wasn't used. It also shows which days of the tour are now
   covered. Outcomes are kept until they have been shown, so bills read while the chat was closed
   are reported in the next conversation. `POST /v1/sessions` returns `bills.pending` and
   `bills.unreported` for the launcher.
4. **Open the draft in the claim form** (`draft_saved.open_in_form`), let the form calculate daily
   allowance, review, and submit. Nothing is ever submitted from the agent.

Stay bills are refused with a reason when check-out is before check-in, or the dates fall outside
the tour (±1 day) or the travel dates. An air ticket on a tour whose plan has no air leg is saved
with an empty travel mode and a warning.

Automatic saving needs draft saving switched on (below). Blocking problems (a missing date, for
example) don't stop an automatic draft save, since a draft may be incomplete; they are listed in
the report. With saving off, or `TCA_AUTO_SAVE_BILLS=false`, bills are matched and merged in the
conversation instead (`ready_not_saved`), one claim per tour.

### Conversation

| Ask | What the agent does |
|---|---|
| "Start a claim for my Mumbai tour" | Own approved (status 2) tours only. If the tour has a saved draft, it is loaded and new bills are added to it; otherwise journeys are seeded from the approved plan |
| "Are my bills read?" | Bills still being read, waiting for a tour, or waiting to be saved again |
| "Review my claim" | The claim in progress with checks: missing dates/places, claimed above billed, journeys or stays outside the tour dates, broken journey chain, missing return, missing tickets or hotel bills, missing invoice numbers, tax-type mismatches, duplicate files or invoices, unreadable bills, air not in the plan |
| "Save draft" | Saves the claim in progress (refused while blocking problems exist) |
| "Cancel this claim" | Stops the claim in progress. Bills still being read are withdrawn; read bills not saved anywhere are handed back. Nothing already saved in a draft changes |

Daily allowance is **not** calculated by the agent: the claim form does that when the draft is
opened (requirements decision DEC-1). No entitlement limits are applied, because the backend has
no API for them yet (DEP-1/2).

Other write requests (answer a clarification, edit, supplementary) are recognised and answered
with a pointer to the app screen.

## Switching on draft saving

Write endpoints are on the allow-list but disabled. After verifying the payload against UAT, an
operator enables saving with:

```
TCA_ENABLED_WRITE_ENDPOINTS=["Claim/SaveRaiseClaimDraft"]
```

| Setting | Default | Meaning |
|---|---|---|
| `TCA_ENABLED_WRITE_ENDPOINTS` | `[]` | Must include `"Claim/SaveRaiseClaimDraft"` for any saving |
| `TCA_AUTO_SAVE_BILLS` | `true` | When saving is enabled, save read bills into drafts automatically. `false` keeps them in the conversation until "save draft" |
| `TCA_BILL_REPORT_KEEP_DAYS` | `14` | Outcomes never shown to the employee are dropped after this; bills still waiting for a tour are handed back |
| `TCA_BILL_SLOW_MINUTES` | `15` | A bill still being read after this is reported once as slow, with the option to remove it |

Read-only (impersonated) sessions can never upload or save.

## Safety design

- **Identity from the token only.** The employee code comes from the validated token; the
  numeric empId from the Organisation service, which must return the same employee code.
- **Ownership.** Tour details are only read for tours found in the employee's own lists. Bills are
  only matched to the employee's own claimable tours. Every upload-store query filters by employee.
- **Allow-list.** Every backend call is checked against `clients/allowlist.py`. Bill processing
  uses no new endpoints.
- **No tokens in state.** Tokens travel as LangGraph runtime context and are never checkpointed
  or logged. That is why bills are processed only inside a request from the app, never in the
  background.
- **Each OCR result used once.** `MarkAsRead` decides; bill processing for one employee runs one
  request at a time.
- **No automatic retries of writes.** Before saving, the agent re-checks that the tour is still
  claimable; an unclear save is reconciled by reading back, never by sending it again. A draft that
  exists but can't be read is never written over.
- **Saved drafts are never changed.** Saved rows keep their position, content and files (the
  server ties files to rows by position); every saved file is sent as retained.
- **Logs** carry document ids, tour ids, outcomes and status codes, never amounts or file content.
- **Confirmation framework** (`agent/confirmation.py`): single-use, expiring confirmations bound
  to a hash of the exact payload, ready for submit and clarification replies.

## Project layout

```
src/tourclaim_agent/
  main.py            FastAPI app, lifecycle, error mapping
  service.py         Authenticates callers, runs workflows, validates uploads, per-employee lock
  config.py          Settings (TCA_* environment variables)
  api/routes.py      /v1 HTTP interface (SSE replies)
  auth/              Token validation (JWKS / introspection / dev) and identity
  clients/           Tour API, OCR and Organisation clients, envelope, allow-list
  domain/            Typed backend models, claim working copy (incl. saved rows), status vocabulary
  drafting/          OCR mapping, bill-to-row merging, checks, draft payload builder,
                     tour_match.py (which tour), saved_draft.py (read a draft),
                     bill_pipeline.py (process finished bills)
  llm/               Intent schema, rule-based extractor, OpenAI-compatible and ARYAAN adapters
  agent/             LangGraph workflow, claim journey, bill flow, resolver, UI events, confirmations
  state/             Workflow registry (ownership, resume, confirmations), upload store
tests/               Unit, API and acceptance tests against a fake backend (synthetic data)
```

## Running locally

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy .env.example .env      # then edit .env
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m uvicorn --factory tourclaim_agent.main:app_factory --port 8010
```

`TCA_AUTH_MODE=unverified_dev` skips the token signature check for local work. It is refused
outside `development`/`test`.

## HTTP interface

All endpoints need `Authorization: Bearer <employee OIDC access token>`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/health` | Liveness |
| POST | `/v1/sessions` | Who you are, pending-action count, `bills: {pending, unreported}`, open conversations |
| POST | `/v1/workflows` | Start a conversation |
| GET | `/v1/workflows` | List your open conversations |
| DELETE | `/v1/workflows/{id}` | Abandon a conversation |
| POST | `/v1/workflows/{id}/messages` | Send `{ "text": "..." }`. Finished bills are processed first |
| POST | `/v1/workflows/{id}/attachments` | Upload bills (multipart field `files`, at most 20 per request), at any time |
| POST | `/v1/workflows/{id}/bills/check` | Process finished bills. Poll every 20 s while `pending > 0` (60 s after an error) |
| POST | `/v1/workflows/{id}/bills/{documentId}/retry` | "Read it again" for an unreadable bill |
| POST | `/v1/workflows/{id}/bills/{documentId}/cancel` | Remove a bill that is still being read |

Replies are an SSE stream of `agent_event` items then `done`; add `?stream=false` for plain JSON
(`{ "events": [...] }`). Event types (see `agent/events.py`): `text`, `notice`, `tour_list`,
`tour_status`, `choices`, `upload_request`, `bill_status`, `bill_report`, `claim_summary`,
`draft_saved`.

`bill_report` file outcomes: `saved`, `already_saved`, `ready_not_saved`, `save_failed`,
`tour_not_claimable`, `no_tour`, `awaiting_tour`, `problem`, `nothing_found`, `unreadable` (with
`retry_document_id`), `used_elsewhere`, `slow` (with `cancel_document_id`).

Errors: `400 attachment_rejected`, `401 invalid_token` / `401 session_expired` (sign in again),
`403 read_only` / `identity_mismatch`, `404 workflow_not_found` / `bill_not_found`,
`502/503 backend_error`. The old `409 no_active_claim` is gone: uploads no longer need a claim.

## To verify on UAT before switching anything on

1. Token claims (`UserId`, scope, `IsReadOnly`) and the empCode → empId mapping.
2. Date formats in `Claim/RaiseClaimRequest` (`tentativeTourPlanList.date`, `tentativeStartDate`
   come from .NET `DateTime.ToString()`, which depends on the server culture).
3. The OCR provider's result fields against `drafting/ocr_mapping.py`.
4. A saved draft opens correctly in the claim form (rows, file-to-row links, tax fields), and
   `LastEditedSection = null` / `RecipentId = null` are accepted.
5. `SaveRaiseClaimDraft` with `DraftId` set plus `Retained…FileIds`: saved files stay linked to
   their rows by `rowIndex`, and new files are linked by `StartFileIndexCount`/`EndFileIndexCount`
   counted over new files only (as the React app does).
6. The file-reference field names in `GetRaiseClaimDraft` (`transportationFileReferences`,
   `accomodationFileReferences`, `fileId`, `rowIndex`) and the type of `fileId`.
7. `GetMyDocuments?ReferencePrefix=agent:` returns documents from every conversation of the
   employee.
8. Carrying a draft's own fields back (camelCase keys are written under the DTO's PascalCase names;
   `ClaimAmount` becomes the saved total plus the new rows).

## Next steps

1. UAT verification (above), then enable draft saving.
2. React: upload at any time, poll `bills/check`, draw `bill_report`, split selections of more
   than 20 files (requirements §7).
3. Let the employee correct rows in the chat (amounts, dates, own-arrangement stays).
4. Clarification replies and submission with the confirmation framework.
5. A PostgreSQL state store for deployment (the per-employee lock assumes one instance).
