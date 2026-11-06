# Deliberate deviations from the old Claim Assistant

The port copies the old behaviour, including its quirks, except where the old code loses or corrupts saved data
(decision 4 in `00_port_plan.md`). Each deviation is listed here with the reason.

| # | Old behaviour | Port | Why |
|---|---|---|---|
| 1 | Re-saving a draft rebuilt saved stays from parsed fields: own-arrangement / free stays became hotel stays (`claimStateFromDraft` bug) and saved stays lost their `InvoiceDate` | Saved rows are written back exactly as read | Data corruption |
| 2 | `RetainedTransportationFileIds` / `RetainedAccomodationFileIds` were never sent from the assistant (`hasResumedDraft` not passed) | Every saved file id still on its row is sent as retained | Saved files could be lost |

Further entries are added as the port finds them.

## Running on the server (runtime, `claimchat/runtime/`)

What the browser did that the server cannot do the same way (not deliberate changes of behaviour):

| # | Old (browser) | Server runtime |
|---|---|---|
| R1 | Closing the window aborted a reply on its way (kept silently, written on reopen) | A reply that started is written to the end within the turn; `active: false` takes effect with the next request (replies are then held, `status.held`) |
| R2 | A reply that failed after part of it was shown had its bubble removed | Event `{type: "reply_remove", id}` (not in the 0.4 contract yet; the frontend should drop that bubble) |
| R3 | Re-greeting cleared the chat (`setMessages([])`) | Event `{type: "messages_cleared"}` (not in the 0.4 contract yet) |
| R4 | A chip that sends words (`send('yes')`) drew a user bubble | `Turn.user_message(text, show=True)` sends `{type: "user_message", text}` (not in the 0.4 contract yet) |
| R5 | Browser leases: heartbeat, `pagehide` keepalive Release, `recoverOcrClaims` of tabs silent > 60 s | Taken results are recorded in the session; given back on Start over (DELETE), and when a chat is not touched for 15 min (`TCA_CHAT_SESSION_IDLE_MINUTES`) on the employee's next request (the server has no token otherwise). An idle chat is not closed (the old chat lived as long as its tab): it carries on with its next request, and a given-back result it still needs is taken again (MarkAsRead) on the normal path |
| R6 | `localFiles` (bytes of files uploaded in this page load) were per page load and survived Start over | Kept per chat: after Start over a read is reported as "uploaded earlier" |
| R7 | Page toasts ("Failed to load the tour…", "Failed to save draft", "Failed to retrieve drafts") | Not shown; the outcomes ('failed', null) are the same |
| R8 | The greeting used the SSO profile's `unique_name` | The token's `unique_name`, else the employee's name from the Organisation service |
| R9 | A file the browser had no type for was checked with type `''` | The multipart type `application/octet-stream` is read as `''` |
| R10 | `onOpenClaim` opened the form with `startClaim` | Bridge `openForm {tourId}` without `state` (the 0.4 contract has no separate method) |

### Within Jurisdiction chat (`claimchat/flows/within.py`, WithinClaimChatbot.tsx)

