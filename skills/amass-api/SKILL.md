---
name: amass-api
description: Query the Amass API (api.amass.tech) to answer questions about biomedical papers (BioMedCore) and clinical trials (TrialCore). Use this skill whenever the user asks about scientific literature, PubMed articles, clinical trials, NCT IDs, PMIDs, DOIs, recruiting studies, trial outcomes, paper citations, journal quality, or any biomedical research question that would benefit from searching structured paper/trial datasets — even if they don't explicitly say "use the Amass API". Trigger on phrases like "find papers on...", "any trials for...", "look up PMID...", "recent studies on...", "Phase 3 trials for...", "what's published about...", or any biomedical literature / clinical trial lookup task.
---

# Amass API

A skill for querying the Amass API at `https://api.amass.tech/api/v1` to answer questions about biomedical papers and clinical trials.

The Amass platform exposes two domain-specific datasets ("Cores") over a REST API:

- **BioMedCore** — biomedical papers (PubMed-derived, plus fulltext, citation counts, journal quality scores, MeSH terms, intra-core citation graph)
- **TrialCore** — clinical trials (ClinicalTrials.gov-derived, with phases, status, sponsors, outcomes, arms, facility countries)

The full API reference is bundled in `AMASS.md` (a snapshot of <https://platform.amass.tech/markdown>). Read it on demand — it's self-contained and includes exhaustive field lists, filter enums, and example requests. If a request behaves contrary to `AMASS.md` (e.g. a 400 cites a filter or value the snapshot doesn't list), the live docs may have moved on — fetch them and reconcile.

## When to use this skill

Use this skill for **any** biomedical literature or clinical trial question where structured search would help. Examples:

- "Find recent high-impact papers on CAR-T therapy" → BioMedCore search with `minPublicationDate` + `minCitationCount`
- "Any recruiting Phase 3 lung cancer trials in Germany?" → TrialCore search with `phase`, `overallStatus`, `facilityCountries`
- "What's PMID 38123456 about?" → BioMedCore lookup by PMID, then get-by-ID
- "Summarize NCT06012345" → TrialCore lookup by NCT ID, then get-by-ID
- "Does this trial have any related publications?" → TrialCore get-by-ID with `include=referencesBiomedCore`, then BioMedCore get-by-ID for each AMBC ID
- "What papers cite this one?" → BioMedCore get-by-ID with `include=citedBy`

Prefer this skill over web search for biomedical papers and trials — the structured filters (phase, journal quality, citation count, country, etc.) return far cleaner results than free-text web queries, and the record schemas include fields web search can't reliably surface.

## Tool selection: MCP vs. HTTP

If Amass MCP tools are available in the environment (e.g. `search_cores_biomedcore`, `search_cores_trialcore` provided by an Amass MCP server), prefer those for search — they handle auth and shape responses for you. Fall back to direct HTTP via `curl` (Bash tool) or `requests` (Python) when:

- An MCP tool doesn't cover what you need (get-by-ID, batch lookup, an `include` flag the MCP doesn't expose)
- The MCP server is not connected
- You're running this skill non-interactively from a script

The rest of this document describes the HTTP path, since that's the universal fallback. The same query/filter design applies regardless of transport.

## Authentication

Every HTTP request requires:

```
Authorization: Bearer amass_YOUR_KEY
```

If no key is available in the environment (check `AMASS_API_KEY` first), ask the user once, then reuse it for the rest of the conversation. Never print the key back to the user, and prefer `-H "Authorization: Bearer $AMASS_API_KEY"` over inlining the literal key (which would leave it in the visible command transcript).

## Core workflow

The API has three endpoint patterns per Core — search, get-by-ID, and batch lookup. Pick based on what the user gave you:

| User provided | Start with |
| --- | --- |
| A topic / free-text question | **Search** (`GET /records?query=...`) |
| An Amass ID (`AMBC_...` or `AMTC_...`) | **Get-by-ID** (`GET /records/{amassId}`) |
| A PMID, DOI, or NCT ID | **Batch lookup** (`POST /records/lookup`) → then get-by-ID |

