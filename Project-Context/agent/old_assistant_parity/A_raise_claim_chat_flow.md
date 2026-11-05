# Spec A — Raise Claim (outside jurisdiction) chat orchestration

Source of truth (all paths under `D:\CETPA\Tour_React\src\`):

- `components/claimChatbot/ClaimChatbot.tsx` (2,573 lines; cited as `CC:<line>`)
- `components/claimChatbot/useNarrator.ts` (cited as `NAR:<line>`)
- `components/claimChatbot/ClaimAssistantLauncher.tsx` (cited as `LAU:<line>`)
- Helpers it calls, cited by file: `lib/claimChatbot/assistantActions.ts` (`AA:`), `lib/claimChatbot/aiAssistant.ts` (`AI:`),
  `lib/claimChatbot/billSummary.ts` (`BS:`), `lib/claimChatbot/billOutcome.ts`, `lib/claimChatbot/keptReports.ts` (`KR:`),
  `lib/claimChatbot/intents.ts` (`INT:`), `lib/ocr/ocrJobs.ts` (`OJ:`), `lib/ocr/ocrClaims.ts` (`OCL:`), `lib/ocr/ocrApi.ts` (`OAPI:`),
  `lib/ocr/ocrResults.ts`, `lib/ocr/ocrReferences.ts`, `lib/claimChatbot/savedClaim.ts` (`SC:`), `lib/claimChatbot/tourMatcher.ts` (`TM:`).

Domain rules (findMissingFields, describeMissing, applyEdits, readClaimEdits, mergeOcrRows, snapshot, summarize*, findClaimIssues,
findFormOnlyIssues, findRelationIssues, findTimeOverlaps, findAmountIssues, stayBillProblem, uncoveredDays, coveredText, groupDays,
buildProgress/progressText, reviewClaim, nextReviewStep, review*Note, settle, missingInShort, matchTours, triageRows, mapOcrToRows,
compareWithSaved/addToSaved/savedClaimText/savedDraftSummary/claimStateFromDraft) are specified elsewhere and are referenced here
by name only. The AI prompts (NARRATOR_RULES, BRIEF_NARRATOR_PROMPT, UNDERSTAND_RULES/EXAMPLES, OUTSIDE_DOMAIN, action
descriptions) are specified elsewhere; this spec says exactly **which** facts go to them and **when**.

> **Critical parity note about "sayExactly".** Despite its name, `sayExactly(text, tone)` is `narrator.say(text, tone)` (CC:450),
> which just queues an event `{note: text, tone}` (NAR:55-58). The text is *not* shown verbatim: it is a NOTE given to the AI
> model, which rewrites it (BRIEF prompt: "at most two short sentences (plus a list if a NOTE has one)", AI:97-106,127).
> Only notes that themselves contain the words "word for word" are forced verbatim by the prompt (e.g. the no-drafts greeting,
> CC:518). `tell(note, tone, data)` (CC:326-329) is the same thing plus an optional `data` object. Every message the employee reads
> is written by the model, except the fixed `SYSTEM_NOTICE` (AI:14). Throughout this spec "says" means "queues this NOTE for the
> narrator".

---

## 0. Two modes of the same component

`ClaimChatbot` is mounted by `ClaimAssistantLauncher` in two places:

| Mode | Where | `form` prop | `inForm` |
|---|---|---|---|
| Raise Claim page ("outside", the main subject here) | `pages/RaiseClaim/RaiseClaim.tsx:882` (only when `tourClaimAgent.enabled` is false) | absent | `false` |
| Inside one claim form | `pages/RaiseClaim/ApproverPage.tsx:2611` (`showAssistant = !isExpandedRow && !activeTab && !readOnly && !isClarificationMode && !!assistantTour`) | `ClaimFormBridge` (`read`, `write`, `submit`, `saveDraft`, `check`) | `true` |

`inForm = !!form` (CC:302). In form mode `tours` is a one-element array (the form's tour); `draftTourIds` is `[tourId]` if the form
loaded a draft; no `pageInfo`, `draftsLoading`, `draftsInfo` are passed.

Props (CC:93-124): `tours: ClaimableTour[]`, `toursLoading`, `loadTour(tour,{quiet?}) → {tour,rules}|null`,
`onFillForm(tour, {tourId,state}) → bool`, `onOpenClaim(tour) → bool`, `onSaveDraft(tour, {tourId,state}, {overwrite?}) →
'saved'|'exists'|'failed'`, `onLoadDraft(tourId) → draft|null`, `draftTourIds`, `pageInfo?`, `draftsLoading?`, `draftsInfo?`
(`{tourId,lastSaved}`), `active` (window open), `onThinkingChange`, `onTourChange`, `onBillsChange`.

Page-side semantics worth knowing (RaiseClaim.tsx:397-445): `onSaveDraft` returns `'exists'` when the tour already has a draft and
`overwrite` is not set; with `overwrite` it rebuilds the draft payload keeping the saved file ids (`retained*FileIds`) and uploads
new `File`s; after saving it re-fetches all drafts (which changes `draftTourIds`). `onFillForm` stores the handoff in memory
(`setAssistantDraft`) then navigates to `/raise-claim-approver` (RaiseClaim.tsx:367-375). `loadTour` is cached per tour and shows a
toast "Failed to load the tour. Please try again." unless `quiet` (RaiseClaim.tsx:349-364).

### 0.1 Launcher (LAU)

- Holds `open`, `tour` (set by `onTourChange`), `thinking` (set by `onThinkingChange`), `chatKey`, `bills {reading, ready}` (LAU:44-49).
- **Header "Start over" button** (ChatPanel) → `startOver()` (LAU:54-58): `thinking=false`, `tour=null`, `chatKey+1` → the whole
  `ClaimChatbot` is unmounted and remounted (fresh state, greeting again). No confirmation question. Unmount gives back every taken
  OCR result (CC:1246-1253).
- The chat stays **mounted while the window is closed** (ChatPanel hides it with `display:none`); `active = open` (LAU:98).
- Launcher bubble notice while closed (LAU:60-65):
  - `ready > 0`: `` `Your ${ready===1 ? 'bill is' : `${ready} bills are`} read. Open me to go through ${ready===1 ? 'it' : 'them'}.` ``
  - else `reading > 0`: `` `Processing ${reading===1 ? 'your bill' : `${reading} bills`}…` ``
  - else none (Brand shows `Need help? Ask Aryaan` on hover).
- Header status line (LAU:67-77): `'Thinking…'` if thinking; else if `reading>0 && !tour`: `` `Processing ${reading===1?'1 bill':`${reading} bills`}…` ``;
  else if tour: form ? `` `Tour #${id} · guiding your claim` `` : `` `Tour #${id} · you review and submit` ``; else if no tours (and not
  loading): `'No approved tours to claim for'`; else `'Upload your bills to begin'`.
- `onBillsChange({reading: inFlightDocs.length, ready: readyCount})` is emitted whenever those counts change (CC:1411).

---

## 1. State held by the chat

### 1.1 React state / refs (CC:230-302 and later)

| Name | Kind | Meaning | Reset by |
|---|---|---|---|
| `messages` | state+ref | chat bubbles (`role: 'user'|'bot'`, `system` for the fixed notice, `streaming`) | re-greet clears (CC:563) |
| `busy` | state `string|null` | label of the running operation; chips/composer disabled; narrator not idle | each op's `finally` |
| `bills` | state `StagedBill[]` | files attached but not yet sent (`{file, hash}`) | after successful upload; `resetClaim` |
| `step` / `stepRef` | `OutsideStep` | see §2 | — |
| `pending` / `pendingRef` | `MissingField|null` | the one missing-field question open in `details` | many |
| `state` / `stateRef` | `ClaimFormState` | the chat's claim (sorted for display by `asSeen`) | `resetClaim` |
| `picked` / `pickedRef` | `{row, tour, rules}` | confirmed tour | never cleared (change tour only replaces it) |
| `suggested` | tour | suggested tour in `confirmTour` | `showTourList`, `chooseTour`, `changeTour` |
| `ranked` | tours | tours offered in `confirmTour`/`pickTour` (best first) | — |
| `noMatch` | bool | **never set true** (dead) | — |
| `undo` | `ClaimFormState|null` | claim before last typed change / reset | — |
| `savedTours` | tours | tours whose claim link buttons are shown ("saved claims") | many |
| `askedRef` | `{stay, leave}` | stay / leave questions asked once | `resetClaim` |
| `skippedRef` | `Set<slotKey>` | missing fields the employee left for the form (`section:row:slot`, CC:200) | `chooseHere`, `resetClaim`, (dead finishRead/chooseSplit) |
| `hintedRef` | bool | the "(say skip)" hint already given | same as above |
| `resetAskedRef` / `resetAsk` | bool | "start over?" waiting | see §5.12 (bug) |
| `askYN` / `askYNRef` | `{question, yes, no, yesLabel, noLabel}` | a generic yes/no question | `clearAskYN` |
| `greetedRef` | bool | greeting done | re-greet |
| `greetAgain` | counter | forces greeting effect to rerun | — |
| `welcomeRef` | `string|null` | greeting deferred into the first bill report (employee name) | `takeResults` |
| `justReadRef` | bool | "bills were just read" — only set true in dead code ⇒ effectively always false | — |
| `readFilesRef` | `Set<fileKey>` | `name|size` of bills already read and in a claim | `resetClaim` |
| `readHashesRef` | `Map<sha256,{key,name}>` | bytes of those bills | never |
| `doneUploadingRef` | bool | employee said no more bills | several |
| `splitRef`, `bulkRef`, `stagedRef`, `heldRowsRef`, `uploadChoiceRef`, `bulkSavedRef` | | **never set to a non-null value on any live path** (dead machinery) | — |
| `draftIdsRef` | `number[]` | `draftTourIds` prop | — |
| `savedRef` | `Map<tourId, ClaimFormState>` | cache of each tour's saved draft | drafts change; after a save |
| `lastSavedRef` | `Map<tourId, claimKey>` | the claim as last loaded/saved (to detect unsaved changes) | drafts change |
| `saveOfferedRef` | bool | "not saved as a draft yet" told once | save / chooseTour |
| `savedHereRef` | `Set<tourId>` | drafts saved by autoSaveRead in this mount | drafts change |
| `draftDetailsRef` | `Map<tourId, SavedDraftSummary>` | what each saved draft holds (for PAGE facts) | drafts change |
| `storedIdsRef` | `{t: ids[], a: ids[]}` | stored order of rows in the draft/form (rows shown date-sorted) | replace commits |
| `discardedRef` | `Set<documentId>` | OCR docs this chat let go of: never taken again **in this mount** | never |
| `sentHashesRef` | `Map<documentId, sha256>` | hashes of files sent this mount | stop reading |
| `takenRef` | `Map<documentId, {file, key}>` | OCR results taken (MarkAsRead) and not yet settled | settle / give back |
| `takingRef` | bool | a read is being gone through | `takeResults` finally |
| `failedDocs` | `number[]` | failed doc ids of last read (retry chip) | retry |
| `pendingSkipRef` | `{duplicate: names[], rejected: {name,reason}[]}` | files skipped at upload, reported with next read | `takeSkipped` |
| `triedRef` | `Set<fileKey>` | staged files already auto-sent once | cleared when no bills staged |
| `reviewModeRef`, `reviewItemRef`/`reviewItem`, `reviewActiveRef`, `reviewPassedRef`, `acknowledgedRef`, `deferredRef` | | review question machinery (§5.8) | — |
| `ocrWaitOver` | bool | 10 s since opening passed (greeting stops waiting for OCR list) | — |
| `formStartedRef` | bool | in-form start ran | — |

