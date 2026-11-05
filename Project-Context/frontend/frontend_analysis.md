# Phase 1.1: Frontend Analysis (Tour_React)

| | |
|---|---|
| Repository | `Tour_React/` (Azure DevOps `DFCCIL/Tour_React`), branch `ocrBackgroundProcessing` at the time of analysis |
| Date | 2026-11-05 |
| Method | Read-only code inspection. No files were modified, built or installed. |
| Related | `Project-Context/backend/*` (Phase 1.2), `Project-Context/agent/functional_requirements.md` |

**Summary.** Tour_React is a large, working React 18 + TypeScript + Vite app: about 108,000 lines in about 300 source files. It serves employees, approvers, bill passing / finance and admins for DFCCIL tour programmes and travel claims.

Business rules live inside very large page components. Several calculations that look like backend rules (DA, hotel limits, GST caps, back-dating window) exist **only** here.

The app also contains an existing AI chatbot ("Claim Assistant"). It was studied for reference only. Its UI components, token handling and OCR client are reusable; its orchestration, prompts and business logic are not.

No secret values or personal data are reproduced in this document. Configuration is listed by key name only.

---

## 1. Technology stack

| Area | What's used | Notes |
|---|---|---|
| Framework | React 18.3, TypeScript 5.6, Vite 6 | Dev server https://localhost:3001 (local certificate tool mkcert); alias `@` → `src` |
| UI | Tailwind 3, shadcn/ui (Radix), lucide icons, framer-motion | 37 components in `src/components/ui` |
| State | Redux Toolkit (`createAsyncThunk`), redux-persist (sessionStorage) | About 30 feature folders in `src/features` |
| Routing | react-router v7 | No lazy loading of pages |
| Auth | `oidc-client-ts` + `react-oidc-context` | DFCCIL SSO, code flow |
| HTTP | axios: 4 instances (Tour API, DMS, Upastithi attendance, Organisation) | Bearer token + `DeviceType: web` |
| Tables | TanStack Table v8 | `ui/data-table2.tsx` (main), `ui/data-table.tsx` (older, admin) |
| Toasts | react-hot-toast (48 files) | react-toastify and sonner in one file each |
| Export | xlsx + jspdf (`BillPassingHistory` only), `@react-pdf/renderer` (demo page) | |
| Charts | recharts (`DashBoardTour` only) | |
| Hosting | IIS (`public/web.config`: SPA rewrite, `/chat-api` proxy, gzip) | No CI pipeline file in the repo; PR template in `.azuredevops/` |

**Installed but unused:** TanStack Query, zod, react-hook-form (only the shadcn `form.tsx` wrapper), chart.js / react-chartjs-2, html2canvas, react-big-calendar, input-otp.

## 2. Code size and structure

| Folder | Files | Lines |
|---|---|---|
| `src/pages` | 71 | 33,900 |
| `src/components` | 116 | 28,000 |
| `src/lib` | 33 | 30,200 (23,100 of it is the unused `test.ts`) |
| `src/features` (Redux) | 47 | 7,100 |
| `src/constant` | 5 | 7,300 (mostly city lists) |
| `src/auth`, `services`, `hooks`, `app` | 21 | 1,900 |

The largest page files:

| File | Lines |
|---|---|
| `pages/tour/ReuasbleTourRequest.tsx` | 2,977 |
| `pages/RaiseClaim/sections/AccommodationSection.tsx` | 2,965 |
| `pages/withinJurisdiction/WithinJurisdictionFormPage.tsx` | 2,699 |
| `pages/RaiseClaim/ApproverPage.tsx` | 2,627 |
| `components/claimChatbot/ClaimChatbot.tsx` | 2,573 |
| `pages/tour/TourRequest.tsx` | 2,519 |

Outside and within claims, and the two tour request pages, are near-duplicates of each other.

## 3. Bootstrapping and routing

- `main.tsx`: BrowserRouter → Redux Provider → PersistGate → `App`.
- `App.tsx`: if the URL carries `?access_token=…`, `bootstrapFromAccessToken` saves it as the OIDC user and strips it from the URL. The JWT is only decoded; its signature is never checked. Errors go to the Organisation API (`Logger/SaveLog`).
- `routes/AppRoutes.tsx`:
  - **Public:** `/unauthorized`, `/logout-notification`.
  - **Main layout, roles `user` and `HR Admin`:** all employee, approver and finance pages.
  - **Admin layout, roles `admin`, `superAdmin`, `HR Admin` and `user`:** `/manage-admin`, `/admin-dashboard`, `/admin-entitlement`, `/approve-claim-history`, `/tourExtension`.
