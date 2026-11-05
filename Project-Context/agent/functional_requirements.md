# Tour Claim AI Agent: Functional Requirements

| | |
|---|---|
| Document | Functional Requirements Specification (FRS) |
| Version | 0.2 (draft for review). Extended by `bill_upload_auto_draft_requirements.md` (release 0.3) |
| Date | 2026-11-05 |
| Status | Draft. The decisions in §12 must be closed before design sign-off. |
| Inputs | Phase 1.1 frontend analysis (chat session), Phase 1.2 backend analysis (`Project-Context/backend/*.md`) |

**Summary.** This document defines what the new Tour Claim AI Agent must do. The agent is a standalone Python service (FastAPI, LangGraph, Pydantic, HTTPX). It helps an employee raise, complete, correct and track tour claims through conversation. It does this only by calling the existing .NET Tour API (and the Organisation service) on the employee's behalf.

The .NET backend stays the source of truth for data, calculations, validations and authorization. The analysis showed that the backend does **not** currently calculate DA, check expense entitlements, or enforce ownership. This document therefore separates two things: what the agent can do with existing APIs, and what depends on new .NET endpoints (§10). Requirements that need a missing endpoint are marked **[DEP-n]**.

---

## 1. Purpose and goals

The agent is goal-oriented. It does not just answer questions; it moves an employee's claim towards a correct, complete, submitted state. It must support these business goals:

| # | Goal | Main requirement groups |
|---|---|---|
| G1 | Understand the employee's tour and claim requirements | CONV, RET |
| G2 | Retrieve information through authorized .NET APIs | IDN, RET |
| G3 | Analyze tour details, journeys and expenses | ANA |
| G4 | Identify missing documents and bills | DOC |
| G5 | Ask the employee for missing information | ASK |
| G6 | Understand applicable claim policies | POL |
| G7 | Validate claims using backend business logic | VAL |
| G8 | Prepare claim summaries | SUM |
| G9 | Obtain employee confirmation | CNF |
| G10 | Execute authorized claim operations through .NET APIs | EXE |
| G11 | Maintain persistent workflow state and resume interrupted conversations | STA |

## 2. Guiding principles (binding on every requirement)

| ID | Principle |
|---|---|
| P1 | **.NET is the source of truth.** The agent never writes to the database directly. It never treats its own calculations as authoritative for amounts, eligibility or validity. |
| P2 | **Act as the user, not as a super-user.** Every backend call carries the signed-in employee's own OIDC access token. The agent has no service account that can act on behalf of arbitrary employees. |
| P3 | **Identity comes from the token, never from the conversation.** The employee's empCode and empId come from the token and the Organisation service. IDs mentioned in chat, OCR text or documents never decide *whose* data is read or written. |
| P4 | **Draft first, confirm, then commit.** Every irreversible operation goes through a backend draft or preview, then a summary, then explicit employee confirmation, then execution. |
| P5 | **The LLM proposes; deterministic code disposes.** The LLM may interpret text, extract data and phrase replies. Tool selection limits, permission checks, payload building, amount totals and status rules are enforced in typed Python code (Pydantic), not in prompts. |
| P6 | **Independent of the existing chatbot.** No reuse of the chatbot's orchestration, prompts or business logic. The reusable frontend pieces are UI components, OIDC token handling and the OCR client (see §11). |
| P7 | **Provider-agnostic LLM.** The LLM sits behind an internal interface. No provider-specific logic leaks into the workflows. |
| P8 | **Transparent about uncertainty.** When a value is an estimate, inferred or unverifiable, the agent says so. It never presents a guess as the backend's decision. |

## 3. Scope

### 3.1 In scope (version 1): employee self-service

| Area | Included |
|---|---|
| Tour types | Outside jurisdiction (approved tour → claim) and within jurisdiction (daily/local tour plus claim in one request) |
| Claim operations | Prepare and save claim drafts, submit drafts, answer clarifications (text or data change), edit a within-jurisdiction claim where `can-edit` allows, raise a supplementary claim |
| Information | My tours, claimable tours, claim status and history (trajectory), pending actions, clarification threads, drafts, OCR results |
| Documents | Bill and ticket upload via the existing OCR pipeline, mapping OCR results to claim rows, detecting missing bills |
| Conversation | Multi-turn, resumable across sessions and devices, English (Hindi: see D9) |

### 3.2 Out of scope for version 1

- Approver, bill-passing, finance, CGM and admin actions (approve, reject, transfer, bulk approval, recovery, SAP). These may come in a later version through a separate role-scoped agent.
- Creating a new outside-jurisdiction tour **request** (the pre-approval step). It is read-only in v1; see D7.
- Payments, advances, refunds (`TourNotAvailed/PaymentRefund`) and recovery.
- Any change to the .NET backend or React app, except the integration items in §10 and §11, which those teams own.
- Reusing or migrating the existing ARYAAN chatbot.

### 3.3 Actors