`noTours = !toursLoading && tours.length === 0` (CC:299).

### 1.2 Committing the claim (CC:570-606)

- The chat claim is always kept **date-sorted** (`asSeen`: `journeysInDateOrder`, `staysInDateOrder`), so "journey 2" means the
  second by date. Storage order (form / saved draft) is `storedIdsRef` order; `asStored` maps back by row id (`inStoredOrder`).
- `commit(next, fromForm=false, replace=false)`: if `replace`, `storedIdsRef = {t:[],a:[]}`; sets state to `asSeen(next)`; if not
  `fromForm` and a form bridge exists, `form.write(asStored(seen), {replace})`.
- `syncFromForm()`: inside a form, reads `form.read()`, sets `storedIdsRef` from it, commits with `fromForm=true`; returns the state.
  Outside a form it just returns `stateRef.current`.
- `openMissing(s) = findMissingFields(s)` minus `skippedRef` slots (CC:608).
- `reviewText(s)` (CC:610-611): `` `Your claim:\n\nJourneys\n${summarizeJourneys(s)}\n\nStays\n${summarizeStays(s)}\n\nLeave\n${summarizeLeave(s)}` ``.
- `hasClaimRows(s)`: any non-empty journey, or (includeAccommodation and any non-empty stay) (CC:188-189). The same expression is
  inlined as `hasClaim` in several places.
- `claimKey(s)`: JSON of non-empty journeys, stays (if included), leave (if included), Files replaced by name, `fileId`,
  `tempFilePath`, `isRetained` dropped (CC:191-199). Used to tell if the claim differs from the last saved one.

### 1.3 Formatting helpers (CC:172-184)

- `dayMonth(iso)` → `dd MMM yyyy`.
- `tourDates(t)` → `` `${start} – ${end}` `` from `tourWindow(t)` (or `''`).
- `tourLabel(t)` → `` `${source||'?'} → ${destination||'?'}${dates ? ` · ${dates}` : ''}` ``.
- `shortRange(t)` → single date if start==end, else `start – end`.
- `placeName(v)` → text before the first comma (or `'?'`).
- `hello()` (CC:483-486) → `` `Hello${name ? `, ${name}` : ''}! 👋\nI'm Aryaan, your Claim Assistant.` `` (`name` = Redux `user.unique_name`, trimmed).

---

## 2. Steps (state machine)

`OutsideStep` = `start | confirmTour | pickTour | bulkDrafts | splitTours | moreFiles | gaps | details | stay | leave | confirm |
validate | uploadChoice | review | submit` (AA:11-26).

`WAIT_STEPS = ['confirmTour','pickTour','bulkDrafts','splitTours','submit']` (CC:127): while in one of these, finished bill reads are
**not** gone through (§4.6).

Orthogonal "overlay" questions that sit on top of any step: `askYN` (yes/no), `resetAsk` (start over?), `uploadChoice` (dead).

### 2.1 Reachability summary

| Step | Raise Claim page | Inside form |
|---|---|---|
| start | yes (initial) | yes |
| confirmTour / pickTour | yes (only after typed details or save/fill without tour) | only in an edge case (tour failed to load at form start, then a typed edit) |
| details, stay, leave | yes (after a tour is confirmed, via `advance`) | **no** (`advance` returns early in form) |
| review | yes (pre-read-out review, filtered) | **no** (dead) |
| confirm | yes (claim read-out) | **no** |
| validate | yes ("fill the form" with problems) | no |
| moreFiles | **no** (only `afterRead`, unreachable outside) | yes (after each read, and at form start) |
| gaps | **no** (`announceGaps` unreachable) | no |
| bulkDrafts, splitTours, uploadChoice | **no** (dead) | no |
| submit | no | **no** (dead: `check_and_submit` never offered) |

### 2.2 Transition table (live paths)

| From | Trigger | Effect / next step |
|---|---|---|
| (mount) | — | `start`; greeting (§3) |
| any | `askTour(s)` with no tours | `start` + error note (CC:629-632) |
| any | `askTour(s)`: typed clues score 0 for every tour | `showTourList` → `pickTour` |
| any | `askTour(s)`: no confident match and >1 tour | `pickTour` |
| any | `askTour(s)`: confident match, or only one tour | `confirmTour` (suggested set) |
| confirmTour | yes / confirm_tour / "Yes, that's right" | `chooseTour(suggested)` |
| confirmTour | no / choose_another_tour / "No, choose another" | `changeTour` → `pickTour` |
| confirmTour/pickTour | pick_tour {tourId} / tour chip | `chooseTour(t)` |
| any (picked, not tour steps) | change_tour / "↩ Different tour" | `pickTour` (all tours) |
| (chooseTour done) | — | `advance` (or saved-draft yes/no first) |
| `advance` (picked, outside) | missing fields | `details` (pending = first) |
| `advance` | none missing, stay not asked and no stay | `stay` |
| `advance` | leave not asked and no leave | `leave` |
| `advance` | review not passed | `runReview('check')` → `review` or `confirm` (blocked) or back to `advance` |
| `advance` | review passed | `confirm` (claim read-out) |
| details | edit applied / skip / "I'll fill the rest on the form" | `advance` / `advance` / **doFill (opens form)** |
| stay, leave | no_stay / no_leave / chip "No stay"/"No leave" (sends "no") | `advance` |
| stay, leave | typed stay/leave details → edit | `advance` |
| review | answer (confirm/later/fixed/remove/edit/upload) | §5.8 |
| confirm (or any non-tour step) | fill_form / "Fill the form" | `requestFill` → `doFill` or `validate` |
| validate | fill_here / "Sort them out here" | `chooseHere` → `advance` (or doFill if nothing missing) |
| validate | open_form_anyway / fill_form / "Open the form anyway" | `doFill` |
| any | bill read finished (outside) | `autoSaveRead` → `start` |
| any (inForm) | bill read finished | `formRead` → `moreFiles` |
| moreFiles (form) | have_more_files / "Yes, I'll upload more" | `start` + file picker |
| moreFiles (form) | no_more_files / "No, that's everything" | `start` |
| any | confirm_start_over | `resetClaim` → `start` |
| any | undo | restore + `advance(undo)` |

---

## 3. Opening / greeting (exact order)

### 3.1 Raise Claim page greeting effect (CC:490-538)

Runs when any of `[toursLoading, draftsLoading, tours.length, ocr.loaded, ocrWaitOver, active, greetAgain]` change. Guards, in order:

1. `greetedRef` already true → nothing. `toursLoading` or `draftsLoading` → wait.
2. `!active && !inForm` → wait (outside, the greeting is worked out only when the window is opened, not on page load).
3. Outside only: `!ocr.loaded && !ocrWaitOver` → wait. When the chat becomes active it calls `refreshOcrDocuments()` and starts a
   10 s timer that sets `ocrWaitOver` (CC:440-446) (timer is per activation; re-armed each open).
4. `greetedRef = true`.
5. `tours.length === 0` → say
   `` `${hello()} You have no approved tours waiting for a claim yet. Once one is approved, I can help with it.` `` (CC:496). Stop.
   (Applies in form mode too.)
6. In form → stop (form greeting is §3.3).
7. **Kept reports**: `sayKeptReports(hello())` (CC:501, 471-481). If this browser has unseen bill reports for scope `'page'`:
   say `[hello(), ...kept.map(r => r.text)].filter(Boolean).join('\n\n')` with tone `error` if any kept report is error, `success`
   if all are success, else none; `onShown` drops those reports from storage. Also `savedTours = tours whose id is in any kept
   report's tourIds` (outside only). Stop.
8. **Bills already on the server for this chat** (`myDocs`, §4.1): if any:
   - if **any is finished** (not Pending/Processing): `welcomeRef = name` (trimmed `unique_name` or `''`) and stop **without saying
     anything**: the greeting becomes the header of the first read report (`readHeader(..., {name})` → `"Hi Anil, your 2 bills have
     been read:"` / `"Hi Anil, the 2 bills you uploaded earlier have been read:"`; with an empty name `"Hi, …"`).
   - else (all still being read): say
     `` `Hi${name ? ` ${name}` : ''}, ${n === 1 ? 'the bill you uploaded is' : `the ${n} bills you uploaded are`} still being read. I'll show what I find and save it to your drafts as soon as ${n === 1 ? "it's" : "they're"} done. You can close this window meanwhile.` `` (CC:511-513). Stop.
9. **No saved drafts** (no tour in `tours` whose id is in `draftTourIds`): say exactly the NOTE
   `'Say word for word: "Upload your tour tickets/bills/receipts"'` (CC:518). (Note: no "Hello, name" in this case.) Stop.
