# Amass API Reference

> Snapshot of <https://platform.amass.tech/markdown> as of 2026-06-11.
> If a request behaves contrary to what's documented here (e.g. a 400 cites a filter or value this file doesn't list), fetch the live page to check whether the API has moved on.

Base URL: `https://api.amass.tech/api/v1`

---

## Endpoints

Every Core follows the same three-endpoint pattern: search, get-by-ID, batch lookup.

### BiomedCore — `/cores/biomedcore`
- `GET  /records` — search biomedical literature (39M+ PubMed/PMC citations)
- `GET  /records/{amassId}` — fetch a single record (Amass ID starts `AMBC_`)
- `POST /records/lookup` — convert PMIDs/DOIs to Amass IDs

### TrialCore — `/cores/trialcore`
- `GET  /records` — search clinical trials (575K+ ClinicalTrials.gov records)
- `GET  /records/{amassId}` — fetch a single record (Amass ID starts `AMTC_`)
- `POST /records/lookup` — convert NCT IDs to Amass IDs

### DrugCore — `/cores/drugcore`
- `GET  /records` — search drugs/molecules (22K+ ChEMBL-derived records)
- `GET  /records/{amassId}` — fetch a single record (Amass ID starts `AMDC_`)
- `POST /records/lookup` — convert ChEMBL IDs to Amass IDs

### RegulatoryCore — `/cores/regulatorycore`
- `GET  /records` — search FDA + EMA drug authorizations (metadata **and** parsed full text of labels, reviews, SmPCs, EPARs)
- `GET  /records/{amassId}` — fetch a single record (Amass ID starts `AMRC_`)
- `GET  /records/{amassId}/document-sections/{documentSectionId}` — fetch one parsed source-document section with full text (section IDs start `AMRCDS_`)
- `POST /records/lookup` — convert FDA application numbers, EMA product numbers, NDCs, or SPL Set IDs to Amass IDs

---

## Authentication

Header on every request:

```
Authorization: Bearer amass_<api-key>
```

- Keys start with `amass_` and are shown once at creation.
- Quota is keyed by user + organization, so multiple keys for the same user share rate-limit budget.
- Manage keys at `platform.amass.tech/api-keys`.

---

## Rate limits

- 60 requests per 60-second sliding window per user+org.
- Every response includes:
  - `X-RateLimit-Limit` — 60
  - `X-RateLimit-Remaining` — requests left in the current window
  - `X-RateLimit-Reset` — ISO timestamp when the window resets
- A 429 includes `Retry-After` (seconds). Back off, then retry.

Read `X-RateLimit-Remaining` proactively when running batches; don't wait for the 429.

---

## Pricing

- Search (`GET /records`): $0.05 per 20 results — scales linearly with `limit`.
- Get-by-ID (`GET /records/{amassId}`): $0.01.
- Document section (`GET .../document-sections/{id}`): $0.01.
- Lookup (`POST /records/lookup`): $0.01 per call (regardless of item count).
- Costs are the same across all Cores. Every billable response carries an `X-Amass-Credit-Cost` header (1 credit = $0.01).
- Org admins can check remaining credits via `GET /credits/api-credits` (members get 403; the call is free).

Implication: prefer narrow filtered searches with small `limit`s over broad scans. Lookup-then-get-by-ID is roughly the same price as a small search but returns exactly the record you want.

---

## Response envelope

- Success (2xx): `{"data": <array-or-object>}`. Always read from `data`.
- Error (4xx/5xx): `{"error": {...}}` — never wrapped in `data`.

---

## Pagination & sorting

- **No pagination.** No cursor, no offset. `limit` is capped at **300** (default 20).
- **No sort parameters.** Results are returned by relevance.

If a query needs more than 300 hits, narrow it with filters rather than trying to page.

---

## Multi-value filter semantics

Repeatable filters combine **OR within one filter, AND across filters**:

- `?phase=PHASE2&phase=PHASE3` → Phase 2 **or** Phase 3.
- `?phase=PHASE3&overallStatus=RECRUITING` → Phase 3 **and** recruiting.
- `?authorNames=Hassabis&institutionNames=DeepMind` → (any Hassabis author) **and** (any DeepMind affiliation) — not necessarily the same author.

