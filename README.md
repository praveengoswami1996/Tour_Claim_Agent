# Tour Claim AI Agent

A standalone, goal-oriented assistant that helps DFCCIL employees with their tour claims.
It talks to the existing .NET Tour API **as the signed-in employee** and never writes to the
database directly. The .NET backend remains the source of truth for data, calculations,
validations and authorization.

Built with Python 3.12, FastAPI, LangGraph, Pydantic and HTTPX. The LLM provider is pluggable
and not yet chosen. Requirements are in `../Project-Context/agent/functional_requirements.md`.

## What works in this release (0.2)

### Tours and status (read-only)

| Ask | What the agent does |
|---|---|
| "Show my tours" | Lists your tours with status and who currently holds each one |
| "What is pending for me?" | Tours that need your action (clarification, sent back) and approved tours not yet claimed |
| "Which tours can I claim?" | Approved tours ready for a claim |
| "Where is my Mumbai claim?" / "Status of tour 1234" | Current status in plain words, who holds it, and the last steps of its history. Asks you to choose if several tours match |

### Preparing an outside-jurisdiction claim (journey J1)

1. **"Start a claim for my Mumbai tour"**: only your own approved (status 2) tours qualify, and
   only if no draft exists for the tour already. Journeys are seeded from the approved tour plan.
2. **Upload bills** (`POST /v1/workflows/{id}/attachments`): PDF/JPG/PNG, checked by type, size and
   content, de-duplicated by hash, and sent to the Tour system's existing OCR queue under the
   reference `agent:<workflow>:tour-<id>`.
3. **"Review my claim"**: finished OCR results are claimed (`MarkAsRead`, so the web app never applies
   them twice), matched to planned journeys (both places must match, date within a day) or added
   as new rows, and checked: missing dates/places, claimed above billed, journeys or stays outside
   the tour dates, broken journey chain, missing return, missing tickets or hotel bills, missing
   invoice numbers, tax-type mismatches, duplicate files or invoices, unreadable bills.
4. **"Save draft"**: saves a backend draft (`Claim/SaveRaiseClaimDraft`) in the exact .NET format,
   reads it back, and returns a link that opens it in the existing claim form. **Off by default**
   (see below). Nothing is ever submitted from the agent.
5. **"Cancel this claim"**: withdraws bills still in the OCR queue and releases read results.

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

Read-only (impersonated) sessions can never upload or save.

## Safety design

- **Identity from the token only.** The employee code comes from the validated token; the
  numeric empId from the Organisation service, which must return the same employee code.
- **Ownership.** Tour details are only read for tours found in the employee's own lists.
- **Allow-list.** Every backend call is checked against `clients/allowlist.py`.
- **No tokens in state.** Tokens travel as LangGraph runtime context and are never checkpointed
  or logged.
- **No automatic retries of writes.** Before saving, the agent re-checks that the tour is still
  claimable and that no draft was created elsewhere; an unclear save is reconciled by reading back.
- **Confirmation framework** (`agent/confirmation.py`): single-use, expiring confirmations bound
  to a hash of the exact payload, ready for submit and clarification replies.

## Project layout

```
src/tourclaim_agent/
  main.py            FastAPI app, lifecycle, error mapping
  service.py         Authenticates callers, runs workflows, validates uploads
  config.py          Settings (TCA_* environment variables)
  api/routes.py      /v1 HTTP interface (SSE replies)
  auth/              Token validation (JWKS / introspection / dev) and identity
  clients/           Tour API, OCR and Organisation clients, envelope, allow-list
  domain/            Typed backend models, claim working copy, status vocabulary
  drafting/          OCR mapping, bill-to-row matching, checks, draft payload builder
  llm/               Intent schema, rule-based extractor, OpenAI-compatible adapter
  agent/             LangGraph workflow, claim journey, resolver, UI events, confirmations
  state/             Workflow registry (ownership, resume, confirmations)
tests/               Unit and API tests against a fake backend (synthetic data)
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
| POST | `/v1/sessions` | Who you are, pending-action count, open conversations |
| POST | `/v1/workflows` | Start a conversation |
| GET | `/v1/workflows` | List your open conversations |
| DELETE | `/v1/workflows/{id}` | Abandon a conversation |
| POST | `/v1/workflows/{id}/messages` | Send `{ "text": "..." }` |
| POST | `/v1/workflows/{id}/attachments` | Upload bills (multipart field `files`) for the claim in progress |

Replies are an SSE stream of `agent_event` items then `done`; add `?stream=false` for plain JSON.
Event types: `text`, `notice`, `tour_list`, `tour_status`, `choices`, `upload_request`,
`claim_summary`, `draft_saved` (see `agent/events.py`).

Errors: `400 attachment_rejected`, `401 invalid_token` / `401 session_expired` (sign in again),
`403 read_only` / `identity_mismatch`, `404 workflow_not_found`, `409 no_active_claim`,
`502/503 backend_error`.

## To verify on UAT before switching anything on

1. Token claims (`UserId`, scope, `IsReadOnly`) and the empCode → empId mapping.
2. Date formats in `Claim/RaiseClaimRequest` (`tentativeTourPlanList.date`, `tentativeStartDate`
   come from .NET `DateTime.ToString()`, which depends on the server culture).
3. The OCR provider's result fields against `drafting/ocr_mapping.py`.
4. A saved draft opens correctly in the claim form (rows, file-to-row links, tax fields), and
   `LastEditedSection = null` / `RecipentId = null` are accepted.

## Next steps

1. UAT verification (above), then enable draft saving.
2. Let the employee correct rows in the chat (amounts, dates, own-arrangement stays).
3. Clarification replies and submission with the confirmation framework.
4. Connect the chosen LLM provider and a PostgreSQL state store for deployment.