| Actor | Description |
|---|---|
| Employee | Primary user. Signed in to the React app via DFCCIL SSO (OIDC). Owns the tours and claims. |
| Agent service | FastAPI + LangGraph service. Calls the Tour API, the Organisation service and the LLM provider. |
| Tour API (.NET) | The system of record for tours, claims, documents, OCR and statuses. |
| Organisation service | The source of employee profile, grade, reporting manager, finance or bill-passing user, and promotion date. |
| LLM provider | To be decided. Reached only from the agent service. |
| Approver / Finance | Not users of the agent in v1. They see the agent's results through the normal app. |

---

## 4. Requirement conventions

- **Priority:** **M** = Must (v1), **S** = Should (v1 if feasible), **C** = Could (later).
- **[DEP-n]** = depends on a new or changed backend capability (§10). **[DEC-n]** = depends on an open decision (§12).
- Endpoint names are exactly as they exist in the Tour API (see `Project-Context/backend/tour_claim_api_reference.md`).

---

## 5. Functional requirements

### 5.1 CONV: Conversation and goal understanding (G1)

| ID | Requirement | Pri |
|---|---|---|
| CONV-01 | The agent shall accept free-text and voice-transcribed messages and file attachments, and identify the employee's **goal**: raise a claim, continue a draft, answer a clarification, check status, edit a claim, raise a supplementary claim, ask a policy question, or general help. | M |
| CONV-02 | The agent shall identify the **target tour or claim**. It resolves references like "my Delhi trip last week" against the employee's own tours (RET-01). When the reference is ambiguous it shall show the candidates and ask the employee to choose; it shall never guess. | M |
| CONV-03 | The agent shall keep one active goal per workflow and confirm before switching goals mid-workflow. Unfinished work is parked as a resumable workflow (STA-04). | M |
| CONV-04 | The agent shall answer in plain language. It shall show amounts in ₹ with two decimals and dates as DD-MMM-YYYY, and always name the tour (tour number, route and dates) it is talking about. | M |
| CONV-05 | When the agent opens a session, it shall proactively tell the employee about pending items: open clarifications, sent-back tours, unsubmitted drafts, claimable tours without a claim, and OCR results waiting. | S |
| CONV-06 | The agent shall decline requests outside its scope (§3.2), such as approving claims or acting for another employee, and point to the right screen in the app. | M |
| CONV-07 | The agent shall stream partial replies to the UI, so the employee sees progress during long operations (OCR, multi-call retrievals). | S |

### 5.2 IDN: Identity, session and authorization (G2)

| ID | Requirement | Pri |
|---|---|---|
| IDN-01 | Every request from the React app to the agent shall carry the employee's OIDC access token. The agent shall validate it: signature against the SSO server's JWKS, issuer, expiry, and audience or scope (`api1`). It shall reject invalid tokens with 401. **[DEC-2]** | M |
| IDN-02 | The agent shall forward the same token as `Authorization: Bearer` on every Tour API and Organisation-service call, with the `DeviceType` header value agreed in [DEC-3]. | M |
| IDN-03 | The agent shall derive the employee's empCode from the token's `UserId` claim. It shall resolve the numeric empId, grade, unit, reporting manager and roles from the Organisation service. This identity is cached for the session and never taken from user input. **[DEP-6]** | M |
| IDN-04 | **Ownership guard.** Before any read of claim detail and before any write, the agent shall verify that the target tour or claim belongs to the signed-in employee (`EmpId` of the record == the session empId). The backend does not enforce this today, so the agent must. | M |
| IDN-05 | **Read-only / impersonated sessions.** If the token indicates read-only or impersonation (`IsReadOnly`), the agent shall allow reads only and refuse every write. **[DEC-4]** | M |
| IDN-06 | The agent shall only call endpoints on an explicit **allow-list** (§9). Anonymous, admin, finance, SAP and destructive endpoints are not on it. | M |
| IDN-07 | **Token expiry.** If the token expires mid-workflow, the agent shall pause the workflow without losing state (STA-02) and ask the UI to refresh the session. It shall resume automatically once a valid token is presented. The agent shall never store refresh tokens. **[DEC-5]** | M |
| IDN-08 | The agent shall never put tokens into LLM prompts, logs or persisted workflow state. | M |

### 5.3 RET: Information retrieval (G1, G2)

All retrieval uses existing endpoints, read-only, with the user's token.