This applies to BiomedCore's author/institution filters, TrialCore's enum filters, and RegulatoryCore's `agency` / `moleculeType` / `authorizationStatus` / `hasDesignation`.

---

## BiomedCore — search parameters

| Param | Type | Notes |
|---|---|---|
| `query` | string (required) | Full-text across titles, abstracts, fulltext, metadata |
| `limit` | int | 1–300, default 20 |
| `include` | string (repeatable) | `fulltext`, `authorsMetadata`, `meshIds`, `substanceIds`, `referencesTrialCore`, `references`, `citedBy` |
| `minPublicationDate` | ISO date | e.g. `2024-01-01` |
| `maxPublicationDate` | ISO date | |
| `minCitationCount` | int | 0–100000 |
| `minJournalQualityJufo` | enum | `0`, `1`, `2`, `3` (see below) |
| `isRetracted` | bool | `true` / `false` |
| `authorOrcids` | string (repeatable) | Match ANY. Bare (`0000-0003-1234-5678`) or URL form |
| `authorNames` | string (repeatable) | Match ANY. Free-text token (PubMed indexes `LastName Initials`, e.g. `Liu DR` — last-name token is safest) |
| `institutionRors` | string (repeatable) | Match ANY. Bare (`03vek6s52`) or URL form |
| `institutionNames` | string (repeatable) | Match ANY. Free-text token |

`include` is repeatable: `?include=fulltext&include=referencesTrialCore`. Pair author/institution filters with `include=authorsMetadata` to verify the match.

### Journal quality (JuFo) tiers

- `3` — highest, extremely consistent impact
- `2` — domain-leading
- `1` — peer-reviewed with expert editorial boards
- `0` — evaluated as low quality
- `null` — **not evaluated** (≠ `0`)

Filtering with `minJournalQualityJufo=2` excludes all `null` records. JuFo is the Finnish ranking, so many legitimate non-Finnish journals are `null`. Use the filter only when you genuinely want to gate on the Finnish ranking; otherwise prefer `minCitationCount`.

---

## BiomedCore — record schema

### Default fields (always returned)

```
amassId               string         AMBC_… canonical ID
pmid                  string|null    PubMed ID
pmcid                 string|null    PubMed Central ID
doi                   string|null
title                 string|null
abstract              string|null
authors               string[]       e.g. ["Smith J", "Doe A"]
journal               string|null
issn                  string|null
volumeIssue           string|null
publicationDate       string|null    ISO date
publicationTypes      string[]       e.g. ["Journal Article", "Review"]
language              string|null    e.g. "eng"
citationCount         number|null
journalQualityJufo    number|null    See JuFo tiers
meshTerms             string[]
keywords              string[]
substances            string[]
hasFulltext           boolean|null
isRetracted           boolean|null
```

### Optional fields (via `include`)

```
fulltext              string|null         Full article text — large; only fetch when needed
authorsMetadata       object[]            Structured author info (see below)
meshIds               string[]            MeSH descriptor IDs
substanceIds          string[]            Chemical substance IDs
referencesTrialCore   string[]            AMTC_… IDs of trials referenced by this paper
references            string[]            AMBC_… IDs of papers this one cites
citedBy               string[]            AMBC_… IDs of papers that cite this one
```

`references` and `citedBy` form an intra-BiomedCore citation graph. `referencesTrialCore` is the cross-core link to TrialCore.

### `authorsMetadata` shape

```json
{
  "name": "Smith J",
  "nameRaw": "John Smith",
  "orcid": "0000-0001-2345-6789",
  "position": 0,
  "affiliations": [
    {
      "name": "Institution name",
      "nameRaw": "Full name with location",
      "ror": "ROR identifier",
      "countryCode": "US"
    }
  ]
}
```

---

## BiomedCore — lookup

`POST /cores/biomedcore/records/lookup`

```json
{
  "items": [
    {"pmid": "38123456"},
    {"doi": "10.1038/s41586-024-00001-x"}
  ]
}
```

**Constraint:** each item must contain exactly one of `pmid` or `doi` — never both.

Response:

```json
{
  "data": [
    {"input": {"pmid": "38123456"}, "amassIds": ["AMBC_abc123"]},
    {"input": {"doi": "..."},        "error": {"code": "NOT_FOUND", "message": "..."}}
  ]
}
```

