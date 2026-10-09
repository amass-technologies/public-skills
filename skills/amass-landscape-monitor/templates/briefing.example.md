# TAAR1 agonists in schizophrenia: landscape briefing

Field `taar1-schizophrenia`, mapped through the Amass MCP on 2026-10-08. Every number below comes
from the run summary (`log/summary-baseline-1.md`), which the helper computed from the ledgers.

## The field in three sentences

TAAR1 agonism in psychiatry is a two-drug clinical field: ulotaront (SEP-363856) carries the
development programme, ralmitaront's programme has stopped, and four newer agonists named in
the literature (selutaront, LK00764, AP163, PCC0105004) have no trial record yet. The map holds
52 in-scope trial records, 159 papers, 45 patent families, 3 drug records and the TAAR1 gene
record; the target-level literature alone has 23 papers from the last 24 months. No TAAR1
agonist is approved anywhere, so there is no authorization to track yet.

## By drug

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

**Ulotaront.** 43 trial records, of which 14 are Phase 3, 4 Phase 2 and 13 Phase 1; 14 are
active and 24 completed, 3 stopped, and 11 have posted results. The trial records include
registrations of the same study in several registries (an NCT record and a Japanese jRCT
record for one trial, for instance), which is why the count is of records rather than studies.
39 papers name the drug, 6 of them from the last two years, and 7 patent families cover it.
DrugCore holds two records, the base compound and its hydrochloride.

**Ralmitaront.** 9 trial records, 2 Phase 2 and 3 Phase 1, none active: 6 completed, 1 stopped,
4 with results. Two papers and one drug record; no patent family names it.

**Newer agonists.** Selutaront, LK00764, AP163 and PCC0105004 each appear in one paper and in no
trial. They have no DrugCore record yet, which is how ChEMBL-anchored data treats compounds this
new; their names are in the plan, so a registered trial will be picked up by the next update.

**Target-level work.** 111 papers and 15 patent families are about TAAR1 as a target without
naming one of these drugs, 23 of the papers from the last two years. 20 papers and 18 patent
families match neither a drug nor the target by name (class reviews, generic patent titles) and
are listed under Unassigned on the Landscape sheet.

## Authorizations

None. No TAAR1 agonist holds an FDA or EMA authorization; the 20 RegulatoryCore records the
searches returned are other products whose label text mentions an agonist, and all are screened
out with that reason.

## Coverage

All three anchor trials were found by search, including the Japanese trial under both its NCT
and jRCT registrations. Of the 32 trials linked from the ulotaront and ralmitaront drug records,
the searches had reached every one. On TrialCore the code, mechanism and sponsor facets
saturated; the drug facet, and the paper and patent facets, were still producing when the run
ended, so a later "extend" would add papers and patents before it adds trials. 23 records are
unsure and need a call: 4 trials whose records do not state the mechanism (two SEP-380135
trials, one SEP-479, one VV119), 6 papers at the boundary of the scope (broad reviews and
trace-amine physiology), and 13 patents, among them a likely ulotaront use patent whose abstract
never names TAAR1.

## How to use the files

The workbook `taar1-schizophrenia.xlsx` opens on the Landscape sheet: the table above, the
scope, the coverage box and these instructions. Key records lists the Phase 2 and later trials
and the authorizations, one line each. The Core sheets (TrialCore, BiomedCore, DrugCore,
GeneCore, RegulatoryCore, PatentCore) hold every in-scope and unsure record with its decision
and reason; filter on the decision column to see the unsure ones. Excluded holds everything that
was screened out, with the reason. The folder `landscapes/taar1-schizophrenia` is the memory
for updates, so keep it; say "run the update" to get what changed since this map, by drug and
by Core. The in-scope trials can also be exported as a watchlist for `amass-watchlist-monitor`,
which tracks those records exactly.