| ID | Requirement | Backend source | Pri |
|---|---|---|---|
| RET-01 | List the employee's tours (outside and within), with status, dates and route. | `GET TourListing/GetTourRequest/{empId}?touretype=1\|2` | M |
| RET-02 | List tours eligible for a claim. | `GET TourListing/GetTourRaiseClaimList/{empId}` | M |
| RET-03 | Get full tour detail. | `GET TourPlan/GetTourDetails/{tourId}` | M |
| RET-04 | Get the full claim view: rows, bills, amounts, approver and finance state. | `GET Claim/RaiseClaimRequest/{tourId}`, `GET Claim/ClaimsDetailsById/{tourId}` | M |
| RET-05 | Get the status history and current status, explained in plain words (§7.2). | `GET TourListing/GetTourTrajectory/{empTourId}` | M |
| RET-06 | Get the employee's drafts (outside and within). | `GetAllRaiseClaimDrafts`, `GetRaiseClaimDraft`, `GetAllClaimWithRequestDrafts`, `GetClaimWithRequestDraft` | M |
| RET-07 | Get clarification threads and the open question for a tour. | `ClaimClarification/thread/{tourId}` and the GET clarification endpoints | M |
| RET-08 | Get pending actions: the count, then derive the list from tours with status ∈ {11, 13, 16, 23} where the employee is the recipient. | `GetTotalPendingTaskCount/{empId}/USER` + RET-01. A real list API is **[DEP-7]** | M |
| RET-09 | Get supplementary claim details and whether a supplementary claim is allowed. | `GET Claim/GetSupplementaryClaimDetails/{empTourPlanId}` | S |
| RET-10 | Check whether a within-jurisdiction claim can be edited, and show the edit history. | `GET ClaimEdit/can-edit/{planId}?empId=`, edit history endpoints | S |
| RET-11 | All responses shall be parsed into typed Pydantic models. The agent shall treat the body's `statusCode`/`error` as the result, because the API often returns HTTP 200 on failure. | All | M |

### 5.4 ANA: Tour, journey and expense analysis (G3)

The analysis is **advisory**. It helps the employee prepare a correct claim before submission. It never replaces backend validation (VAL).

| ID | Requirement | Pri |
|---|---|---|
| ANA-01 | Build a **tour timeline** from the tour detail and claim rows: journeys (from → to, date and time, mode), stays (check-in and check-out), DA days and leave periods. | M |
| ANA-02 | Detect **itinerary gaps and inconsistencies**: a broken journey chain (the destination of leg *n* ≠ the source of leg *n+1*), a missing return leg, journeys outside the approved tour dates, overlapping journeys, stays not covered by journeys, a check-out before check-in, and stays outside the tour dates. | M |
| ANA-03 | Detect **amount inconsistencies**: claimed > billed on a row, the claim total ≠ the sum of rows, tax fields that don't match the tax structure (IGST vs CGST+SGST), and missing invoice number or date on a billed row. | M |
| ANA-04 | **Pre-check overlaps** against the employee's other tours (RET-01), so a likely backend 409 can be explained before submission. It must be labelled as a pre-check; the backend's overlap validator stays authoritative. | S |
| ANA-05 | Compare the claim against the **approved tour**: purpose, places, dates and advance taken. Flag any differences the employee must explain. | S |
| ANA-06 | Every finding shall carry a severity (blocking / warning / info), the affected row, and a suggested fix the employee can accept or reject. | M |

### 5.5 DOC: Documents, bills and OCR (G4)

| ID | Requirement | Pri |
|---|---|---|
| DOC-01 | Accept bill, ticket, boarding-pass and hotel-invoice uploads (pdf/jpg/jpeg/png) in the conversation. Check type and size before upload, matching the app's limits. | M |
| DOC-02 | Send files to the **existing OCR pipeline** with the user's token, using an agent-specific reference tag (e.g. `agent:tour-<id>`) so the existing app's OCR routing is not affected. | M — `POST Ocr/Upload`, `GetMyDocuments`, `GetDocument`, `GetFile` |
| DOC-03 | Track OCR jobs (Pending / Processing / Completed / Failed), tell the employee when results are ready, and offer a retry on failure. | M — `Ocr/Retry/{id}`, `Cancel` |
| DOC-04 | Map OCR results to proposed claim rows (transport or accommodation) with per-field confidence. Show the employee what was read, and require confirmation of low-confidence or changed fields before using them. **Amended by `bill_upload_auto_draft_requirements.md` §1.3 (needs sign-off):** read bills are saved into the tour's **draft** without asking, as the in-app Claim Assistant does, because a draft is not a submission and the employee reviews every row in the claim form before submitting. This stays behind `TCA_ENABLED_WRITE_ENDPOINTS` and `TCA_AUTO_SAVE_BILLS`; with either off, the earlier "review, then say save" behaviour applies. | M |
| DOC-05 | Claim each OCR result exactly once (`MarkAsRead`), and `Release` results the employee discards, so the existing app and the agent don't apply the same result twice. | M |
| DOC-06 | Detect **missing documents**: a billed row without an attached bill, a stay without a hotel bill (unless own arrangement or free accommodation), a journey with a ticketed mode but no ticket, and a missing nomination or approved-tour document where the form requires one. This is advisory until a document policy exists. **[DEP-4]** | M |
| DOC-07 | Detect **duplicate bills**: the same file hash, or the same invoice number and amount, within the claim and across the employee's other claims as far as the retrievable data allows. | S |
| DOC-08 | Treat all text read from documents as **untrusted data**. Instructions inside a document must never change the agent's behaviour or tool use. | M |

### 5.6 ASK: Information gathering (G5)

