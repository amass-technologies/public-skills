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

Five task-scoped skills that run over the [Amass MCP server](https://amass.tech/mcp). Each one has a live, validated example — a real prompt, the cited answer it produced, and a downloadable sample output — on the Amass site. Links are in [Live examples](#live-examples) below.

| Skill | What it does |
| --- | --- |
| [`trial-evidence-trace`](skills/trial-evidence-trace) | Takes one clinical-trial ID and returns the exact published papers that describe it — via the TrialCore→BiomedCore `referencesBiomedCore` graph — as a citation-ranked, trust-tagged table plus a CSV. |
| [`indication-pipeline-landscape`](skills/indication-pipeline-landscape) | Maps an indication's current Phase-3 drug pipeline across every sponsor — each program tagged novel-agent vs repurposed-generic and scored by cross-core publication evidence — into one sorted Excel matrix. |
| [`kol-paper-trial-triangulator`](skills/kol-paper-trial-triangulator) | Takes a KOL plus a context query and returns that author's most-relevant papers, each triangulated to the clinical trial it reports, as a trust-tagged engagement-dossier workbook. |
| [`orcid-record-self-verify`](skills/orcid-record-self-verify) | Self-audits your own ORCID-linked publication record for integrity — retractions, below-peer-review journals, and trial linkage — before a grant, tenure, or promotion submission. |
| [`submission-evidence-assembler`](skills/submission-evidence-assembler) | Assembles a citation-auditable, Module-2.5-style evidence narrative for a drug asset — every pivotal Phase-3 trial and the papers that describe it — as a `.docx` narrative plus an `.xlsx` trial×paper matrix. |

## Live examples

Each MCP showcase skill has a validated example on the Amass site — the paste-ready prompt, the grounded answer it produced, and a downloadable sample output:

| Skill | Live example & sample output |
| --- | --- |
| `trial-evidence-trace` | <https://amass.tech/mcp-showcases/trial-evidence-trace> |
| `indication-pipeline-landscape` | <https://amass.tech/mcp-showcases/indication-pipeline-landscape> |
| `kol-paper-trial-triangulator` | <https://amass.tech/mcp-showcases/kol-paper-trial-triangulator> |
| `orcid-record-self-verify` | <https://amass.tech/mcp-showcases/orcid-record-self-verify> |
| `submission-evidence-assembler` | <https://amass.tech/mcp-showcases/submission-evidence-assembler> |

Browse the full gallery at <https://amass.tech/mcp-showcases>.

## Packaged downloads

Most agents install skills straight from source — via `npx skills` above, or by dropping a `SKILL.md` into the tool's skills directory. For tools that take a packaged file, prebuilt `.skill` (double-click to install in Claude) and `.zip` archives live in [`dist/`](dist):

- [`amass-biomedical-evidence-scout.skill`](dist/amass-biomedical-evidence-scout.skill) / [`.zip`](dist/amass-biomedical-evidence-scout.zip)

Rebuild them from the current `SKILL.md` with `scripts/build-dist.sh`. Rerun it whenever a packaged skill changes, so the download never drifts behind the source.

## Setup

The `amass-api` skill needs an Amass API key for direct HTTP calls. Get one at <https://platform.amass.tech>, then export it in your shell:

```bash
export AMASS_API_KEY=amass_…   # add to ~/.zshrc or ~/.bashrc to persist
```

The `amass-biomedical-evidence-scout` skill and the five MCP showcase skills run entirely over the [Amass MCP server](https://amass.tech/mcp) — connect it in your agent and no key export is needed.
