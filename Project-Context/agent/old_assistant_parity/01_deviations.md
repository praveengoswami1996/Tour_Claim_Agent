# Deliberate deviations from the old Claim Assistant

The port copies the old behaviour, including its quirks, except where the old code loses or corrupts saved data
(decision 4 in `00_port_plan.md`). Each deviation is listed here with the reason.

| # | Old behaviour | Port | Why |
|---|---|---|---|
| 1 | Re-saving a draft rebuilt saved stays from parsed fields: own-arrangement / free stays became hotel stays (`claimStateFromDraft` bug) and saved stays lost their `InvoiceDate` | Saved rows are written back exactly as read | Data corruption |
| 2 | `RetainedTransportationFileIds` / `RetainedAccomodationFileIds` were never sent from the assistant (`hasResumedDraft` not passed) | Every saved file id still on its row is sent as retained | Saved files could be lost |

Further entries are added as the port finds them.

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