| # | Old (browser) | Server |
|---|---|---|
| W1 | `form.write`, `form.check()`, `form.info()` were synchronous and could not fail | They are bridge calls. A failed or unanswered `write` puts the snapshot and the undo copy back and raises: inside a typed turn that is the old catch-all (system notice + "Try again"); from a chip the runtime's `error` event; inside `applyPlan` the old "I couldn't make tours from that plan…" note. A failed `check` / `info` raises the same way |
| W2 | `readPlan` checked `navigator.onLine` first ("You seem to be offline…", :285-289) | No server equivalent: the check is left out (an unreachable Tour API gives the upload's own "not reachable" message, as before) |
| W3 | `doSubmit` said "I started the submission…" only while the chat was still mounted (`aliveRef`, :517; a successful submit navigates away) | Always said; the `submit` answer's `{submitted, message}` is not read (the old code ignored the outcome too). If the page navigates away the reply is simply never shown |
| W4 | A plan taken and waiting for "replace the tours?" stayed taken until the chat unmounted | Same, but an idle chat gives it back (R5). If the employee then says yes, `applyPlan` takes it again (MarkAsRead) before settling, so it does not come back; if they typed something else, the given-back plan is gone through again (and the question asked again) on the next turn |
| W5 | `form.read()` after `write` returned the written tours at once (ref) | The snapshot is set to the written tours before the call; the browser's answer (`form`) then replaces it, so later reads see the form's own state (e.g. re-sorted ended tours) as the old ref did after the form's effects ran |
| W6 | The closed launcher's resting bubble was a constant in the launcher (WithinClaimAssistantLauncher.tsx:11) | Sent as `status.greeting` (not in the 0.4 contract; the frontend may keep its own copy) |
| W7 | The composer highlighted 📎 when there were no tours and nothing was typed (:843) | `composer.highlightUpload` (not in the 0.4 contract) |
| W8 | The plan file was a `File` held in memory (`planFile`, the yes/no closure) | Its bytes are kept in the chat's file store (`plan-<n>`) until the upload succeeds (or the chat is closed) |

## How the port carries out 1 and 2 (`claimchat/claim_payload.py`)

**1. Saved rows.** A row read from a saved draft carries the saved row it came from under the key `_saved`:

```
row["_saved"] = {
    "dto": {...},  # the item of the draft's TransportaionDetailDtos / AccomodationDetailDto / LeaveAvailedDto, as read
    "row": {...},  # optional: a copy of the chat row exactly as claimStateFromDraft parsed it from that item
}
```

`build_claim_payload` writes such a row back as `dto` unchanged, except that `Id`, `StartFileIndexCount` and
`EndFileIndexCount` are this save's (the row's place and its newly attached files), and, when `row` is given, every
field the chat changed is applied on top: the TypeScript DTO is built from `row` and from the current row, and each
field that differs takes the new value (or is removed when the new DTO has no such field). Keys match without regard
to case and keep the saved spelling. Rows without `_saved` (new bills) are built exactly as the TypeScript built them,
and `_saved` is never sent. Journeys, stays and leave rows are all handled this way.