Items fail independently — always check each result for an `error` field before reading `amassIds`. `amassIds` is always an array (one identifier can resolve to multiple records).

---

## TrialCore — search parameters

| Param | Type | Notes |
|---|---|---|
| `query` | string (required) | Full-text search |
| `limit` | int | 1–300, default 20 |
| `include` | string (repeatable) | `outcomes`, `detailedDescription`, `referencesBiomedCore` |
| `phase` | enum (repeatable) | `EARLY_PHASE1`, `PHASE1`, `PHASE1/PHASE2`, `PHASE2`, `PHASE2/PHASE3`, `PHASE3`, `PHASE4`, `NA` |
| `overallStatus` | enum (repeatable) | `RECRUITING`, `NOT_YET_RECRUITING`, `ENROLLING_BY_INVITATION`, `ACTIVE_NOT_RECRUITING`, `SUSPENDED`, `TERMINATED`, `COMPLETED`, `WITHDRAWN`, `UNKNOWN`, `WITHHELD`, `AVAILABLE`, `NO_LONGER_AVAILABLE`, `TEMPORARILY_NOT_AVAILABLE`, `APPROVED_FOR_MARKETING` |
| `studyType` | enum (repeatable) | `INTERVENTIONAL`, `OBSERVATIONAL`, `EXPANDED_ACCESS` |
| `sponsorType` | enum (repeatable) | `NIH`, `FED`, `INDUSTRY`, `OTHER`, `OTHER_GOV`, `INDIV`, `NETWORK` |
| `interventionType` | enum (repeatable) | `DRUG`, `DEVICE`, `BIOLOGICAL`, `PROCEDURE`, `RADIATION`, `BEHAVIORAL`, `GENETIC`, `DIETARY_SUPPLEMENT`, `DIAGNOSTIC_TEST`, `COMBINATION_PRODUCT`, `OTHER` |
| `facilityCountries` | string | Comma-separated ISO codes, e.g. `DE,US` |
| `hasResults` | bool | |
| `minStartDate` / `maxStartDate` | ISO date | |
| `minCompletionDate` / `maxCompletionDate` | ISO date | |
| `minEnrollment` | int | Minimum participants |

---

## TrialCore — record schema

### Default fields (always returned)

```
amassId                      string         AMTC_… canonical ID
nctId                        string|null    ClinicalTrials.gov identifier
briefTitle                   string|null
officialTitle                string|null
briefSummary                 string|null
acronym                      string|null    e.g. "KEYNOTE-189"
phase                        string|null
overallStatus                string|null
studyType                    string|null
startDate                    string|null    ISO date
completionDate               string|null    ISO date
lastUpdateDate               string|null    ISO date
hasResults                   boolean
enrollment                   number|null
enrollmentType               string|null    "ACTUAL" | "ESTIMATED"
sponsorName                  string|null
sponsorType                  string|null
collaborators                string[]
conditions                   string[]
conditionMeshTerms           string[]
interventionTypes            string[]
interventionNames            string[]
interventionMeshTerms        string[]
facilityCountries            string[]       ISO codes
keywords                     string[]
orgStudyId                   string|null
secondaryIds                 string[]
primaryOutcomeMeasures       string[]
secondaryOutcomeMeasures     string[]
designAllocation             string|null    RANDOMIZED | NON_RANDOMIZED | NA
designInterventionModel      string|null    SINGLE_GROUP | PARALLEL | CROSSOVER | FACTORIAL | SEQUENTIAL
designPrimaryPurpose         string|null    e.g. TREATMENT | PREVENTION | DIAGNOSTIC
designMasking                string|null    NONE | SINGLE | DOUBLE | TRIPLE | QUADRUPLE
resultsFirstPostDate         string|null
whyStopped                   string|null
isFdaRegulatedDrug           boolean|null
isFdaRegulatedDevice         boolean|null
armGroups                    object[]       See arm group shape
oversightHasDmc              boolean|null   Data Monitoring Committee
```

### Optional fields (via `include`)

```
detailedDescription      string|null
outcomes                 object[]      Structured outcome results (see below)
referencesBiomedCore     string[]      AMBC_… IDs of papers this trial references
```

### Arm group shape

