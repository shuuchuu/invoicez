# `invoicez`

[![PyPI Project](https://img.shields.io/pypi/v/invoicez?style=for-the-badge)](https://pypi.org/project/invoicez/)

Tool to handle invoices. It is currently not meant to be usable directly by people finding about the package on GitHub. Please open an issue if you want more details or want to discuss this solution.

## Installation

With `pip`:

    pip install invoicez

## Directory Structure

`invoicez` works with big assumptions on the directory structure of your presentation repository. Among those assumptions:

- your directory should be a git repository
- it should contain a jinja2 LaTeX invoice template in the `jinja2` directory, with a specific name (`main.tex.jinja2`)
- your invoice folders should be contained in an organization/company folder. This is meant to avoid repeating the company details all over the place

```console
root (git repository)
├── global-config.yml
├── jinja2
│   ├── main.tex.jinja2
├── assets
│   ├── img
│   │   ├── logo.png
│   │   └── signature.jpg
├── company1
│   └── company-config.yml
│       ├── invoice1.yml
│       └── invoice2.yml
└── company2
    └── company-config.yml
        ├── invoice1.yml
        └── invoice2.yml
```

## Configuration

`invoicez` uses two configuration files to avoid repetition, one for your details and global values, one for the details of the company you're writing the invoice for.

### Configuration merging

The company config is merged into the global config, you can use that fact to override global values for a specific company.

### Using the configuration values

The values obtained from the merged configurations can be used directly in Jinja2 templates, or in LaTeX after a conversion from snake case to camel case: if the configuration contains the key `user_email`, it will be defined as the `\UserEmail` command in LaTeX.

## Billing trainings from Google Calendar

`invoicez gcal` drafts invoices from the training sessions of a Google Calendar,
and tells which sessions are billed. It only reads the calendar.

### Setup

1. Install the extra: `pip install "invoicez[gcal]"`.
2. In Google Cloud, enable the Google Calendar API and create an OAuth client of
   type *Desktop app*. Save its JSON as `gcal-client-secrets.json` in the
   invoicez user config directory (`~/.config/invoicez/` on Linux). The first
   command opens a browser for consent; the token is cached next to it, readable
   by you only.
3. Add a `gcal` section to `settings.yml`. `invoicez migrate-config OLD --gcal`
   prints one from an old-format `config.yml`.

```yaml
gcal:
  calendar_id: someone@example.com  # from `invoicez gcal calendars`
  # Engagements ending before this date are never drafted.
  start_date: 2020-01-01
  # Verbose regex: groups continuation, company, training, place, and optionally
  # extra and tentative. Titles are matched whole.
  title_pattern: |
    \s*
    (?P<tentative> \? \s+ )?
    (?P<continuation> -> \s+ )?
    (?P<company> .*? ) \s+ - \s+
    (?P<training> .*? ) \s+ - \s+
    (?P<place> .*? )
    (?: \s+ - \s+ (?P<extra> .*? ) )?
    \s*
  templates:  # Jinja: company, training, days, start, end, n, reference
    description: Facture pour {{ days }} jours de formation « {{ training.name }} ».
    day_line: Journée de formation
    half_day_line: Journée de formation
    preparation_line: Journée de préparation
  rates:
    standard: {day: 740, preparation_day: 500}
  trainings:  # company key of variables.yml -> training in titles -> training
    orsys:
      bda: {name: Big Data Analytics, rate: standard}
```

Optional keys: `unit` (`DAY`), `output_dir` (`gcal-ymls/{year}`) and
`durations` (`half_day_hours: [2.5, 4]`, `day_hours: [6, 9]`). Numbers print
the French way in templates (`2,5`). Documents hold LaTeX, so `&`, `%`, `$`,
`#` and `_` must be escaped (`\&`) in names and references.

### Calendar conventions

- A title such as `Orsys - BDA - CV - Intra Acme` is a session; company and
  training are looked up in `settings.yml` ignoring case.
- `-> Orsys - BDA - CV - Intra Acme` continues the latest earlier session with the
  same title: together they make one engagement, billed by one invoice.
- `? Orsys - BDA - CV` is an unconfirmed booking, never billed.
- All-day events count their days; timed events of 2.5 to 4 hours are
  half-days, of 6 to 9 hours days; others are reported, not billed.
- The description may hold YAML: `ref` (the client's reference, also used in
  the file name), `preparation_days`, and `invoice` (a number billed by hand).

### Commands

- `invoicez gcal calendars`: list your calendars and their ids.
- `invoicez gcal status [--all]`: engagements needing action (unbilled,
  changed since billing, billed twice, cancelled) and upcoming ones, the
  problems found in the calendar, and invoices whose events disappeared. Exits
  with 1 on an error.
- `invoicez gcal bill [ID...] [--rebill] [--build]`: drafts the unbilled
  engagements one at a time, after a preview and a confirmation; numbers are
  taken at confirmation. `--build` also builds the PDF and XML.
- `invoicez gcal link [--write] [--pair ID=NUMBER...]`: links invoices made
  before by hand to their engagements, by the days they bill, so that
  `start_date` can go back to the first one. Dry run without `--write`.

Every command takes `--events FILE` to read events from a JSON file instead of
Google Calendar. `scripts/dump_gcal_fixture.py` writes such a file, anonymised,
which is how `tests/gcal/assets/events.json` was made.

## Usage

See the `--help` flag of the `invoicez` command line tool.