10. **Saved drafts**: `busy = 'Looking at your saved drafts…'`; for the first 10 tours with a draft, `loadSaved(tourId)` in parallel
    and `rememberDraft(tour, saved)` (builds `savedDraftSummary(row, saved, {lastSaved: draftsInfo.lastSaved ?? now, savedInChat:
    id in localStorage chatDrafts, tourDates})`) (CC:410-418, 522-528). Then `savedTours = those ≤10 tours` and say
    `` `${hello()} You have **${n} saved draft${n === 1 ? '' : 's'}**. You can continue one, or upload new bills.` `` where `n` is
    the total number of tours with a draft (not capped at 10) (CC:532). `busy = null`.
    The draft details are not in the greeting; they are given to the model as PAGE facts on later typed turns
    (`draftDetailsText()`, CC:419-422):
    `SAVED DRAFTS IN DETAIL (what each saved claim holds):\n${JSON.stringify(list.map(draftForModel))}`.

The buttons that follow (§6): with `savedTours` → one link chip per saved tour (label is the full claim URL, opens in a new tab),
"📎 Upload other bills", "Not now". Without → "✍️ I'll type the tour details".

### 3.2 Re-greeting when drafts change (CC:540-568)

`draftsKey` = sorted unique `draftTourIds`. When it changes (and drafts are not loading):
- every per-tour memory of a tour that no longer has a draft is forgotten: `savedRef`, `draftDetailsRef`, `lastSavedRef`,
  `savedHereRef`, the localStorage `chatDrafts` entry (`forgetSavedInChat`), and it is removed from `savedTours`.
- Outside only, and only if greeted already: if the chat is untouched (no user message, `step==='start'`, not busy, no `askYN`,
  no staged bills) → clear all messages, `greetedRef=false`, `greetAgain++` → the greeting is worked out again from scratch.
  Note: a save by the chat itself (which refreshes the drafts list) also triggers this, but by then the chat is normally not
  untouched.

### 3.3 In-form start (CC:1753-1790)

Runs once when `form` exists, tours loaded and non-empty (does **not** wait for `active`; the narrator only writes when the window is
open):
1. `busy = 'Looking at your claim…'`; `loadTour(tours[0])`.
2. Fail → `formStartedRef=false`; say `"I couldn't load this tour just now. Please close and reopen me in a moment."` (error).
   (The effect only re-runs if `form/toursLoading/tours` change.)
3. Success → `picked = {row, tour, rules}`; `onTourChange(row)`; `s = syncFromForm()`; `hasClaim = hasClaimRows(s)`;
   `claimFor = **${placeName(src)} → ${placeName(dst)}**${shortRange ? ` (${shortRange})` : ''}`.
4. If kept reports exist for scope `tour-<id>`: say `hello() + " Let's ${hasClaim ? 'finish' : 'do'} your claim for ${claimFor}."`
   followed by the kept reports (each kept report already ends with the "more bills?" question); `step = moreFiles`.
5. Else if no claim rows: say `` `${hello()} Let's do your claim for ${claimFor}. Please upload your tickets and bills.` ``.
6. Else: say `` `${hello()} Let's finish your claim for ${claimFor}.` ``, `doneUploadingRef=false`, `afterRead(s)` → (inForm branch)
   `step=moreFiles`, say `` `${claimHolds(s)}\n\nDo you have more tickets or bills to upload for this claim?` ``. Both notes are
   combined into one model reply.

`claimHolds(s)` (CC:1647-1655): `'Your claim has no journeys or stays yet.'` or
`` `Your claim has **${parts.join('** and **')}**${covered ? `, covering **${covered}**` : ''}${bills ? `, with ${countOf(bills,'bill')} attached` : ''}.` ``
where parts are "N journey(s)", "N stay(s)", covered = `coveredText(s)`, bills = number of file names on those rows.

---

## 4. Bills

### 4.1 Which server documents belong to this chat (CC:424-437)

- `ocr = useOcrDocuments()`: global list from `GET /Ocr/GetMyDocuments` (IncludeResponse=false, PageSize 200), polled every 20 s
  while any doc is Pending/Processing and the tab is visible, 60 s after an error; refreshed on visibility change and when the chat
  opens (OJ:12-94). Docs taken/cancelled in this tab are hidden locally until the server stops listing them.
- `myDocs` = docs whose `reference` is:
  - outside: `isRaiseClaimChatRef` — starts with `outside-chat:` and is **not** the legacy `outside-chat:tour-<id>`;
  - in form: `isClaimChatRef(ref, tourId)` — `claim-chat:tour-<id>` or legacy `outside-chat:tour-<id>`;
  - and not in `discardedRef`.
- `inFlightDocs` = status Pending/Processing; `readyCount = myDocs - inFlight`.
- Upload reference: outside `outside-chat:unassigned`; in form `claim-chat:tour-<tourId>` (CC:1228-1229).

### 4.2 Attaching files: `addBills(files)` (CC:804-863)

Entry points: the composer's 📎 file picker (accept pdf/png/jpeg, multiple), camera capture (one photo), and every "upload"
button that calls `composerRef.pickFiles()`. The composer is disabled while `busy`, `narrator.writing`, `noTours` or `toursLoading`.

1. Empty list → nothing.
2. `noTours` → say `'There is no approved tour to claim for right now.'` (error). Stop.
3. Step `splitTours`/`bulkDrafts` → tell (dead: those steps are unreachable)
   `"Let's sort out the drafts first, then you can upload more bills."` / `'Please choose the tour first, then you can upload more bills.'`.
4. `clearAskYN()` — attaching files closes any open yes/no question.
5. `checkFiles(files)` (INT:136-151) → rejected ones are appended to `pendingSkipRef.rejected` with reasons:
   - HEIC/HEIF name or type: `"HEIC photos can't be read. Please send it as a JPG or PNG, or a PDF."`
   - `.doc/.docx/.xls/.xlsx/.ppt/.pptx/.txt/.csv/.zip/.rar`: `'I can only read PDFs and photos or scans (JPG, PNG), not this kind of file.'`
   - not `.pdf/.jpg/.jpeg/.png` or MIME not in (``''``, application/pdf, image/jpeg, image/png): `'I can only read PDFs and JPG or PNG images.'`
   - size 0: `'the file is empty. Please attach it again.'`
   - > 20 MB: `` `it is ${mb} MB, which is too large (the limit is 20 MB). Please send a smaller copy.` `` (rounded MB).
   Name used: `f.name || 'This file'`.
6. SHA-256 of each accepted file (`hashFile`; `null` if `crypto.subtle` is unavailable or reading fails — then never a duplicate).
7. Known hashes (CC:824-831): `read` = hashes in `readHashesRef` whose file key is still in `readFilesRef`; `staged` = hashes of
   currently staged `bills` + hashes (from `sentHashesRef`) of this chat's in-flight docs.
8. For each accepted file: `uploadVerdict(hash, known, inUpload)` (billOutcome.ts:18-22) — duplicate if the hash is known read,
   staged, or already in this same upload → name pushed to `pendingSkipRef.duplicate`; else kept. Name collisions with staged
   bills are made unique: `name (2).ext`, `name (3).ext`… (a new `File` with the new name).
9. Limit: at most `MAX_FILES_PER_READ = 20` minus already-staged count are kept; the rest (`later`) are dropped with the note
   (tell, CC:851-854):
   `` `I can read up to 20 bills at a time, so I'm reading ${fresh.length > 0 ? `the first ${fresh.length}` : 'none of these yet'} now. Please attach the other ${later.length} after this, in groups of up to 20:\n${shown}` ``
   where `shown` is up to 10 lines `• <name>` plus `• and N more`.
10. Nothing fresh → `sayUploadOnly(takeSkipped())` (CC:731-736): say `uploadSortedText(names, fates)` (BS:185-192), tone `error` if
    any rejected. Text: `'Here is how I sorted your upload:'`, blank, then groups: `**↩ Duplicate (n)**` lines
    `• **name**: same file as one already uploaded, skipped`; `**🚫 Not relevant (n)**` lines `• **name**: **not uploaded**: <reason>`;
    then `'Nothing new to read, so nothing was added.'` Stop.
11. Otherwise: `savedTours = []` (the saved-claim link buttons go away), append fresh to `bills`, and add a **user** bubble per file:
    `📎 <name>`. Skipped (duplicate/rejected) names stay in `pendingSkipRef` and are reported inside the next read report.

### 4.3 Sending: auto-upload effect + `uploadBills` (CC:1697-1707, 1306-1340)

- Effect on `[bills, busy]`: when not busy and some staged file's key (`name|size`) is not in `triedRef`, mark all staged as tried
  and call `uploadBills()`. Each file is auto-sent once; if sending fails it stays staged and the **"Read again"** chip
  (`bills.length>0 && !busy`) calls `uploadBills` manually. Staged files can be removed individually (✕ on the staged pill).
- `uploadBills()`:
  - `navigator.onLine === false` → say `"You seem to be offline, so I can't send the bills. Your files are still here, so please try again once you are back online."` (error).
  - `busy = 'Processing N bill(s)…'` (`` `Processing ${n} bill${n>1?'s':''}…` ``).
  - `uploadForOcr(files, reference)` → `POST /Ocr/Upload` multipart `Files[]`, `Reference`; all-or-nothing. Remembers the local
    `File` per documentId (so results in this page session reuse it), records `sentHashesRef[documentId] = hash`, removes the sent
    files from `bills`.
  - Success: say `` `${n === 1 ? 'Your bill is' : `Your ${n} bills are`} being read. This takes a few minutes, and you can close this window or the browser meanwhile. ` `` + (inForm ? `"I'll add what I find to this claim."` : `"I'll show what I find and save it to your drafts."`).
  - `OcrApiError` with `invalid` list → tell (error):
    `` `The bills were not sent: the server turned away ${k===1?'this file':'these files'}, and then it takes none of them:\n${lines}\n\nRemove ${k===1?'it':'them'} and try again.` `` with lines `• fileName: reason`.
  - Other error → say `` `${error.message || "I couldn't send the bills just now."} Your files are still here, so please try again.` `` (error).
    OcrApiError messages include `'You are not signed in yet.'`, `'Your session has expired. Please sign in again.'`,
    `'This login is read-only, so bills cannot be sent.'`, `'The server is not reachable right now. Please check your connection and try again.'`,
    or the server's `message` / `'The bills could not be sent.'` (OAPI:51-66).

### 4.4 Stop processing (CC:1288-1304)

