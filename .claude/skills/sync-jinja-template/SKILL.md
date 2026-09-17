---
name: sync-jinja-template
description: Audit a Jinja2 LaTeX invoice template (the `\V{...}` / `%% if %%` style used by invoicez, e.g. `templates/jinja2/main.tex`) against the current invoicez `Variables` schema and fix schema drift in place — renamed/moved fields, dead `is defined` truthiness checks, fields superseded by newer ones. Use when a `.tex` invoice template hasn't been updated to match recent changes to invoicez's `Variables` model, or when asked to migrate/sync/update an invoice template to the current schema.
---

# sync-jinja-template

Bring a `invoicez` Jinja2 LaTeX invoice template back in sync with the
current `Variables` schema. This formalizes an audit that used to be done by
hand: `Variables` evolves (new e-invoicing fields get added, old ones get
renamed or moved into `latex_extras`), and templates silently drift out of
sync — Jinja2's default `Undefined` renders missing/misnamed variables as
blank text instead of erroring, and `model_dump()` always includes every
optional field (as `None`), so `%% if x is defined %%` checks are almost
never a useful test. Both failure modes are easy to miss just by reading the
`.tex` file, which is why this needs a deliberate pass against the schema
rather than a visual read-through.

This skill is normally invoked from a companion *data* repo (the one with
your invoices and templates), where `invoicez` is installed as an ordinary
dependency rather than checked out as source — see step 1 below for how to
locate its schema either way.

## Inputs

- The path to the `.tex` template to audit. If not given, ask which file —
  the usual location is `templates/jinja2/main.tex` in the current repo.

## Procedure

### 1. Load the ground truth

Locate the installed `invoicez` package — don't assume a relative
`src/invoicez/...` path exists, since this skill usually runs from a repo
where `invoicez` is a dependency, not checked-out source. Resolve it with:

    uv run python -c "import invoicez, os; print(os.path.dirname(invoicez.__file__))"

