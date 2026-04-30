---
name: amass-api
description: Query the Amass API (api.amass.tech) to answer questions about biomedical papers (BioMedCore) and clinical trials (TrialCore). Use this skill whenever the user asks about scientific literature, PubMed articles, clinical trials, NCT IDs, PMIDs, DOIs, recruiting studies, trial outcomes, paper citations, journal quality, or any biomedical research question that would benefit from searching structured paper/trial datasets — even if they don't explicitly say "use the Amass API". Trigger on phrases like "find papers on...", "any trials for...", "look up PMID...", "recent studies on...", "Phase 3 trials for...", "what's published about...", or any biomedical literature / clinical trial lookup task.
---

# Amass API

A skill for querying the Amass API at `https://api.amass.tech/api/v1` to answer questions about biomedical papers and clinical trials.

The Amass platform exposes two domain-specific datasets ("Cores") over a REST API:

- **BioMedCore** — biomedical papers (PubMed-derived, plus fulltext, citation counts, journal quality scores, MeSH terms)
- **TrialCore** — clinical trials (ClinicalTrials.gov-derived, with phases, status, sponsors, outcomes, facility countries)

The full API reference is bundled in `AMASS.md`. Read it on demand — it's self-contained and includes exhaustive field lists, filter enums, and example requests.

## When to use this skill

Use this skill for **any** biomedical literature or clinical trial question where structured search would help. Examples:

- "Find recent high-impact papers on CAR-T therapy" → BioMedCore search with `minPublicationDate` + `minJournalQualityJufo`
- "Any recruiting Phase 3 lung cancer trials in Germany?" → TrialCore search with `phase`, `overallStatus`, `facilityCountries`
- "What's PMID 38123456 about?" → BioMedCore lookup by PMID, then get-by-ID
- "Summarize NCT06012345" → TrialCore lookup by NCT ID, then get-by-ID
- "Does this trial have any related publications?" → TrialCore get-by-ID with `referenceAmassIds`, then cross-reference BioMedCore

Prefer this skill over web search for biomedical papers and trials — the structured filters (phase, journal quality, citation count, country, etc.) return far cleaner results than free-text web queries, and the record schemas include fields web search can't reliably surface.

## Authentication

Every request requires:

```
Authorization: Bearer amass_YOUR_KEY
```

If no key is available in the environment (check env vars like `AMASS_API_KEY` first), ask the user once, then reuse it for the rest of the conversation. Never print the key back to the user in full.

## Core workflow

The API has three endpoint patterns per Core — search, get-by-ID, and batch lookup. Pick based on what the user gave you:

| User provided | Start with |
| --- | --- |
| A topic / free-text question | **Search** (`GET /records?query=...`) |
| An Amass ID (`AMBC_...` or `AMTC_...`) | **Get-by-ID** (`GET /records/{amassId}`) |
| A PMID, DOI, or NCT ID | **Batch lookup** (`POST /records/lookup`) → then get-by-ID |

### Step 1: Decide which Core

- Papers, publications, literature, journal articles → **BioMedCore** (`/cores/biomedcore/`)
- Clinical trials, studies, recruiting, Phase 1/2/3/4 → **TrialCore** (`/cores/trialcore/`)
- User wants both (e.g. "recent trial and related paper") → query both

### Step 2: Apply filters aggressively

Free-text search without filters often returns noise. Before calling, ask yourself which filters the user's question implies and apply them. Examples:

- "Recent" → `minPublicationDate` (papers) or `minStartDate` (trials). Use a concrete date, e.g. 2 years before today.
- "High-impact" / "top journals" → `minJournalQualityJufo=2` (domain-leading) or `3` (highest)
- "Well-cited" → `minCitationCount=10` (or higher for broad topics)
- "Not retracted" → `isRetracted=false`
- "Recruiting" / "enrolling" → `overallStatus=RECRUITING`
- "Phase 3 drug trial" → `phase=PHASE3&interventionType=DRUG`
- Country mentioned → `facilityCountries=DE,US` (ISO codes, comma-separated)

### Step 3: Read the response correctly

**All successful responses are wrapped in `{"data": ...}`.** Read from `data`, not the top-level object.

Errors use a different shape: `{"error": {"status", "code", "message"}}`.

### Step 4: Don't over-fetch

Default response fields are usually enough to answer the user. Only add `include=fulltext`, `include=outcomes`, etc. when the user's question actually requires that content — `fulltext` in particular makes responses huge. If you're just summarising a paper, the abstract + metadata is almost always sufficient.

## Calling the API

Use `bash_tool` with `curl`, or Python with `requests`. Example:

```bash
curl -sS "https://api.amass.tech/api/v1/cores/biomedcore/records?query=CRISPR+Alzheimer&minPublicationDate=2023-01-01&minJournalQualityJufo=2&limit=10" \
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

Remember: batch lookup items can fail independently. Each result in the array is either `{"amassIds": [...]}` or `{"error": "..."}`. Check each before using.

## Handling errors

| Status | What to do |
| --- | --- |
| 401 | Missing/invalid key — ask the user for a valid `amass_...` key |
| 404 | Record not found — if looking up by PMID/DOI/NCT, the paper/trial may not be indexed; tell the user |
| 429 | Rate limited (60 req / 60s) — read `Retry-After` header, back off exponentially, then retry |
| 422 | Input was semantically invalid — re-read the filter enums in `AMASS.md` |
| 5xx | Server error — retry once with backoff, then tell the user |

## Presenting results to the user

After fetching, don't dump the raw JSON. Summarise:

- For **search results**: lead with the top 3–5 most relevant records. Include title, journal/sponsor, date, and one line on why it's relevant. Offer to fetch fulltext or more results if useful.
- For **a single record**: give a brief summary (title, authors, journal, date; for trials: phase, status, sponsor, enrollment) and then answer the user's specific question using the record's fields.
- Always include the canonical identifier (PMID/DOI for papers, NCT ID for trials) so the user can verify or cite.
- Link out when useful: PubMed (`https://pubmed.ncbi.nlm.nih.gov/{pmid}/`) or ClinicalTrials.gov (`https://clinicaltrials.gov/study/{nctId}`).

## When to read the full reference

Consult `AMASS.md` whenever you need:

- The full list of available filters for a query
- Complete field list for a record schema
- Exact enum values (e.g. all possible `overallStatus` values, all `interventionType` values)
- Error code details
- Less common patterns (cross-referencing trials ↔ publications via `referenceAmassIds`)

The reference file is compact and self-contained — reading it end-to-end is cheap.

## Common patterns — cheat sheet

**Recent high-impact papers:**
```
GET /cores/biomedcore/records?query=<topic>&minPublicationDate=<date>&minCitationCount=10&minJournalQualityJufo=2&limit=20
```

**Recruiting Phase 3 drug trials:**
```
GET /cores/trialcore/records?query=<condition>&phase=PHASE3&overallStatus=RECRUITING&interventionType=DRUG&limit=20
```

**Trials with published results in a country:**
```
GET /cores/trialcore/records?query=<condition>&hasResults=true&facilityCountries=US&limit=50
```

**PMID → full paper record:**
```
POST /cores/biomedcore/records/lookup  {"items":[{"pmid":"..."}]}
→ GET /cores/biomedcore/records/{amassId}
```

**Trial → linked publications:**
```
GET /cores/trialcore/records/{amassId}   (returns referenceAmassIds)
→ GET /cores/biomedcore/records/{referenceAmassId}  (for each reference)
```