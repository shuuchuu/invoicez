# Open points

What is left to do after 6.4.0 and the SIREN check, as of 2026-11-24, with a plan for
each. Remove a point once it is done. Names, invoices and emails are left out: this
repository is public.

| Point | Waits for |
|---|---|
| [1. Second answer about duplicates](#1-second-answer-about-duplicates) | SuperPDP |
| [2. Flow 1 of invoices without the buyer's SIREN](#2-flow-1-of-invoices-without-the-buyers-siren) | SuperPDP |
| [3. Retrying too soon](#3-retrying-too-soon) | nothing, but 1 may make part of it useless |
| [4. VAT-exempt invoices (issue #3)](#4-vat-exempt-invoices-issue-3) | a choice, and SuperPDP |
| [5. Attachments (issue #2)](#5-attachments-issue-2) | the code the client expects |
| [6. SIRENs missing from the directory](#6-sirens-missing-from-the-directory) | nothing |
| [7. Rejected invoices without any identifier](#7-rejected-invoices-without-any-identifier) | nothing |

Suggested order: 4 (warning only) and 3, then 5, then 6 and 7. Points 1 and 2 go first
whenever SuperPDP answers.

## 1. Second answer about duplicates

Overlapping requests, sending again after a rejection, id of the request, cause of the
timeout: see "What to do with the answer" in
[superpdp-duplicates.md](superpdp-duplicates.md).

## 2. Flow 1 of invoices without the buyer's SIREN

invoicez up to 6.1.0 wrote no BT-47 (factur-x 6.8 left it out). SuperPDP took these
invoices, and the administration (PPF) rejected the flow 1 of one of them: code 251,
`REJ_COH`, G1.63. Another was rejected without its reason having been read, and the
others had no answer yet. Invoices sent by 6.2.0 or later have it, and one was accepted.
`send` now refuses an XML without it.

Nothing to send again nor to cancel until SuperPDP and the accountant have answered:
buyers may have the invoices, and SuperPDP refuses the same number and date.

To ask SuperPDP, then:

| Question | Then |
|---|---|
| Can flow 1 be sent again with the SIREN, without the buyer getting the invoice again? | If not, follow their procedure, checked with the accountant. `invoicez cancel` writes the credit note if one is needed. |
| Were those without an answer rejected? A rejection showed up as an event more than two days after the date in its message. | If events can come that late, `status` cannot tell that flow 1 went well. Consider showing, with `--all`, the invoices with `ppf:flow-1` and neither `ppf:flow-1-response-ok` nor a rejection after some days. |
| Must payments (`fr:212`) reported on them be reported again? | By hand if so: `paid` skips invoices already paid. |
| Why are invoices breaking BR-FR-11 validated (`fr:200`)? | Keep `sending._check_sirens` in any case. |
| Can the API give the reason of PPF rejections? | Show it in `statuses.describe`, which only reads `details`. Point 4 needs it too. |

## 3. Retrying too soon

After an unknown outcome, running the command again before SuperPDP shows the first
request can do it twice:

- `paid`: the sandbox records a second `fr:212`.
- `send`: only if the date of the invoice changed in between, as SuperPDP guarantees
  the uniqueness of seller, number and date. Whether it does while the first request is
  still running is asked in point 1.

Plan, using the log rather than a marker of its own:

1. In `superpdp/http.py`, a function returning the recent requests of unknown outcome:
   without an answer, with an error, or with a status of 500 or more, in the last 10
   minutes (to adjust to what SuperPDP says).
2. `sending._send` refuses to send if one is a `POST /invoices` with the same
   `external_id`, unless SuperPDP lists the invoice. `paid._report` does the same with a
   `POST /invoice_events` with the same `invoice_id` and `status_code`. Both say when to
   try again.
3. Connection timeouts are not unknown outcomes (see `invoices._create`).
4. Tests: refused within the delay, accepted after it, accepted without a log.

Limits: it does not see other machines, and a log that cannot be written protects from
nothing.

Independently, ask SuperPDP whether a second `fr:212` can be refused.

## 4. VAT-exempt invoices (issue #3)

The PPF rejects the flow 1 of invoices exempt under article 261 (G2.32). According to
SuperPDP, as reported in the issue, this has no consequence. Hiding the event, as the
issue first suggests, would hide other reasons too, as the API does not give them. Worst,
a taxable invoice wrongly marked `E` is rejected with the same rule: the rejection may be
the only sign.

Tried on the sandbox on 2026-11-23: nothing takes an invoice out of `B2B`. A `BAR` note
with `OUTOFSCOPE` is read but has no effect, and `processing_rule=OutOfScope` is refused
as not supported yet. The sandbox shows no `ppf:*` event, so flow 1 itself was not seen,
and production was not tried. Do not use `B2C` or `B2BInt` to get around it: it would be
a false declaration.

1. Ask SuperPDP: will `OutOfScope` (delivered to the buyer, without flow 1) be supported,
   will the `BAR` note be used, and can the API give the reason of rejections?
2. Meanwhile, in `status`, for invoices with only category `E` and a `VATEX-FR-CGI261*`
   code or only category `O`: keep the state the invoice would have without
   `ppf:flow-1-rejected`, show next to it that the rejection is expected if the
   exemption is right, and do not exit with 1. Other events stay problems, as does this
   one for other invoices. `_raise_problems` in `sending` follows the same rule.
   `vat_break_down` was found empty in the listing: read the VAT categories from the
   document or from the invoice fetched by id.
3. Show the exemption in the preview of `send`, where a wrong `E` can still be caught.
4. Once `OutOfScope` works: send exempt invoices with it, and treat any flow 1 rejection
   of these as a problem again.

## 5. Attachments (issue #2)

factur-x 7.9 writes BG-24 and SuperPDP reads it, according to the issue. `send_invoice`
posts the XML only, so the files have to be in it.

1. Ask the client which BT-123 code they expect (`PJA` or `DOCUMENT_ANNEXE`?); they
   offered to look at test sends.
2. `attachments` in `Variables`: a list of `path` (relative to the repository) and
   `code`, checked early (file, MIME type, code among those of BR-FR-17, at most one
   `LISIBLE`). Credit notes must not inherit them, as with `payment_due_date`.
3. Convert to BG-24, with a BT-122 that only depends on the document: `_is_as_sent`
   compares XMLs, so building twice must give the same one.
4. Show them in the preview of `send`, with a warning above some total size. Ask
   SuperPDP for its limit, and check that 120s is enough to post it.
5. Try on the sandbox, then with the client, before releasing.
6. Update the README and the `sync-jinja-template` skill if templates can use them.

## 6. SIRENs missing from the directory

G1.63 also wants both SIRENs to exist in the directory, which `sending._check_sirens`
does not check. `GET /french_directory/companies` takes a SIREN (`number`, OpenAPI
1.41.0.beta). Try it, then warn in the preview of `send` if the buyer is not found. Only
a warning, as it is not known whether this directory is the one the PPF checks, and the
request may fail.

## 7. Rejected invoices without any identifier

`status` cannot tell which document a rejected invoice is when it has neither
`en_invoice` nor `external_id` (sent before 6.0.0, or by another tool). The number is in
`GET /invoices/{id}?format=original`, XML or PDF: read it there, only for those, at one
more request each. Only worth it if it happens again, as invoicez now always sends an
`external_id`.
