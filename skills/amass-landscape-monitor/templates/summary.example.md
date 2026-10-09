# taar1-schizophrenia: update run update-1, 2026-10-09

Question: TAAR1 agonists and ulotaront in schizophrenia and related psychiatric indications

One line: update over DrugCore, GeneCore, TrialCore, RegulatoryCore; nothing new in the field and no change on a tracked column.

## Scope

- In: Clinical trials (any phase, status or registry) of a TAAR1 agonist in schizophrenia or another psychiatric or neurological indication, including healthy-volunteer and PK studies of such a drug
- In: Papers reporting clinical, translational or pharmacological work on a TAAR1 agonist, or on TAAR1 as a target in psychiatry
- In: Drugs whose mechanism of action includes TAAR1 agonism; the TAAR1 gene record
- In: FDA or EMA authorizations of a TAAR1 agonist
- In: Patents claiming TAAR1 agonist compounds, their formulations or their use in psychiatric disease
- Out: Solriamfetol and other drugs where TAAR1 activity is incidental, when studied in sleep or non-psychiatric indications
- Out: Trace amine or TAAR1 biology with no agonist drug and no psychiatric angle (olfaction, metabolism, TAAR1 knockout physiology)
- Out: Antipsychotic trials that merely use a TAAR1 agonist as a comparator name in background text

## By entity

| Entity | Kind | Trials | Phase 3 | Phase 2 | Phase 1 | Active | Completed | Stopped | With results | Papers | Papers, last 24 months | Patent families | Authorizations | Drug records |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ulotaront | drug | 43 | 14 | 4 | 13 | 14 | 24 | 3 | 11 | 39 | 6 | 7 | 0 | 2 |
| ralmitaront | drug | 9 | 0 | 2 | 3 | 0 | 6 | 1 | 4 | 2 | 1 | 0 | 0 | 1 |
| selutaront | drug | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 1 | 0 | 0 | 0 |
| LK00764 | drug | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| AP163 | drug | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| PCC0105004 | drug | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 1 | 0 | 0 | 0 |
| TAAR1 | target | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 111 | 23 | 15 | 0 | 0 |
| TAAR1 agonists | class | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 3 | 3 | 0 | 0 |
| Unassigned |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 20 | 4 | 18 | 0 | 0 |

A record naming two drugs counts under both; a target entity counts gene records, papers and patents, not trials; a class entity takes the records that name no drug or target. Unassigned records match no alias yet.

## This run

| Core | Since | Queries | New records | Changes | Rewritten unchanged |
| --- | --- | --- | --- | --- | --- |
| DrugCore | 2026-10-07 | 2 | 0 | 0 | 3 |
| GeneCore | 2026-10-07 | 2 | 0 | 0 | 1 |
| TrialCore | 2026-10-07 | 4 | 157 | 157 | 43 |
| RegulatoryCore | 2026-10-07 | 2 | 0 | 0 | 0 |

Skipped (not due): BiomedCore, PatentCore

## Coverage

| Core | In scope | Unsure | Screened out | Queries | Last checked | Interval |
| --- | --- | --- | --- | --- | --- | --- |
| DrugCore | 3 | 0 | 52 | 6 | 2026-10-08 | 90d |
| GeneCore | 1 | 0 | 2 | 2 | 2026-10-08 | 7d |
| TrialCore | 52 | 4 | 319 | 15 | 2026-10-08 | 1d |
| BiomedCore | 159 | 6 | 63 | 10 | 2026-10-08 | 1d |
| RegulatoryCore | 0 | 0 | 20 | 5 | 2026-10-08 | 7d |
| PatentCore | 45 | 13 | 115 | 5 | 2026-10-08 | 90d |

Anchors: 3 of 3 found by search.
Cross-links: of 1 BiomedCore ids linked from in-scope records: 1 reached by search, 0 only by fetch, 0 still open, 0 out of scope by design.
Cross-links: of 2 GeneCore ids linked from in-scope records: 1 reached by search, 0 only by fetch, 0 still open, 1 out of scope by design.
Cross-links: of 32 TrialCore ids linked from in-scope records: 32 reached by search, 0 only by fetch, 0 still open, 0 out of scope by design.
Cross-links: of 1 DrugCore ids linked from in-scope records: 1 reached by search, 0 only by fetch, 0 still open, 0 out of scope by design.
Saturated facets: DrugCore drug, TrialCore code, TrialCore mechanism, TrialCore sponsor, BiomedCore drug, RegulatoryCore drug. Still producing: DrugCore mechanism, DrugCore target, GeneCore target, TrialCore drug, BiomedCore code, BiomedCore mechanism, RegulatoryCore mechanism, PatentCore mechanism, PatentCore drug, PatentCore sponsor.
Unsure records to settle: 23 (4 TrialCore, 6 BiomedCore, 13 PatentCore).

## Files

- `taar1-schizophrenia.xlsx`: Landscape and Key records first, then one sheet per Core, Excluded, and the run log.
- `ledger/<core>.csv`: the source of truth the workbook is built from.
- `field.yaml`: the field definition, entities and plan; `log/`: every call, run and change.

## What this cannot see

Search is relevance-ranked and capped per call, so the map is as complete as the plan and its saturation say. Date-filtered searches miss deletions, results-only revisions on trials, label and SmPC section-only revisions, and in-place publicationDate corrections; the REST change feed used by amass-watchlist-monitor sees those. PatentCore carries no Amass dates on MCP, so its updates find newly published patents only. A record rewritten by Amass is not a change; only a differing tracked column is.

## Credits

Baseline 73; updates 20; total 93 nominal credits (search 2, fetch 1). For the plan owner; not relayed in chat.