Chip `⏹ Stop processing` while `inFlightDocs.length > 0`. `busy='Stopping…'`; `discardOcr(ids)` → `POST /Ocr/Cancel`; ids added to
`discardedRef`, removed from `sentHashesRef`; say `'Stopped reading. Nothing was added. Attach the bills again whenever you want me to read them.'`.
Error → say `` `${error.message || "I couldn't stop the reading just now."} Please try again.` `` (error).

### 4.5 Retry failed reads (CC:1413-1428)

Chip `` `↻ Read the unreadable bill${n>1?'s':''} again` `` while `failedDocs.length > 0` (failed docs of the last read).
`busy='Processing them again…'`; `POST /Ocr/Retry/{id}` for each (a 409 is retried once); `failedDocs=[]`; un-hide them in the
list; say `` `${n===1?'That bill is':'Those bills are'} being processed again. I'll go through ${n===1?'it':'them'} when ${n===1?"it's":"they're"} done.` ``.
Error → say `` `${error.message || "I couldn't send them again just now."} Please try again, or attach a clearer copy.` `` (error).
Failed docs were taken and then settled (not added to `discardedRef`), so once retried and un-hidden they reappear in `myDocs`
when the server lists them again, and they are gone through again when finished.

### 4.6 When finished reads are gone through (CC:1393-1408)

Effect on `[active, busy, narrator.writing, myDocsKey, step, askYN, resetAsk, toursLoading, picked]`. Goes ahead only if **all**:
`active` (window open), not `busy`, narrator not writing, not `takingRef`, tours loaded, `!noTours`, `greetedRef` true; in form:
a tour is picked; no `askYN` open; `resetAskedRef` false; `uploadChoiceRef` null; `step` not in `WAIT_STEPS`.

Then `done = batchesOf(myDocs).filter(b => b.done)` (batches = uploads by `batchId`, oldest first; done = no doc Pending/Processing;
`local` = every doc's file is in this page session's memory). If any: **all done batches are combined into one** call
`takeResults({...done[0], documents: done.flatMap(b=>b.documents), local: done.every(b=>b.local)})` — one report for all.

### 4.7 `takeResults(batch)` (CC:1342-1391)

1. `takingRef = true`; `busy = 'Going through N read bill(s)…'` (`` `Going through ${n} read bill${n>1?'s':''}…` ``).
2. `mine = takeOcrResults(ids)` → `POST /Ocr/MarkAsRead`; only ids in `marked` are applied by this tab; marked ids are recorded in
   localStorage `aryaan.ocrClaims.v1.<emp>` with this tab's id; marked + alreadyRead + notFound are hidden from the list.
3. `mine` empty (another tab/device took them): if a greeting was deferred (`welcomeRef !== null`) → `welcomeRef=null`,
   `greetedRef=false`, `greetAgain++` (normal greeting is said instead). Return.
4. `readTakenDocuments(docs, {withFiles: true, takenNames: file names on the current claim rows})`: full results via
   `GET /Ocr/GetDocument/{id}` for Completed docs; the file from page memory, else `GET /Ocr/GetFile/{id}` (null if gone); names made
   unique against each other and the claim's existing file names (`x (2).pdf`); result `{fileName, failed: status!=='Completed', response}`.
5. Every taken doc → `takenRef[id] = {file, key: fileKey(file)}`.
6. `staged` = taken docs with a file → `{file, hash: sentHashesRef[id] ?? sha256(file)}`.
7. `lost` = names of docs that are not failed but whose file is gone; `failed` doc ids → `failedDocs`.
8. `welcome = welcomeRef; welcomeRef = null`.
9. `lostNote` = `` `⚠️ The server no longer has ${lost.join(', ')}, so please attach ${lost.length===1?'it':'them'} again on the claim form.` ``;
   in form it is also told separately as an error event.
10. `processRead(combineOcrResults(taken.map(t=>t.result)), staged, {fileNames: taken names (upload order), header: readHeader(taken.length, {earlier: !batch.local, name: welcome ?? undefined}), notes: lostNote ? [lostNote] : []})`.
    `processRead` → `formRead` in form, `autoSaveRead` outside (CC:1641-1644).
11. On any thrown error: every `mine` id is removed from `takenRef`, added to `discardedRef` (so **this mounted chat never takes them
    again**), given back (`POST /Ocr/Release`, unhidden); say
    `` `${error.message || "I couldn't go through the read bills just now."} They are kept on the server; I'll go through them the next time you open the assistant.` `` (error).
    ("Next time" in practice = after a page reload or the header Start over, because closing/reopening the window does not remount.)
12. `finally`: `takingRef=false`, `busy=null`, `settleTaken()`.

`readHeader(count, {earlier, name})` (BS:219-228):
- earlier: `the bill you uploaded earlier has been read:` / `the N bills you uploaded earlier have been read:`
- not earlier: `your bill has been read:` / `your N bills have been read:`
- with `name !== undefined`: prefixed `` `Hi${name ? ` ${name}` : ''}, ` ``; otherwise first letter capitalised.

`combineOcrResults` joins per-file results (first file's main journey is the main tour, others become legs; stays concatenated; each
row tied to its file name; a failed file or one whose per-file results are all unsuccessful → `per_file_results {success:false}`).

### 4.8 Raise Claim page: `autoSaveRead(result, bills, read)` (CC:1430-1556)

No question is asked: each bill goes straight into the draft of the tour it belongs to.

1. `mapped = mapOcrToRows(result, bills)`; `allRows` = journeys + stays (for the "what was found" lines).
2. `skipped = takeSkipped()`; `names` = unique `[read.fileNames (or staged names), ...skipped.duplicate, ...skipped.rejected names]`.
3. Fates per file (`strongerFate` keeps the higher-ranked of `saved > alreadySaved > saveFailed > problem > unsure > noTour >
   nothingFound > unreadable > duplicate > rejected`, BS:33-34):
   - `mapped.failedFiles` → `unreadable`; `mapped.unmatchedFiles` → `nothingFound`; skipped duplicates → `duplicate`;
     rejected → `rejected` with reason.
4. Stays with `stayBillProblem(row, undefined, [])` (no rules here) → file marked `problem` with that reason, stay removed.
5. `triage = triageRows(mapped, allTours)` (no picked tour: every approved tour counts). Dropped rows' files → `noTour`
   (file name = key before the last `|`).
6. Neutral rows (no clue which tour): home tour = the single group's tour if exactly one group; if no groups: the picked tour, else
   the only tour if there is exactly one; otherwise none. With a home → added to that tour's group (created if missing); without →
   files marked `unsure`.
7. `busy = 'Saving to your drafts…'`. For each group (tour) in order, `tour` label = `placeName(src) → placeName(dst)`:
   - **Group is the chat's picked tour** (a claim is being built here): `merged = mergeOcrRows(chatState, rows)`.
     - nothing added → files `alreadySaved`; tour into `withDraft` if it has a draft; next.
     - else `commit(enforceAirRule(merged.state))`, `saveToDraft(tour)` (§5.10): `'saved'` → `savedNow`, files `saved`;
       otherwise files `saveFailed` (they stay in the chat's claim, which can be saved later).
   - **Other tour**: `hasDraft` = in `draftTourIds` or saved by this mount (`savedHereRef`). `saved = loadSaved(tourId, fresh=true)`
     (always reads the server). If `hasDraft && !saved` → files `saveFailed`, rows to `notSaved` (a draft that could not be read is
     never overwritten). Else `vs = compareWithSaved(saved, rows)` (if saved); `added = vs ? vs.added : rows`; already-saved rows'
     files → `alreadySaved`. Nothing added → `withDraft`; next. Else `state = saved ? addToSaved(saved, added) :
     mergeOcrRows(initialClaimState(), added).state`; `onSaveDraft(tour, {tourId, state}, {overwrite: !!saved})`:
     - `'saved'` → `savedNow`, `savedHereRef.add`, `markSavedInChat`, `draftDetailsRef[tour] = savedDraftSummary(...savedInChat:true)`,
       `savedRef.delete(tour)`, added files `saved`.
     - otherwise added files `saveFailed`, `notSaved.push(added)`.
   - **Note:** `enforceAirRule` is applied only in the picked-tour branch; drafts of other tours are saved without it.
8. `giveBackFilesOf(notSaved)`: their taken results are released (unread again) and added to `discardedRef`.
9. `stagedRef = null`. For each staged bill whose fate is `saved`/`alreadySaved`: add `fileKey` to `readFilesRef` and hash to
   `readHashesRef` (so re-attaching it is a duplicate).
10. `offered` = unique `savedNow ∪ withDraft`. `clearAskYN()`, `pending=null`, `noMatch=false`, **`step='start'`** (whatever step
    the chat was in, e.g. `details`, the read moves it back to start), `savedTours = offered` (link chips).
11. `bad` = any fate `saveFailed|unreadable|noTour|problem|rejected`. Text = `readSummaryText({header: read.header ??
    readHeader(names.length,{}), files: names, rows: allRows, fates, savedTours: savedNow.map(fullName), notes: read.notes})`
    where `fullName = shortName + (shortRange ? ` (${shortRange})` : '')`.
12. `sayReport(text, bad ? 'error' : savedNow.length ? 'success' : undefined, offered tourIds)`: the report is first stored in
    localStorage (`aryaan.billReports.v1.<empId>`, scope `'page'`) and dropped only once the model's reply carrying it is on screen.

`readSummaryText` (BS:195-207) layout: header, blank, groups `**✅ New (n)**`, `**↩ Duplicate (n)**`, `**🚫 Not relevant (n)**`
(empty groups left out; files in upload order), notes, blank, footer:
- 1 saved tour: `` `✅ Saved to your draft for **${tour}**. Nothing is submitted.` ``
- >1: `` `✅ Saved to your drafts for **${n} tours**. Nothing is submitted.` ``
- any `saveFailed`: `"⚠️ I couldn't save some of them just now. I'll try again the next time you open the assistant."`
- nothing saved and no saveFailed/alreadySaved: `'⚠️ **Nothing was saved.**'`

Grouping (`groupOf`): saved/saveFailed → New; alreadySaved/duplicate → Duplicate; no fate → New if something was found in it else
Not relevant; everything else → Not relevant. Per-file line (`fileLine`, BS:97-124), `what` = up to 3 findings joined by `; ` (+
`; and N more`), each finding `"Train, Delhi → Jaipur, **18 Jan 2026**"` or `"Hotel stay in Jaipur, **18–20 Jan 2026**"`
(modes: Flight, Train, Cab or bus, Own vehicle, Office vehicle, else Journey):
- unreadable `• **f**: ⚠️ couldn't be read`; nothingFound `• **f**: no journey or hotel stay found`;
  duplicate `• **f**: same file as one already uploaded, skipped`; rejected `• **f**: **not uploaded**: <reason>`;
  saved `• **f**: <what>` + (` → **<tour>**` when more than one tour was saved);
  alreadySaved `• **f**: <what> (already in your <tour> draft)`; saveFailed `• **f**: <what> (**not saved yet**)`;
  problem `• **f**: <what or 'hotel bill'> (**not added**: <reason>)`; unsure `• **f**: <what> (**couldn't tell which tour it's for**)`;
  noTour `• **f**: <what> (**doesn't match any of your approved tours**)`.

Settlement of OCR results after `autoSaveRead` (via `settleTaken()` in `takeResults` finally): every taken result whose `File` is not
somewhere in the chat's claim (or the dead holders) is **settled** — i.e. consumed for good (local claim record dropped; the server
already has it marked read). This includes `saved`, `alreadySaved`, `noTour`, `unsure`, `nothingFound`, `unreadable`, `problem`
files. Only `notSaved` (non-picked tours whose save failed / draft unreadable) are released. Files that went into the chat's
claim (picked tour) stay taken until that claim is saved, handed to the form, or the chat unmounts (then released).