`claimchat/saved_claim.py` (`claim_state_from_draft`) is what sets `_saved`: every journey, stay and leave row it
builds from an item of the saved draft gets `{"dto": <deep copy of the item as read>, "row": <deep copy of the row as
just parsed>}`. The parsing itself is the old one, quirks included (a stay's `selectedType` is read but its
`ownArrangement` / `freeProvidedByCompany` / `companyPaid` flags stay "No", which the payload builder turns into a hotel
stay; a stay's `InvoiceDate` is not read); the payload's `_saved` handling is what keeps that data. Nothing else reads `_saved`: the review, comparisons, summaries and texts are unchanged (the
parity check `tools/parity_diff/run1c_py.py` strips it, compares with Node, and checks every `_saved` separately).

**2. Retained file ids.** `assistant_draft_save` passes `hasResumedDraft` = saving into an existing draft (overwrite
agreed and a draft id known), so `RetainedTransportationFileIds` / `RetainedAccomodationFileIds` carry the `fileId`s
still on the rows, the lists the old code computed but dropped. A new draft sends neither key, as before.

Everything else in the payload is the old one: the parity check (`tools/parity_diff/run1b_py.py`) compares the Python
payload with the TypeScript one with only the two `Retained...` keys removed.

## Raise Claim chat on the server (`claimchat/flows/raise_claim.py`)

`ClaimChatbot.tsx` (both modes) and the texts of `ClaimAssistantLauncher.tsx`, ported line for line on the runtime.
What could not be reproduced exactly (CC = ClaimChatbot.tsx line):

| # | Old (browser) | Port |
|---|---|---|
| RC1 | `hasMoreFiles` (CC:1709-1713) said its note and opened the file picker (`composerRef.pickFiles()`) | The note, and the event `{type: "open_file_picker"}` (not in the 0.4 contract yet; the frontend should open the picker). The upload chips themselves are chips of kind `upload` |
| RC2 | Effects ran when their dependencies changed (CC:490, 545, 1241, 1395, 1699, 1755) | They run whenever their guards hold, after the request's handler (runtime design §3). The drafts-changed effect (CC:545-568) also runs as soon as the page's drafts are loaded and right after each save (the old page's drafts prop changed while the save was still busy, so it never re-greeted then) |
| RC3 | The in-form start (CC:1755-1790) ran again after a failed tour load whenever the page re-rendered (`form` was a new object each render) | Tried again once per request |
| RC4 | In the form, `tours` was ApproverPage's `assistantTour` (statusId from the URL, `navState.totalDays ?? details.totalDays`) | POST /workflows carries only `tourId`: source, destination and start date come from loadAssistantTour (the same RaiseClaimRequest), `statusId` and `totalDays` from the Raise Claim page's tour list (GetTourRequest). Until the tour is loaded the chat counts as "tours loading" |
| RC5 | In the form, `onLoadDraft` returned the draft the form had loaded, and `draftTourIds` was `[tourId]` when it had one | The server's draft (GetRaiseClaimDraft), and `[tourId]` when the drafts list has the tour (both are only used on paths the form does not reach) |
| RC6 | The page re-fetched the drafts after its own changes (delete, save) | The drafts are loaded once per chat and again after the chat's own saves; a draft deleted on the page meanwhile is seen by the next chat |
| RC7 | `form.write()` was synchronous and could not fail | The bridge `write`; a failed or timed-out write is logged and the chat carries on (the next request's `form` is the truth) |
| RC8 | `form.read()` always returned the form | A request without `form` keeps the chat's claim as it is |
| RC9 | A `File` was matched by identity (settleTaken, settleFilesOf, giveBackFilesOf, claimKey) | A bill attached in this chat is a file entry with a `documentId` and no `fileId`; it is matched by that id. The frontend must keep `documentId` on such files in `form` until the form has saved them |
| RC10 | `error.message` of any thrown error was said (takeResults, uploadBills, stopReading, retryFailed) | The OCR API's and the bridge's messages are said as before; any other (internal) error gives the old fallback text |
| RC11 | `navigator.onLine === false` in uploadBills: "You seem to be offline…" | Not reproducible on the server (the send fails with the API's "not reachable" message instead) |
| RC12 | The 10 s `ocrWaitOver` timer let the greeting go on without the bill list | The bill list is asked for when the window opens and on the first request; a failed list counts as loaded (as the old error state) |
| RC13 | `text.length` / `slice` count UTF-16 units; a cut surrogate pair kept a lone half | Same counts; a pair cut at 200/280/300 units is dropped |
| RC14 | A typed message or tap while busy was ignored (composer and chips disabled) | Turns run one at a time per chat, so a request never starts busy |

Dead code of ClaimChatbot.tsx (Spec A §8) is not ported: the bulk / split / upload-choice machinery, `finishRead`,
`dropTaken`, `offerUpload`, `announceGaps`, the steps `gaps` and `submit` (`startSubmit`, `doSubmit`), the outside
branches of `afterRead` / `noMoreFiles`, and the "Choose a tour myself" chip. Their actions are never offered on a live
path; one that was would run "talk", as `runs[spec.name] ?? runs.talk`. The quirks of Spec A §8 are kept (e.g.
`resetAskedRef` stays set after a confirmed start over; the no-drafts greeting has no "Hello"; the saved-claim link
chip's label is the URL; "I'll fill the rest on the form" opens the form at once).

## Owner requirements 2026-11-06

New requirements on top of exact parity, for the Raise Claim chat on the Raise Claim page (`raise_page`) and inside
the claim form (`raise_form`). The Within Jurisdiction chat (`flows/within.py`) is unchanged. Every row's reason is
**owner requirement**. CC = ClaimChatbot.tsx line.

### A. Nothing is said about missing information

| # | Old behaviour | Now | Why |
|---|---|---|---|
| O1 | After the tour was chosen (and after every edit, undo, save offer) `advance` asked one missing detail at a time (`findMissingFields`, "(N more after this one)", the "skip" hint; CC:662-685) | No question: the claim is read out as it is (the old confirm read-out) and the step is `confirm` | owner requirement |
| O2 | "Did you stay anywhere overnight?…" / "Were you on leave on any day of the tour?…" (CC:686-698) | Not asked | owner requirement |
| O3 | Before the read-out, the review questions one at a time (`runReview`, `reviewClaim` without `LIST_PAGE_SKIPS`, CC:699-702, 1810-1899): uncovered days, nights without a stay, cabs/taxis, tickets, odd places… | No review on the page (none in the form either: it was only reached from the page's `advance`) | owner requirement |
| O4 | The read-out added "👀 Worth a check" (`findRelationIssues`, e.g. "Is a ticket missing in between?") and "⚠️ The form will flag these" (`findFormOnlyIssues` + `findClaimIssues`: amounts not read, city class…) | The read-out lists the claim only (tone success) | owner requirement |
| O5 | The read-out showed a journey without a travel mode as "· mode not set" | That segment is left out | owner requirement |
| O6 | "Fill the form" with missing details or form issues went to the `validate` step: "Quick heads-up, the form will flag these…" (`describeMissing`), chips "Sort them out here" / "Open the form anyway" (CC:1927-1985) | Straight to the hand-off (`doFill`); the `validate` step, its chips and `choose_here` are gone | owner requirement |
| O7 | The hand-off note: "Ask the employee to review each section, attach anything missing (amounts and bill files go on the form), then submit." | "…review each section (amounts and bill files go on the form), then submit." | owner requirement |
| O8 | "What's left?" chip and the `show_progress` action (`buildProgress` / `progressText`; in the form the review list with `missingInShort`) | Removed: the chip is not shown, the action is not offered to the model (a message asking it is "talk"); `requestFill` in the form (unreachable) no longer goes on to it | owner requirement |
| O9 | The saved-drafts data given to the model (PAGE, `SAVED DRAFTS IN DETAIL`) carried each draft's `missing` sentence ("Missing Return journey ticket, …" / "Nothing is missing.") | `missing` (and `stillMissing`) are left out | owner requirement |
| O10 | `savedClaimText` ended with "Still no bills for 11 Sep." or "Every day of the tour is now covered." | That last line is left out (`saved_claim_text(..., gaps=False)`): keeping only the "covered" line would tell the gap by its absence | owner requirement |
| O11 | The chat state given to understand had `hasMissing`, `pendingQuestion`, `review`; CONTEXT had "The open question fills …" | Always false / none / left out | owner requirement |
| O12 | Prompts: the old strings only | prompts.json is unchanged; added in code (`flows/raise_claim.py`): `NO_GAPS_RULE` after the narrator prompt (typed turns) and as a last rule of the `brief` prompt (other replies: `ChatFlow.brief_prompt()`, new `narrate(brief_prompt=…)`); `UNDERSTAND_RULES` after the understand prompt (amount questions are "talk", with the owner's examples; questions about what is missing are "talk"); one line in understand's CONTEXT | owner requirement |

Kept, as they are not about what a claim lacks: the ✅ New / ↩ Duplicate / 🚫 Not relevant report (unchanged, both
pages), the in-form "Do you have more tickets or bills to upload for this claim?", "⚠️ Please check:" after an edit that
makes the claim contradict itself (dates outside the tour, arriving before departing), the air-travel note, the
"server no longer has <file>" note, and FORM_TALK_NOTE / FORM_READY_REPLY in the form (already the owner's rule).

### C. Billing questions

| # | Old behaviour | Now | Why |
|---|---|---|---|
| O13 | The narrator never gave amounts ("Never calculate or estimate DA, totals, fares or any amount… say they are entered on the claim form") | A "talk" message gets a `BILLS` section in the narrate message (after PAGE; new `narrate(bills=…)`, `NarratorView.bills`) with figures worked out in code (`claimchat/billing.py`), and `BILLS_RULE` after the narrator prompt: answer amount questions only from BILLS, exactly as written; nothing added, subtracted or estimated; a figure not in BILLS "could not be determined". The last line of the typed message says the same when BILLS is given | owner requirement |
| O14 | — | A per-conversation bill ledger (`state.billLedger`), recorded when a read is gone through (`autoSaveRead`, `formRead`) and when an upload had only exact copies or files turned away: file, documentId, hash, tour, journey/stay, route or hotel and place, date, invoice number, amount (`claimedAmountWithTax`, else none), duplicate of, counted (only a bill that went into a claim) | owner requirement |
| O15 | — | On each "talk" turn every outside-jurisdiction draft is read (the page's drafts list, then GetRaiseClaimDraft for each, once per turn) and its totals worked out as the claim form does (see below) | owner requirement |

**What BILLS holds** (amounts in ₹, en-IN as the old texts: `₹12,34,567.50`), in two parts kept apart (the model is
told never to mix them and to say which one it gives):
- **BILL AMOUNTS** (each bill's total with tax, as read from the bills uploaded in this chat): every file read or
  turned away in this conversation, upload by upload, with its amount or "amount could not be determined"; a file that
  did not go into a claim, or a duplicate, is listed with "not counted" and why; the latest upload's total and the
  whole conversation's total (each bill counted once), with how many bills had no amount, how many duplicates and how
  many files that did not go into a claim were left out; the highest and the lowest bill; the totals per tour;
- **DRAFT TOTALS** (as the claim form works them out from the saved draft): every saved draft's transportation,
  accommodation, DA, leave deduction (when there is one), total with DA, total without DA; and all drafts together. A
  draft without DA rows: "DA could not be determined (no DA is saved in the draft: it is worked out when the draft is
  opened in the claim form)".

**Rules (billing.py):**
- A bill's amount is the OCR's `claimedAmountWithTax` (a journey's `amountDetails`, a stay's `invoice`), never a sum of
  line items or taxes. Missing, not a number, 0 or less: "could not be determined". A ticket with several journeys is
  one bill (its first usable `claimedAmountWithTax`). Text amounts may carry "₹" and "," separators.
- One bill per file for its journeys; one per invoice number for its stays.
- Only a bill that went into a claim (fate `saved`: added to a draft, or to the form's claim) is counted. A bill that
  matches no tour, whose tour can't be told, with a problem on the bill, that could not be saved, a file that could not
  be read or had nothing in it, and a file turned away at upload are listed with why and left out of every bill total
  (latest upload, the whole chat, highest / lowest, per tour). Saved journeys keep the old claimed amount
  (`totalClaimedAmount` / `claimedAmount`), so a draft's transportation total can differ from the bill amounts.
- Duplicates (counted once, the copies listed as "duplicate of …"): an exact copy skipped at upload (same bytes); a
  different scan of a journey or stay already read in this chat (same journey key / stay key) or already in the claim
  (`alreadySaved`); the same invoice number with the same amount.
- A draft's totals, as the claim form works them out once it has loaded the draft (Tour_React
  `src/pages/RaiseClaim`): transportation = Σ `parseFloat(String(ClaimedAmount ?? 0)) || 0` (ApproverPage.tsx:1098,
  TransportationSection.tsx:541-543); accommodation = the same over the stays, 0 without stays (ApproverPage.tsx:1162,
  1195; AccommodationSection.tsx:2284-2290); DA = Σ `Number(Amount || 0)` of the `DailyAllowanceDto` rows with
  `IsApplicable === true` (ApproverPage.tsx:1198-1220, 543, 1800; DailyAllowanceSection.tsx:429); leave deduction = for
  each leave row with a date and `DADeductable`, the DA of the DA rows whose date is within it (LeaveSection.tsx:133-158,
  208-217, 236-241; ApproverPage.tsx:1225-1238); total = `toFixed(2)` of transportation + accommodation + DA − leave
  (ApproverPage.tsx:543-553). Without DA: transportation + accommodation (the leave deduction is DA taken back, so it
  goes with the DA). A draft without DA rows has its DA worked out by the form itself: the agent never works out DA, so
  DA, the leave deduction and the total with DA are "could not be determined". Each subtotal is rounded as
  `Number(x.toFixed(2))`. The leave dates are not clamped to the tour's dates (the form's clamp needs the tour plan;
  DA rows lie within the tour anyway).