```json
{
  "type": "EXPERIMENTAL|ACTIVE_COMPARATOR|PLACEBO_COMPARATOR|SHAM_COMPARATOR|NO_INTERVENTION|OTHER",
  "title": "...",
  "description": "..."
}
```

### Outcome shape

```json
{
  "outcomeType": "PRIMARY|SECONDARY|OTHER_PRE_SPECIFIED|POST_HOC",
  "title": "...",
  "description": "...",
  "timeFrame": "...",
  "population": "...",
  "units": "...",
  "paramType": "GEOMETRIC_MEAN|GEOMETRIC_LEAST_SQUARES_MEAN|LEAST_SQUARES_MEAN|LOG_MEAN|MEAN|MEDIAN|NUMBER|COUNT_OF_PARTICIPANTS|COUNT_OF_UNITS",
  "dispersionType": "NA|STANDARD_DEVIATION|STANDARD_ERROR|INTER_QUARTILE_RANGE|FULL_RANGE|CONFIDENCE_80|CONFIDENCE_90|CONFIDENCE_95|CONFIDENCE_975|CONFIDENCE_99|CONFIDENCE_OTHER|GEOMETRIC_COEFFICIENT",
  "measurements": [
    {
      "group": "Arm group name",
      "groupId": "...",
      "paramValue": "...",
      "dispersionLowerLimit": "...",
      "dispersionUpperLimit": "..."
    }
  ]
}
```

---

## TrialCore — lookup

`POST /cores/trialcore/records/lookup`

```json
{
  "items": [
    {"nctId": "NCT06012345"},
    {"nctId": "NCT05999999"}
  ]
}
```

**Constraint:** each item must contain exactly one `nctId`. Same response shape as BiomedCore lookup; items fail independently.

---

## DrugCore — search parameters

Search matches drug names, trade names, synonyms, and descriptions — query by drug-class terms (e.g. `GLP-1`), not gene/target symbols.

| Param | Type | Notes |
|---|---|---|
| `query` | string (required) | Drug names, trade names, synonyms, descriptions |
| `limit` | int | 1–300, default 20 |
| `include` | string (repeatable) | `parent`, `children`, `referencesTrialCore`, `referencesBiomedCore`, `referencesRegulatoryCore` |
| `drugType` | enum (repeatable) | `SMALL_MOLECULE`, `ANTIBODY`, `PROTEIN`, `OLIGONUCLEOTIDE`, `GENE`, `ENZYME`, `ANTIBODY_DRUG_CONJUGATE`, `VACCINE_COMPONENT`, `CELL`, `OLIGOSACCHARIDE`, `VACCINE`, `UNKNOWN` |
| `maxClinicalStage` | enum (repeatable) | `PRECLINICAL`, `IND`, `EARLY_PHASE1`, `PHASE1`, `PHASE1/PHASE2`, `PHASE2`, `PHASE2/PHASE3`, `PHASE3`, `PREAPPROVAL`, `APPROVAL`, `UNKNOWN` |

---

## DrugCore — record schema

### Default fields (always returned)

```
amassId            string         AMDC_… canonical ID
chemblId           string|null    ChEMBL molecule ID
name               string|null    Primary drug name
description        string|null    Free-text description (clinical stage, indications)
synonyms           string[]       Alternative names
tradeNames         string[]       Brand / trade names
drugType           string|null    Modality, e.g. SMALL_MOLECULE, ANTIBODY
maxClinicalStage   string|null    Highest stage reached, e.g. PHASE3, APPROVAL
inchiKey           string|null    InChIKey structure hash
canonicalSmiles    string|null    Canonical SMILES
```

### Optional fields (via `include`)

```
parent                     string|null   AMDC_… ID of the parent record (intra-core hierarchy)
children                   string[]      AMDC_… IDs of child records (salts, combos)
referencesTrialCore        string[]      AMTC_… IDs of associated trials
referencesBiomedCore       string[]      AMBC_… IDs of associated publications
referencesRegulatoryCore   string[]      AMRC_… IDs of FDA/EMA authorizations
```

The parent/children hierarchy collapses salt forms, prodrugs, and fixed-dose combinations onto a single active ingredient. `parent: null` = top of hierarchy; `children: []` = leaf.