Get-by-ID only accepts Amass IDs. External identifiers (PMID, DOI, NCT) must be resolved through the lookup endpoint first.

### Step 1: Decide which Core

- Papers, publications, literature, journal articles → **BioMedCore** (`/cores/biomedcore/`)
- Clinical trials, studies, recruiting, Phase 1/2/3/4 → **TrialCore** (`/cores/trialcore/`)
- User wants both (e.g. "recent trial and related paper") → query both, then cross-reference via `referencesTrialCore` / `referencesBiomedCore`

### Step 2: Apply filters aggressively

Free-text search without filters often returns noise, and there is no pagination — `limit` caps at 300, and there's no cursor or offset. Narrow the query with filters before calling. Common mappings:

**BioMedCore:**
- "Recent" → `minPublicationDate=<concrete-date>` (e.g. 2 years before today's date in context)
- "Well-cited" → `minCitationCount=10` (or higher for broad topics)
- "Not retracted" → `isRetracted=false`
- "High-impact" / "top journals" → `minJournalQualityJufo=2` (domain-leading) or `3` (highest). **Caveat:** JuFo is the Finnish ranking; many legitimate journals are unranked (`null`) and are excluded by any min filter. For a general "high impact" gate, prefer `minCitationCount`.

**TrialCore:**
- "Recruiting" / "enrolling" → `overallStatus=RECRUITING`
- "Phase 3" → `phase=PHASE3`
- "Drug trial" → `interventionType=DRUG`
- "Industry-sponsored" → `sponsorType=INDUSTRY`
- "Interventional" / "observational" → `studyType=INTERVENTIONAL` etc.
- Country mentioned → `facilityCountries=DE,US` (ISO codes, comma-separated)
- "Has results posted" → `hasResults=true`
- Date windows → `minStartDate` / `maxStartDate` / `minCompletionDate` / `maxCompletionDate`
- "Large trial" → `minEnrollment=500`

See `AMASS.md` for the complete enum values for `phase`, `overallStatus`, `studyType`, `sponsorType`, and `interventionType`.

There are no sort parameters; results come back by relevance only.

### Step 3: Read the response correctly

**All successful responses are wrapped in `{"data": ...}`.** Read from `data`, not the top-level object.

Errors use a different shape: `{"error": {"status", "code", "message"}}`. On a 400, also check `error.fields` — the API tells you exactly which parameter is invalid.

### Step 4: Don't over-fetch

Default response fields are usually enough to answer the user. The `include` parameter is repeatable (`?include=a&include=b`), but only opt in to what the question actually needs. In particular:

- `include=fulltext` (BioMedCore) — large; only when you need to quote or analyse the article body.
- `include=outcomes` (TrialCore) — structured results; only when the user asks about endpoints/measurements.
- `include=references` / `citedBy` (BioMedCore) — citation graph; pull only when doing reference analysis.
- `include=referencesTrialCore` (BioMedCore) / `include=referencesBiomedCore` (TrialCore) — cross-core linking; pull when bridging papers ↔ trials.
- `include=authorsMetadata` (BioMedCore) — ORCID, ROR, country; pull for "who at what institution" questions.

Search pricing scales linearly with `limit` ($0.05 per 20 results), so a smaller filtered query is cheaper than a broad one followed by client-side filtering.

## Calling the API

Use the Bash tool with `curl`, or Python with `requests`. Example:

```bash
curl -sS "https://api.amass.tech/api/v1/cores/biomedcore/records?query=CRISPR+Alzheimer&minPublicationDate=2024-01-01&minCitationCount=10&limit=10" \
  -H "Authorization: Bearer $AMASS_API_KEY" \
  | python -c "import sys, json; d = json.load(sys.stdin); [print(r['title'], '—', r.get('journal'), '—', r.get('publicationDate')) for r in d['data']]"
```

For batch lookup (POST with JSON body):

```bash
curl -sS -X POST "https://api.amass.tech/api/v1/cores/biomedcore/records/lookup" \
  -H "Authorization: Bearer $AMASS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"items":[{"pmid":"38123456"},{"doi":"10.1038/s41586-024-00001-x"}]}'
```

Each lookup item must contain **exactly one** identifier (`pmid` *or* `doi` for BioMedCore; `nctId` for TrialCore) — never both. Items fail independently: each result is `{"input": ..., "amassIds": [...]}` or `{"input": ..., "error": {"code", "message"}}`. Check for `error` on each entry before reading `amassIds`.

## Rate limits

The API allows 60 requests per 60-second window per user+org (multiple keys for the same user share quota). Every response carries:

- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`
- `X-RateLimit-Reset` (ISO timestamp)

When running batches, read `X-RateLimit-Remaining` proactively and pace yourself rather than waiting for a 429.

## Handling errors

| Status | What to do |
| --- | --- |
| 400 | Invalid parameters — read `error.fields` for the offending parameter, then fix and retry |
| 401 | Missing/invalid key — ask the user for a valid `amass_...` key |
| 403 | Valid key but no permission — tell the user; nothing to retry |
| 404 | Record not found — if looking up by PMID/DOI/NCT, the paper/trial may not be indexed; tell the user |
| 422 | Input was semantically invalid — re-read the filter enums in `AMASS.md` |
| 429 | Rate limited — read `Retry-After` header, back off exponentially, then retry |
| 5xx | Server error — retry once with backoff, then tell the user |

## Presenting results to the user

After fetching, don't dump the raw JSON. Summarise:

- For **search results**: lead with the top 3–5 most relevant records. Include title, journal/sponsor, date, and one line on why it's relevant. Offer to fetch fulltext or more results if useful.
- For **a single record**: give a brief summary (title, authors, journal, date; for trials: phase, status, sponsor, enrollment, design) and then answer the user's specific question using the record's fields.
- Always include the canonical identifier (PMID/DOI for papers, NCT ID for trials) so the user can verify or cite.
- Link out when useful:
  - PubMed: `https://pubmed.ncbi.nlm.nih.gov/{pmid}/`
  - PMC fulltext: `https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/`
  - DOI: `https://doi.org/{doi}`
  - ClinicalTrials.gov: `https://clinicaltrials.gov/study/{nctId}`

## When to read the full reference

Consult `AMASS.md` whenever you need:

- The full list of available filters for a query
- Complete field list for a record schema (especially trial design fields, arm groups, outcome measure shapes, author metadata)
- Exact enum values (e.g. all possible `overallStatus` values, all `interventionType` values)
- Error code details
- Less common patterns (cross-referencing trials ↔ publications via `referencesTrialCore` / `referencesBiomedCore`, intra-core citation graph via `references` / `citedBy`)

The reference file is compact and self-contained — reading it end-to-end is cheap.

## Common patterns — cheat sheet

**Recent high-impact papers** (note `limit` caps at 300; narrow further if needed):
```
GET /cores/biomedcore/records?query=<topic>&minPublicationDate=<date>&minCitationCount=10&limit=20
```

**Recruiting Phase 3 drug trials:**
```
GET /cores/trialcore/records?query=<condition>&phase=PHASE3&overallStatus=RECRUITING&interventionType=DRUG&limit=20
```

**Trials with published results in a country:**
```
GET /cores/trialcore/records?query=<condition>&hasResults=true&facilityCountries=US&limit=50
```

**Industry pipeline analysis:**
```
GET /cores/trialcore/records?query=<target-or-condition>&sponsorType=INDUSTRY&minStartDate=<date>&limit=100
```

**PMID → full paper record:**
```
POST /cores/biomedcore/records/lookup  {"items":[{"pmid":"..."}]}
→ GET /cores/biomedcore/records/{amassId}
```

**Trial → linked publications:**
```
GET /cores/trialcore/records/{amassId}?include=referencesBiomedCore
→ GET /cores/biomedcore/records/{ambcId}   (for each AMBC_… in referencesBiomedCore)
```

**Paper → linked trials:**
```
GET /cores/biomedcore/records/{amassId}?include=referencesTrialCore
→ GET /cores/trialcore/records/{amtcId}    (for each AMTC_… in referencesTrialCore)
```

**Citation graph for a paper:**
```
GET /cores/biomedcore/records/{amassId}?include=references&include=citedBy
```