### 4.9 Inside the form: `formRead(result, bills, read)` (CC:1558-1638)

1. `row = picked.row ?? tours[0]`; map; skipped as above; fates as above; `tour` label = `placeName → placeName` or `'this claim'`.
2. Stays with `stayBillProblem(r, picked.rules, [])` → `problem`, removed.
3. `triage = triageRows(mapped, tours, row)` (picked): rows not fitting this tour → `noTour`. Journeys/stays = all groups + neutral.
4. `current = syncFromForm()`; rows whose `journeyKey`/`stayKey` already exist → files `alreadySaved`; the rest are new.
5. `merged = mergeOcrRows(current, new)`; `added = {journeys, stays}` counts.
6. If something added: if tour disallows air and a row is air → its mode set to `'select'` and note
   `'⚠️ This tour has no air travel, so the travel mode of the flight was left empty.'`; `commit(next)` (writes to the form); new
   files → `saved`; `saved = form.saveDraft() !== false` (errors count as false); if saved → `settleFilesOf(next)`.
7. Update `readFilesRef`/`readHashesRef` as in §4.8.
8. `clearAskYN()`, `pending=null`, **`step='moreFiles'`**.
9. `bad = !saved || any unreadable|noTour|problem|rejected`. Say (as a kept report, scope `tour-<id>`):
   `readSummaryText({..., savedTours: [], notes, claim: {...added, saved}}) + '\n\nDo you have more tickets or bills to upload for this claim?'`
   tone error if bad, success if anything added, else none. With `claim` set, file wording is the in-claim variant
   (alreadySaved `(already in this claim, not added again)`, saveFailed `(**could not be added**)`, noTour
   `(**doesn't belong to this claim, not added**)`) and the footer is `claimFooter` (BS:212-216):
   `'Nothing new was added to this claim.'` or
   `` `✅ Added **${parts}** to this claim.${saved ? ' The draft is saved.' : ' ⚠️ The draft could not be saved yet; it is saved again when you leave the form.'}` ``.

### 4.10 Releasing / settling (CC:1213-1284)

- `settleTaken(all=false)`: no-op if nothing taken or (a read is in progress and not `all`). Collects every `File` in the chat claim
  and the (dead) holders; settles every taken id whose file is null or not among them (`settleOcrResults`: drop the localStorage
  claim record, forget the in-memory file). Also runs on every claim state change (CC:1241-1244).
- `settleFilesOf(value)`: settle ids whose file appears in `value` (after a save into the form, a form save, or hand-off).
- `giveBackFilesOf(value)`: release ids whose file appears in `value` (`POST /Ocr/Release`, unhide), and add to `discardedRef`.
- `dropTaken(keys)`: discard (`POST /Ocr/Cancel`) by file key — only used by dead `declineUpload`.
- Unmount (page navigation, Start over): every still-taken id is released (`giveBackOcrResults`).
- `ocrClaims.ts` also: heartbeat every 15 s in `aryaan.ocrTabs.v1`; on `pagehide` releases this tab's taken ids with a keepalive
  fetch; `recoverOcrClaims()` (called app-wide by `OcrReadyNotifier`) releases ids held by tabs silent for > 60 s.

### 4.11 Kept reports (KR, CC:452-481)

- Key `aryaan.billReports.v1.<empId|anon>` in localStorage; each `{id, scope ('page' | 'tour-<id>'), text, tone, tourIds, at}`;
  expires after 14 days.
- `sayReport(text, tone, tourIds)` stores then says it; `onShown` drops it.
- Said at the next greeting of the same scope, before anything else (§3.1 step 7, §3.3 step 4).

---

## 5. Claim building flow (Raise Claim page)

### 5.1 `askTour(s)` (CC:626-659)

- No tours → `step=start`, say `'You have no approved tours to claim for right now. Once a tour is approved, I can help you with its claim.'` (error).
- `{ranked, confident, hasClues} = matchTours(s, tours)`; `sorted` = tours best first.
- `hasClues && ranked[0].score === 0` → `showTourList(sorted, "I couldn't match what you've told me to any of your approved tours. Which tour is this claim for?")`.
- `guess = confident?.tour ?? (only one tour ? it : null)`. No guess → `showTourList(sorted, ranked[0]?.score > 0 ? "I couldn't tell for sure which tour these belong to. Which one is it?" : 'Which tour is this claim for?')`.
- Guess → `suggested=guess`, `ranked=sorted`, `step=confirmTour`, `pending=null`; `which = **${placeName(src)} → ${placeName(dst)}**${shortRange ? ` (${shortRange})` : ''}, tour #${id}`;
  say `confident ? `These bills look like they belong to ${which}. Is that right?` : `Is this claim for ${which}?``.
- `showTourList(list, text)` (CC:614-624): `suggested=null`, `ranked=list`, `step=pickTour`, `pending=null`, say `text`.

### 5.2 `chooseTour(row)` (CC:897-940)

1. `busy='Getting your tour details…'`; in parallel `loadTour(row)` and `loadSaved(row.tourId)`.
2. Load failed → `showTourList(ranked || all tours, "I couldn't load that tour just now. Please try again, or pick another.")`.
3. `chatRows` = non-empty journeys/stays of the chat claim; `vsSaved = saved ? compareWithSaved(saved, chatRows) : null`.
4. `picked = {row, tour, rules}`; `stagedRef=null`; if saved: `storedIdsRef` = saved row ids.
5. `next = enforceAirRule(saved && vsSaved ? addToSaved(saved, vsSaved.added) : chatState)`. `setPicked`, `suggested=null`,
   `onTourChange(row)`, `commit(next)`.
6. tell `` `Claim for ${row.source||'?'} → ${row.destination||'?'}.` `` tone success, data
   `[{label:'Tour', value:'#<id>'}, {label:'Dates', value: tourDates}]` (Dates only if known).
7. If saved: `lastSavedRef[id] = claimKey(asSeen(added>0 ? saved : next))`. `saveOfferedRef=false`.
8. If saved: tell `savedClaimText({claim: tourLabel(row), saved, added, alreadySaved, merged: next, tour: row})` (SC:216-232). If the
   chat added rows to it → `offerSave(row, carryOn)` and return.
9. If not saved: `offerDraftSave(row, chatState)`.
10. `carryOn()` = `justReadRef ? afterRead(state) : advance(state)` → in practice always `advance`.

`enforceAirRule(s)` (CC:866-871): if rules disallow air and any journey is air → say
`'One bill shows air travel, but this tour has no air leg, so I left the travel mode empty.'` (error) and set those modes to `'select'`.

`loadSaved(tourId, fresh=false)` (CC:741-749): outside and not fresh → cached `savedRef`; not fresh and the tour has no draft id →
`null`; else `onLoadDraft` → `claimStateFromDraft` → cached.

### 5.3 `advance(s)` — what to ask next (CC:662-719)

1. No picked tour → `askTour(s)`.
2. In form → `pending=null`, stop (never asks).
3. `missing = openMissing(s)`; if any: `step=details`, `pending=missing[0]`; say
   `missing[0].question + more + hint` where `more = missing.length>1 ? `\n\n(${missing.length-1} more after this one)` : ''` and
   `hint = hintedRef ? '' : '\n\n(If you would rather fill something on the form yourself, just say "skip".)'`; `hintedRef=true`.
4. `pending=null`. If stay not asked and no stay with a location or check-in → `askedRef.stay=true`, `step=stay`, say
   `'Did you stay anywhere overnight? You can upload the hotel bill or just tell me, like "guest house in Ahmedabad, 5th to 8th". Say "no" if you didn\'t.'`
5. If leave not asked and `!includeLeave` → `askedRef.leave=true`, `step=leave`, say
   `'Were you on leave on any day of the tour? For example, "leave on 7th". Say "no" if not.'`
6. If `!reviewPassedRef` → `runReview('check')` (§5.8). Stop.
7. `reviewPassedRef=false`; `step=confirm`; `heads = [...findFormOnlyIssues(s), ...findClaimIssues(s, rules)]`; `notes = findRelationIssues(s)`; say
   ```
   reviewText(s)
   + (notes ? "\n\n👀 Worth a check:\n" + "• n" lines : "")
   + (heads ? "\n\n⚠️ The form will flag these:\n" + "• h" lines + "\nYou can sort them out with me or on the form." : "")
   + (inForm ? "\n\nIf anything looks off, tell me and I'll fix it. Amounts and bill files are added in the form itself. When you're happy, just say \"submit\"."
             : "\n\nIf anything looks off, tell me and I'll fix it. When you're happy, say \"fill the form\".")
   ```
   tone success when no heads.

### 5.4 Details step