**Coverage note:** `referencesTrialCore` is densely populated (metformin → 1,818 trials) but `referencesBiomedCore` is currently sparse (single digits, sometimes empty). Treat an empty list as "no links recorded," not "no evidence."

---

## DrugCore — lookup

`POST /cores/drugcore/records/lookup`

```json
{
  "items": [
    {"chemblId": "CHEMBL1201583"},
    {"chemblId": "CHEMBL9999999"}
  ]
}
```

**Constraint:** each item must contain exactly one `chemblId`. A single ChEMBL ID can resolve to multiple Amass IDs, so `amassIds` is always an array. Items fail independently.

---

## RegulatoryCore — search parameters

One record = one authorization (FDA or EMA). `query` matches the structured metadata **and** sweeps the parsed full text of every FDA label, FDA review, EMA SmPC, and EMA EPAR. When document content drives a match, the hit sections come back on `documentSections[]` with a `matchedText` excerpt (empty array = metadata-only match).

| Param | Type | Notes |
|---|---|---|
| `query` | string (required) | Product name, active substance, indication, holder — plus document full text |
| `limit` | int | 1–300, default 20 |
| `include` | string (repeatable) | `emaDetails`, `fdaDetails`, `referencesDrugCore` |
| `agency` | enum (repeatable) | `FDA`, `EMA` |
| `moleculeType` | enum (repeatable) | `SMALL_MOLECULE`, `ANTIBODY`, `PROTEIN`, `ENZYME`, `OLIGONUCLEOTIDE`, `GENE`, `CELL`, `ANTIBODY_DRUG_CONJUGATE`, `VACCINE_COMPONENT`, `VACCINE`, `OLIGOSACCHARIDE`, `UNKNOWN` |
| `authorizationStatus` | enum (repeatable) | `ACTIVE`, `APPROVED_NOT_MARKETED`, `CONDITIONAL`, `SUSPENDED`, `WITHDRAWN_VOLUNTARY`, `WITHDRAWN_FORCED`, `REVOKED`, `LAPSED_SUNSET`, `REFUSED`, `WITHDRAWN_DURING_REVIEW`, `EXPIRED`, `UNKNOWN` (case-insensitive on input) |
| `hasDesignation` | enum (repeatable) | `PRIORITY_REVIEW`, `BREAKTHROUGH_THERAPY`, `FAST_TRACK`, `RMAT`, `ACCELERATED_APPROVAL`, `ACCELERATED_ASSESSMENT`, `PRIME`, `CONDITIONAL_MA`, `EXCEPTIONAL_CIRCUMSTANCES` — each applies only to the agency that owns it |
| `isOrphan` | bool | Exact cross-walk: FDA Orphan Drug / EMA Orphan Medicine |
| `minAuthorizationDate` / `maxAuthorizationDate` | ISO date | |
| `amassId` | string | Scope the full-text search to a single record's source documents |

---

## RegulatoryCore — record schema

### Default fields (always returned)

```
amassId                        string        AMRC_… canonical ID
agency                         string        FDA | EMA
name                           string|null   Primary product / brand name
activeSubstance                string|null
moleculeType                   string|null   Projected from DrugCore
authorizationStatus            string|null   Unified FDA + EMA status
procedureType                  string|null   FDA: NDA | BLA | ANDA | UNKNOWN; EMA: CENTRALISED_HUMAN, WITHDRAWAL_HUMAN, CENTRALISED_VETERINARY, WITHDRAWAL_VETERINARY, UNKNOWN
therapeuticIndication          string|null
marketingAuthorisationHolder   string|null
authorizationDate              string|null   ISO date (FDA approval / EMA MA grant)
firstAuthorizationDate         string|null
lastUpdateDate                 string|null
sourceUrl                      string|null   Agency landing page
isOrphan                       boolean|null
designations                   object[]      See designations shape
authorizationsByAgency         object[]      Cross-market link — always populated, cannot be suppressed
documentSections               object[]      Search: match evidence w/ matchedText. Get-by-ID: full content-free TOC. Always populated
```

### Optional fields (via `include`)

```
fdaDetails           object|null   FDA-specific block (null on EMA records)
emaDetails           object|null   EMA-specific block (null on FDA records)
referencesDrugCore   string[]      AMDC_… IDs of the product's active ingredients
```

