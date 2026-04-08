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

## Usage

See the `--help` flag of the `invoicez` command line tool.