- Open question = `pending.question`. Chips: for slot `mode`: `✈️ Air` (omitted if tour disallows air), `🚆 Rail`,
  `🚌 Bus / taxi`, `🚗 Personal vehicle`, `🏢 Official vehicle` (each **sends a typed message** `air` / `rail` / `bus or taxi` /
  `personal vehicle` / `official vehicle` through `send`, i.e. through the model); `Skip this one` → `skipPending`;
  outside: `I'll fill the rest on the form` → `chooseForm` → since step ≠ gaps → **`doFill()` (opens the form immediately)**.
- `skipPending()` (CC:1987-1992): add slot to `skippedRef`, say `'Understood, left for the form.'`, `advance`.
- Typed answer → `edit` (§5.11) → `advance` if applied, else re-tell the pending question.
- understandContext includes `` `The open question fills ${section} row ${row}: ${label}.` ``.

### 5.5 Stay / leave steps

- stay chips: `📎 Yes, upload hotel bill` (file picker), `✍️ Yes, I'll type it` → say `'Tell me the city and dates, like "guest house in Ahmedabad, 5th to 8th".'`, `No stay` → `send('no')`.
- leave chips: `Yes, I was on leave` → say `'Which day or days? For example, "leave on 7th" or "leave 7th to 9th".'`, `No leave` → `send('no')`.
- Actions `no_stay` / `no_leave` → `advance(state)` (already marked asked). Typed details → `edit`.
- Uploading a hotel bill here: it is read later (minutes) and `autoSaveRead` puts the chat back to `step=start` (the stay question
  is not re-asked because `askedRef.stay` is already true; the next `advance` happens only on a later action).

### 5.6 `requestFill()` — "fill the form" (CC:1927-1960)

- In form (dead path): say `"Your form is already up to date as we go, so there's nothing more to fill."` + `showProgress()`.
- No claim rows → say `"There's nothing to fill yet. Add your tickets or hotel bills, or tell me about the journey first."`
- No tour → say `'Which tour is this for?'` + `askTour(s)`.
- `missing = findMissingFields(s)` (skips ignored), `formOnly = [...findFormOnlyIssues(s), ...findClaimIssues(s, rules)]`; none → `doFill()`.
- Else `step=validate`, `pending=null`, tell
  `` `Quick heads-up, the form will flag these when you submit:\n${lines}\n\n` `` + (missing ? `'Would you like to sort them out here with me, or open the form as it is?'` : `'Some of these can only be fixed on the form itself. Shall I open it now?'`)
  with `lines` = `describeMissing(s, missing)` (if any) then `• issue` lines.
- Action `fill_form` while in `validate` runs `doFill` directly (CC:2117).

### 5.7 `doFill()` hand-off (CC:1792-1808)

`busy='Opening your claim form…'`; `handed = asStored(state)`; `onFillForm(row, {tourId, state: handed})`. True → `settleFilesOf(handed)`
and tell `'The claim form was filled with the claim and opened. Ask the employee to review each section, attach anything missing (amounts and bill files go on the form), then submit.'` (success). False or throw →
say `"I couldn't open the form just now. Your claim is still here, so please try again."` (error). (On success the page navigates to
`/raise-claim-approver`; the Raise Claim chat unmounts, so this reply is normally never seen.) `chooseHere`/`chooseForm` (§5.9) also
lead here.

### 5.8 Review (pre-read-out check; CC:1810-1899)

- `reviewItems(s) = reviewClaim({state, tour, rules, formProblems: form.check?.() ?? [], leaveAnswered: acknowledged has 'askLeave' || (askedRef.leave && !includeLeave)})`;
  outside, items of kind `missingField, amountMissing, formOnly, askLeave, noJourneys` are removed (`LIST_PAGE_SKIPS`).
- `runReview(mode)`: `next = nextReviewStep(items, acknowledged, deferred)`:
  - `ask` → `reviewActiveRef=true`, `reviewItem=item`, `pending=null`, `step=review`, tell
    `reviewQuestionNote(item, open, askedAgain = previous item has same id)`, tone error if blocking, data `{kind, mustFix: blocking}`.
  - otherwise clear review item; `blocked` → `step=confirm`, tell `reviewBlockedNote(blocking)` (error).
  - done, outside → `reviewPassedRef=true`; `advance(s)` (read-out).
  - done, in form (dead): `submit` mode → `step=submit`, tell `reviewDoneNote(reviewText, true)`; else `step=confirm`, tell `reviewDoneNote(reviewText, false)`.
- `settleReview(how)`: no item → `runReview`. `upload` → tell `reviewSettledNote(item,'upload')` (step stays `review`; the bill is
  later read by `autoSaveRead`, which sets `step=start`). `remove` with section/row and section ≠ leave → `finishEdits(state, [{section,row,field:'remove',value:'yes'}], '')`.
  `confirm`/`later` → `settle(item, how, acknowledged, deferred)` + tell `reviewSettledNote(item, how)` then `runReview`.
  `fixed` (and `remove` that cannot be applied) → `runReview` directly.
- Chips in review: `📎 Upload it` (if resolutions include upload; primary); if not blocking and resolutions include confirm:
  label `"Yes, it's right"` (yes==='confirm') / `'No leave'` (kind askLeave) / `"I don't have it / not needed"` → confirm;
  `No, remove it` if `no==='remove'`; if blocking or resolutions include `form`: `"Done, I've fixed it"` → fixed, `"I'll fix it on the form"` → later.
- Typed: `yes_have_it`→upload, `yes_keep_it`/`no_keep_as_is`→confirm, `no_remove_it`→remove, `fixed_it`→fixed,
  `fix_on_form_later`→later, `edit` (with `newJourney`: all existing non-empty journeys are locked, CC:2090-2092).
- `deferredRef` is cleared only by `startSubmit` (dead), so on the Raise Claim page a blocking item answered "later" stays deferred
  for the life of the mount.

### 5.9 Gaps / validate choices (CC:1962-1985)

- `chooseHere()`: step `gaps` (dead) → `advance`; else if nothing missing → `doFill()`; else clear skips, reset hint, say
  `"Understood, let's sort them out."`, `advance`.
- `chooseForm()`: step `gaps` (dead) → mark all open missing as skipped, say `'Understood, those are left for the form. Checking the rest.'`,
  `advance`; else `doFill()`.

### 5.10 Saving drafts (CC:738-803, 942-996)

- `saveToDraft(row)`: `onSaveDraft(row, {tourId, state: asStored(state)}, {overwrite: savedRef.has(tourId)})`. On `'saved'`:
  `markSavedInChat`, `saveOfferedRef=false`, `lastSavedRef = claimKey(state)`, then re-read the draft fresh; if read: `storedIdsRef`
  from it, `lastSavedRef = claimKey(asSeen(saved))`, `commit(saved)` (the claim now holds server file references),
  `rememberDraft`.
  Note: `overwrite` is true only if the draft was loaded into `savedRef` earlier; otherwise an existing server draft yields `'exists'`.
- `offerDraftSave(row, s)`: outside, once, only if claim has rows and differs from last saved → tell
  `` `The bills are now in a claim for ${tourLabel(row)}, but it is not saved yet. In one short line, say it is not saved as a draft yet.` ``
- `offerSave(row, then)` (saved draft + new rows): `askYesNo(`Shall I save the new data into your saved claim for ${tourLabel(row)}? Nothing is submitted.`, yes, no, 'Yes, save it', 'Not now')`.
  Yes → `busy='Saving your draft…'`, `saveToDraft`; saved → say `'Saved to your claim. It stays in the Raise Claim tab until you submit it.'` (success);
  else `'I couldn\'t save it just now. Say "save draft" to try again.'` (error); then `then()`.
  No → say `'Okay, nothing is saved. Say "save draft" whenever you want to keep it.'`; `then()`.
- `saveDraftHere()` (action `save_draft`, chip `Save as draft`):
  - In form: `syncFromForm`, `busy='Saving your draft…'`, `form.saveDraft()`; `false` → error; success → `settleFilesOf(formClaim)`, say
    `'Your draft is saved. Nothing has been submitted yet, so you can carry on or come back later.'` (success); error → `"I couldn't save the draft just now. Please try again."` (error).
  - Outside, no rows → say `"There's nothing to save yet. Add your tickets or bills, or tell me about the trip first."`
  - No tour → say `'Which tour is this for?'` + `askTour(s)`.
  - `busy='Saving your draft…'`, `saveToDraft(row)`:
    - saved → say `` `Draft saved for ${tourLabel(row)}. It stays in the Raise Claim tab until you submit it.` `` (success), then
      `askYesNo('Would you like to open the claim now?', openSavedClaim(row), say 'Okay, we can keep working here.', 'Yes, open it', 'No, keep working here')`.
    - exists → say `"This tour has a saved draft I couldn't read just now, so I left it as it is."` (error), then
      `askYesNo('Would you like to open that claim now?', openSavedClaim(row), say 'Okay, I left it as it is.', 'Yes, open it', 'No, not now')`.
    - failed → say `"I couldn't save the draft just now. Please try again."` (error).
- `openSavedClaim(row)`: `busy='Opening the claim…'`; `onOpenClaim(row)` (navigates); false → `"I couldn't open the claim just now. Please try again."` (error).
- `localStorage aryaan.chatDrafts.v1.<empId|anon>` = `{tourId: ISO time}` of drafts saved by the chat; only used to tell the model
  `savedInChat` in draft summaries.

### 5.11 Typed edits: `finishEdits(base, edits, answer, lockedJourneys?)` (CC:2239-2257)

- `edit` action: `valid = readClaimEdits(edits)`; none → tell
  `'The employee seemed to give claim details, but none could be read. Ask them to give the detail again, more plainly.'`.
- `{state: next, applied, rejected} = applyEdits(base, valid, {...(rules ?? {allowAir:true}), lockedJourneys})`; `commit(next)`;
  `undo = base` if any applied.
- Note lines: `answer` (always `''` here), `` `Updated:\n• a…` ``, `` `I couldn't use these:\n• r…` ``, and if applied: new
  `findClaimIssues` not present before → `` `⚠️ Please check:\n• i…` ``. tell `lines.join('\n\n') || 'There was nothing to change there.'`,
  tone error if only rejections.
- Applied → `advance(next)` (outside: continues the question flow / asks the tour if none; in form: nothing). Not applied and a
  pending question → tell `pending.question` again.

### 5.12 Start over / undo (CC:2104-2113, 2210-2237)