- **Roles barely restrict anything.** `userSlice` always adds `user`, so every route is reachable by every signed-in user. The sidebar hides items by role (`Finance Tour`, `Bill Passing Tour`, `Super Bill Passing Manager`); these role names are missing from the `UserRole` type.
- `PrivateRoute.tsx`: redirects to sign-in, then loads the profile, admin data, token data, entitlements, employees and approver details.

## 4. Pages by user role

### 4.1 Employee

| Route | File (lines) | Purpose and tabs | Writes |
|---|---|---|---|
| `/` | `home/Home.tsx` | Redirects to `/tourRequest` | – |
| `/tourRequest?mode=normal\|exfacto` | `tour/TourRequest.tsx` (2,519) + `ReuasbleTourRequest.tsx` (2,977) | Tour request. Tabs: new request, drafts, tour approval, approved, rejected, seek clarification | Save/submit tour drafts, `createTourRequest`, `outsideReply`, delete draft |
| `/raise-claim?mode=auto` | `RaiseClaim/RaiseClaim.tsx` (938) | Outside-jurisdiction claim list. Tabs: raise claim (status 2, or 9 with advance), clarification (11), pending finance (3/8/17/25), availed (12), not availed (7/10). Rows expand into `ApproverPage` | Save/delete draft, `tourNotAvailed` |
| `/raise-claim-approver` | `RaiseClaim/ApproverPage.tsx` (2,627) | Outside-jurisdiction claim form. Sections: Transportation, Transportation Amount, Accommodation, Daily Allowance, Leave, Documents & Summary, Declaration. Also used for clarification replies | Draft autosave (`useClaimDraft`), `submitFinalDraftRaiseClaim`, `amendTourClaim` (status 15), `respondClarification` |
| `/within-juridiction` | `withinJurisdiction/WithinJurisdictionPage.tsx` (264) | Within-jurisdiction list. Tabs: draft, claim raised, under processing, clarification, approved, rejected | Delete draft; clarification responses |
| `/within-juridiction/new`, `/edit/:draftId` | `WithinJurisdictionFormPage.tsx` (2,699) | Tour and claim submitted together. Multiple tours (one per date) with legs; monthly tour-plan import by OCR; self-approval when the employee is in the cadre allotment | `saveClaimDraft`, `submitClaimDraft`, `submitClaimEdit` |

### 4.2 Approver

| Route | File (lines) | Purpose |
|---|---|---|
| `/approveTourProgram` | `tour/ApproveTourProgram.tsx` (844) + `TentativeTourPlan.tsx` (1,333) | Inbox for outside and within tours, pending, history; approve, reject or send back (`updateTourRequestStatus`); advance approval |
| `/within-approver` | `withINApprover/WithInApprover.tsx` (350) + `withinApprover/RowDetailsComponent.tsx` (1,066) | Within-claim approval: pending, clarification, history, excessive tours; attendance check-ins from Upastithi |
| `/transfer-pending-actions` | `TransferPendingActions.tsx` (424) | CGM, Cadre GM or Director moves pending tasks to another approver |

### 4.3 Bill passing / finance

| Route | File (lines) | Purpose |
|---|---|---|
| `/bill-passing` | `BillPassing/BIllPassingWithin.tsx` (1,136) + `components/Billpass/*` | Outside, within, and requests for rejection (Super Bill Passing only). Approve amount, reject, ask for clarification |
| `/bill-passing-history` | `BillPassingHistory.tsx` (504) | History with Excel and PDF export |
| `/approve-claim-advance` | `Advance/AdvanceListing.tsx` | Pass advances |
| `/tourHistory`, `/tourApproveAdvance`, `/tour-dashboard` | | Approved claim history; older advance list; half-built dashboard (not in the sidebar) |

### 4.4 Admin
`/manage-admin` (admin users), `/admin-entitlement` (entitlements), `/approve-claim-history`, `/tourExtension` (sets the extension date per employee). Stub pages: `/dashboard`, `/admin-dashboard`, `/pdf`.

## 5. Business workflow as seen in the UI

1. **Tour request.** The employee submits a request (outside jurisdiction) to the approver.
2. **Approver decision.** Approve (2), reject (5), send back (13), with an optional advance. Amendments use re-approval (14) and pre-approval (15/16).
3. **Raise claim.** On an approved tour, the employee fills the claim form; the draft autosaves and is then submitted.
4. **Bill passing / finance.** Approve the amount, reject (4), or ask for clarification (11). The employee responds (17).
5. **Payment / completion.** 9 or 12; supplementary claims use 20 and 21.

