# Amass API Reference

> Snapshot of <https://platform.amass.tech/markdown> as of 2026-04-30.
> If a request behaves contrary to what's documented here (e.g. a 400 cites a filter or value this file doesn't list), fetch the live page to check whether the API has moved on.

Base URL: `https://api.amass.tech/api/v1`

---

## Endpoints

### BioMedCore — `/cores/biomedcore`
- `GET  /records` — search biomedical literature
- `GET  /records/{amassId}` — fetch a single record (Amass ID starts `AMBC_`)
- `POST /records/lookup` — convert PMIDs/DOIs to Amass IDs

### TrialCore — `/cores/trialcore`
- `GET  /records` — search clinical trials
- `GET  /records/{amassId}` — fetch a single record (Amass ID starts `AMTC_`)
- `POST /records/lookup` — convert NCT IDs to Amass IDs

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
- Lookup (`POST /records/lookup`): $0.01 per call (regardless of item count).

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

## BioMedCore — search parameters

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

`include` is repeatable: `?include=fulltext&include=referencesTrialCore`.

### Journal quality (JuFo) tiers

- `3` — highest, extremely consistent impact
- `2` — domain-leading
- `1` — peer-reviewed with expert editorial boards
- `0` — evaluated as low quality
- `null` — **not evaluated** (≠ `0`)

Filtering with `minJournalQualityJufo=2` excludes all `null` records. JuFo is the Finnish ranking, so many legitimate non-Finnish journals are `null`. Use the filter only when you genuinely want to gate on the Finnish ranking; otherwise prefer `minCitationCount`.

---

## BioMedCore — record schema

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

`references` and `citedBy` form an intra-BioMedCore citation graph. `referencesTrialCore` is the cross-core link to TrialCore.

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

## BioMedCore — lookup

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

Items fail independently — always check each result for an `error` field before reading `amassIds`.

---

## TrialCore — search parameters

| Param | Type | Notes |
|---|---|---|
| `query` | string (required) | Full-text search |
| `limit` | int | 1–300, default 20 |
| `include` | string (repeatable) | `outcomes`, `detailedDescription`, `referencesBiomedCore` |
| `phase` | enum | `EARLY_PHASE1`, `PHASE1`, `PHASE1/PHASE2`, `PHASE2`, `PHASE2/PHASE3`, `PHASE3`, `PHASE4`, `NA` |
| `overallStatus` | enum | `RECRUITING`, `NOT_YET_RECRUITING`, `ENROLLING_BY_INVITATION`, `ACTIVE_NOT_RECRUITING`, `SUSPENDED`, `TERMINATED`, `COMPLETED`, `WITHDRAWN`, `UNKNOWN`, `WITHHELD`, `AVAILABLE`, `NO_LONGER_AVAILABLE`, `TEMPORARILY_NOT_AVAILABLE`, `APPROVED_FOR_MARKETING` |
| `studyType` | enum | `INTERVENTIONAL`, `OBSERVATIONAL`, `EXPANDED_ACCESS` |
| `sponsorType` | enum | `NIH`, `FED`, `INDUSTRY`, `OTHER`, `OTHER_GOV`, `INDIV`, `NETWORK` |
| `interventionType` | enum | `DRUG`, `DEVICE`, `BIOLOGICAL`, `PROCEDURE`, `RADIATION`, `BEHAVIORAL`, `GENETIC`, `DIETARY_SUPPLEMENT`, `DIAGNOSTIC_TEST`, `COMBINATION_PRODUCT`, `OTHER` |
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

**Constraint:** each item must contain exactly one `nctId`. Same response shape as BioMedCore lookup; items fail independently.

---

## Cross-core linking

| Direction | Field | Include flag | Contents |
|---|---|---|---|
| Paper → trials | `referencesTrialCore` | `include=referencesTrialCore` | `AMTC_…` IDs |
| Trial → papers | `referencesBiomedCore` | `include=referencesBiomedCore` | `AMBC_…` IDs |
| Paper → papers (cites)  | `references` | `include=references` | `AMBC_…` IDs |
| Paper → papers (cited by) | `citedBy` | `include=citedBy` | `AMBC_…` IDs |

There is no intra-core link in TrialCore (no trial-to-trial graph).

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

---

## Gotchas

1. **No pagination.** `limit` caps at 300; narrow with filters.
2. **No sort.** Relevance only.
3. **Lookup items are mutually exclusive** — exactly one identifier per item.
4. **Lookup items fail independently** — always check each result for `error`.
5. **`fulltext` is large** — only request when answering the question requires the body text.
6. **`include` is repeatable** — chain `?include=a&include=b`.
7. **JuFo `null` ≠ `0`** — `null` means "not evaluated" and is excluded by any `minJournalQualityJufo` filter.
8. **Rate limits are per user+org**, not per key.
9. **Always read from `data`** on success; errors live at top-level `error`.
10. **The lookup endpoint is `POST`** even though it's a read.
