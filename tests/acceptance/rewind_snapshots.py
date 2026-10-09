"""Test-only helper for the watchlist monitor acceptance tests (API-mode acceptance tests 3 and 4).

A replay over snapshots captured minutes earlier finds nothing to diff, because the
API has no history: the snapshot already holds the current state. This rewinds the
stored snapshots of tests/fixtures/trialcore-watchlist.yaml to a state that predates
a known change, so a replay shows how the digest classifies it.

    python3 tests/acceptance/rewind_snapshots.py <state>/trialcore-fixture/snapshots pre-change
    python3 tests/acceptance/rewind_snapshots.py <state>/trialcore-fixture/snapshots results-revision

pre-change       NCT07066254 back to NOT_YET_RECRUITING (simulated; the registry's own
                 history is not public); NCT04907084 back to before its results were
                 first posted on 2026-09-21 (no results, no outcome rows).
results-revision NCT04907084 with one measurement at an earlier value and one extra
                 outcome that the current record no longer has.
citation-counts  Any BiomedCore watchlist: the first five snapshots (by Amass ID) get a
                 citationCount three lower, as before a citation-count refresh.
"""

import json
import os
import sys

WITHDRAWN = "AMTC_KGCxl0fhol8AzsKSl4lwafcHyxW"
HAS_RESULTS = "AMTC_NOGpeSN9g9AS8zST4wJmGXmNbdW"


def edit(snapdir, amass_id, change):
    path = f"{snapdir}/{amass_id}.json"
    with open(path) as fh:
        snap = json.load(fh)
    change(snap["record"])
    snap["fetchedAt"] = "2026-07-01T00:00:00Z"
    snap["appliedThrough"] = None
    with open(path, "w") as fh:
        json.dump(snap, fh, indent=1, sort_keys=True)


def pre_withdrawal(record):
    record["overallStatus"] = "NOT_YET_RECRUITING"
    record["whyStopped"] = None


def pre_results(record):
    record["hasResults"] = False
    record["resultsFirstPostDate"] = None
    record["outcomes"] = []


def earlier_results(record):
    record["outcomes"][2]["measurements"][0]["paramValue"] = "999"
    record["outcomes"].append({"outcomeType": "SECONDARY", "title": "Outcome later removed by the sponsor", "measurements": []})


if __name__ == "__main__":
    snapdir, mode = sys.argv[1], sys.argv[2]
    if mode == "pre-change":
        edit(snapdir, WITHDRAWN, pre_withdrawal)
        edit(snapdir, HAS_RESULTS, pre_results)
    elif mode == "results-revision":
        edit(snapdir, HAS_RESULTS, earlier_results)
    elif mode == "citation-counts":
        papers = sorted(f[:-5] for f in os.listdir(snapdir) if f.startswith("AMBC_") and f.endswith(".json") and ".deleted" not in f)
        for amass_id in papers[:5]:
            edit(snapdir, amass_id, lambda r: r.update(citationCount=max(0, (r.get("citationCount") or 0) - 3)))
    else:
        sys.exit("mode is pre-change, results-revision or citation-counts")
    print(f"rewound snapshots: {mode}")
