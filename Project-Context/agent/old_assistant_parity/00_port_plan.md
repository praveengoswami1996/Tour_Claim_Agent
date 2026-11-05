# Porting the old Claim Assistant into the agent: plan and decisions

**Goal:** the agent behaves exactly like the old in-app Claim Assistant (Tour_React `src/lib/claimChatbot/`,
`src/components/claimChatbot/`, `src/lib/ocr/`), for both the Raise Claim (outside jurisdiction) and the Within
Jurisdiction assistants. The specifications in this folder describe the old behaviour:

| File | Area |
|---|---|
| `A_raise_claim_chat_flow.md` | Raise Claim chat: steps, greeting, bills, actions, questions |
| `B_claim_domain_rules.md` | Claim rules: state, merging, missing fields, edits, review, saved drafts, texts |
| `C_ai_prompts_and_actions.md`, `C_prompts.json`, `C_eval_cases.json` | AI layer: gateway, understand, narrate, actions, 749 eval cases |
| `D_within_jurisdiction.md` | Within Jurisdiction assistant and its form bridge |
| `E_ocr_form_integration.md` | OCR, claim form, payloads, backend calls |

## Decisions (owner, 2026-11-05)

1. **Scope:** everything: the Raise Claim assistant (list page and inside the claim form) and the Within Jurisdiction
   assistant, including submitting with the declarations.
2. **Replies:** the AI model writes every reply, as before (ARYAAN gateway `chat/stream`, the original prompts). If
   the gateway fails, the original fixed text is shown ("The assistant is unavailable right now. Please try again.").
3. **Form work goes through a browser bridge.** Each turn, React sends the open form's state with the message; the
   agent decides exactly as the old assistant did and answers with bridge commands (write rows, run checks, save,
   submit) that React carries out with the existing form code. DA and totals stay calculated by the form.
4. **Old bugs are copied, except data loss.** Where the old code loses or corrupts saved data, the port keeps the
   data instead: saved draft rows and files are kept exactly, and the retained file ids are sent. Every deviation is
   listed in `01_deviations.md`.
5. **Old upload tags and tour list:** references `outside-chat:unassigned` / `claim-chat:tour-<id>` / `within-plan`,
   and tours from `TourListing/GetTourRequest/{empId}?toureType=1` filtered to status 2, or 9 with an advance taken.

## Code layout

A new package `tourclaim_agent/claimchat/` mirrors the old files one to one, so each Python module can be checked
against its TypeScript source. Claim state is kept as plain dictionaries with the **same field names as the old
TypeScript objects** (`transportationRows`, `DateOfTravelSource`, `uploadedFiles`, ...), because the bridge passes
exactly these objects between React and the agent. Function names are the TypeScript names in snake_case.

| TypeScript | Python |
|---|---|
| `lib/ocr/ocrResults.ts` | `claimchat/ocr_results.py` |
| `lib/claimChatbot/ocrMapping.ts` | `claimchat/ocr_mapping.py` |
| `lib/claimChatbot/claimAssistant.ts` + `rowOrder.ts` | `claimchat/claim_assistant.py`, `claimchat/row_order.py` |
| `tourMatcher.ts`, `claimProgress.ts`, `billSummary.ts`, `billOutcome.ts`, `savedClaim.ts` | same names in snake_case |
| `tourReview.ts`, `lib/placeCheck.ts` | `claimchat/tour_review.py`, `claimchat/place_check.py` |
| `lib/claimPayloadBuilder.ts` | `claimchat/claim_payload.py` |
| `withinTourPlan.ts`, `withinAssistant.ts` | `claimchat/within_tour_plan.py`, `claimchat/within_assistant.py` |
| `llmClient.ts`, `aiAssistant.ts`, `assistantActions.ts`, `useNarrator.ts` | `claimchat/ai/...` |
| `ClaimChatbot.tsx`, `WithinClaimChatbot.tsx` | `claimchat/flows/raise_claim.py`, `claimchat/flows/within.py` |

Tests are ported from `Tour_React/tests/claimChatbot/*.test.ts` into `tests/claimchat/`.

## Phases

1. Foundations: OCR results and mapping, claim state, merging and row order, tour matching, progress, bill
   summary and outcomes, saved drafts, review, payload; Within plan mapping and rules.
2. AI layer: gateway client, understand, narrate, action catalogue, prompts, fallback, eval runner.
3. Raise Claim list-page flow (greeting, bills, auto-save, reports, typed claim path, review, handoff).
4. Browser bridge protocol; in-form Raise Claim assistant.
5. Within Jurisdiction assistant through the bridge, including submit.
6. Replace the 0.3 conversation layer, update the frontend requirements, README and FRS.
