<!--
Example digest, copied unedited from a real run on 2026-10-05 of templates/watchlist.example.yaml
against api.amass.tech. To show several classes at once, the stored snapshots of two trials were
first rewound to before their changes (tests/acceptance/rewind_snapshots.py pre-change: NCT07066254
back to NOT_YET_RECRUITING, NCT04907084 back to before its results were posted), then the feed was
replayed with --since 2026-07-07. Everything else is live output.
-->

# Watchlist digest: example-trials

**Core:** TrialCore · **Run:** 2026-10-05 10:20:39 UTC · **Records watched:** 3 in 1 chunk(s)

**Feed window:** from 2026-07-07 (replay requested with --since)

## TrialCore

### Status and stage changes (1)

- **The Mepilex Cesarean Delivery Trial** · `AMTC_KGCxl0fhol8AzsKSl4lwafcHyxW` · NCT07066254 · https://clinicaltrials.gov/study/NCT07066254 · feed `updated` 2026-08-28
  - overallStatus: `NOT_YET_RECRUITING` → `WITHDRAWN`
  - whyStopped: (none) → “Proposed superiority study framework was not approved by the IRB. Study will be redeveloped as a non-inferiority study and resubmitted to the IRB.”

### Results posted or revised (1)

- **Serine and Fenofibrate Study in Patients With MacTel Type 2** · `AMTC_NOGpeSN9g9AS8zST4wJmGXmNbdW` · NCT04907084 · https://clinicaltrials.gov/study/NCT04907084 · feed `updated` 2026-09-23
  - hasResults: false → true
  - resultsFirstPostDate: (none) → `2026-09-21`
  - outcomes: 4 added
    - added SECONDARY “Percentage Change in Serum Serine and Glycine Levels From Baseline to 6 Weeks”
    - added SECONDARY “Percentage Change in Serum Lipid Levels From Baseline to 6 Weeks”
    - added PRIMARY “Safety Assessment”
    - added PRIMARY “Percentage Change of Serum Deoxysphingolipid Levels From Baseline to 6 Weeks”

### Metadata-only (1)

- **A Trial of the Efficacy and Safety of Fixed Doses of SEP-363856 in Adults With Generalized Anxiety Disorder** · `AMTC_UOVYXZ2dY42Kn663vgBL0221KY4` · NCT07759128 · https://clinicaltrials.gov/study/NCT07759128 · feed `updated` 2026-09-24
  - no field in the record changed

## Summary

| Class | Records |
| --- | ---: |
| Status and stage changes | 1 |
| Results posted or revised | 1 |
| Metadata-only | 1 |

- Records with substantive changes or removals: 2
- Feed: 1 page(s), 3 event(s) on 3 record(s); 0 already reflected in the stored snapshots
- Fetched: 3 record(s) (0 baseline, 3 with feed events); 0 removal(s) need no fetch
- Credits spent: 4 credits ($0.04): feed pages 1, record GETs 3, lookups 0
- Next expected refresh: 2026-10-06. TrialCore refreshes daily; a day's changes reach the feed about one day later.
