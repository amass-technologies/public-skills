---
name: amass-api
description: Query the Amass API (api.amass.tech) to answer questions about biomedical papers (BiomedCore), clinical trials (TrialCore), drugs and molecules (DrugCore), FDA/EMA drug authorizations (RegulatoryCore), and genes / drug targets (GeneCore). Use this skill whenever the user asks about scientific literature, PubMed articles, clinical trials, NCT IDs, PMIDs, DOIs, journal quality, drug modalities, ChEMBL IDs, drug approvals, orphan designations, drug labels, genes, drug targets, target druggability/tractability, target safety, gene constraint, essentiality, target class, or Ensembl/HGNC/UniProt IDs — or any biomedical research question that benefits from searching structured datasets, even if they don't say "use the Amass API". Trigger on phrases like "find papers on...", "any trials for...", "look up PMID...", "Phase 3 trials for...", "is X approved...", "FDA label for...", "is gene X druggable...", "what drugs target...", or any literature / trial / drug / regulatory / gene-target lookup.
---

# Amass API

A skill for querying the Amass API at `https://api.amass.tech/api/v1` to answer questions about biomedical papers, clinical trials, drugs, regulatory authorizations, and genes / drug targets.

The Amass platform exposes five domain-specific datasets ("Cores") over a REST API:

- **BiomedCore** — biomedical papers (39M+ PubMed/PMC citations, plus fulltext, citation counts, journal quality scores, MeSH terms, author/institution metadata, intra-core citation graph)
- **TrialCore** — clinical trials (575K+ ClinicalTrials.gov records, with phases, status, sponsors, outcomes, arms, facility countries)
- **DrugCore** — drugs and molecules (22K+ ChEMBL-derived records: names, trade names, synonyms, modality, clinical stage, structure, parent/child hierarchy)
- **RegulatoryCore** — FDA + EMA drug authorizations on a shared schema (unified status, procedure type, designations), plus the parsed full text of every FDA label, FDA review, EMA SmPC, and EMA EPAR
- **GeneCore** — genes / drug targets (43K+ records harmonized from HGNC, NCBI, UniProt, Open Targets: symbols, synonyms, gene families, biotype, RefSeq summaries, external IDs, plus drug-target intelligence — druggability/tractability, target safety, ChEMBL target class, gnomAD genetic constraint, DepMap essentiality, and a UniProt protein-annotation block)

Records are cross-linked across Cores: papers ↔ trials, drugs → trials/papers/authorizations, authorizations → active ingredients, each authorization → its other-market counterpart, and genes ↔ drugs (a gene knows its targeting drugs; a drug knows its target genes).