### `designations` shape

```json
{
  "axis": "REVIEW_ACCELERATION | DEVELOPMENT_SUPPORT | EARLY_ACCESS_BASIS",
  "type": "ACCELERATED_APPROVAL",
  "agency": "FDA",
  "nativeName": "Accelerated Approval",
  "basis": "SURROGATE_ENDPOINT | INCOMPLETE_DATA | UNCONFIRMABLE_DATA | null",
  "indication": "… (FDA Accelerated Approval only)",
  "postMarketingObligation": true
}
```

The `axis` puts agency-native programs on shared comparison axes so FDA and EMA designations are comparable without being equated.

### `authorizationsByAgency` shape

```json
{
  "amassId": "AMRC_…",
  "agency": "EMA",
  "name": "...",
  "authorizationStatus": "WITHDRAWN_VOLUNTARY"
}
```

Lists the same product's other-market authorizations (self excluded), each with its own status — cross-market status divergence (active in US, withdrawn in EU) reads straight off one response.

### `documentSections` shape

Three variants, by where the array appears:

- **Evidence** (on search): includes `matchedText` excerpt that drove the hit; no `content`.
- **TOC** (on get-by-ID): structural fields only; no `matchedText`, no `content`.
- **Section** (single-section fetch): includes full `content`; no `matchedText`.

Shared base fields:

```
documentSectionId   string        AMRCDS_… — fetch full text via the document-sections endpoint
amassId             string        Parent AMRC_… record
docType             string        FDA_LABEL | FDA_REVIEW | EMA_SMPC | EMA_EPAR
path                string|null   Numbered/stable for FDA_LABEL (e.g. "5.2") and EMA_SMPC (e.g. "4.1"); opaque for FDA_REVIEW and EMA_EPAR
title               string|null   Section heading
textType            string|null   e.g. Label, SmPC, Medical_Review
sourceUrl           string|null   Direct URL to the source PDF
sourceDate          string|null   Source revision date (ISO)
```

Always address a section by its `documentSectionId`, never by `path`.

### `fdaDetails` shape

```
applicationNumber, prescriptionClass, submissionClassCode, labelDate, labelUrl,
ndc[], splSetId[], withdrawalDate, withdrawalReason, withdrawalSourceUrl
```

### `emaDetails` shape

```
productNumber, category, opinionStatus, isBiosimilar, isAdvancedTherapy,
isGenericOrHybrid, additionalMonitoring, pharmacotherapeuticGroup, patientSafety,
latestProcedure, revisionNumber, smpcUrl, smpcDate, opinionAdoptedDate,
europeanCommissionDecisionDate, withdrawalOfApplicationDate,
refusalOfMarketingAuthorisationDate, withdrawalExpiryRevocationLapseDate, firstPublishedDate
```

---

## RegulatoryCore — reading source documents

Three steps, no PDF parsing on your side:

1. **List the TOC.** `GET /records/{amassId}` → `documentSections[]` (content-free index).
2. **(Optional) Scoped search.** `GET /records?query=<phrase>&amassId=AMRC_…` → just that record, `documentSections[]` narrowed to hits with `matchedText`.
3. **Fetch the section.** `GET /records/{amassId}/document-sections/{documentSectionId}` → full `content` + `sourceUrl` to the PDF.

To compare the same topic across markets: resolve the counterpart record from `authorizationsByAgency`, list its TOC, fetch the equivalent section (FDA label `2 Dosage and Administration` ↔ SmPC `4.2 Posology`).

---

## RegulatoryCore — lookup

`POST /cores/regulatorycore/records/lookup`

```json
{
  "items": [
    {"fdaApplicationNumber": "BLA125514"},
    {"emaProductNumber": "EMEA/H/C/003820"},
    {"ndc": "0169-4404"},
    {"splSetId": "ee06186f-2aa3-4990-a760-757579d8f77b"}
  ]
}
```

**Constraint:** each item must contain exactly one of `fdaApplicationNumber`, `emaProductNumber`, `ndc`, or `splSetId`. `amassIds` is always an array; items fail independently.

---

## Cross-core linking

