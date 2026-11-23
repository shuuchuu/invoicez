# SuperPDP: duplicates and unanswered requests

Notes for [issue #1](https://github.com/shuuchuu/invoicez/issues/1), where
`POST /invoices` timed out after 30s although SuperPDP created the invoice about 30s
later. They are to be updated with SuperPDP's answer (asked on 2026-11-23).

## What we know

From the OpenAPI specification (1.41.0.beta): nothing on idempotency or duplicates.
`POST /invoices` checks the recipient in the Peppol directory before answering, which
may be what took a minute. `GET /invoices` cannot be filtered by `external_id`.

From the sandbox, on 2026-11-23, with `scripts/probe_superpdp_duplicates.py`:

| Case | Result |
|---|---|
| Same number, whatever the content or `external_id` | HTTP 400 `La facture est déjà existante (id ...)` |
| Same invoice, in overlapping requests | one HTTP 200, one HTTP 400 |
| Other number, same `external_id` | HTTP 200: `external_id` is not unique |
| Client gone before the answer (0.2s timeout) | created all the same, listed within about 1s |
| Wrong total | HTTP 200, then `fr:213` (not `api:invalid`), without `en_invoice` |
| Corrected, with the number of the one before | HTTP 400 `La facture est déjà existante` |
| `fr:212` (paid) reported twice | HTTP 200 twice, two events recorded |
| `GET /invoices/{id}?format=original` | the XML sent, byte for byte |

Not known: whether production does the same, what happens while a request stays
blocked for a minute (those above took about 1s), what makes an invoice unique besides
its number (year, type, buyer), and whether a number can be used again after
`api:invalid` (not obtained in the sandbox).

## What invoicez does

- A request creating something waits 120s. Without a usable answer (timeout, dropped
  connection, 5xx), it raises `OutcomeUnknownError`.
- `send` and `paid` then look for the invoice or the payment for 60s
  (`superpdp/recovery.py`), and say that the outcome is unknown if they do not find it.
- `send` finds invoices by number or `external_id`: those rejected have no readable
  number. It refuses those already sent, unless their latest event is `api:invalid`.
- `send` archives a PDF left in `sendbox/` if its XML is the original.

## What to do with the answer

| Question asked | If yes | If no |
|---|---|---|
| 1. Is the refusal of duplicates guaranteed in production, even while the first request is running? | Soften the warning of `sending._send` and the README. On that HTTP 400, take the id and carry on with the status and the archiving, after comparing with the original. | Keep the warning. Consider a local "pending" marker. |
| 2. What is unique: seller and number, or also the year, the type or the buyer? | Check that `sending._is_invoice` compares the same things. | |
| 3. Can a number be sent again after `api:invalid`? After `api:rejected` or `fr:213`? | Make `statuses.was_transmitted` accept exactly the statuses given. | Make it refuse them, drop "unless SuperPDP found it invalid" from the README, and say to use a new number. |
| 4. Is there a stable error code, with the id of the existing invoice? | Use it for 1 rather than parsing the message. | Parse the message, or look the invoice up. |
| 5. Which client timeout is advised? | Set `invoices.CREATE_TIMEOUT_SECONDS`, and `recovery.RECOVERY_WAIT_SECONDS` to go with it. | |
| 6. Can `GET /invoices` be filtered by `external_id`? | Use it in `sending._send` rather than listing everything. | |

Not asked yet: can a second `fr:212` be refused? Until then, `paid` is the command a
hasty retry can make report twice.

Known and not fixed: `status`, `paid` and `ledger` find invoices by number only, so
`status` shows those SuperPDP rejected (without `en_invoice`) as not sent. `status` also
reads `issue_date` from `en_invoice`, so falling back on `external_id` in
`invoice_number` is not enough.