The full API reference is bundled in `AMASS.md` (a snapshot of <https://platform.amass.tech/markdown>). Read it on demand — it's self-contained and includes exhaustive field lists, filter enums, and example requests. If a request behaves contrary to `AMASS.md` (e.g. a 400 cites a filter or value the snapshot doesn't list), the live docs may have moved on — fetch them and reconcile.

## When to use this skill

Use this skill for **any** biomedical literature, clinical trial, drug, regulatory, or gene/drug-target question where structured search would help. Examples:

- "Find recent high-impact papers on CAR-T therapy" → BiomedCore search with `minPublicationDate` + `minCitationCount`
- "Any recruiting Phase 3 lung cancer trials in Germany?" → TrialCore search with `phase`, `overallStatus`, `facilityCountries`
- "What's PMID 38123456 about?" → BiomedCore lookup by PMID, then get-by-ID
- "Summarize NCT06012345" → TrialCore lookup by NCT ID, then get-by-ID
- "Does this trial have any related publications?" → TrialCore get-by-ID with `include=referencesBiomedCore`, then BiomedCore get-by-ID for each AMBC ID
- "What papers cite this one?" → BiomedCore get-by-ID with `include=citedBy`
- "What has Liu's lab at the Broad published on base editing?" → BiomedCore search with `authorNames` / `institutionNames` (+ `include=authorsMetadata` to verify)
- "What modality is sotatercept, and how far along is it?" → DrugCore search
- "Which approved GLP-1 drugs exist, by modality?" → DrugCore search with `maxClinicalStage=APPROVAL`, bucket by `drugType`
- "Is Keytruda approved in both the US and EU?" → RegulatoryCore search; read `authorizationsByAgency` for the cross-market status
- "Which drugs carry a breakthrough-therapy designation in oncology?" → RegulatoryCore search with `hasDesignation`
- "What does the Keytruda label say about immune-mediated hepatitis?" → RegulatoryCore full-text search (scoped with `amassId`), then fetch the matching document section
- "Is KRAS a druggable target, and how far along is it?" → GeneCore search with `isDruggable=true` (read `tractability`)
- "Find LoF-constrained kinases with an approved small-molecule precedent" → GeneCore search with `targetClass=ENZYME`, `tractabilityModality=SMALL_MOLECULE`, `tractabilityStage=APPROVED_DRUG`, `maxConstraintLoeuf=0.6`
- "What's the target safety profile of JAK2?" → GeneCore lookup/get, read `safetyLiabilities`
- "Which drugs target EGFR?" → GeneCore get-by-ID with `include=referencesDrugCore`, then DrugCore get-by-ID for each AMDC ID
- "What gene does sotorasib target, and is that target essential?" → DrugCore get-by-ID with `include=referencesGeneCore`, then GeneCore get-by-ID (read `depmapEssentiality`)

Prefer this skill over web search for these domains — the structured filters (phase, journal quality, citation count, modality, authorization status, etc.) return far cleaner results than free-text web queries, and the record schemas include fields web search can't reliably surface (e.g. unified FDA/EMA status, parsed label sections, per-arm outcome measurements).

## Tool selection: MCP vs. HTTP

If Amass MCP tools are available in the environment, prefer those for search and get-by-ID — they handle auth and shape responses for you. The current Amass MCP server exposes eleven tools (hosted installs may add a prefix like `mcp__claude_ai_Amass__`, but the base names are stable):

| MCP tool | Wraps |
| --- | --- |
| `search_amass_biomedcore_records` | BiomedCore search (incl. author/institution filters) |
| `get_amass_biomedcore_record` | BiomedCore get by Amass ID, PMID, **or** DOI (no separate lookup step; `includeFulltext` optional) |
| `search_amass_trialcore_records` | TrialCore search |
| `get_amass_trialcore_record` | TrialCore get by Amass ID **or** NCT ID |
| `search_amass_drugcore_records` | DrugCore search |
| `get_amass_drugcore_record` | DrugCore get by Amass ID **or** ChEMBL ID (returns parent/children + all cross-links) |
| `search_amass_regulatorycore_records` | RegulatoryCore search |
| `get_amass_regulatorycore_record` | RegulatoryCore get by Amass ID, FDA application number, EMA product number, NDC, or SPL Set ID |
| `get_amass_regulatorycore_document_section` | One parsed label/review/SmPC/EPAR section with full text |
| `search_amass_genecore_records` | GeneCore search (incl. druggability/tractability, target class, safety, constraint, essentiality filters) |
| `get_amass_genecore_record` | GeneCore get by Amass ID **or** Ensembl gene ID (returns target intelligence + cross-links) |

Three MCP-specific behaviors to know:
- Searches return **up to 10 records** (no `limit` parameter — run more, narrower searches for coverage).
- The `get_*` tools accept external IDs directly (PMID/DOI, NCT, ChEMBL, FDA/EMA identifiers), so you never need the REST lookup endpoint over MCP. **Exception:** `get_amass_genecore_record` takes only an Amass ID or Ensembl gene ID — to resolve any other gene identifier (HGNC, Entrez, UniProt, symbol, OMIM, Orphanet, IUPHAR) you must use the HTTP lookup endpoint.
- The MCP RegulatoryCore *search* does **not** return `documentSections`/`matchedText` (the HTTP search does). To read label/SmPC/review/EPAR text over MCP, use `get_amass_regulatorycore_record` for the section table of contents, then `get_amass_regulatorycore_document_section` to fetch a section's full text. The HTTP path additionally lets you scope a full-text search to one record with `&amassId=AMRC_…` and get `matchedText` excerpts back.

Fall back to direct HTTP via `curl` (Bash tool) or `requests` (Python) when:

- You need something the MCP doesn't expose (`limit` > 10, an `include` flag like `outcomes` or `authorsMetadata` on search, `minCitationCount`, `sponsorType`, `facilityCountries`, a gene identifier other than Ensembl, batch lookup of many IDs at once)
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

Every Core has the same three endpoint patterns — search, get-by-ID, and batch lookup. Pick based on what the user gave you:

| User provided | Start with |
| --- | --- |
| A topic / free-text question | **Search** (`GET /records?query=...`) |
| An Amass ID (`AMBC_`, `AMTC_`, `AMDC_`, `AMRC_`, `AMGC_...`) | **Get-by-ID** (`GET /records/{amassId}`) |
| A PMID, DOI, NCT ID, ChEMBL ID, FDA application number, EMA product number, NDC, SPL Set ID, or a gene identifier (Ensembl, HGNC, Entrez, UniProt, symbol, OMIM, Orphanet, IUPHAR) | **Batch lookup** (`POST /records/lookup`) → then get-by-ID |

Get-by-ID only accepts Amass IDs. External identifiers must be resolved through the lookup endpoint first (over HTTP; the MCP `get_*` tools take external IDs directly).

### Step 1: Decide which Core

- Papers, publications, literature, journal articles → **BiomedCore** (`/cores/biomedcore/`)
- Clinical trials, studies, recruiting, Phase 1/2/3/4 → **TrialCore** (`/cores/trialcore/`)
- Drug identity: modality, clinical stage, synonyms/trade names, structure, salt/combination hierarchy → **DrugCore** (`/cores/drugcore/`)
- Approvals, marketing authorizations, FDA/EMA status, designations, orphan status, labels/SmPCs/reviews/EPARs → **RegulatoryCore** (`/cores/regulatorycore/`)
- Genes, drug targets, target druggability/tractability, target safety liabilities, ChEMBL target class, genetic constraint (LOEUF/pLI), DepMap essentiality, gene biotype/family, Ensembl/HGNC/UniProt/Entrez/OMIM/Orphanet/IUPHAR IDs, protein annotations → **GeneCore** (`/cores/genecore/`)
- Questions spanning Cores (e.g. "evidence base for drug X", "is the drug in this trial approved anywhere?", "which drugs hit this target?") → start where the user's identifier lives, then traverse the cross-links (`referencesTrialCore`, `referencesBiomedCore`, `referencesRegulatoryCore`, `referencesDrugCore`, `referencesGeneCore`, `authorizationsByAgency`)

DrugCore is often the best **entry point** for drug-centric questions even when the answer lives elsewhere: one drug record links out to its trials, papers, authorizations, and target genes in a single get-by-ID. Symmetrically, GeneCore is the entry point for target-centric questions — a gene record carries its drug-target intelligence inline and links out to the drugs that hit it.

Note: DrugCore search matches **drug/molecule names**, while GeneCore search matches **gene/target symbols and function**. For "which drugs target gene X", start in GeneCore (or look up the gene) and follow `referencesDrugCore` — don't search DrugCore by the gene symbol.

### Step 2: Apply filters aggressively

Free-text search without filters often returns noise, and there is no pagination — `limit` caps at 300, and there's no cursor or offset. Narrow the query with filters before calling.

Multi-value semantics everywhere: **repeat a filter to OR within it; combine different filters to AND across them.**

**BiomedCore:**
- "Recent" → `minPublicationDate=<concrete-date>` (e.g. 2 years before today's date in context)
- "Well-cited" → `minCitationCount=10` (or higher for broad topics)
- "Not retracted" → `isRetracted=false`
- "High-impact" / "top journals" → `minJournalQualityJufo=2` (domain-leading) or `3` (highest). **Caveat:** JuFo is the Finnish ranking; many legitimate journals are unranked (`null`) and are excluded by any min filter. For a general "high impact" gate, prefer `minCitationCount`.
- "By author X" / "from institution Y" → `authorNames` / `authorOrcids` / `institutionNames` / `institutionRors` (repeatable, OR within each). Name matching is free-text token — use the most distinctive token (last name, institution head noun) and pair with `include=authorsMetadata` to verify the match. Note `authorNames=X&institutionNames=Y` means *some* author X AND *some* affiliation Y — not necessarily the same person.

**TrialCore:**
- "Recruiting" / "enrolling" → `overallStatus=RECRUITING`
- "Phase 3" → `phase=PHASE3` (repeat for ranges: `phase=PHASE2&phase=PHASE3`)
- "Drug trial" → `interventionType=DRUG`
- "Industry-sponsored" → `sponsorType=INDUSTRY`
- "Interventional" / "observational" → `studyType=INTERVENTIONAL` etc.
- Country mentioned → `facilityCountries=DE,US` (ISO codes, comma-separated)
- "Has results posted" → `hasResults=true`
- Date windows → `minStartDate` / `maxStartDate` / `minCompletionDate` / `maxCompletionDate`
- "Large trial" → `minEnrollment=500`

**DrugCore:**
- "Antibody" / "small molecule" / "gene therapy" → `drugType=ANTIBODY` etc.
- "Approved" / "in Phase 3" → `maxClinicalStage=APPROVAL` / `PHASE3` (this is the *highest stage reached*)
- Query by drug-class terms or names ("GLP-1", "pembrolizumab") — search matches names, trade names, synonyms, and descriptions, **not** gene/target symbols

**RegulatoryCore:**
- "FDA" / "EMA" / "both markets" → `agency=FDA`, `agency=EMA`, or omit for cross-agency
- "Still on the market" → `authorizationStatus=ACTIVE` (add `CONDITIONAL` for EU conditional MAs)
- "Withdrawn" → `authorizationStatus=WITHDRAWN_VOLUNTARY&authorizationStatus=WITHDRAWN_FORCED`
- "Breakthrough / accelerated / PRIME" → `hasDesignation=BREAKTHROUGH_THERAPY` etc. (each designation only applies to the agency that owns it — listing FDA-only and EMA-only ones together keeps records from both)
- "Orphan drug" → `isOrphan=true` (exact cross-walk: FDA Orphan Drug / EMA Orphan Medicine)
- Approval window → `minAuthorizationDate` / `maxAuthorizationDate`
- A clinical phrase ("immune-mediated hepatitis", "QT prolongation") → just put it in `query` — it sweeps the parsed full text of labels, reviews, SmPCs, and EPARs, and matching sections come back on `documentSections[]` with `matchedText` excerpts

**GeneCore:**
- "Druggable" / "is X a good target" → `isDruggable=true` (any small-molecule or antibody tractability bucket; read the `tractability` object for detail)
- "With an approved/clinical drug precedent" → `tractabilityModality=SMALL_MOLECULE`/`ANTIBODY`/`PROTAC`/`OTHER_CLINICAL` and/or `tractabilityStage=APPROVED_DRUG`/`ADVANCED_CLINICAL`/`PHASE_1_CLINICAL` (omit a dimension to mean "any"; repeat to OR)
- "Kinase" / "GPCR" / "ion channel" / "transcription factor" → `targetClass=ENZYME`/`MEMBRANE_RECEPTOR`/`ION_CHANNEL`/`TRANSCRIPTION_FACTOR` etc. (top-level ChEMBL class only — the full path is still returned). Concept words also work in free-text `query`
- "Protein-coding" / "non-coding RNA" / "pseudogene" → `geneType=PROTEIN_CODING`/`NCRNA`/`PSEUDO` etc. (repeat to OR)
- "Has known target-safety signals" → `hasSafetyLiabilities=true` (read events from `safetyLiabilities`)
- "Loss-of-function constrained" / "intolerant to LoF" → `maxConstraintLoeuf=0.6` (lower LOEUF = more constrained; 0.6 is the gnomAD v4.0 cutoff)
- "Essential gene" / "DepMap dependency" → `isEssential=true`
- Query by gene symbol, name, synonym, or functional concept ("tyrosine kinase", "apoptosis") — search matches symbols, names, synonyms, gene families, RefSeq summaries, UniProt keywords, and ChEMBL target class, **not** drug names

See `AMASS.md` for the complete enum values of every filter.

There are no sort parameters; results come back by relevance only.

### Step 3: Read the response correctly

**All successful responses are wrapped in `{"data": ...}`.** Read from `data`, not the top-level object.

Errors use a different shape: `{"error": {"status", "code", "message"}}`. On a 400, also check `error.fields` — the API tells you exactly which parameter is invalid.

### Step 4: Don't over-fetch

Default response fields are usually enough to answer the user. The `include` parameter is repeatable (`?include=a&include=b`), but only opt in to what the question actually needs. In particular:

- `include=fulltext` (BiomedCore) — large; only when you need to quote or analyse the article body.
- `include=outcomes` (TrialCore) — structured results; only when the user asks about endpoints/measurements.
- `include=references` / `citedBy` (BiomedCore) — citation graph; pull only when doing reference analysis.
- `include=referencesTrialCore` / `referencesBiomedCore` / `referencesRegulatoryCore` / `referencesDrugCore` — cross-core links; pull when bridging Cores.
- `include=authorsMetadata` (BiomedCore) — ORCID, ROR, country; pull for "who at what institution" questions.
- `include=parent` / `children` (DrugCore) — drug hierarchy; pull when collapsing salts/combos onto an active ingredient.
- `include=fdaDetails` / `emaDetails` (RegulatoryCore) — agency-specific blocks (label URL, NDCs, withdrawal reasons; EPAR dates, biosimilar flags); pull when the question is agency-specific.
- `include=referencesGeneCore` (DrugCore) — the drug's mechanism-of-action target genes; pull for "what does this drug target?".
- `include=referencesDrugCore` (GeneCore) — drugs that target this gene; pull for "what drugs hit this target?".
- `include=protein` (GeneCore) — the UniProt Swiss-Prot annotation block (function, disease, biophysics, structure/PTMs); pull only when you need protein-level detail.

On RegulatoryCore, `authorizationsByAgency` (cross-market link) and `documentSections` are always present — no `include` needed. On GeneCore, the five target-intelligence objects (`tractability`, `safetyLiabilities`, `targetClass`, `gnomadConstraint`, `depmapEssentiality`) are returned by default (no `include`), but are `null` when Open Targets has no data for the gene.

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

Each lookup item must contain **exactly one** identifier (`pmid` *or* `doi` for BiomedCore; `nctId` for TrialCore; `chemblId` for DrugCore; one of `fdaApplicationNumber` / `emaProductNumber` / `ndc` / `splSetId` for RegulatoryCore; one of `ensemblGeneId` / `hgncId` / `entrezGeneId` / `uniprotId` / `symbol` / `omimId` / `orphanet` / `iuphar` for GeneCore) — never more than one. Items fail independently: each result is `{"input": ..., "amassIds": [...]}` or `{"input": ..., "error": {"code", "message"}}`. Check for `error` on each entry before reading `amassIds`, and remember `amassIds` is always an array — one external ID can resolve to multiple records.

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
| 404 | Record not found — if looking up by external ID, the record may not be indexed; tell the user |
| 422 | Input was semantically invalid — re-read the filter enums in `AMASS.md` |
| 429 | Rate limited — read `Retry-After` header, back off exponentially, then retry |
| 5xx | Server error — retry once with backoff, then tell the user |

## Presenting results to the user

After fetching, don't dump the raw JSON. Summarise:

- For **search results**: lead with the top 3–5 most relevant records. Include title/name, journal/sponsor/holder, date, and one line on why it's relevant. Offer to fetch fulltext, document sections, or more results if useful.
- For **a single record**: give a brief summary (papers: title, authors, journal, date; trials: phase, status, sponsor, enrollment, design; drugs: name, modality, clinical stage; authorizations: agency, status, indication, holder; genes: symbol, name, biotype, and — for targets — druggability, target class, and key constraint/safety/essentiality signals) and then answer the user's specific question using the record's fields.
- Always include the canonical identifier (PMID/DOI for papers, NCT ID for trials, ChEMBL ID for drugs, FDA application number / EMA product number for authorizations, gene symbol + Ensembl gene ID for genes) so the user can verify or cite.
- Link out when useful:
  - PubMed: `https://pubmed.ncbi.nlm.nih.gov/{pmid}/`
  - PMC fulltext: `https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/`
  - DOI: `https://doi.org/{doi}`
  - ClinicalTrials.gov: `https://clinicaltrials.gov/study/{nctId}`
  - ChEMBL: `https://www.ebi.ac.uk/chembl/explore/compound/{chemblId}`
  - Regulatory source page / PDFs: the record's `sourceUrl`, `fdaDetails.labelUrl`, `emaDetails.smpcUrl`
  - Ensembl gene: `https://www.ensembl.org/Homo_sapiens/Gene/Summary?g={ensemblGeneId}`
  - NCBI Gene: `https://www.ncbi.nlm.nih.gov/gene/{entrezGeneId}`
  - UniProt: `https://www.uniprot.org/uniprotkb/{uniprotId}`

## When to read the full reference

Consult `AMASS.md` whenever you need:

- The full list of available filters for a query
- Complete field list for a record schema (especially trial design fields, arm groups, outcome measure shapes, author metadata, regulatory designation/details shapes, gene target-intelligence object shapes — `tractability`, `safetyLiabilities`, `targetClass`, `gnomadConstraint`, `depmapEssentiality` — and the `protein` block)
- Exact enum values (e.g. all `overallStatus`, `drugType`, `authorizationStatus`, `hasDesignation`, `geneType`, `targetClass`, or `tractability` values)
- Error code details
- Less common patterns (cross-core traversal, drug hierarchy walking, document-section reading, intra-core citation graph, gene ↔ drug target traversal)

The reference file is compact and self-contained — reading it end-to-end is cheap.

## Common patterns — cheat sheet

**Recent high-impact papers** (note `limit` caps at 300; narrow further if needed):
```
GET /cores/biomedcore/records?query=<topic>&minPublicationDate=<date>&minCitationCount=10&limit=20
```

**Papers by an author at an institution:**
```
GET /cores/biomedcore/records?query=<topic>&authorNames=<lastname>&institutionNames=<inst>&include=authorsMetadata&limit=20
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

**Approved drugs in a class, by modality:**
```
GET /cores/drugcore/records?query=<class>&maxClinicalStage=APPROVAL&limit=100
(bucket results by drugType; add drugType=ANTIBODY etc. to narrow)
```

**Drug → its full evidence base (trials + papers + approvals):**
```
GET /cores/drugcore/records/{amassId}?include=referencesTrialCore&include=referencesBiomedCore&include=referencesRegulatoryCore
→ resolve any linked ID in its own Core
```

**Drug hierarchy (salts, combos → active ingredient):**
```
GET /cores/drugcore/records/{amassId}?include=parent&include=children
```

**US vs EU approval status for a drug:**
```
GET /cores/regulatorycore/records?query=<drug>&limit=50
(read authorizationsByAgency on each record — no second query needed)
```

**Expedited-pathway approvals across both agencies:**
```
GET /cores/regulatorycore/records?query=<area>&hasDesignation=BREAKTHROUGH_THERAPY&hasDesignation=ACCELERATED_APPROVAL&hasDesignation=PRIME&limit=100
```

**Safety-signal sweep across labels/reviews/SmPCs/EPARs:**
```
GET /cores/regulatorycore/records?query=<clinical phrase>&limit=25
(matching sections arrive on documentSections[] with matchedText)
```

**Read a specific label/SmPC section:**
```
GET /cores/regulatorycore/records/{amassId}                      (TOC on documentSections[])
GET /cores/regulatorycore/records?query=<phrase>&amassId=AMRC_…  (optional: scoped search)
→ GET /cores/regulatorycore/records/{amassId}/document-sections/{documentSectionId}
```

**FDA/EMA identifier → authorization record:**
```
POST /cores/regulatorycore/records/lookup  {"items":[{"fdaApplicationNumber":"BLA125514"}]}
→ GET /cores/regulatorycore/records/{amassId}?include=fdaDetails
```

**Druggable targets in a class with a clinical precedent:**
```
GET /cores/genecore/records?query=<concept>&targetClass=ENZYME&tractabilityModality=SMALL_MOLECULE&tractabilityStage=APPROVED_DRUG&limit=20
(tractabilityModality+tractabilityStage already imply druggability; add isDruggable=true only to broaden to "any SM/Ab bucket")
```

**LoF-constrained or essential targets:**
```
GET /cores/genecore/records?query=<concept>&maxConstraintLoeuf=0.6&limit=20      (constrained)
GET /cores/genecore/records?query=<concept>&isEssential=true&limit=20            (DepMap-essential)
```

**Gene identifier → full gene record:**
```
POST /cores/genecore/records/lookup  {"items":[{"symbol":"EGFR"}]}   (or ensemblGeneId/hgncId/uniprotId/…)
→ GET /cores/genecore/records/{amassId}
```

**Gene → drugs that target it:**
```
GET /cores/genecore/records/{amassId}?include=referencesDrugCore
→ GET /cores/drugcore/records/{amdcId}   (for each AMDC_… in referencesDrugCore)
```

**Drug → genes it targets (mechanism of action):**
```
GET /cores/drugcore/records/{amassId}?include=referencesGeneCore
→ GET /cores/genecore/records/{amgcId}   (for each AMGC_… in referencesGeneCore)
```