- Action `start_over` (offered only when not already asking): if claim empty → tell
  `'The employee asked to start over, but the claim is still empty, so there is nothing to clear.'`; else `resetAskedRef=true`,
  `resetAsk=true`, tell `OUTSIDE_START_OVER_ASK` (AA:272-273). Open question becomes `'Shall I clear the claim and start over?'`.
  Chips: `Yes, start over` → `send('yes')`, `No, keep my claim` (primary) → `send('no')` (both go through the model).
- `confirm_start_over` → `resetClaim()`: `setResetAsk(false)` (**but `resetAskedRef` is not cleared — see §8**), clear skips/hint/
  doneUploading, `askedRef={stay:false,leave:false}`, `readFilesRef` emptied, `uploadChoiceRef=null`, `pending=null`, `bills=[]`,
  `commit(initialClaimState(), false, replace=true)` (in form: clears the form rows), `undo=before`, `step=start`; say
  `'Cleared. Attach your tickets and bills, or tell me about the trip. Say "undo" if you change your mind.'`. The tour stays picked.
- `keep_claim` → tell `'The employee chose to keep the claim. Nothing was cleared.'` (resetAsk is cleared by `interpret` because the
  chosen action is not confirm/start_over).
- `undo` / chip `↩ Undo last change` → `undoLast()`: no undo → say `"There's nothing to undo."`; else `commit(undo)`, `undo=null`,
  say `'Last change undone.'`, `advance(undo)`.
- Header "Start over" = full remount (§0.1).

### 5.13 Change tour (CC:998-1004)

`changeTour()`: `step=pickTour`, `suggested=null`, `ranked=all tours`, `pending=null`, say `'Okay, which tour is this claim for?'`.
`pickedRef` is **not** cleared. The next `chooseTour` merges the chat's claim rows into the new tour's saved draft (if any).

### 5.14 Progress / info

- `showProgress()` (action `show_progress`, chip `What's left?`): outside → `buildProgress({state, tour, rules, staysAnswered: hasStay || askedRef.stay, leaveAnswered: includeLeave || askedRef.leave, doneUploading})`; tell `progressText(report)`, tone success if `ready && heads.length===0`.
  In form (dead branch): review-items list note
  `` `What is left before the claim can be submitted (${n}). In one sentence, to be said word for word: "${missingInShort(items) || 'Some details need checking.'}"\n• [must fix] fact (confirmed as it is)|(to fix on the form)` `` or
  `` `Nothing is left: the tour is complete and the form's checks pass. ${reviewText(s)}` ``.
