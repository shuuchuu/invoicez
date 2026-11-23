# SuperPDP: duplicates and unanswered requests

Notes for [issue #1](https://github.com/shuuchuu/invoicez/issues/1), where
`POST /invoices` timed out after 30s although SuperPDP created the invoice about 30s
later. SuperPDP was asked about it on 2026-11-23, and answered in part the same day. A
second email then gave them what identifies the request, and asked the questions below:
**its answer is awaited**.

## What we know

From SuperPDP, by email on 2026-11-23:

- Uniqueness is guaranteed, on the SIREN of the seller, the number and the date of the
  invoice. The same number with another date is thus not covered.
- Timing out is not normal. They ask for the `X-Request-ID` header of the response, to
  find the request in their logs. A request that timed out has none: the response never
  came. They were given the id, number, date and `created_at` of the invoice instead,
  with the estimated time of the request (not noted here, this repository is public).

From the OpenAPI specification (1.41.0.beta): nothing on idempotency or duplicates.
`POST /invoices` checks the recipient in the Peppol directory before answering, which
may be what took a minute. `GET /invoices` cannot be filtered by `external_id`.

From the sandbox, on 2026-11-23, with `scripts/probe_superpdp_duplicates.py`:

| Case | Result |
|---|---|
| Same number, other content or `external_id` (another date was not tried) | HTTP 400 `La facture est déjà existante (id ...)` |
| Same invoice, in overlapping requests | one HTTP 200, one HTTP 400 |
| Other number, same `external_id` | HTTP 200: `external_id` is not unique |
| Client gone before the answer (0.2s timeout) | created all the same, listed within about 1s |
| Wrong total | HTTP 200, then `fr:213` (not `api:invalid`), without `en_invoice` |
| Corrected, with the number of the one before | HTTP 400 `La facture est déjà existante` |
| `fr:212` (paid) reported twice | HTTP 200 twice, two events recorded |
| `GET /invoices/{id}?format=original` | the XML sent, byte for byte |
| `X-Request-ID` (looked at by hand, not by the script) | in every response looked at, errors included (undocumented); one set by the client is ignored |

Not known: what happens while a request stays blocked for a minute (those above took
about 1s, and the answer does not mention overlapping requests), and whether a number
can be used again after `api:invalid` (not obtained in the sandbox).

## What invoicez does

- A request creating something waits 120s. Without a usable answer (timeout, dropped
  connection, 5xx), it raises `OutcomeUnknownError`.
- `send` and `paid` then look for the invoice or the payment for 60s
  (`superpdp/recovery.py`), and say that the outcome is unknown if they do not find it.
- `send` finds invoices by number or `external_id`: those rejected have no readable
  number. It refuses those already sent, unless their latest event is `api:invalid`.
- `send` archives a PDF left in `sendbox/` if its XML is the original.
- When SuperPDP refuses an invoice as `déjà existante (id ...)`, `send_invoice` raises
  `DuplicateInvoiceError`. `send` carries on (status, archiving) if the original is the
  XML it was sending, and fails otherwise. Without an id in the message, it is a plain
  error. Checked on the sandbox on 2026-11-23, by sending again an invoice it had.
- Errors give the `X-Request-ID` of the response, if there is one
  (`superpdp/errors.py`).

## What to do with the answer

Update "What we know" with what they answer, whatever it is, then:

### 1. Does uniqueness also hold while the first request is still running?

- **Yes:** running `send` again is safe, as long as the number and the date stay the
  same. Say so in the message at the end of `sending._send` and in the README, instead of
  asking to wait and check `invoicez status`
  (`test_send_tells_when_the_outcome_is_unknown` matches the message). Looking for the
  invoice after a timeout is still worth it, to archive in one go.
- **No:** keep the warning. Consider a local marker, written before the request and
  removed once its outcome is known, making `send` refuse for a few minutes.
- **Either way:** the answer is about invoices. `paid` keeps its own warning, as the
  sandbox records a second `fr:212`.

### 2. Can a corrected invoice be sent with the same number and date after `api:invalid`? After `api:rejected` or `fr:213`?

The sandbox refuses it after `fr:213`. If the answer differs, ask again rather than
trusting either.

- **Yes for some:** make `statuses.was_transmitted` return False for exactly those. It
  is also what `paid` uses to tell that an invoice was not sent. Only `api:invalid`
  keeps the PDF in `sendbox/` at the end of `sending.send_document`: the other statuses
  that allow sending again need the same.
- **No for `api:invalid`:** do not just make `was_transmitted` return True, as `send`
  would then archive the PDF of the invalid invoice as already sent, and `paid` would
  accept it. Make `send` refuse with a message saying to use a new number, drop "unless
  SuperPDP found it invalid" from the README, and drop `known_ids` from `sending._send`.
  `test_send_again_invoices_found_invalid` says the opposite of what is then wanted.

### 3. Can the client set the id of the request?

- **Yes:** set it (a UUID) in `invoices._call`, and give it in the errors without a
  response too: those are the ones it is for. Mind that the request is made twice when
  the token is refused.
- **No:** nothing to change. The time of the request (UTC) in the message of
  `OutcomeUnknownError` would help SuperPDP find it.

### What they found about the timeout

- **A cause or a fix on their side:** update the comment of
  `invoices.CREATE_TIMEOUT_SECONDS` ("has been seen taking a minute"). Keep 120s unless
  they advise another value, then set `recovery.RECOVERY_WAIT_SECONDS` to go with it.
- **Nothing found:** the times given are estimates (the request started about 60s before
  `created_at`), and the invoice was identified by matching the times of the events. Ask
  whoever opened the issue to confirm it. If it happens again, note the time and the
  invoice at once.

## Asked in the first email, not answered nor asked again

- Is there a stable error code for duplicates, with the id in a field? It would replace
  parsing the message (`invoices._DUPLICATE_MESSAGE`).
- Can `GET /invoices` be filtered by `external_id`? `sending._send` would then not list
  everything.
- Which client timeout is advised?

Never asked: can a second `fr:212` be refused? Until then, `paid` is the command a
hasty retry can make report twice.

## Known and not fixed

- `status`, `paid` and `ledger` find invoices by number only, so `status` shows those
  SuperPDP rejected (without `en_invoice`) as not sent. `status` also reads `issue_date`
  from `en_invoice`, so falling back on `external_id` in `invoice_number` is not enough.
- The refusal of duplicates does not cover another date: `send` compares the number
  only, but cannot see an invoice SuperPDP does not list yet. Changing the date between
  two attempts could thus send the invoice twice.
