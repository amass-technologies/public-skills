# Amass Skills

Agent skills for working with the [Amass](https://platform.amass.tech) Core API platform — BiomedCore (biomedical publications), TrialCore (clinical trials), DrugCore (drugs & molecules), RegulatoryCore (FDA & EMA drug authorizations), and GeneCore (genes & drug targets).

## Install

Using [`npx skills`](https://skills.sh):

```bash
npx skills add amass-technologies/public-skills
```

This installs every skill under `skills/` in this repo.

## Skills in this repo

### Foundations

| Skill | What it does |
| --- | --- |
| [`amass-api`](skills/amass-api) | Search, look up, and cross-link records across all Amass Cores — BiomedCore (publications), TrialCore (clinical trials), DrugCore (drugs/molecules), RegulatoryCore (FDA/EMA authorizations, including parsed label/SmPC/review/EPAR text), GeneCore (genes/drug targets with druggability, target-class, constraint, safety, and essentiality intelligence), and PatentCore (life-science patents, preview) — via the Amass platform API or MCP. Bundles a full API reference (`AMASS.md`). |
| [`amass-biomedical-evidence-scout`](skills/amass-biomedical-evidence-scout) | Maps the evidence landscape for a drug, target, mechanism, or indication by searching papers (BiomedCore) and trials (TrialCore) in parallel via the Amass MCP, then synthesizes a cited landscape briefing — with optional DrugCore/RegulatoryCore enrichment. |

### MCP showcase skills

Task-scoped skills that run over the [Amass MCP server](https://amass.tech/mcp), plus one general, interview-driven **target-discovery workbench**. Each one has a live, validated example — a real prompt, the cited answer it produced, and a downloadable sample output — in the **Skills & Prompts** library on the Amass site. Links are in [Live examples](#live-examples) below.

| Skill | What it does |
| --- | --- |
| [`target-prioritization-matrix`](skills/target-prioritization-matrix) | General target-discovery workbench: interviews you for modality and criteria (druggability, safety, genetic constraint, essentiality, competition, clinical/literature/IP activity), discovers candidate targets in GeneCore, scores each across the chosen criteria using DrugCore/TrialCore/BiomedCore/PatentCore cross-links, and returns a tabular target overview in the format you pick — then offers to freeze that configuration into a reusable skill. |
| [`trial-evidence-trace`](skills/trial-evidence-trace) | Takes one clinical-trial ID and returns the exact published papers that describe it — via the TrialCore→BiomedCore `referencesBiomedCore` graph — as a citation-ranked, trust-tagged table plus a CSV. |
| [`indication-pipeline-landscape`](skills/indication-pipeline-landscape) | Maps an indication's current Phase-3 drug pipeline across every sponsor — each program tagged novel-agent vs repurposed-generic and scored by cross-core publication evidence — into one sorted Excel matrix. |
| [`kol-paper-trial-triangulator`](skills/kol-paper-trial-triangulator) | Takes a KOL plus a context query and returns that author's most-relevant papers, each triangulated to the clinical trial it reports, as a trust-tagged engagement-dossier workbook. |
| [`orcid-record-self-verify`](skills/orcid-record-self-verify) | Self-audits your own ORCID-linked publication record for integrity — retractions, below-peer-review journals, and trial linkage — before a grant, tenure, or promotion submission. |
| [`submission-evidence-assembler`](skills/submission-evidence-assembler) | Assembles a citation-auditable, Module-2.5-style evidence narrative for a drug asset — every pivotal Phase-3 trial and the papers that describe it — as a `.docx` narrative plus an `.xlsx` trial×paper matrix. |

### Monitoring skills

| Skill | What it does |
| --- | --- |
| [`amass-watchlist-monitor`](skills/amass-watchlist-monitor) | Keeps a named list of Amass records — trials, papers, FDA/EMA authorizations, drugs, or genes — under watch and reports what changed since the last check: status and stage changes, results posted, label and SmPC revisions, retractions, new cross-links, and records gone from Amass. By default it runs through the Amass MCP connector with no API key (Claude chat, Cowork, Claude Code): the list lives in one Excel board file that is also its memory, and a bundled script (`scripts/board.py`, Python standard library) compares each record field by field. With `AMASS_API_KEY`, `scripts/monitor.py` runs an exact monitor over the REST change feed instead, for automation or long lists. |
| [`amass-landscape-monitor`](skills/amass-landscape-monitor) | Maps a whole field — a mechanism class in an indication, a target's drug landscape, or a drug and its competitors — across trials, papers, drugs, genes, FDA/EMA authorizations and patents. It searches facet by facet until new queries stop adding records, screens every record against the field's criteria, and delivers a landscape page with charts, a workbook and one ledger per Core; later runs report what is new and what changed, by drug and by Core. Runs through the Amass MCP connector with no API key; a bundled script (`scripts/ledger.py`, Python standard library) keeps the state and computes every figure. |

## Live examples

Each MCP showcase skill has a validated example in the [Skills & Prompts](https://amass.tech/skills) library (under **Resources**) on the Amass site — the paste-ready prompt, the grounded answer it produced, and a downloadable sample output:

| Skill | Live example & sample output |
| --- | --- |
| `target-prioritization-matrix` | <https://amass.tech/skills/target-prioritization-matrix> |
| `trial-evidence-trace` | <https://amass.tech/skills/trial-evidence-trace> |
| `indication-pipeline-landscape` | <https://amass.tech/skills/indication-pipeline-landscape> |
| `kol-paper-trial-triangulator` | <https://amass.tech/skills/kol-paper-trial-triangulator> |
| `orcid-record-self-verify` | <https://amass.tech/skills/orcid-record-self-verify> |
| `submission-evidence-assembler` | <https://amass.tech/skills/submission-evidence-assembler> |

Browse the full library at <https://amass.tech/skills>.

## Packaged downloads

Most agents install skills straight from source — via `npx skills` above, or by dropping a skill's folder into the tool's skills directory. For tools that take a packaged file, prebuilt `.skill` (double-click to install in Claude) and `.zip` archives live in [`dist/`](dist):

- [`amass-biomedical-evidence-scout.skill`](dist/amass-biomedical-evidence-scout.skill) / [`.zip`](dist/amass-biomedical-evidence-scout.zip)
- [`amass-watchlist-monitor.skill`](dist/amass-watchlist-monitor.skill) / [`.zip`](dist/amass-watchlist-monitor.zip)
- [`amass-landscape-monitor.skill`](dist/amass-landscape-monitor.skill) / [`.zip`](dist/amass-landscape-monitor.zip)

Each package holds the skill's whole folder: its `SKILL.md` plus any scripts, references and templates. Rebuild them from the committed sources with `scripts/build-dist.sh`. Rerun it whenever a packaged skill changes, so the download never drifts behind the source.

## Setup

The `amass-api` skill needs an Amass API key for direct HTTP calls, and so does the optional API mode of `amass-watchlist-monitor`. Get one at <https://platform.amass.tech>, then export it in your shell:

```bash
export AMASS_API_KEY=amass_…   # add to ~/.zshrc or ~/.bashrc to persist
```

The `amass-biomedical-evidence-scout` skill and the five MCP showcase skills run entirely over the [Amass MCP server](https://amass.tech/mcp) — connect it in your agent and no key export is needed. `amass-landscape-monitor`, and `amass-watchlist-monitor` by default, run over the MCP server too; both also need a place to run Python (code execution in Claude chat, or a shell in Cowork and Claude Code).