(plain `python -c "..."` if the repo doesn't use `uv`). If that fails
because the current directory *is* the `invoicez` source repo itself
(developing the tool rather than auditing a data repo's template), fall
back to the local `src/invoicez` path directly.

Read `<package path>/variables.py` in full (`Variables`, `Seller`,
`Company`, `InvoiceLine`, `Notices`, `InvoiceMeta`, `TVA`) — this is the
only source of truth for what fields exist and where. Also read
`<package path>/building/pdf/environment.py` for the exact Jinja delimiter
config (`\V{...}` for output, `%% ... %%` line statements, `\#{...}`
comments) and the registered filters (`camelcase`, `path_join`,
`display_number`, `display_date_range`).

### 2. Extract every reference

Read the target template and pull out every `\V{...}` expression and every
`%% if ... %%` / `%% elif ... %%` condition. For each one that indexes into
a known top-level variable (`user`, `company`, `latex_extras`, `notices`,
`tva`, `products`/`product`, or a bare top-level name like
`document_number`, `previous_document_number`, `reference`, `description`,
`date`), note the exact path (e.g. `user["place"]`) and where it appears.

### 3. Classify each reference

- **Valid** — the field exists at that path on the current schema. Leave it
  untouched.

- **Renamed/moved** — rewrite directly. Known renames (seed this list, but
  also search all models for a field literally named the same as an
  unresolved key and propose that as a candidate fix for anything not
  listed here):
  - `user["place"]` → `user["city"]` (redundant with `Seller.city`, which
    the same address the old `place` came from already yields).
  - top-level `cancels` / `invoice_number` / `ref` → `previous_document_number`
    / `document_number` / `reference`.
  - `product["date"]` (a single free-text string) →
    `product["start"] | display_date_range(product["end"])` (structured
    `InvoiceLine.start`/`.end` dates, formatted via the
    `display_date_range` filter).
  - `latex_extras["siret"]` → `user["siret"]` (SIRET is now a first-class,
    validated `Seller` field, not a free-form PDF extra).

- **Superseded — recommend deletion, not a rename** —
  `user["account_bank"]` / `user["rib"]` (or their `latex_extras`
  equivalents) have no new home: they're legacy French RIB-style bank
  details, fully covered by `user["iban"]` / `user["bic"]`, which are
  already present as validated `Seller` fields. Recommend deleting the
  corresponding table rows/lines (e.g. a `Domiciliation` / `RIB` line in a
  bank-details table) rather than proposing a rename, and say so explicitly
  in the report. Likewise `user["account_owner"]`/`latex_extras["account_owner"]`
  → `user["account_name"]` is a rename, not a deletion (it's the "Titulaire"
  line, not a dropped one).

- **Dead truthiness check** — any `%% if X is defined %%` (or `is not
  none`, `"x" in y`) where `X` is an optional field belonging to a model
  that gets passed through `Variables.model_dump()`. `model_dump()` always
  includes every field, `None` or not, so `is defined` is always true — a
  latent no-op bug. Rewrite to a plain truthiness check: `%% if X %%`.

- **Structural/judgment gap** — do not silently rewrite. Report clearly
  with the exact snippet, why it's ambiguous, and 1-2 candidate fixes,
  and leave the template untouched at that spot. Known examples to expect:
  - `tva` used to be an optional presence/absence toggle (`%% if tva is
    defined and tva is not none %%`, branching between a hardcoded "TVA
    20%" and "TVA non applicable, art 293 B du CGI"). It's now a required
    `{category, rate}` object — this needs a real branch on
    `tva["category"]` (e.g. `"S"` for standard rate vs. other codes for
    exempt/out-of-scope), not a rename. Propose the branch but don't
    silently guess the exact wording of the exemption text.
  - `user["tva_number"]` — the VAT id is now computed from `siren` inside
    the XML converter (`_compute_vat_id` in
    `building/xml/variables_to_bt_converter.py`), and nothing currently
    exposes that computed value back to the PDF template context. Report
    this as "no current PDF exposure for this data" — don't invent a field.
  - A hardcoded NDA number, or any reference to the `notices` block
    (`recouvrement`/`taux_retard`/`escompte`/`legal`/`general`) — as of
    this schema version, `notices` is populated and flows into the XML
    (`BG-1`/`BT-22`) but is **not** wired into any current PDF template at
    all. If the template hardcodes boilerplate legal text inline instead
    of referencing `notices`, report the mismatch (structured data exists
    but isn't used) rather than silently wiring it in, since that's a real
    design decision (do all five notices belong on the printed page, and
    in what order/wording?).

### 4. Apply the safe fixes

Edit the file directly for everything classified as renamed/moved,
superseded-for-deletion, or dead-truthiness. Do not touch anything
classified as a structural/judgment gap.

### 5. Verify

- If auditing invoicez's own `tests/test_cli/templates/jinja2/main.tex`,
  re-run `uv run pytest tests/test_cli.py`, `uv run ruff check src/invoicez
  tests`, `uv run ruff format --check src/invoicez tests`, and `uv run ty
  check src/invoicez tests` — this template is exercised by the test suite
  (including an actual `latexmk` compile), so these need to stay green.
- Otherwise (the usual case — a companion data repo with no such test
  harness), there's nothing to run automatically. Say so, and suggest doing
  a real `invoicez run` build to eyeball the rendered PDF once any missing
  e-invoicing config fields (`electronic_address`, etc.) are filled in.

### 6. Report

End with two clearly separated parts:
- **Fixed**: each rename/deletion/truthiness-fix applied, with a short
  before/after snippet.
- **Needs a decision**: each structural/judgment gap found, with enough
  context (the exact current text, why it's ambiguous, and 1-2 concrete
  candidate fixes) that it can be resolved in one pass rather than
  re-deriving the investigation from scratch.