- `show_claim` → tell `` `The employee asked to see the claim. ${reviewText(syncFromForm())}` ``; chip `Show my claim` → say `reviewText(syncFromForm())`.
- `explain_da` → tell `` `The employee asked how DA is worked out. The rules:\n${DA_RULES}` ``; chip `How is DA worked out?` → tell `DA_RULES`.
- `tour_info` → tell `` `The claim is for ${tourLabel(row)} (tour #${id}).` `` or `'No tour is chosen yet. It is found from the bills, or the employee can say which tour it is.'`.
- `upload_help` → tell `'The employee wants to upload bills. Tell them to tap the 📎 button and attach PDFs or photos of their tickets and hotel bills (JPG or PNG), several at once if they like, up to 20 at a time. Reading takes a few minutes, and they do not have to wait: the bills are read on the server even if they close the chat or the browser.'` (does **not** open the file picker).
- `talk` → tell `inForm ? FORM_TALK_NOTE : TALK_NOTE` (AA:263-267).
- `ask_if_ready` (form only) → say `FORM_READY_REPLY` = `'I only read your bills into this claim. You can check it and submit it on the claim form.'`.
- `open_saved_claim {tourId}` → tour must be in `savedTours`, else `notListed`; `askYesNo(`Open the saved claim for ${tourLabel(t)} (tour #${id})?`, openSavedClaim(t), tell 'The employee did not want to open that claim. Ask which of the saved claims they want, or whether they want to upload more bills.', 'Yes, open it', 'No')`.
- `notListed()` → tell `'The tour the employee named is not one of the tours on offer. Ask them to pick one of the tours shown.'`.

### 5.15 More files (form; CC:1709-1726)

- `hasMoreFiles()`: `step=start`, say `'Okay, attach them whenever you are ready. Everything already in your claim stays as it is.'`, open file picker.
- `noMoreFiles()`: `doneUploadingRef=true`; in form: `step=start`, `pending=null`, say `'Okay. Everything I read is in your claim.'`.
  Outside branch (dead): say `'Continuing with what we have.'` + `announceGaps`.

### 5.16 Generic yes/no (`askYesNo`, CC:331-362)

`askYesNo(text, yes, no, yesLabel='Yes', noLabel='No')` stores the question and says `text`. Chips show `yesLabel` (primary) and
`noLabel`; tapping clears the question then runs the handler. Typed `yes`/`no` actions do the same. Any other chosen action closes
the question first (CC:2193). Attaching files closes it (CC:818). `offerUpload(text)` = askYesNo with
yes → say `'Okay, attach your tickets and bills whenever you are ready. You can also tell me about the trip.'` + file picker,
no → say `'Okay. Tell me about your trip whenever you are ready, or say "what\'s left?".'`, labels `'Yes, upload bills'` / `'No, not now'`
(only used from dead `saveBulk`/`declineBulk`).

---

## 6. Buttons (chips) — exact order and conditions (CC:2259-2461)

Hidden entirely when `noTours || toursLoading || narrator.writing || busy === 'Thinking…'`; disabled while `busy` or writing. When
the narrator is unavailable, the only chip is `Try again` (`narrator.retry`). Tapping any chip first calls `narrator.resume()`.
The list (concatenated in this order):

1. **reviewChips** (step review) — §5.8.
2. `Read again` (primary) if staged `bills` and not busy → `uploadBills`.
3. **askChips**: if `askYN`: `[yesLabel (primary), noLabel]`; else if `resetAsk`: `Yes, start over` (`send('yes')`), `No, keep my claim` (primary, `send('no')`);
   else step stay / leave / details chips (§5.4-5.5).
4. **tourChips**: confirmTour+suggested: `Yes, that's right` (primary) → `chooseTour(suggested)`, `No, choose another` → `changeTour`;
   pickTour: one chip per tour in `ranked` (or all `tours`), label `tourLabel(t)`, first primary → `chooseTour(t)`.
5. bulkChips (dead), 6. splitChips (dead).
7. **moreChips** (moreFiles): `Yes, I'll upload more` (primary) → `hasMoreFiles`; `No, that's everything` → `noMoreFiles`.
8. **startChips** (step start, no staged bills, no savedTours, no askYN): `✍️ I'll type the tour details` → say
   `'Tell me about your journey, for example "Left Noida 5 Oct 10:30 am by air, back on 8th by train".'`; (+ `Choose a tour myself` if `noMatch` — dead).
9. **savedChips** (step start, savedTours, no askYN): per tour a **link** chip whose label and href are `claimLink(t)` =
   `${origin}/raise-claim-approver?tourId=<id>&isDraft=true[&statusId=..][&totalDays=..]` (opens in a new tab, runs nothing in chat);
   `📎 Upload other bills` → file picker; `Not now` → `savedTours=[]`, say `'Okay. Upload bills or tell me about a trip whenever you are ready.'`.
10. uploadChoiceChips (dead): `Add to existing`, `Replace existing data`, `Not now`.
11. **choiceChips**: gaps (dead) `Let's fill them here` / `I'll do it on the form`; validate: `Sort them out here` (only if missing
    fields; primary) → `chooseHere`, `Open the form anyway` (primary when nothing missing) → `doFill`.
12. `Save as draft` (first position variant) when `unsaved && !askYN && step !== 'submit'`, where
    `unsaved = !inForm && picked && !inTourStep && hasClaimRows(state) && claimKey(state) !== lastSavedRef[tour]`.
13. step submit (dead): `Yes, submit` → `doSubmit`, `Not yet` → `send('no')`.
14. `Fill the form` (outside, not tour step, step not gaps/validate/start; primary in confirm) → `requestFill`.
15. If not tour step and (step ≠ start or inForm): outside `What's left?` → `showProgress`; always `Show my claim`.
16. `How is DA worked out?` if not tour step and picked.
17. `Save as draft` (second variant) if picked, outside, not unsaved, not tour step, step not start/gaps/validate.
18. `↩ Undo last change` if undo and not tour step.
19. `↩ Different tour` if picked, not tour step, outside → `changeTour`.
20. `⏹ Stop processing` if any in-flight docs.
21. `↻ Read the unreadable bill(s) again` if failedDocs.

`inTourStep = step ∈ {uploadChoice, confirmTour, pickTour, splitTours, bulkDrafts}`.

`buttonsRef` = all chip labels (empty when noTours/toursLoading); the narrator removes reply lines that just repeat a label or point
at buttons (`dropButtonLines`).

Composer placeholder: no tours `'No approved tours to claim for yet'`; loading `'Checking your tours…'`; pending `'Type your answer…'`;
else `'Type a detail, a correction or a question…'`. Typing indicator label: `busy` text, else `'Typing…'` while writing, else
`'Checking your tours…'`. Staged files show as pills; in-flight docs show `Processing your bill…` / `Processing N bills…` with names.

---

## 7. Typed messages, the model, and concurrency

### 7.1 `send(raw)` (CC:2159-2171)

1. `text = cleanMessage(raw)` (control/zero-width chars removed, whitespace collapsed, trimmed). Ignored if empty, `busy` or narrator writing.
2. Clear input; `narrator.resume()`; add a user bubble (text, or first 200 chars + `…` if > 1000 chars).
3. Length > `MAX_MESSAGE_CHARS (1000)` → `heard(first 300 chars + '…')`, tell
   `` `The employee's message is ${len} characters long, more than the 1000 that can be read, so it was not read. Ask them to send it in shorter pieces: one journey or stay at a time.` `` (error). Stop.
4. `interpret(text)`.

### 7.2 `interpret(text)` (CC:2173-2208)

1. `narrator.heard(text)` (the next reply answers this message); `current = syncFromForm()`; `actions = chatActions()` (built now,
   from the current state); `busy='Thinking…'`.
2. `understand({systemPrompt: UNDERSTAND_PROMPT (understandPrompt(CLAIM_EDIT_FORMAT)), message: text, actions: [{name, description, args}], openQuestion: openQuestion(), context: understandContext(current), recentChat: recentChatText()})` — fresh conversation, temp 0.1, max 600 tokens; returns `{action, args, edits}`; anything unparseable or not on offer → `talk`.
3. `chosen = actions[name] ?? actions[0]` (talk).
4. If a yes/no question is open and chosen ∉ {yes, no} → `clearAskYN()`. If start-over is being asked and chosen ∉ {confirm_start_over, start_over} → clear it.
5. `await chosen.run(args, edits)`.
6. Error: AbortError (window closed) → nothing. Otherwise `heard(undefined)` and `narrator.showUnavailable(() => interpret(text))`
   (fixed notice + Try again re-runs the whole interpretation). `finally` busy=null.

Actions offered at each moment = `outsideActions(chatState())` (AA:97-195); action → handler map in CC:2034-2131 (summarised in §5).
`chatState()` (CC:1995-2020): `{step, inForm, noTours, yesNo:{question,yesLabel,noLabel}?, resetAsk: resetAskedRef, suggestedTourId,
hasTour, hasBulk, hasSplit, hasMissing: findMissingFields(state).length>0, pendingQuestion, canUndo, hasSavedTours, review:{question,
blocking, resolutions, yes, no, newJourney, oddPlace: kind==='oddPlace'}?}`.

`openQuestion()` = `outsideOpenQuestion(chatState())` (AA:86-92): yes/no question text → `'Shall I clear the claim and start over?'` →
pending question (details) → review question (review) → `OUTSIDE_STEP_QUESTIONS[step]`.

`understandContext(s)` (CC:2135-2156), lines joined by `\n`, empty ones dropped:
```
Where the chat is: <inside the claim form | the Raise Claim page>. Tour: <#id label | not chosen yet>.
TOURS ON OFFER:\n#id: label …        (splitTours: split groups; confirmTour/pickTour: ranked or all tours)
SAVED CLAIMS:\n#id: label …          (savedTours)
The open question fills <section> row <row>: <label>.   (details only)
CURRENT CLAIM:\n<snapshot(s, picked.tour)>
```
`recentChatText()`: last 6 non-system messages as `Assistant: …` / `Employee: …`, whitespace collapsed, 280 chars each (includes
the `📎 name` bubbles).

### 7.3 The narrator (NAR)

- `tell`/`say` push events and bump a tick. A reply is written when **idle (`!busy`) and `active` and not already writing and not in
  the unavailable state**, and there are events or a heard message (NAR:175-179). Everything queued since the last reply plus the
  heard message becomes **one** model call and one chat bubble (streamed).
- Without a typed message: BRIEF prompt; user message =
  `NOTES:\n1. <note>\n   data: <compact JSON ≤3500 chars>\n2. …` + `QUESTION:\n<openQuestion>` (if any) +
  `'Write the message now: only what the NOTES say, at most two short sentences (plus a list if a NOTE has one).'`; no conversation
  id; temp 0.2; 400 tokens (AI:123-139).
- With a typed message: `NARRATOR_PROMPT = narratorPrompt(OUTSIDE_DOMAIN)`; `EMPLOYEE SAID`, `WHAT HAPPENED` (numbered notes + data),
  `OPEN QUESTION`, `ALREADY SHOWN (do not repeat)` (the last ≤4 app-only replies since the last typed turn; only when the message is
  not an acknowledgement), `CLAIM` (= `snapshot(state, picked.tour)`) and `PAGE` (= `pageInfo` + draft details) **only if the message
  is not an acknowledgement** (`isAcknowledgement`, AI:170-174), and a closing instruction (AI:154-156); conversation id carried
  across typed turns; temp 0.3; 700 tokens.
- `openQuestion`, buttons, claim and page are read **when the reply is written** (after the action ran), not when queued.
- Reply post-processing: `cleanReply` (strip echoed labels/fences/"Aryaan:"), `dropButtonLines(reply, buttons)`; an empty reply is
  an error. After a successful reply every event's `onShown` runs (drops kept reports).
- Failure: events (and heard text) kept in `failedRef`, fixed bot bubble `'The assistant is unavailable right now. Please try again.'`
  (system), chips replaced by `Try again`. Try again → write again. Any other chip/typed message calls `resume()` → the failed events
  go out with the next reply. AbortError (window closed mid-reply) → kept silently, written when the window reopens.
- Closing the window aborts the in-flight `understand` (`abortRef`) and the narrator's stream, and stops speech.

### 7.4 Concurrency rules (summary)

- `busy` serialises operations: composer + chips disabled; typed `send` ignored while busy or writing; the read-taking effect waits.
- The read effect never interrupts: an open yes/no, a start-over question, the tour steps (`WAIT_STEPS`), or the window closed.
  It **does** interrupt `details/stay/leave/review/confirm/validate` (outside → `step=start`; form → `moreFiles`).
- Exactly-once across tabs/devices: MarkAsRead; release on failure/unmount/pagehide; heartbeat recovery.
- Duplicate protection: by SHA-256 within the mount (read and staged/in-flight) and by row identity against saved drafts
  (`compareWithSaved`) / the form (`journeyKey`/`stayKey`).
- `onThinkingChange(!!busy)` feeds the launcher's status.

---

## 8. Dead / unreachable code and quirks

Dead (never reachable on any live path):
- `bulkRef`, `splitRef`, `stagedRef`, `heldRowsRef`, `uploadChoiceRef`, `bulkSavedRef` are never assigned a non-null value ⇒ steps
  `bulkDrafts`, `splitTours`, `uploadChoice`; functions `saveBulk`, `declineBulk`, `workOnOneTour`, `chooseSplit`, `chooseUpload`,
  `declineUpload`, `finishRead`, `dropTaken`, `droppedLines`; actions `save_drafts`, `dont_save`, `work_on_one_tour`,
  `add_to_claim`, `replace_claim_data`, `dont_add`; their chips; `offerUpload` (only called from `saveBulk`/`declineBulk`);
  `addBills`' split/bulk guard. (The background doc also calls these unreachable.)
- `justReadRef` is only set true in dead code ⇒ `chooseTour`'s `carryOn` always `advance`; `afterRead` is reachable only in form;
  outside, `moreFiles`, `hasMoreFiles`/`noMoreFiles` outside branch, `announceGaps`, step `gaps` and its chips/actions
  (`fill_on_form`, the gaps branches of `chooseHere`/`chooseForm`) are unreachable. (Inside the form `afterRead` and `noMoreFiles`
  return early, so `announceGaps` is dead everywhere, including its amount note `"I can't enter amounts, so please add these on the form:\n• …"`.)
- `noMatch` is never set true ⇒ chip `Choose a tour myself` dead.
- `check_and_submit` / `hold_submit` handlers exist but are never offered ⇒ `startSubmit`, step `submit`, `confirm_submit`,
  `dont_submit_yet`, `doSubmit` (`"I've sent it for submission…"`) and the `Yes, submit`/`Not yet` chips are dead. The Raise Claim
  assistant never submits.
- In form, `advance` returns early ⇒ `details/stay/leave/review/confirm` never happen in form; `runReview`'s form branches,
  `reviewActiveRef` effects, `showProgress`'s form branch (show_progress and "What's left?" are not offered in form), `requestFill`'s
  form branch (fill_form not offered in form) are dead.
- `_ownBusy` parameter, `savedDraftsGreetingNote` (savedClaim.ts) and `readOutcome` (billOutcome.ts) are unused.

Quirks to reproduce (or consciously fix) for parity:
1. **`resetClaim` does not clear `resetAskedRef`** (CC:2213 only calls `setResetAsk(false)`). After a confirmed start-over, the open
   question stays `'Shall I clear the claim and start over?'`, the model is still offered `confirm_start_over`/`keep_claim` and not
   `start_over`, and finished bill reads are blocked, until the next typed message picks any other action (which clears it). The
   reset chips are hidden (state is false).
2. `sayExactly` is not verbatim (see top note).
3. The no-drafts greeting has no "Hello, name" (only the word-for-word line).
4. The saved-claim link chip's **label is the full URL**.
5. "I'll fill the rest on the form" in `details` opens the claim form immediately (`chooseForm` → `doFill`).
6. A bill read while answering questions resets the step to `start` (outside) without re-asking.
7. `changeTour` keeps the old `picked` until a new tour is chosen; rows then merge into the new tour's saved draft.
8. After a `takeResults` error, ids go into `discardedRef`; this mount never retries them (despite the message saying "next time you
   open the assistant").
9. `saveToDraft` sends `overwrite` only when the draft is in `savedRef`; an un-loaded existing draft returns `'exists'`.
10. In form, a lost-file note is said twice (separate error event + inside the report notes).
11. Mode chips send plain words (`bus or taxi` …) through the model rather than setting the field directly.

---

## 9. Persistence (browser)

| Key | Store | Content | Why |
|---|---|---|---|
| `aryaan.billReports.v1.<empId\|anon>` | localStorage | kept bill reports (scope, text, tone, tourIds, at), 14-day expiry | report survives window/browser close; said at next greeting |
| `aryaan.chatDrafts.v1.<empId\|anon>` | localStorage | `{tourId: savedAtISO}` drafts saved by the chat | tells the model `savedInChat` |
| `aryaan.ocrClaims.v1.<emp>` | localStorage | taken OCR ids → `{tab, at}` | release results held by crashed/closed tabs |
| `aryaan.ocrTabs.v1` | localStorage | tab heartbeats | detect dead tabs (> 60 s) |
| `aryaan.ocrTags.v1` | localStorage | per-document tags | not used by this chat |
| `claimAssistant.theme`, `claimAssistant.language`, `claimAssistant.fullScreen`, `claimAssistant.launcherPosition`, `claimAssistant.greeted` | localStorage | UI prefs | UI only |
| `claimAssistant.introSeen` | sessionStorage | launcher intro shown | UI only |
| in-memory `localFiles` (ocrJobs), `setAssistantDraft` (draftHandoff) | JS memory | uploaded `File`s by documentId; the chat→form hand-off | files cannot go through URL/router state; lost on reload |

Everything else (claim, step, refs, conversation id) lives only in the mounted component and is lost on reload / Start over.