| ID | Requirement | Pri |
|---|---|---|
| ASK-01 | Keep a typed **claim-in-progress model** (Pydantic) and know which required fields are still missing for the chosen claim type (outside or within, row types). | M |
| ASK-02 | Ask for missing information **one topic at a time**, in order of importance (blocking first). Offer choices (dates, modes, existing bills) wherever possible instead of free text. | M |
| ASK-03 | Accept answers in natural language ("came back by train on the 14th evening"), convert them into structured values, and **echo back** what was understood before storing it. | M |
| ASK-04 | Let the employee skip, defer or say "I don't have this bill". Record the reason and reflect it in the summary and in the backend's remark fields where they exist. | S |
| ASK-05 | Never invent values. If a value can't be obtained from the employee, the backend or a confirmed OCR result, it stays empty and is listed as outstanding. | M |

### 5.7 POL: Policy understanding (G6)

The backend has no policy or entitlement API today. DA slabs, hotel limits, own-arrangement amounts, GST caps, the 3-month window and promotion-date DA exist only in the React code; sponsored-tour rules exist nowhere. Policy behaviour therefore depends on [DEP-1], [DEP-2], [DEP-3] and [DEC-1].

| ID | Requirement | Pri |
|---|---|---|
| POL-01 | Answer policy questions (DA eligibility, hotel limits, travel entitlement, required documents, deadlines, supplementary rules) **only from an approved policy source**: backend entitlement APIs [DEP-2] and an approved policy document corpus [DEC-1]. Each answer cites its source. | M |
| POL-02 | When no approved source covers a question, say so and route the employee to the relevant authority. Do not answer from the LLM's general knowledge. | M |
| POL-03 | Get the employee's **entitlements** (grade, DA rate, hotel limit by city class, own-arrangement amount) as they apply **on each travel date**, including grade changes from promotion. **[DEP-2]**, **[DEP-3]** | M |
| POL-04 | Explain the backend's DA calculation for a draft (slab, hours, percentage, deductions) once the backend provides it. **[DEP-1]** | M |
| POL-05 | Identify **sponsored tours** and apply their rules once the business defines them and the backend exposes them. **[DEC-6]** | S |
| POL-06 | Keep policy content versioned. Each summary records which policy version and entitlement values were used. | S |

### 5.8 VAL: Validation (G7)

| ID | Requirement | Pri |
|---|---|---|
| VAL-01 | Before any submission, run **backend validation** of the full claim without saving it, and show the employee every error and warning. **[DEP-5]** Until DEP-5 exists, see VAL-05. | M |
| VAL-02 | Translate backend errors into plain language with the affected row and a fix. This includes the overlap 409 (`TourConflict`: name the conflicting tour and dates), the duplicate-submission windows (30 s / 5 min), supplementary preconditions and edit-status guards. | M |
| VAL-03 | Combine the results: backend validation (authoritative) + ANA findings (advisory) + DOC findings (advisory). A claim may be submitted only when there are no **blocking** backend errors and the employee has seen all warnings. | M |
| VAL-04 | Never override, suppress or "fix silently" a backend validation result. Any change to fix a problem goes back through ASK, SUM and CNF. | M |
| VAL-05 | **Interim mode (no DEP-5):** validate as far as the existing APIs allow. That means a successful draft save plus ANA, DOC and the draft-submit required-field checks (`ValidateTourRequestDraftForSubmit` where it applies). The summary must say clearly that final validation happens at submission and that the submission may still be rejected. | M |

### 5.9 SUM: Claim summary (G8)

| ID | Requirement | Pri |
|---|---|---|
| SUM-01 | Produce a structured **claim summary** before any confirmation: the tour (number, purpose, route, dates); the journeys, stays, DA days and leave, each with amounts; the attached documents per row; the totals (claimed, billed, tax, advance adjusted where the backend gives it); outstanding items; warnings; and the policy or entitlement version used. | M |
| SUM-02 | Every amount in the summary shall come from the backend (the saved draft read back, or the DA and entitlement APIs). Values the agent calculated itself are labelled "estimate" and never shown as final. **[DEP-1]** | M |
| SUM-03 | Show the summary in the chat as a structured card (rendered by the UI from JSON, not free text), with a link that opens the same draft in the existing claim form. | M |
| SUM-04 | Make the summary available as a downloadable or printable view. | C |

### 5.10 CNF: Confirmation (G9)