| Direction | Field | Include flag | Contents |
|---|---|---|---|
| Paper → trials | `referencesTrialCore` | `include=referencesTrialCore` | `AMTC_…` IDs |
| Paper → papers (cites) | `references` | `include=references` | `AMBC_…` IDs |
| Paper → papers (cited by) | `citedBy` | `include=citedBy` | `AMBC_…` IDs |
| Trial → papers | `referencesBiomedCore` | `include=referencesBiomedCore` | `AMBC_…` IDs |
| Drug → parent / children | `parent` / `children` | `include=parent&include=children` | `AMDC_…` IDs |
| Drug → trials | `referencesTrialCore` | `include=referencesTrialCore` | `AMTC_…` IDs |
| Drug → papers | `referencesBiomedCore` | `include=referencesBiomedCore` | `AMBC_…` IDs |
| Drug → authorizations | `referencesRegulatoryCore` | `include=referencesRegulatoryCore` | `AMRC_…` IDs |
| Authorization → active ingredients | `referencesDrugCore` | `include=referencesDrugCore` | `AMDC_…` IDs |
| Authorization → other-market authorizations | `authorizationsByAgency` | always present | `AMRC_…` IDs + status |

There is no intra-core link in TrialCore (no trial-to-trial graph). The drug ↔ authorization relationship can be traversed from either side.

---

## Errors

### Standard error

```json
{"error": {"status": 400, "code": "BAD_REQUEST", "message": "..."}}
```

### Validation error (per-field)

```json
{
  "error": {
    "status": 400,
    "code": "BAD_REQUEST",
    "message": "Validation failed",
    "in": "query",
    "fields": {"limit": "Must be between 1 and 300"}
  }
}
```

When you get a 400, read `error.fields` before retrying — it tells you exactly which parameter is wrong.

### Status codes

| Status | Code | Meaning |
|---|---|---|
| 400 | `BAD_REQUEST` | Invalid parameters; inspect `error.fields` |
| 401 | `UNAUTHORIZED` | Missing or malformed key |
| 403 | `FORBIDDEN` | Valid key but lacks permission for the resource |
| 404 | `NOT_FOUND` | Record not found (get-by-ID) |
| 422 | `UNPROCESSABLE_ENTITY` | Semantically invalid input |
| 429 | `TOO_MANY_REQUESTS` | Read `Retry-After`; exponential backoff |
| 500 | `INTERNAL_SERVER_ERROR` | Retry once with backoff |

---

## Useful external links from a record

- PubMed: `https://pubmed.ncbi.nlm.nih.gov/{pmid}/`
- PubMed Central: `https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/`
- DOI: `https://doi.org/{doi}`
- ClinicalTrials.gov: `https://clinicaltrials.gov/study/{nctId}`
- ChEMBL: `https://www.ebi.ac.uk/chembl/explore/compound/{chemblId}`
- Agency landing page / source PDFs: `sourceUrl` on RegulatoryCore records and document sections

---

## Gotchas

1. **No pagination.** `limit` caps at 300; narrow with filters.
2. **No sort.** Relevance only.
3. **Lookup items are mutually exclusive** — exactly one identifier per item.
4. **Lookup items fail independently** — always check each result for `error`. `amassIds` is always an array.
5. **`fulltext` is large** — only request when answering the question requires the body text.
6. **`include` is repeatable** — chain `?include=a&include=b`.
7. **Multi-value filters: OR within a filter, AND across filters.**
8. **JuFo `null` ≠ `0`** — `null` means "not evaluated" and is excluded by any `minJournalQualityJufo` filter.
9. **Rate limits are per user+org**, not per key.
10. **Always read from `data`** on success; errors live at top-level `error`.
11. **The lookup endpoint is `POST`** even though it's a read.
12. **DrugCore search matches names/synonyms/descriptions**, not gene/target symbols — query drug-class terms.
13. **RegulatoryCore `query` sweeps document full text** — an empty `documentSections[]` on a search hit means the match was metadata-only.
14. **Address document sections by `documentSectionId`**, not `path` (paths are opaque for FDA reviews and EPARs).
15. **`authorizationsByAgency` and `documentSections` are always present** on RegulatoryCore records — they cannot be requested or suppressed via `include`.
16. **DrugCore `referencesBiomedCore` is sparse** — empty means "no links recorded," not "no evidence."