**Other flows:**
- **Within jurisdiction:** the tour and claim are created together. The employee self-approves when in the cadre allotment; otherwise an approving authority is selected.
- **Tour not availed** (7/10), **tour extension**, **excessive tours** (CGM).

Status IDs and labels are copied into several files (`components/Trajectory/EnhanceTrajectory.tsx` is the fullest list). The frontend shows a status 18 ("Pending with unit Finance") that the backend does not define; see `backend/claim_lifecycle.md`.

## 6. Authentication and session

- **OIDC:** `auth/config.ts`.
  - authority = `environment.authUrl` (DFCCIL SSO; a separate host for UAT)
  - code flow, scope `openid profile api1`
  - automatic silent renew and session monitoring
  - front-channel logout at `/logout-notification`
- **Token storage:** sessionStorage key `oidc.user:{authority}:{client_id}`, read by `getAccessToken()` in `services/axiosInstance.ts`.
- **Employee identity:** from the Organisation API `Organization/GetEmployeDetailsWithEmpCode/{UserId}`: empCode, empId, grade, unit, department, reporting officer, and a comma-separated role string.
- **Read-only (impersonation):** if the decoded JWT has `IsReadOnly = 'True'`, axios blocks POST/PUT/PATCH/DELETE. This is enforced **only in the browser**.
- **Session:**
  - silent sign-in check every 20 minutes (`useSessionChecker`)
  - logout synced across tabs (BroadcastChannel + localStorage)
  - delegate / switch-user support (`services/ssoService.ts`)
  - **no token refresh on 401:** any 401 clears storage and redirects to the exit URL (except requests flagged `skipAuthRedirect`)

## 7. API integration

- **Axios instances:** `axiosInstance` (Tour API), `axiosDmsInstance`, `axiosUpastithiInstance`, `axiosOrganisationInstance`. Base URLs come from `src/config.ts`, selected by `VITE_ENV`.
- **No central endpoint list:** `constant/apiRoutes.ts` is empty, and paths are written directly into the Redux slices.
- **Response envelope:** `{statusCode, message, data}`.
- **Claim payloads** (`lib/claimPayloadBuilder.ts`, `lib/withinClarificationDtosBuilder.ts`):
  - multipart forms with JSON-string fields, using the backend's spellings: `TransportaionDetailDtos`, `AccomodationDetailDto`, `DailyAllowanceDto`, `LeaveAvailedDto`, `RecipentId`, `TantetiveDateOfDeparture`, `TimeOfTravelDestiNation`
  - files are linked to rows by `StartFileIndexCount` / `EndFileIndexCount`
  - file parts: `transportationFiles`, `accommodationFiles`, `additionalFiles`, `advancePaymentFiles`, `nominationFiles`, `daFiles`, `NominationFiles`
  - dates as `YYYY-MM-DD`, times as `HH:mm`
- **Draft ↔ form mapping:** `lib/transformDraftDataToFrontendState.ts`.
- **Known bugs:**
  - `features/tour/TourRequestStatusSlice.ts` builds `/api/api/...` URLs.
  - 18 imports use `@/features/raiseClaim/...` while the folder is `RaiseClaim`. This works on Windows and **breaks on a case-sensitive (Linux) build**.

The full endpoint catalogue is in `backend/controllers_and_endpoints.md`. Frontend paths that are not implemented in the Tour API are also listed there.

## 8. Forms, validation and business rules

- All forms are hand-written React state; react-hook-form and zod are not used in pages.
- **Client-side validation:**
  - outside tours: `collectSubmitProblems` in `ApproverPage.tsx` (required fields, claimed ≤ billed, entitlement caps, city class, invoice number, bill-passing manager present)
  - within jurisdiction: `validateTour` + `ValidationErrorModal`
  - OCR heuristics: `lib/placeCheck.ts` flags OCR junk in From/To
- **Rules that exist only in the frontend** (see `backend/da_calculation_rules.md` and `backend/expense_eligibility_rules.md`):
  - DA hours slabs: up to 6h = 30%, up to 12h = 70%, over 12h = 100%
  - daily split, DA rate from grade data, 25% DA with free lodging, leave deduction
  - promotion-date grade handling
  - hotel limit by city class × nights; own arrangement = 25% of the limit
  - GST caps (IGST 18%, CGST/SGST 9%)
  - 3-month back-dating window plus tour extension