| ID | Requirement | Pri |
|---|---|---|
| CNF-01 | Classify every operation by risk: **R0** read; **R1** reversible (save or update the employee's own draft); **R2** irreversible or externally visible (submit, respond to clarification, edit a submitted claim, raise supplementary, delete a draft, cancel). | M |
| CNF-02 | R1 operations need the employee's agreement to work on that claim (once per workflow). R2 operations need an **explicit confirmation of the exact summary**, through a UI button and not only free text. | M |
| CNF-03 | Bind each confirmation to a **hash of the exact payload** being sent. If anything changes after confirmation (data, files, recipient, amount), the confirmation is void and must be asked for again. | M |
| CNF-04 | Confirmations expire (default 15 minutes) and are single-use. | M |
| CNF-05 | State the consequence in the confirmation: who receives the claim (approver or finance name and role), the resulting status, and that it can't be edited afterwards except through the clarification or edit flows. | M |
| CNF-06 | A rejected confirmation ("no", "change X") returns to ASK or SUM with the workflow intact. | M |

### 5.11 EXE: Execution of claim operations (G10)

Only these operations are allowed in v1. Each runs only after CNF as its risk class requires, and only after IDN-04.

| ID | Operation | Backend call(s) | Risk | Pri |
|---|---|---|---|---|
| EXE-01 | Save or update an outside-jurisdiction claim draft | `POST Claim/SaveRaiseClaimDraft` (multipart) | R1 | M |
| EXE-02 | Submit an outside-jurisdiction claim draft | `POST Claim/SubmitRaiseClaimDraft/{draftId}?empId=` | R2 | M |
| EXE-03 | Save or update a within-jurisdiction claim-with-request draft | `POST Claim/SaveClaimWithRequestDraft` | R1 | M |
| EXE-04 | Submit a within-jurisdiction draft | `POST Claim/SubmitClaimWithRequestDraft/{id}?empId=` | R2 | M |
| EXE-05 | Answer a clarification with text only | `POST ClaimClarification/respond-message` | R2 | M |
| EXE-06 | Answer a clarification with data or document changes | `POST ClaimClarification/respond` (multipart, **all** rows) | R2 | M |
| EXE-07 | Edit a within-jurisdiction claim (only if `can-edit` = true) | `POST ClaimEdit/edit` | R2 | S |
| EXE-08 | Raise a supplementary claim (only on status 12 or 9, one per tour) | `POST Claim/RaiseSupplementaryClaim` | R2 | S |
| EXE-09 | Delete the employee's own draft | `DELETE Claim/DeleteRaiseClaimDraft/{id}`, `DeleteClaimWithRequestDraft/{id}` | R2 | S |
| EXE-10 | Hand off a draft to the existing claim form ("open in form") instead of submitting from chat | Navigation via the existing draft ID, no backend write | R0 | M |

| ID | Execution rule | Pri |
|---|---|---|
| EXE-11 | Payloads shall be built by deterministic code from the confirmed claim model. They must match the backend contract exactly: multipart with JSON-string fields, the exact (misspelled) field names, `YYYY-MM-DD` dates and `HH:mm` times, and file-index ranges (`StartFileIndexCount`/`EndFileIndexCount`). The LLM never writes payloads. | M |
| EXE-12 | **Idempotency.** Each R2 operation carries an idempotency key. The agent shall never retry an R2 call automatically after an ambiguous result (timeout, 5xx). It shall re-read the backend state (RET-04/05/06) to find out whether the operation took effect before offering a retry. | M |
| EXE-13 | After every write, read the record back and confirm the resulting status to the employee, using the backend's values and not the agent's assumption. | M |
| EXE-14 | Recipient IDs (`RecipentId` for approver or bill-passing/finance) shall be resolved from the Organisation service for the signed-in employee, never typed in by the user. **[DEP-6]** | M |
| EXE-15 | The agent shall never call status-transition, approval, finance, SAP, bulk, recovery or anonymous write endpoints (e.g. `TourStatus/*`, `ApproveRaiseClaimAmount`, `BulkApproval/*`, `EditTourRequest`, `SubmitTourRequestDraft` as anonymous). | M |

### 5.12 STA: Workflow state and resumption (G11)

| ID | Requirement | Pri |
|---|---|---|
| STA-01 | Each goal runs as a **LangGraph workflow** with a durable checkpointer in a server-side database (e.g. PostgreSQL). State survives restarts and deployments. | M |
| STA-02 | A workflow can be interrupted at any node (the employee leaves, the token expires, OCR is pending, waiting for confirmation) and resumed later from the same point, on any device, by the same employee only. | M |
| STA-03 | Each workflow is identified by (employee, goal, target tour or claim). Starting the same goal on the same tour resumes the existing workflow instead of creating a duplicate. | M |
| STA-04 | The employee can list, resume or abandon their open workflows ("continue my Bhopal claim"). | M |
| STA-05 | **Reconcile on resume.** The agent shall re-read the backend state before continuing. If the tour or claim changed elsewhere (submitted from the app, status moved, draft edited), it shall tell the employee and re-plan instead of acting on stale state. | M |
| STA-06 | The backend draft is the canonical copy of claim data. Agent state holds the conversation, the plan, pending questions, confirmations, OCR job references and draft IDs, not a second copy of claim data that could diverge. | M |
| STA-07 | Retention: abandoned workflows expire after a configurable period (default 30 days); conversation transcripts follow the retention policy in [DEC-8]. | S |
| STA-08 | Conversation history passed to the LLM is summarized or trimmed. The full transcript is kept only in the agent's database. | S |

### 5.13 AUD: Audit and observability

| ID | Requirement | Pri |
|---|---|---|
| AUD-01 | Log every backend call (endpoint, outcome, latency, correlation ID), every confirmation (payload hash, timestamp, employee) and every R1/R2 execution. | M |
| AUD-02 | Logs and traces shall not contain tokens, bill images or unnecessary personal data. Employee identifiers are pseudonymized where possible. | M |
| AUD-03 | Each conversation turn has a correlation ID that is passed to backend calls as a header, so agent actions can be traced in .NET logs. | S |
| AUD-04 | Record LLM usage per turn (model, tokens, latency) for cost monitoring. Prompts and outputs are logged only in non-production, or with redaction. | S |

### 5.14 SEC: Safety and guardrails

| ID | Requirement | Pri |
|---|---|---|
| SEC-01 | LLM provider keys exist only in the agent service's secret store. They are never in the React bundle or the repositories. | M |
| SEC-02 | Send the LLM only the data needed for the current step. No tokens, no full employee master data, and no documents beyond the one being processed. | M |
| SEC-03 | Prompt-injection resistance: the LLM's tool choices are limited to the allow-list for the current workflow state, and every tool argument is checked by Pydantic and by IDN-04 before execution. | M |
| SEC-04 | Rate-limit messages, uploads and write operations per employee. | S |
| SEC-05 | The agent never states that a claim *will* be approved or paid, or what the final payable amount will be, beyond what the backend has recorded. | M |

### 5.15 UI: Frontend integration

| ID | Requirement | Pri |
|---|---|---|
| UI-01 | The agent exposes a FastAPI interface for the React app (§8). Replies stream over SSE. | M |
| UI-02 | The chat panel may reuse the existing **generic UI components** (`ChatPanel`, `Composer`, `ChatParts`, `FormattedText`, `useVoiceInput`, `ChatTheme`) behind a new launcher. No chatbot orchestration code is reused. | S |
| UI-03 | Structured messages (summary card, choice lists, confirmation, finding list, OCR preview) are sent as typed JSON events that the UI renders. The confirm button posts back the confirmation ID and payload hash. | M |
| UI-04 | Mount points: a global launcher (app layout) and context-aware launchers on the claim pages, which pass the tour ID as a hint only (the agent re-checks ownership). | S |
| UI-05 | "Open in form": the agent can hand a saved draft to the existing claim form by draft ID, so the employee can finish in the standard UI. | M |

---

## 6. Key user journeys (acceptance scenarios)

**J1: Raise an outside-jurisdiction claim**
1. The employee says "I want to claim my Mumbai tour". (CONV-01/02)
2. RET-02 shows the tour as claimable; RET-03/04 fetch the prefill.
3. The employee uploads tickets and a hotel bill (DOC-01..05). The agent proposes rows and the employee confirms the fields.
4. ANA and DOC findings → ASK fills the gaps (e.g. the missing return leg, the hotel bill).
5. EXE-01 saves the draft (R1) → VAL (DEP-5, or the interim VAL-05).
6. SUM shows backend-sourced totals → CNF (R2, bound to the payload hash).
7. EXE-02 submits → EXE-13 reads back: "Status: Finance Processing (3), with ‹finance user›."

*Accept when:* no write happens before the confirmation, the submitted payload hash equals the confirmed hash, and the reported status matches the trajectory.

**J2: Within-jurisdiction daily claim.** Same as J1, using EXE-03/04, the `DailyTourDateValidation` data and the reporting manager as recipient.

**J3: Answer a clarification.** Session start → CONV-05 says "Finance asked a question on tour T". RET-07 shows the question. The employee answers in text (EXE-05), or changes rows and documents (ASK → SUM → CNF → EXE-06 with all rows and the recomputed total) → read back status 17.

**J4: Status enquiry.** "Where is my Kolkata claim?" → RET-05 → plain-language status, who holds it, since when, and what (if anything) the employee must do.

**J5: Resume after interruption.** The employee closes the browser after uploading bills. The next day: "continue my claim" → STA-04 lists open workflows → STA-05 reconciles with the backend → the workflow continues where it stopped.

**J6: Policy question.** "Can I claim a hotel in Pune for 3 nights?" → POL-03 entitlements (DEP-2) → an answer with citation, or POL-02 when no source exists.

---

## 7. Data and status handling

### 7.1 Claim-in-progress model (logical)
The Pydantic model mirrors the backend DTOs: tour reference; transport rows; accommodation rows; DA rows; leave rows; documents per row; totals; recipient. A separate mapping layer converts it to and from the backend field names, including the misspellings (`TransportaionDetailDtos`, `AccomodationDetailDto`, `RecipentId`, `TantetiveDateOfDeparture`, `TimeOfTravelDestiNation`). Money is handled as `Decimal` in the agent, even though the backend stores float.

### 7.2 Status vocabulary (from backend seed data)
The agent explains statuses using the backend's own table (`ClaimStatusCategory`), read at startup or kept in versioned configuration:

| Employee-relevant | Meaning for the employee |
|---|---|
| 1 | Waiting for the approver |
| 2 | Tour approved; a claim can be raised |
| 3, 8 | With finance or bill passing |
| 4, 25 | Rejected by finance / rejection in progress |
| 5 | Tour rejected |
| 7, 10 | Tour not availed / cancellation in progress |
| 9 | Payment transferred |
| 11, 23 | **Action needed:** clarification asked (23 = on a supplementary claim) |
| 12 | Process completed |
| 13, 16 | **Action needed:** sent back to the employee |
| 17, 24 | Clarification answered, waiting for finance |
| 20, 21, 22 | Supplementary raised / approved / rejected |

Status 18 is not defined in the backend. The agent shall not use it (see Q4 in the backend docs).

---

## 8. Agent service interface (functional view)

| Endpoint (indicative) | Purpose |
|---|---|
| `POST /v1/sessions` | Start or resume a session for the token's employee; returns open workflows and pending items |
| `POST /v1/workflows/{id}/messages` | Send a message (text plus attachment references); SSE stream of reply events |
| `POST /v1/workflows/{id}/attachments` | Upload bills at any time (forwarded to OCR); no claim in progress needed (BIL-01) |
| `POST /v1/workflows/{id}/bills/check` | Process finished bills: match each to a tour and save it into that tour's draft (BIL-10) |
| `POST /v1/workflows/{id}/bills/{documentId}/retry` / `.../cancel` | Read an unreadable bill again (BIL-15); remove a slow one (BIL-16) |
| `POST /v1/workflows/{id}/confirmations/{cid}` | Approve or reject a pending confirmation (payload hash required) |
| `GET /v1/workflows` / `DELETE /v1/workflows/{id}` | List or abandon workflows |
| `GET /v1/health` | Health check, including reachability of the backend and the LLM |

Exact schemas are defined in the design phase.

## 9. Backend endpoint allow-list (v1)

**Read (R0):**
- `TourListing/GetTourRequest`, `GetTourRaiseClaimList`, `GetTourTrajectory`, `GetTotalPendingTaskCount`
- `TourPlan/GetTourDetails`
- `Claim/RaiseClaimRequest` (GET), `ClaimsDetailsById`, `GetSupplementaryClaimDetails`
- all `Get*Draft` endpoints
- `ClaimClarification` GET and thread endpoints
- `ClaimEdit/can-edit`, edit history
- `Ocr/GetMyDocuments`, `GetDocument`, `GetFile`
- the Organisation-service profile and approver lookups

**Write:**
- **R1:** `SaveRaiseClaimDraft`, `SaveClaimWithRequestDraft`
- **OCR housekeeping (no claim impact):** `Ocr/Upload`, `MarkAsRead`, `Release`, `Cancel`, `Retry`
- **R2:** `SubmitRaiseClaimDraft`, `SubmitClaimWithRequestDraft`, `ClaimClarification/respond`, `respond-message`, `ClaimEdit/edit`, `RaiseSupplementaryClaim`, `Delete*Draft`

Everything else is denied by default.

---

## 10. Backend dependencies (new or changed .NET capabilities)

These follow from principle P1 and the Phase 1.2 findings. They are owned by the .NET team.

| ID | Capability | Why | Blocks | Pri |
|---|---|---|---|---|
| DEP-1 | **DA calculation endpoint.** Input: claim legs and dates. Output: DA rows (slab, hours, %, amount, deductions). The backend should also recompute or verify DA on save and submit. | DA is currently calculated only in the browser and stored unchecked | POL-04, SUM-02, EXE-01..04 (DA rows) | M |
| DEP-2 | **Entitlement lookup by employee and date:** grade on the date, DA rate, hotel limit by city class, own-arrangement amount, travel class | Limits exist only in frontend code and grade data | POL-03, ANA-03 | M |
| DEP-3 | **Grade-on-date (promotion-aware) resolution** inside DEP-1/DEP-2 | Promotion-date logic exists only in the frontend | POL-03 | M |
| DEP-4 | **Required-document policy per expense type** (and server-side enforcement) | `DocumentsAttached` flags are accepted but not stored or checked | DOC-06 | S |
| DEP-5 | **Validate-only (dry-run) endpoint** for claim payloads: overlap, duplicates, amounts, DA, entitlements, documents. No save. | The only way to validate today is to submit | VAL-01 | M |
| DEP-6 | **"My approver / finance recipient" lookup** for the signed-in employee, or the backend resolving `RecipentId` itself | Recipients are currently supplied by the client | EXE-02/04/14 | M |
| DEP-7 | **Employee pending-actions list** (not just a count) | Only `GetTotalPendingTaskCount` exists | RET-08, CONV-05 | S |
| DEP-8 | **Server-side ownership and role checks on the allow-listed endpoints,** and removal of `[AllowAnonymous]` from write endpoints | Today the agent is the only line of defence | IDN-04 (defence in depth) | M |
| DEP-9 | **HTTP status codes that match the envelope** (or a documented error contract) | Errors arrive as HTTP 200 | RET-11, VAL-02 | C |
| DEP-10 | **Fix known bugs** that affect agent output: the CGST/SGST swap on accommodation save; the merged approval PDF leaving out hotel bills; the supplementary approval lookup; the non-atomic `raise-claim-with-request` | Incorrect stored data would surface in agent summaries | SUM, EXE | S |

**Until DEP-1 exists, the agent shall not generate DA rows itself.** See DEC-1 for the interim option.

---

## 11. Reuse from the existing system (reference only)

| Item | Use in the agent project |
|---|---|
| Chat UI components (`claimChatbot/ui/*`) | May be reused in the new React launcher (UI-02) |
| OIDC token handling (`axiosInstance`, `getAccessToken`) | The pattern for sending the token to the agent |
| OCR client and backend OCR pipeline | Reused through the APIs (DOC-02..05) |
| `fileHash` | Pattern for DOC-07 |
| Chatbot orchestration, prompts, `claimAssistant`/`withinAssistant` logic, ARYAAN gateway client | **Not reused** |

---

## 12. Open decisions

| ID | Decision needed | Options / recommendation | Owner |
|---|---|---|---|
| DEC-1 | **Policy and calculation source until DEP-1/2/3 exist** | (a) Agent prepares everything except DA and limits, then hands off to the existing claim form, which computes them (EXE-10) — **recommended interim**; (b) wait for DEP-1/2/3 before go-live; (c) port the frontend rules to Python — **not recommended** (breaks P1). Also: approve an official policy document corpus for POL-01. | Business + architecture |
| DEC-2 | Token validation method for the agent: JWKS signature check (recommended) vs SSO introspection (as the .NET filter does) | | Security |
| DEC-3 | `DeviceType` value for agent calls (`web`, or a new `agent` value for traceability) | Recommend `agent` if the backend accepts it | .NET team |
| DEC-4 | Source of truth for read-only / impersonation (`IsReadOnly` is never set by SSO today) | | SSO team |
| DEC-5 | Long workflows across token expiry: the UI refreshes and re-presents the token (recommended) vs any form of delegated or offline access | | Security |
| DEC-6 | Definition of **sponsored tours** and their eligibility rules | No implementation found in any repository | Business |
| DEC-7 | Whether v1 includes creating outside-jurisdiction tour requests and cancelling tours (`TourNotAvailed`) | Recommend v1.1 | Product |
| DEC-8 | Retention period for conversation transcripts and uploaded files held by the agent | | Compliance |
| DEC-9 | Languages: English only, or English + Hindi (the existing voice input supports hi-IN) | | Product |
| DEC-10 | LLM provider and hosting (data residency for employee data and bills) | | Architecture + compliance |
| DEC-11 | Which claim-draft family is canonical where several exist | Needs confirmation from the .NET team | .NET team |

---

## 13. Traceability

| Goal | Requirements | Fully supported by existing APIs? |
|---|---|---|
| G1 Understand needs | CONV-01..07, RET-01..05 | Yes |
| G2 Retrieve via APIs | IDN-01..08, RET-01..11 | Yes; ownership enforced by the agent until DEP-8 |
| G3 Analyze | ANA-01..06 | Yes (advisory) |
| G4 Missing documents | DOC-01..08 | Partly; no document policy (DEP-4) |
| G5 Ask employee | ASK-01..05 | Yes |
| G6 Policies | POL-01..06 | **No.** Needs DEP-1/2/3 and DEC-1/6 |
| G7 Validate with backend | VAL-01..05 | **Partly.** Only on submit until DEP-5 |
| G8 Summaries | SUM-01..04 | Partly; DA and limits need DEP-1/2 |
| G9 Confirmation | CNF-01..06 | Yes (agent-side) |
| G10 Execute | EXE-01..15 | Yes for drafts, submit and clarification; recipient lookup needs DEP-6 |
| G11 Persist and resume | STA-01..08 | Yes (agent-side) |

### 13.1 Bills at any time and automatic drafts (`bill_upload_auto_draft_requirements.md`, release 0.3)

| Requirements | Goal | Where | Tests |
|---|---|---|---|
| BIL-01..07 Uploading | G4 | `service.add_attachments`, `state/uploads.py`, `agent/bill_flow.register_bills` | `test_uploads.py` |
| BIL-10..18 Reading and claiming results | G4 | `drafting/bill_pipeline.py` (`check`), `POST bills/check`, `retry`/`cancel` | `test_bill_check.py`, scenarios 7, 8, 10, 14 |
| BIL-20..24 Results to rows, stay and air rules | G3, G4 | `bill_pipeline.stay_problem`, `is_air`, `drafting/ocr_mapping.py` | scenarios 12, 13 |
| BIL-30..37 Which tour | G3 | `drafting/tour_match.py`, `bill_pipeline._place` | `test_tour_match.py`, scenarios 1, 2, 5, 6 |
| BIL-40..53 Saving into the tour's draft | G10 | `bill_pipeline._save_into`/`write_draft`, `drafting/saved_draft.py`, `drafting/payload.py`, `drafting/merge.py` | `test_saved_draft.py`, scenarios 3, 4, 7, 9 |
| BIL-60..65 Telling the employee | G8 | `agent/bill_flow.report`, `bill_report` event, `POST /v1/sessions` `bills` | `test_bill_check.py`, scenario 10 |
| BIL-70..74 Conversation | G1 | `claim_flow.start_claim`, `bill_flow.bill_status_node`/`assign_bills`, `llm/rules.py` | `test_conversation_bills.py`, scenario 5 |
| NFR-1..4 | | per-employee lock in `service.py`; allow-list unchanged | scenario 14 |

DOC-04 is amended by §1.3 of that document (see §5.5).
