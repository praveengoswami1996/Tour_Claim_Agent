# Deliberate deviations from the old Claim Assistant

The port copies the old behaviour, including its quirks, except where the old code loses or corrupts saved data
(decision 4 in `00_port_plan.md`). Each deviation is listed here with the reason.

| # | Old behaviour | Port | Why |
|---|---|---|---|
| 1 | Re-saving a draft rebuilt saved stays from parsed fields: own-arrangement / free stays became hotel stays (`claimStateFromDraft` bug) and saved stays lost their `InvoiceDate` | Saved rows are written back exactly as read | Data corruption |
| 2 | `RetainedTransportationFileIds` / `RetainedAccomodationFileIds` were never sent from the assistant (`hasResumedDraft` not passed) | Every saved file id still on its row is sent as retained | Saved files could be lost |

Further entries are added as the port finds them.