- **Autosave:** `app/hooks/useClaimDraft.ts` (debounced) is used by `ApproverPage` only.

## 9. Documents and uploads

| Item | Detail |
|---|---|
| Accepted types | Bills: pdf, jpg, jpeg, png. Nomination and tour-request attachments also accept doc and docx |
| Size limits | 1 MB (within bills, nominations), 10 MB (outside transport), 5 MB / 1 MB (tour request) — enforced in the browser only |
| Upload path | Inside the claim, draft or clarification multipart requests; `/Ocr/Upload` only for reading bills |
| Preview | `environment.imageBaseUrl + tempFilePath` (a static path that doesn't require sign-in); `useFilePreviewHook` falls back to a placeholder `/api/files/{id}/download` |

## 10. Cross-cutting implementation patterns

| Concern | Pattern |
|---|---|
| Data fetching | Redux thunks + `.unwrap()`; per-slice `loading` flags; direct axios calls in components are rare |
| Errors | `App.tsx` error boundary sends crashes to the Organisation API (`Logger/SaveLog`); `ErrorFallbackUI`; mostly `console.error` + toast elsewhere |
| Logging | `lib/logger.ts` (loglevel) with dev mode hard-coded on; 141 `console.*` calls |
| Responsiveness | Tailwind `md:`/`lg:` classes; shadcn off-canvas sidebar on mobile |
| Accessibility | Very few `aria-*` attributes outside the Radix primitives |
| i18n | None (English only) |
| State persistence | redux-persist to sessionStorage, whitelist `employee, user, applications, allRole, admin, adminUserData, level` |

## 11. Existing AI chatbot ("Claim Assistant"): reference study

### 11.1 What it is (not to be reused)
- **Two orchestrator components:** `components/claimChatbot/ClaimChatbot.tsx` (2,573 lines, outside jurisdiction) and `WithinClaimChatbot.tsx` (850 lines). Each is a large React state machine tied to Redux and the claim forms.
- **LLM use:** `lib/claimChatbot/aiAssistant.ts`. `understand()` asks the LLM to pick one of the actions the chat offers at that step, plus form edits; `narrate()` writes the replies.
- **Prompts and business logic:** prompts live in `aiAssistant.ts` and `assistantActions.ts`. The business logic is in `claimAssistant.ts`, `withinAssistant.ts`, `tourMatcher.ts`, `tourReview.ts`, `claimProgress.ts`, `savedClaim.ts`, `billSummary.ts`, `billOutcome.ts` and `rowOrder.ts`.
- **LLM gateway:**
  - DFCCIL's ARYAAN gateway (Gemma), called from the **browser**.
  - The path is `/chat-api/chat/stream`, proxied by Vite in dev and IIS in UAT/PROD, because the gateway sends no CORS headers.
  - Replies stream as plain text chunks; the conversation ID comes back in `X-Conversation-Id`.
- **State:** browser memory and localStorage only. Nothing is persisted server-side, so conversations can't be resumed.
- **Draft handoff:** `draftHandoff.ts` passes one draft, in memory, from chat to the claim form.
- **Mounted at:**
  - `RaiseClaim.tsx` (around line 877) and `ApproverPage.tsx` (around line 2611)
  - `WithinJurisdictionFormPage.tsx` (around line 2694)
  - `OcrReadyNotifier` app-wide in `components/layout/app-layout.tsx`
- **Tests:** `tests/claimChatbot/*` (21 unit-test files) and `scripts/ai-eval/*` (prompt evaluations against the live gateway). The evaluation style is worth copying; the cases themselves are specific to the old prompts.

### 11.2 Reuse assessment

| Piece | Verdict | Why |
|---|---|---|
| `ui/ChatPanel`, `Composer` (attach, camera, mic), `ChatParts`, `FormattedText`, `useVoiceInput` (en-IN, hi-IN), `ChatTheme` + css | Reuse as-is | No dependency on any backend or on claim logic |
| `ui/Brand`, launchers | Adapt | Branding; claim-specific props |
| `axiosInstance` / `getAccessToken` | Reuse as-is | Pattern for sending the OIDC token to the agent |
| `lib/ocr/*` (`ocrApi`, `ocrJobs` poller, `ocrClaims` exactly-once apply, `ocrReferences`, `ocrResults`), `OcrReadyNotifier` | Reuse or adapt | Solid client for `/api/Ocr/*`; add an agent reference tag |
| `fileHash.ts`, `intents.ts` (`cleanMessage`, `checkFiles`) | Reuse as-is | Generic checks on text and files |
| `llmClient.ts` | Adapt (pattern only) | Stream reader and abort handling; point it at FastAPI and drop the browser key |
| `ocrMapping.ts`, `draftHandoff.ts` | Adapt | Tied to the current form's data types |
| `ClaimChatbot`, `WithinClaimChatbot`, `useNarrator`, `aiAssistant`, `assistantActions`, `claimAssistant`, `withinAssistant`, and the other `lib/claimChatbot/*` logic | **Do not reuse** | Orchestration, prompts and business logic |

## 12. Configuration (key names only)

- `src/config.ts`: UAT and PROD settings selected by `VITE_ENV`. Keys: `apiUrl`, `dmsApiUrl`, `orgHierarchy`, `upastithiApiUrl`, `logoutUrl`, `exitUrl`, `authUrl`, `clientId`, `postLogout`, `redirectPath`, `applicationId`, `imageBaseUrl`, `claimAssistantApi` (`VITE_CHAT_API_BASE`, `VITE_CHAT_API_KEY`).
- `vite.config.ts`: port 3001 with HTTPS (mkcert); a `/chat-api` proxy to `LLM_GATEWAY_URL` with a server-side `LLM_GATEWAY_KEY`.
- **Env files committed to Git:**
  - `src/.env`: `MODE`, `LLM_GATEWAY_URL`, `LLM_GATEWAY_KEY`, `VITE_OCR_API_KEY`
  - `.env.local`: `VITE_CHAT_API_KEY`, `VITE_OCR_API_KEY`

## 13. Code quality

| Signal | Finding |
|---|---|
| TypeScript | `strict: false`; about 950 uses of `any` |
| Lint | Basic recommended rules; `no-explicit-any` is only a warning |
| Tests | None outside the chatbot; no `test` script in `package.json` |
| Bundle | One 5.5 MB JS file; no code splitting |
| Dead code | About 15 files never routed or imported (e.g. `BillPassing/ApproverPageReusable.tsx`, `admin/AdminManagement.tsx`, `tour/Train.tsx`, `AutoSaveIndicator.tsx`, `DraftBanner.tsx`, `constant/mockLlmData.ts`, `src/test.tsx`) |
| Fragile names | Trailing space in `Advance/ApprovalDetailsCompact .tsx`; folder casing `raiseClaim`/`RaiseClaim`; typos `BIllPassingWithin`, `ReuasbleTourRequest`, `withINApprover`, `juridiction` |
| Activity | 942 commits; 102 since August 2026; 8+ contributors |

## 14. Security findings

1. **`src/.env` is committed and contains `LLM_GATEWAY_KEY`.** `.gitignore` only covers `/.env` at the repo root. The key should be rotated and removed from the repo history.
2. **`.env.local` is committed.** Its `VITE_CHAT_API_KEY` is built into the browser bundle, so anyone can extract it.
3. **`src/lib/test.ts` is committed but not used.** It holds about 23,000 lines of employee data, with roughly 2,000 lines mentioning mobile numbers or emails. It should be removed and the repo history purged.
4. **Browser-only protections:** read-only enforcement, page access by role, an unverified token taken from the URL, and employee/recipient IDs sent in URLs. The backend analysis confirms the server does not enforce these either.
5. **Token storage:** the OIDC token sits in sessionStorage, so it is exposed to any XSS.
6. **File previews** use a static path that doesn't require sign-in (`imageBaseUrl`).

## 15. Implications for the AI agent

- **Where to mount the agent panel:**

  | Place | Context available |
  |---|---|
  | Global launcher in `components/layout/app-layout.tsx` (next to `OcrReadyNotifier`) | User, roles, pending counts, current route |
  | `RaiseClaim/ApproverPage.tsx` | tourId, statusId, empId, all claim rows, the draft |
  | `WithinJurisdictionFormPage.tsx` | Mode, draft or plan ID, form state |
  | Approver and finance expanded rows | For a later "summarise this claim" feature |

- **Don't copy frontend rules into the agent.** They are duplicated and incomplete. The agent should rely on .NET endpoints (`agent/functional_requirements.md` §10).
- **Draft handoff to the form is a viable stopgap:** the agent prepares a backend draft and the existing form completes the DA calculation (DEC-1).
- **Agent calls must match the payload format exactly:** the misspelled field names, multipart with JSON strings, and file-index ranges.
- **Plan for expired tokens:** there is no token refresh, so long agent workflows must handle expiry.
- **LLM keys must live only in the FastAPI service.**
