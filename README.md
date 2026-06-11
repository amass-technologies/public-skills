# Amass Skills

Agent skills for working with the [Amass](https://platform.amass.tech) *Core API platform — BiomedCore (biomedical publications), TrialCore (clinical trials), DrugCore (drugs & molecules), and RegulatoryCore (FDA & EMA drug authorizations).

## Install

Using [`npx skills`](https://skills.sh):

```bash
npx skills add amass-technologies/public-skills
```

This installs every skill under `skills/` in this repo.

## Skills in this repo

| Skill | What it does |
| --- | --- |
| [`amass-api`](skills/amass-api) | Search, look up, and cross-link records across all four Amass Cores — BiomedCore (publications), TrialCore (clinical trials), DrugCore (drugs/molecules), and RegulatoryCore (FDA/EMA authorizations, including parsed label/SmPC/review/EPAR text) — via the Amass platform API or MCP. Bundles a full API reference (`AMASS.md`). |
| [`amass-biomedical-evidence-scout`](skills/amass-biomedical-evidence-scout) | Maps the evidence landscape for a drug, target, mechanism, or indication by searching papers (BiomedCore) and trials (TrialCore) in parallel via the Amass MCP, then synthesizes a cited landscape briefing — with optional DrugCore/RegulatoryCore enrichment. |

## Setup

The `amass-api` skill needs an Amass API key for direct HTTP calls. Get one at <https://platform.amass.tech>, then export it in your shell:

```bash
export AMASS_API_KEY=amass_…   # add to ~/.zshrc or ~/.bashrc to persist
```

The `amass-biomedical-evidence-scout` skill runs entirely over the [Amass MCP server](https://amass.tech/mcp) — connect it in your agent and no key export is needed.
