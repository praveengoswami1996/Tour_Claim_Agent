# Running the old chat components on the server: runtime design

The old `ClaimChatbot.tsx` and `WithinClaimChatbot.tsx` are React components: state in `useState`/`useRef`, work in
event handlers, and **effects** that fire when state changes (the greeting, going through read bills, the narrator
writing a reply when idle). The port keeps their logic line for line and replaces React with a small runtime.
The HTTP contract is `Project-Context/frontend/agent_0.4_frontend_requirements.md`.

## 1. One chat = one `ChatSession` object

- A conversation (`workflow_id`, mode `raise_page` / `raise_form` / `within_form`) owns one session object holding
  everything the component held: every `useState` value and every `useRef` (`step`, `pending`, `picked`,
  `askYN`, `resetAsk`, `savedTours`, `bills` (staged files), `sentHashes`, `readHashes`, `failedDocs`, `undo`,
  `lastSaved`, the claim state, the narrator's queue, `heard`, `failed`, conversation id, `buttons`, ...).
- Plain data only (dicts, lists, strings), so it is serialised as JSON after every request and stored per workflow
  (SQLite now, PostgreSQL later). Callbacks in old state (`askYN.yes()`, `askYN.no()`) become **named
  continuations**: `{"yes": ["readPlan", {...args}], "no": ["keepTours", {}]}` resolved through a table of handler
  functions, so they survive serialisation.
- Staged file bytes (uploaded but not yet sent to OCR, e.g. offline or a failed send, "Read again") are stored next
  to the session (file store keyed by workflow and file key) and deleted when sent or removed.

## 2. A request = a turn

Each HTTP call (`open`, `messages`, `actions`, `attachments`, `poll`, `retry`, `bridge`) is one turn:

1. Load the session; set `active` from the request; replace the form snapshot (`form`) when the mode has a form
   (the old `syncFromForm()`).
2. Run the entry point: the old handler for that input (`send(text)`, the chip's `onClick`, `addBills(files)`,
   `narrator.retry`, ...), ported as an `async` method.
3. **Settle effects** (§3) until nothing more fires.
4. Narrate (§4) if the narrator's conditions hold.
5. Emit the derived UI state: `chips`, `composer`, `status`, `bills` (§5).
6. Save the session.

Per workflow, turns run one at a time (a lock); per employee, OCR taking runs one at a time.

## 3. Effects

Each old `useEffect` that does work becomes a function `effect_<name>(session) -> bool` with **the same guard
conditions** as the dependency check and early returns of the original (e.g. the read effect: `active && !busy &&
!narrator.writing && !taking && !toursLoading && !noTours && greeted && ...`). After the entry point the runtime calls
the effects in the order they are declared in the component, repeatedly, until none fires (each fires at most once per
turn unless the original could loop). Effects that only mirror state into the UI (scrolling, focus) are dropped.

`busy` in the old code blocked other work while an `await` was pending. On the server an operation finishes within
the turn, so `busy` is set during the handler and cleared before effects settle; its label is reported in
`status.busy` while a turn is running (the frontend shows the typing indicator).

## 4. Narrator

`useNarrator` ported as a session part: `tell`/`say` queue events; `heard(text)` records the typed message; a reply is
written when idle, `active`, not in the unavailable state and there is something to say — exactly the old trigger.
Everything queued becomes one model call, streamed to the client as `reply_start` / `reply_delta` / `reply`. Failure →
`system_notice` and the "Try again" chip; queued events are kept (`failed`) as before. With `active = false` nothing is
written; the queue waits (the old window-closed behaviour) and `status.held = true`. `onShown` callbacks become named
continuations too (e.g. drop a kept report).

## 5. Derived UI state

The old render computed the chips (Spec A §6, Spec D), the composer placeholder, the typing label, header status and
launcher notice from state. These become pure functions of the session (`chips(session)`, `composer(session)`,
`status(session)`, `launcher(session)`); chip `onClick`s become chip ids mapped to handlers. `buttonsRef` (for
`dropButtonLines`) is the label list of the chips computed at reply time.

## 6. Bridge calls (form modes)

Form operations the old code awaited (`form.write`, `form.saveDraft()`, `form.check()`, `form.submit()`,
`form.info()`, `onFillForm`) are `await bridge.call(method, args)`. The turn runs as an `asyncio` task: when it reaches
a bridge call, the runtime sends `bridge_call {id, method, args}` to the client, ends that HTTP response, and the task
waits on a future. `POST /bridge/{id}` resolves the future (and replaces the form snapshot), and its HTTP response
streams the rest of the turn until the next bridge call or the end. If no answer comes within 60 s the call fails as
the old code would on an error (the form threw). `form.read()` needs no call: it returns the snapshot.

## 7. OCR on the server

`src/lib/ocr/*` ported into the session runtime with the old semantics: upload with the old references, polling via
`poll` turns (the frontend calls every 20 s / 60 s), `MarkAsRead` taking, `Release` on failure or when the session is
abandoned, and settling. Browser leases and heartbeats are replaced by the per-employee lock and by releasing what an
abandoned or expired session still holds (a session not touched for 15 minutes counts as a closed tab).

## 8. What gets ported where

| Old | Port |
|---|---|
| `ClaimChatbot.tsx` (both modes) | `claimchat/flows/raise_claim.py` (`RaiseClaimChat`) |
| `WithinClaimChatbot.tsx` | `claimchat/flows/within.py` (`WithinChat`) |
| `useNarrator.ts` | `claimchat/runtime/narrator.py` |
| `ClaimAssistantLauncher.tsx`, `WithinClaimAssistantLauncher.tsx` (status texts) | `status()` / `launcher()` in the flows |
| `src/lib/ocr/*` | `claimchat/runtime/ocr.py` |
| page callbacks (`RaiseClaim.tsx`: `loadTour`, `onSaveDraft`, `onLoadDraft`, `onFillForm`, `onOpenClaim`, draft list) | `claimchat/runtime/page.py` (server calls to the Tour API; `onFillForm` → bridge `openForm`) |
| HTTP | `api/chat_routes.py` (`/v1/workflows` with `mode`, `/open`, `/actions`, `/poll`, `/retry`, `/bridge/{id}`) |
