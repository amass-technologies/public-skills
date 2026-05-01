# Amass Skills

Agent skills for working with the [Amass](https://platform.amass.tech) *Core API platform.

## Install

Using [`npx skills`](https://skills.sh):

```bash
npx skills add amass-technologies/public-skills
```

This installs every skill under `skills/` in this repo.

## Skills in this repo

| Skill | What it does |
| --- | --- |
| [`amass-api`](skills/amass-api) | Search, look up, and cross-link records in BioMedCore (biomedical publications) and TrialCore (clinical trials) via the Amass platform API. |

## Setup

Each skill needs an Amass API key. Get one at <https://platform.amass.tech>, then export it in your shell:

```bash
export AMASS_API_KEY=amass_…   # add to ~/.zshrc or ~/.bashrc to persist
```
