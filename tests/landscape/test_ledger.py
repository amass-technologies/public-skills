"""Unit and end-to-end tests for amass-landscape-monitor's ledger.py on synthetic tool results.
No network, no Amass calls. Run from the repo root:

    python3 tests/landscape/test_ledger.py
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "skills" / "amass-landscape-monitor" / "scripts"))

import ledger  # noqa: E402

FIELD_YAML = """\
name: test-field
question: TAAR1 agonists in schizophrenia
cores: [trialcore, biomedcore, drugcore, regulatorycore, patentcore]
include:
  - "Trials of a TAAR1 agonist: any psychiatric indication"
  - Papers on ulotaront
exclude:
  - Trace amine chemistry without TAAR1 agonism
anchors:
  - core: trialcore
    id: NCT04072354
    note: DIAMOND 1
  - core: trialcore
    id: NCT09999999
    note: a trial the plan should miss
budget:
  baseline: 30
  update: 12
stopping:
  consecutive: 2
  minNew: 1
queries:
  - id: T01
    core: trialcore
    facet: drug
    query: ulotaront
    filters:
      interventionType: DRUG
    limit: 2
    update: true
  - id: T02
    core: trialcore
    facet: drug
    query: SEP-363856
    limit: 2
    update: true
  - id: B01
    core: biomedcore
    facet: drug
    query: ulotaront
    limit: 2
    update: true
  - id: D01
    core: drugcore
    facet: mechanism
    query: TAAR1 agonist
    limit: 2
  - id: R01
    core: regulatorycore
    facet: drug
    query: ulotaront
    limit: 2
    update: true
  - id: P01
    core: patentcore
    facet: mechanism
    query: TAAR1 agonist
    limit: 2
    update: true
"""

T1 = {"amassId": "AMTC_aaaaaaaaaaaaaaaa", "decision": "in", "reason": "ulotaront phase 3", "nctId": "NCT04072354",
      "registryId": "NCT04072354", "sourceRegistry": "clinicaltrials_gov", "briefTitle": "DIAMOND 1",
      "sponsorName": "Sumitomo", "phase": "PHASE3", "overallStatus": "COMPLETED", "studyType": "INTERVENTIONAL",
      "startDate": "2019-11-01", "completionDate": "2023-02-01", "enrollment": 435,
      "conditions": ["Schizophrenia"], "interventionNames": ["Ulotaront", "Placebo"],
      "interventionTypes": ["DRUG"], "facilityCountries": ["US", "JP"], "hasResults": True}
T2 = {"amassId": "AMTC_bbbbbbbbbbbbbbbb", "decision": "out", "reason": "not a TAAR1 agonist", "nctId": "NCT00000002",
      "briefTitle": "Something else", "phase": "PHASE2", "overallStatus": "RECRUITING", "hasResults": False,
      "conditions": ["Depression"], "interventionNames": ["Drug X"]}
T3 = {"amassId": "AMTC_cccccccccccccccc", "decision": "in", "reason": "ralmitaront", "nctId": "NCT00000003",
      "briefTitle": "Ralmitaront in schizophrenia", "sponsorName": "Roche", "phase": "PHASE2",
      "overallStatus": "TERMINATED", "hasResults": False, "conditions": ["Schizophrenia"],
      "interventionNames": ["Ralmitaront", "Placebo"]}


def run(*argv: str, stdin: str = "") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    old_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ledger.main(list(argv))
    finally:
        sys.stdin = old_stdin
    return code, out.getvalue(), err.getvalue()


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class YamlTests(unittest.TestCase):
    def test_roundtrip(self):
        data = ledger.parse_yaml(FIELD_YAML)
        field, errors = ledger.normalise_field(data)
        self.assertEqual(errors, [])
        text = ledger.field_to_yaml(field)
        again, errors2 = ledger.normalise_field(ledger.parse_yaml(text))
        self.assertEqual(errors2, [])
        self.assertEqual(field, again)
        self.assertEqual(again["queries"][0]["filters"], {"interventionType": "DRUG"})
        self.assertEqual(again["include"][0], "Trials of a TAAR1 agonist: any psychiatric indication")

    def test_scalars_and_quotes(self):
        d = ledger.parse_yaml('a: "x: y"\nb: \'it\'\'s\'\nc: 3\nd: 1.5\ne: true\nf: null\ng: [1, two, "three, four"]\nh: {k: v, n: 2}\n')
        self.assertEqual(d, {"a": "x: y", "b": "it's", "c": 3, "d": 1.5, "e": True, "f": None,
                             "g": [1, "two", "three, four"], "h": {"k": "v", "n": 2}})

    def test_errors(self):
        with self.assertRaises(ledger.LedgerError):
            ledger.parse_yaml("a:\n\t- x\n")
        with self.assertRaises(ledger.LedgerError):
            ledger.parse_yaml("a: 1\na: 2\n")
        _, errors = ledger.normalise_field(ledger.parse_yaml("name: X\n"))
        self.assertTrue(any("name" in e for e in errors))
        _, errors = ledger.normalise_field(ledger.parse_yaml(FIELD_YAML.replace("interventionType: DRUG", "minCreateDate: 2026-01-01")))
        self.assertTrue(any("update pass" in e for e in errors))
        _, errors = ledger.normalise_field(ledger.parse_yaml(FIELD_YAML.replace("interventionType: DRUG", "phase: PHASE9")))
        self.assertTrue(any("phase" in e for e in errors))
        patent = FIELD_YAML.replace("    query: TAAR1 agonist\n    limit: 2\n    update: true\n",
                                    "    query: TAAR1 agonist\n    filters: {minPublicationDate: 2018-01-01, assignee: [Sunovion, Sumitomo]}\n    limit: 2\n    update: true\n")
        field, errors = ledger.normalise_field(ledger.parse_yaml(patent))
        self.assertEqual(errors, [])
        p01 = next(q for q in field["queries"] if q["id"] == "P01")
        self.assertEqual(p01["filters"], {"minPublicationDate": "2018-01-01", "assignee": "Sunovion,Sumitomo"})

    def test_dump_quotes_dangerous_strings(self):
        for s in ("a: b", "- x", "true", "12", "", " lead", "has # hash", "trail:"):
            self.assertEqual(ledger.parse_yaml("v: " + ledger._fmt_scalar(s))["v"], s)


class RowTests(unittest.TestCase):
    def test_normalise_values(self):
        core = ledger.CORES["trialcore"]
        errors: list[str] = []
        row = ledger.normalise_row(core, T1, "row 1", errors)
        self.assertEqual(errors, [])
        self.assertEqual(row["fields"]["enrollment"], "435")
        self.assertEqual(row["fields"]["hasResults"], "true")
        self.assertEqual(row["fields"]["interventionNames"], "Ulotaront | Placebo")
        self.assertEqual(row["fields"]["phase"], "PHASE3")

    def test_rejects(self):
        core = ledger.CORES["trialcore"]
        errors: list[str] = []
        ledger.normalise_row(core, {**T1, "phase": "PHASE 3"}, "row 1", errors)
        self.assertTrue(any("phase" in e for e in errors))
        errors = []
        ledger.normalise_row(core, {**T1, "unknownKey": 1}, "row 1", errors)
        self.assertTrue(any("unknown keys" in e for e in errors))
        errors = []
        ledger.normalise_row(core, {**T1, "amassId": "AMBC_aaaaaaaaaaaaaaaa"}, "row 1", errors)
        self.assertTrue(any("amassId" in e for e in errors))
        errors = []
        ledger.normalise_row(core, {**T2, "reason": ""}, "row 1", errors)
        self.assertTrue(any("needs a short reason" in e for e in errors))

    def test_gene_objects(self):
        core = ledger.CORES["genecore"]
        errors: list[str] = []
        row = ledger.normalise_row(core, {
            "amassId": "AMGC_aaaaaaaaaaaaaaaa", "decision": "in", "symbol": "TAAR1",
            "tractability": {"smallMolecule": {"clinical": ["Advanced Clinical"], "predictive": ["x"]}, "antibody": {"clinical": []}},
            "targetClass": {"path": ["Membrane receptor", "GPCR"], "leafChemblClassId": 1},
            "loeuf": {"lossOfFunction": {"loeuf": 1.234, "pli": 0.1}},
            "isEssential": {"isEssential": False, "cellLinesTested": 1000},
            "safetyEvents": [{"event": "cardiac", "datasource": "x"}, {"event": "cardiac"}, "hepatic"],
        }, "row 1", errors)
        self.assertEqual(errors, [])
        f = row["fields"]
        self.assertEqual(f["tractability"], "smallMolecule: Advanced Clinical")
        self.assertEqual(f["targetClass"], "Membrane receptor > GPCR")
        self.assertEqual(f["loeuf"], "1.23")
        self.assertEqual(f["isEssential"], "false")
        self.assertEqual(f["safetyEvents"], "cardiac | hepatic")


class EntityTests(unittest.TestCase):
    def test_matcher(self):
        ents = [{"name": "sotorasib", "aliases": ["AMG 510", "Lumakras"], "kind": "drug"},
                {"name": "KBP-042", "aliases": ["KBP"], "kind": "drug"},
                {"name": "D-1553", "aliases": ["garsorasib"], "kind": "drug"}]
        m = ledger.EntityMatcher(ents)
        self.assertEqual(m.match(["AMG-510 in NSCLC"]), ["sotorasib"])
        self.assertEqual(m.match(["LUMAKRAS tablets"]), ["sotorasib"])
        self.assertEqual(m.match(["amg510 racemate"]), ["sotorasib"])
        self.assertEqual(m.match(["KBP-042 in obesity"]), ["KBP-042"])
        self.assertEqual(m.match(["kbp089 phase 1"]), [])  # a short alias must be a whole token
        self.assertEqual(m.match(["D1553 monotherapy"]), ["D-1553"])
        self.assertEqual(m.match(["D 1553 plus sotorasib"]), ["sotorasib", "D-1553"])
        self.assertEqual(m.match([""]), [])
        # kinds: a target does not take trials; a class is the fallback bucket
        kinds = ledger.EntityMatcher([{"name": "KRAS", "aliases": [], "kind": "target"},
                                      {"name": "sotorasib", "aliases": [], "kind": "drug"},
                                      {"name": "G12C inhibitors", "aliases": ["KRAS G12C", "G12C"], "kind": "class"}])
        self.assertEqual(kinds.match(["KRAS G12C sotorasib trial"], "trialcore"), ["sotorasib"])
        self.assertEqual(kinds.match(["KRAS G12C docetaxel trial"], "trialcore"), ["G12C inhibitors"])
        self.assertEqual(kinds.match(["KRAS G12C resistance mechanisms"], "biomedcore"), ["KRAS"])
        self.assertEqual(kinds.match(["KRAS gene"], "genecore"), ["KRAS"])

    def test_entities_in_field(self):
        data = ledger.parse_yaml(FIELD_YAML + "entities:\n  - {name: ulotaront, aliases: [SEP-363856, SEP-856], kind: drug}\n  - {name: TAAR1, kind: target}\n")
        field, errors = ledger.normalise_field(data)
        self.assertEqual(errors, [])
        self.assertEqual(field["entities"][0]["aliases"], ["SEP-363856", "SEP-856"])
        again, errors = ledger.normalise_field(ledger.parse_yaml(ledger.field_to_yaml(field)))
        self.assertEqual(again["entities"], field["entities"])
        _, errors = ledger.normalise_field(ledger.parse_yaml(FIELD_YAML + "entities:\n  - {name: x, kind: thing}\n"))
        self.assertTrue(any("kind" in e for e in errors))


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "landscapes" / "test-field"
        self.dir.mkdir(parents=True)
        (self.dir / "field.yaml").write_text(FIELD_YAML, encoding="utf-8")
        os.environ["LEDGER_TODAY"] = "2026-10-08"
        self.f = ["--field", str(self.dir)]

    def tearDown(self):
        os.environ.pop("LEDGER_TODAY", None)
        self.tmp.cleanup()

    def ok(self, *argv: str, stdin: str = "", code: int = 0) -> str:
        c, out, err = run(*argv, stdin=stdin)
        self.assertEqual(c, code, f"{argv}: {out}{err}")
        return out

    def test_baseline_update_resume(self):
        self.ok("validate", *self.f)
        out = self.ok("plan", *self.f)
        self.assertIn("planned searches: 6", out)
        self.ok("start-run", *self.f, "--mode", "baseline")
        c, out, _ = run("start-run", *self.f, "--mode", "baseline")
        self.assertEqual(c, 1)  # already open

        out = self.ok("ingest", *self.f, "--query-id", "T01", stdin=json.dumps([T1, T2]))
        self.assertIn("returned 2 (CAP HIT)", out)
        self.assertIn("new 2 (in 1, out 1, unsure 0)", out)
        # same record again from another query: known, unchanged; a new in-scope one
        out = self.ok("ingest", *self.f, "--query-id", "T02", stdin=json.dumps([T1, T3]))
        self.assertIn("known 1 (changed 0, unchanged 1)", out)
        self.assertIn("facet drug: new in-scope per query 1, 1 -> still producing", out)
        # rejected rows write nothing
        c, out, err = run("ingest", *self.f, "--query-id", "T01", stdin=json.dumps([{**T1, "phase": "P3"}]))
        self.assertEqual(c, 1)
        self.assertIn("phase", err)
        rows = read_csv(self.dir / "ledger" / "trialcore.csv")
        self.assertEqual(len(rows), 3)
        t1 = next(r for r in rows if r["amassId"] == T1["amassId"])
        self.assertEqual(t1["seenCount"], "2")
        self.assertEqual(t1["firstQuery"], "T01")
        self.assertEqual(t1["lastQuery"], "T02")
        # a new record with no decision is refused
        c, _, err = run("ingest", *self.f, "--query-id", "T02", stdin=json.dumps([{"amassId": "AMTC_dddddddddddddddd", "briefTitle": "x"}]))
        self.assertEqual(c, 1)
        self.assertIn("needs a decision", err)

        # status shows what remains
        out = self.ok("status", *self.f)
        self.assertIn("open run baseline-1", out)
        self.assertIn("biomedcore: 1 planned, next: B01", out)
        self.assertIn("trialcore: all 2 planned queries logged", out)
        self.assertIn("anchors: 1 of 2 found by search; missing: NCT09999999", out)

        # fetch with links -> candidates; crosscheck
        drug = {"amassId": "AMDC_aaaaaaaaaaaaaaaa", "decision": "in", "name": "ULOTARONT", "chemblId": "CHEMBL4297537",
                "drugType": "SMALL_MOLECULE", "maxClinicalStage": "PHASE3",
                "mechanisms": [{"actionType": "AGONIST", "targets": [{"symbol": "TAAR1"}, {"symbol": "HTR1A"}]}],
                "links": {"trialcore": [T1["amassId"], T3["amassId"], "AMTC_eeeeeeeeeeeeeeee"], "regulatorycore": []}}
        out = self.ok("ingest", *self.f, stdin=json.dumps([drug]))
        self.assertIn("fetch drugcore: 1 record(s) fetched", out)
        self.assertIn("open candidates (linked ids not in any ledger): 1", out)
        cands = read_csv(self.dir / "log" / "candidates.csv")
        self.assertEqual(cands[0]["amassId"], "AMTC_eeeeeeeeeeeeeeee")
        drow = read_csv(self.dir / "ledger" / "drugcore.csv")[0]
        self.assertEqual(drow["mechanisms"], "AGONIST on TAAR1, HTR1A")
        # a fetch that adds columns to a known row is not a change in a baseline run
        self.assertFalse((self.dir / "log" / "changes.csv").is_file())
        self.assertEqual(drow["links"], "regulatorycore:0 | trialcore:3")
        self.assertEqual(drow["source"], "fetch")
        out = self.ok("crosscheck", *self.f, "--core", "trialcore", "--source", "AMDC_aaaaaaaaaaaaaaaa",
                      stdin=json.dumps([T1["amassId"], T2["amassId"], "AMTC_ffffffffffffffff"]))
        self.assertIn("3 linked ids, 2 in the ledger (67%; in 1, out 1, unsure 0), 1 unknown", out)
        # fetching a candidate resolves it; fetched-only anchor status
        cand = {"amassId": "AMTC_eeeeeeeeeeeeeeee", "decision": "in", "reason": "linked trial", "nctId": "NCT09999999",
                "briefTitle": "Missed trial", "overallStatus": "RECRUITING"}
        self.ok("ingest", *self.f, stdin=json.dumps([cand]))
        cands = read_csv(self.dir / "log" / "candidates.csv")
        self.assertEqual({c["amassId"]: c["status"] for c in cands}["AMTC_eeeeeeeeeeeeeeee"], "resolved")
        out = self.ok("anchors", *self.f)
        self.assertIn("fetched only (plan did not reach it)", out)
        self.assertIn("1 of 2 anchors found by search", out)

        # terms: Ralmitaront and Roche appear on in-scope rows but in no query
        out = self.ok("terms", *self.f)
        self.assertIn("Ralmitaront", out)
        self.assertIn("Roche", out)
        self.assertNotIn("Ulotaront", out)
        self.assertNotIn("SEP-363856 HYDROCHLORIDE", out)  # covered by the "SEP-363856" query once punctuation is ignored
        # add an expansion query, run it: it returns the fetched record -> source upgrades to search
        out = self.ok("add-query", *self.f, "--core", "trialcore", "--facet", "drug", "--query", "ralmitaront RO6889450",
                      "--filter", "phase=PHASE2,PHASE3", "--limit", "2", "--update")
        self.assertIn("added T03", out)
        text = (self.dir / "field.yaml").read_text()
        self.assertIn("phase: [PHASE2, PHASE3]", text)
        self.ok("ingest", *self.f, "--query-id", "T03", stdin=json.dumps([T3, cand]))
        out = self.ok("anchors", *self.f)
        self.assertIn("2 of 2 anchors found by search", out)
        out = self.ok("saturation", *self.f)
        self.assertIn("T01:2/2/1! T02:2/1/1! T03:2/0/0!", out)

        # remaining cores: run their queries, finish
        self.ok("ingest", *self.f, "--query-id", "B01", stdin=json.dumps([
            {"amassId": "AMBC_aaaaaaaaaaaaaaaa", "decision": "in", "pmid": "1", "title": "Ulotaront RCT", "journal": "NEJM",
             "publicationDate": "2020-04-16", "authors": ["A", "B", "C", "D", "E", "F", "G"], "citationCount": 100,
             "isRetracted": False}]))
        brow = read_csv(self.dir / "ledger" / "biomedcore.csv")[0]
        self.assertEqual(brow["authors"], "A; B; C; D; E; +2")
        errors: list[str] = []
        pre_joined = ledger.normalise_value(ledger.CORES["biomedcore"].columns["authors"], "A; B; C; D; E; F; G", "x", errors)
        self.assertEqual(pre_joined, "A; B; C; D; E; +2")
        self.assertEqual(ledger.normalise_value(ledger.CORES["biomedcore"].columns["authors"], "A; B; C; D; E; +2", "x", errors), "A; B; C; D; E")
        self.ok("ingest", *self.f, "--query-id", "D01", stdin=json.dumps([drug | {"links": {"trialcore": []}}]))
        out = self.ok("ingest", *self.f, "--query-id", "R01", stdin=json.dumps([]))  # zero results is a valid, logged call
        self.assertIn("returned 0", out)
        self.ok("ingest", *self.f, "--query-id", "R01", stdin=json.dumps([
            {"amassId": "AMRC_aaaaaaaaaaaaaaaa", "decision": "out", "reason": "no TAAR1 product approved", "agency": "FDA",
             "name": "Other", "authorizationStatus": "ACTIVE",
             "authorizationsByAgency": [{"amassId": "AMRC_bbbbbbbbbbbbbbbb", "agency": "EMA", "authorizationStatus": "ACTIVE"}]}]))
        self.ok("ingest", *self.f, "--query-id", "P01", stdin=json.dumps([
            {"amassId": "AMPC_aaaaaaaaaaaaaaaa", "decision": "in", "publicationNumber": "US-1-B2", "familyId": "F1",
             "title": "TAAR1 agonists", "assignees": ["Sunovion"], "publicationDate": "2021-01-01", "citedByCount": 3}]))
        c, _, err = run("finish-core", *self.f, "--core", "trialcore")
        self.assertEqual(c, 0, err)
        for core in ("biomedcore", "drugcore", "regulatorycore", "patentcore"):
            self.ok("finish-core", *self.f, "--core", core)
        out = self.ok("finish-run", *self.f, "--force")
        self.assertIn("wrote", out)
        self.assertTrue((self.dir / "test-field.xlsx").is_file())
        summary = (self.dir / "log" / "summary-baseline-1.md").read_text()
        self.assertIn("One line: baseline with 3 trials, 1 paper, 1 drug record, 0 authorizations, 1 patent record in scope.", summary)
        self.assertIn("Anchors: 2 of 2 found by search", summary)
        runs = read_csv(self.dir / "log" / "runs.csv")
        self.assertTrue(all(r["finishedAt"] for r in runs))
        # credits: 8 searches (T01 T02 T03 B01 D01 R01 twice P01 = 16) + 2 fetches = 18
        self.assertEqual(sum(float(r["credits"]) for r in runs), 18.0)

        # xlsx is well-formed
        with zipfile.ZipFile(self.dir / "test-field.xlsx") as zf:
            names = zf.namelist()
            self.assertIn("xl/workbook.xml", names)
            wb = ElementTree.fromstring(zf.read("xl/workbook.xml"))
            ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            sheet_names = [s.get("name") for s in wb.find("m:sheets", ns)]
            self.assertEqual(sheet_names, ["Landscape", "Key records", "TrialCore", "BiomedCore", "DrugCore", "RegulatoryCore",
                                           "PatentCore", "Excluded", "Queries", "Candidates", "Changes", "New"])
            for n in names:
                if n.endswith(".xml") or n.endswith(".rels"):
                    ElementTree.fromstring(zf.read(n))
            landscape = zf.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn("<t>TAAR1 agonists in schizophrenia</t>", landscape)
            self.assertIn("How to use this workbook", landscape)
            self.assertIn("Anchors: 2 of 2 found by search.", landscape)
            key = zf.read("xl/worksheets/sheet2.xml").decode()
            self.assertIn("<t>DIAMOND 1</t>", key)  # a Phase 3 trial is a key record
            trial_sheet = zf.read("xl/worksheets/sheet3.xml").decode()
            self.assertIn("<v>435</v>", trial_sheet)
            self.assertNotIn("Something else", trial_sheet)  # out rows live on Excluded
            excluded = zf.read("xl/worksheets/sheet8.xml").decode()
            self.assertIn("Something else", excluded)
            self.assertIn("not a TAAR1 agonist", excluded)

        # ---- update mode: nothing due the same day ----
        out = self.ok("start-run", *self.f, "--mode", "update")
        self.assertIn("nothing is due", out)
        self.assertIn("trialcore", out)
        self.assertIn("skip: last checked 2026-10-08, 0 day(s) ago, interval 1", out)
        # next day: daily cores due, weekly/quarterly not
        os.environ["LEDGER_TODAY"] = "2026-10-09"
        out = self.ok("start-run", *self.f, "--mode", "update")
        self.assertIn("opened update-1 for trialcore, biomedcore", out)
        self.assertIn("skip: last checked 2026-10-08, 1 day(s) ago, interval 7", out)
        self.assertIn("since 2026-10-07", out)
        # pass created: a brand new trial
        new_trial = {"amassId": "AMTC_gggggggggggggggg", "decision": "in", "reason": "new ulotaront trial", "nctId": "NCT07000000",
                     "briefTitle": "New ulotaront trial", "overallStatus": "NOT_YET_RECRUITING", "phase": "PHASE3", "hasResults": False}
        out = self.ok("ingest", *self.f, "--query-id", "T01", "--pass", "created", stdin=json.dumps([new_trial]))
        self.assertIn("changes: new-to-amass 1", out)
        added = next(r for r in read_csv(self.dir / "ledger" / "trialcore.csv") if r["amassId"] == new_trial["amassId"])
        self.assertEqual(added["firstQuery"], "T01@created")
        # a column first observed on a known row in an update is not a change either
        out = self.ok("ingest", *self.f, "--pass", "updated", stdin=json.dumps([
            {"amassId": T3["amassId"], "briefTitle": T3["briefTitle"], "links": {"biomedcore": []}, "completionDate": "2024-01-01"}]))
        self.assertIn("known 1 (changed 0, unchanged 1)", out)
        rows = read_csv(self.dir / "ledger" / "trialcore.csv")
        t3 = next(r for r in rows if r["amassId"] == T3["amassId"])
        self.assertIn("completionDate", t3["observed"].split(" | "))
        self.assertEqual(t3["completionDate"], "2024-01-01")
        # a re-fetch with --pass updated satisfies the updated pass for the Core
        out = self.ok("status", *self.f)
        self.assertIn("trialcore: 3 planned, next: T02 (created) T03 (created); 1 records re-fetched", out)
        # pass updated: known trial changed status + enrollment; one unchanged; a newly matched one
        changed = {**T1, "overallStatus": "ACTIVE_NOT_RECRUITING", "enrollment": 440, "interventionNames": ["Ulotaront", "Placebo", "Risperidone"]}
        matched = {"amassId": "AMTC_hhhhhhhhhhhhhhhh", "decision": "in", "reason": "missed before", "nctId": "NCT06000000",
                   "briefTitle": "Older ulotaront trial", "overallStatus": "COMPLETED"}
        filler = {"amassId": "AMTC_zzzzzzzzzzzzzzzz", "decision": "out", "reason": "filler", "briefTitle": "unrelated"}
        out = self.ok("ingest", *self.f, "--query-id", "T01", "--pass", "updated", stdin=json.dumps([changed, T3, matched, filler]))
        self.assertIn("known 2 (changed 1, unchanged 1)", out)
        self.assertIn("newly-matched 1", out)  # the filler "out" row is stored but is not a change
        self.assertIn("changed 3", out)
        changes = read_csv(self.dir / "log" / "changes.csv")
        fields = {(c["class"], c["field"]) for c in changes if c["runId"] == "update-1"}
        self.assertIn(("changed", "overallStatus"), fields)
        self.assertIn(("changed", "enrollment"), fields)
        self.assertIn(("changed", "interventionNames"), fields)
        iv = next(c for c in changes if c["field"] == "interventionNames")
        self.assertIn("+Risperidone", iv["new"])
        self.assertEqual(iv["group"], "other")
        # decision change
        out = self.ok("ingest", *self.f, "--query-id", "T02", "--pass", "updated", stdin=json.dumps([{**T2, "decision": "in", "reason": "now in scope"}]))
        self.assertIn("decision-changed 1", out)
        # --pass on the wrong core / wrong mode is refused
        c, _, err = run("ingest", *self.f, "--query-id", "T02", "--pass", "published", stdin=json.dumps([T1]))
        self.assertEqual(c, 1)
        # status lists remaining passes; finish-core refuses until done unless forced
        out = self.ok("status", *self.f)
        self.assertIn("T02 (created)", out)
        self.assertIn("T03 (created)", out)
        self.assertNotIn("T03 (updated)", out)
        c, _, err = run("finish-core", *self.f, "--core", "trialcore")
        self.assertEqual(c, 1)
        self.assertIn("unlogged work", err)
        self.ok("finish-core", *self.f, "--core", "trialcore", "--force")
        # biomedcore: both passes, nothing new; a rewritten-unchanged paper
        self.ok("ingest", *self.f, "--query-id", "B01", "--pass", "created", stdin=json.dumps([
            {"amassId": "AMBC_aaaaaaaaaaaaaaaa", "title": "Ulotaront RCT", "citationCount": 100}]))
        out = self.ok("ingest", *self.f, "--query-id", "B01", "--pass", "updated", stdin=json.dumps([
            {"amassId": "AMBC_aaaaaaaaaaaaaaaa", "title": "Ulotaront RCT", "citationCount": 101}]))
        self.assertIn("changed 1", out)
        self.ok("finish-core", *self.f, "--core", "biomedcore")
        out = self.ok("finish-run", *self.f, "--force")
        summary = (self.dir / "log" / "summary-update-1.md").read_text()
        self.assertIn("One line: update over TrialCore, BiomedCore; 1 new-to-amass, 4 changed, 1 newly-matched, 1 decision-changed.", summary)
        self.assertIn("Skipped (not due): DrugCore, RegulatoryCore, PatentCore", summary)
        self.assertIn("citationCount '100' -> '101' (metadata)", summary)
        # five update searches at 2 credits plus one fetch
        runs = read_csv(self.dir / "log" / "runs.csv")
        upd = sum(float(r["credits"]) for r in runs if r["runId"] == "update-1")
        self.assertEqual(upd, 11.0)
        # watchlist export
        out = self.ok("watchlist", *self.f, "--core", "trialcore")
        wl = (self.dir / "watchlists" / "test-field-trialcore.yaml").read_text()
        self.assertIn("core: trialcore", wl)
        self.assertIn("  - AMTC_aaaaaaaaaaaaaaaa   # NCT04072354 DIAMOND 1", wl)
        self.assertIn("cadence: daily", wl)
        c, _, err = run("watchlist", *self.f, "--core", "patentcore")
        self.assertEqual(c, 1)
        # entities: add the roster, see the table, the unassigned records and the zero-trial hint
        out = self.ok("add-entity", *self.f, "--name", "ulotaront", "--alias", "Ulotaront", "--alias", "SEP-363856", "--kind", "drug")
        self.assertIn("added entity ulotaront", out)
        self.ok("add-entity", *self.f, "--name", "ralmitaront", "--alias", "RO6889450", "--kind", "drug")
        self.ok("add-entity", *self.f, "--name", "ghost drug", "--kind", "drug")
        out = self.ok("add-entity", *self.f, "--name", "ulotaront", "--alias", "SEP-856")
        self.assertIn("updated entity ulotaront", out)
        out = self.ok("entities", *self.f)
        self.assertIn("ulotaront | drug | ", out)
        self.assertIn("drug entities with no trial in scope: ghost drug", out)
        self.assertIn("trialcore: ", out)  # the new-to-amass trial names no entity
        rows, unassigned = ledger.entity_table(ledger.Field(self.dir), {c: ledger.load_ledger(ledger.Field(self.dir), ledger.CORES[c]) for c in ["trialcore", "biomedcore", "drugcore", "regulatorycore", "patentcore"]})
        by = {r["entity"]: r for r in rows}
        self.assertEqual(by["ulotaront"]["trials"], 3)  # DIAMOND 1, the new trial and the older trial
        self.assertEqual(by["ulotaront"]["p3"], 2)
        self.assertEqual(by["ulotaront"]["papers"], 1)
        self.assertEqual(by["ulotaront"]["drugs"], 1)
        self.assertEqual(by["ralmitaront"]["trials"], 1)
        self.assertIn("Unassigned", by)
        self.ok("export", *self.f)
        summary = (self.dir / "log" / "summary-update-1.md").read_text()
        self.assertIn("## By entity", summary)
        self.assertIn("| ulotaront | drug | 3 |", summary)
        self.assertIn("### Changes by entity", summary)
        # dismiss a candidate that is out of scope by design
        out = self.ok("dismiss", *self.f, "--core", "trialcore", "--id", "AMTC_ffffffffffffffff", "--note", "not in scope")
        self.assertIn("dismissed", out)
        cands = {c["amassId"]: c["status"] for c in read_csv(self.dir / "log" / "candidates.csv")}
        self.assertEqual(cands["AMTC_ffffffffffffffff"], "dismissed")

    def test_raw_ingest(self):
        self.ok("start-run", *self.f, "--mode", "baseline", "--core", "trialcore")
        raw = {"results": [
            {"amassId": "AMTC_aaaaaaaaaaaaaaaa", "nctId": "NCT04072354", "registryId": "NCT04072354", "briefTitle": "DIAMOND 1",
             "phase": "PHASE3", "overallStatus": "COMPLETED", "enrollment": 435, "conditions": ["Schizophrenia"],
             "interventionNames": ["Ulotaront", "Placebo"], "hasResults": True, "acronym": None, "unusedField": 1},
            {"amassId": "AMTC_bbbbbbbbbbbbbbbb", "nctId": "NCT00000002", "briefTitle": "Other", "phase": "PHASE2", "hasResults": False},
            {"amassId": "AMTC_cccccccccccccccc", "nctId": "NCT00000003", "briefTitle": "Third", "phase": "PHASE1", "hasResults": False},
        ]}
        raw_path = Path(self.tmp.name) / "raw.json"
        raw_path.write_text(json.dumps(raw))
        out = self.ok("ingest", *self.f, "--query-id", "T01", "--raw", str(raw_path), "--default", "out", "not a TAAR1 agonist",
                      stdin=json.dumps({"AMTC_aaaaaaaaaaaaaaaa": ["in", "ulotaront phase 3"], "AMTC_cccccccccccccccc": {"decision": "unsure", "reason": "mechanism not stated"}}))
        self.assertIn("new 3 (in 1, out 1, unsure 1)", out)
        rows = {r["amassId"]: r for r in read_csv(self.dir / "ledger" / "trialcore.csv")}
        self.assertEqual(rows["AMTC_aaaaaaaaaaaaaaaa"]["enrollment"], "435")
        self.assertEqual(rows["AMTC_aaaaaaaaaaaaaaaa"]["interventionNames"], "Ulotaront | Placebo")
        self.assertEqual(rows["AMTC_aaaaaaaaaaaaaaaa"]["acronym"], "")
        self.assertEqual(rows["AMTC_bbbbbbbbbbbbbbbb"]["reason"], "not a TAAR1 agonist")
        self.assertEqual(rows["AMTC_cccccccccccccccc"]["decision"], "unsure")
        fetch = {"data": {"amassId": "AMDC_aaaaaaaaaaaaaaaa", "chemblId": "CHEMBL1", "name": "ULOTARONT", "synonyms": ["SEP-363856"],
                          "drugType": "SMALL_MOLECULE", "maxClinicalStage": "PHASE3", "parent": None,
                          "referencesTrialCore": ["AMTC_aaaaaaaaaaaaaaaa", "AMTC_dddddddddddddddd"], "referencesGeneCore": ["AMGC_aaaaaaaaaaaaaaaa"],
                          "mechanismsOfAction": [{"actionType": "AGONIST", "targets": [{"symbol": "TAAR1"}]}]}}
        raw_path.write_text(json.dumps(fetch))
        c, _, err = run("ingest", *self.f, "--raw", str(raw_path), stdin=json.dumps({"AMDC_aaaaaaaaaaaaaaaa": "in"}))
        self.assertEqual(c, 1)
        self.assertIn("not open for drugcore", err)
        self.ok("abandon-run", *self.f)
        self.ok("start-run", *self.f, "--mode", "baseline")
        out = self.ok("ingest", *self.f, "--raw", str(raw_path), stdin=json.dumps({"AMDC_aaaaaaaaaaaaaaaa": "in"}))
        self.assertIn("fetch drugcore: 1 record(s) fetched", out)
        drow = read_csv(self.dir / "ledger" / "drugcore.csv")[0]
        self.assertEqual(drow["mechanisms"], "AGONIST on TAAR1")
        self.assertEqual(drow["links"], "genecore:1 | trialcore:2")
        cands = {c["amassId"] for c in read_csv(self.dir / "log" / "candidates.csv")}
        self.assertEqual(cands, {"AMTC_dddddddddddddddd", "AMGC_aaaaaaaaaaaaaaaa"})
        c, _, err = run("ingest", *self.f, "--raw", str(raw_path), stdin=json.dumps({"AMDC_zzzzzzzzzzzzzzzz": "in"}))
        self.assertEqual(c, 1)
        # --default drop: unknown filler is counted, not stored; known records are still updated
        raw_path.write_text(json.dumps({"results": [
            {"amassId": "AMTC_aaaaaaaaaaaaaaaa", "briefTitle": "DIAMOND 1", "enrollment": 436},
            {"amassId": "AMTC_eeeeeeeeeeeeeeee", "briefTitle": "filler one"},
            {"amassId": "AMTC_ffffffffffffffff", "briefTitle": "filler two"},
            {"amassId": "AMTC_gggggggggggggggg", "briefTitle": "a new in-scope trial"}]}))
        out = self.ok("ingest", *self.f, "--query-id", "T02", "--raw", str(raw_path), "--default", "drop", "filler",
                      stdin=json.dumps({"AMTC_gggggggggggggggg": ["in", "ulotaront"]}))
        self.assertIn("returned 4", out)
        self.assertIn("new 1 (in 1, out 0, unsure 0); known 1 (changed 1, unchanged 0)", out)
        ids = {r["amassId"] for r in read_csv(self.dir / "ledger" / "trialcore.csv")}
        self.assertNotIn("AMTC_eeeeeeeeeeeeeeee", ids)
        self.assertIn("AMTC_gggggggggggggggg", ids)
        # --returned records the true page size when filler rows are omitted from stdin
        out = self.ok("ingest", *self.f, "--query-id", "T02", "--returned", "50", stdin=json.dumps([{**T2, "decision": "out"}]))
        self.assertIn("returned 50 (CAP HIT)", out)

    def test_allowance_and_page(self):
        self.ok("start-run", *self.f, "--mode", "baseline")
        out = self.ok("allowance", *self.f, "--breadth", "narrow")
        self.assertIn("trialcore 10", out)
        self.ok("allowance", *self.f, "--core", "trialcore", "--searches", "2")
        # the fixture plans 2 trialcore queries: a 3rd is refused unless forced
        c, _, err = run("add-query", *self.f, "--core", "trialcore", "--facet", "drug", "--query", "x")
        self.assertEqual(c, 1)
        self.assertIn("allowance", err)
        self.ok("add-query", *self.f, "--core", "trialcore", "--facet", "drug", "--query", "x", "--force")
        out = self.ok("validate", *self.f)
        self.assertIn("warning: the plan exceeds the allowance for trialcore", out)
        out = self.ok("ingest", *self.f, "--query-id", "T01", stdin=json.dumps([T1, T2]))
        self.assertIn("searches this run for trialcore: 1 of 2 allowed", out)
        # the landscape page
        (self.dir / "test-field-briefing.md").write_text("# Brief\n\nSome **bold** text.\n\n- a point\n- another\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n")
        self.ok("add-entity", *self.f, "--name", "ulotaront", "--kind", "drug")
        self.ok("export", *self.f)
        html = (self.dir / "test-field-landscape.html").read_text()
        self.assertIn("<svg", html)
        self.assertIn("Trials by phase, per drug", html)
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<li>a point</li>", html)
        self.assertIn("<td class=\"n\">1</td>", html)
        self.assertIn("DIAMOND 1", html)
        self.assertIn("prefers-color-scheme:dark", html)
        self.assertNotIn("credits", html.lower())

    def test_budget_exit_code(self):
        self.ok("start-run", *self.f, "--mode", "baseline", "--core", "trialcore")
        field = ledger.Field(self.dir)
        field.data["budget"]["baseline"] = 2
        field.save()
        self.ok("ingest", *self.f, "--query-id", "T01", stdin=json.dumps([T1]))
        self.ok("ingest", *self.f, "--query-id", "T02", stdin=json.dumps([T3]), code=2)

    def test_resume_identical(self):
        """Two sessions that ingest the same results in the same order give the same ledger."""
        self.ok("start-run", *self.f, "--mode", "baseline", "--core", "trialcore")
        self.ok("ingest", *self.f, "--query-id", "T01", stdin=json.dumps([T1, T2]))
        # "new session": nothing in memory; status tells us what is next
        out = self.ok("status", *self.f)
        self.assertIn("next: T02", out)
        self.ok("ingest", *self.f, "--query-id", "T02", stdin=json.dumps([T1, T3]))
        a = (self.dir / "ledger" / "trialcore.csv").read_text()
        # uninterrupted run in a second folder
        other = Path(self.tmp.name) / "landscapes" / "other"
        other.mkdir()
        (other / "field.yaml").write_text(FIELD_YAML.replace("name: test-field", "name: other"))
        g = ["--field", str(other)]
        self.ok("start-run", *g, "--mode", "baseline", "--core", "trialcore")
        self.ok("ingest", *g, "--query-id", "T01", stdin=json.dumps([T1, T2]))
        self.ok("ingest", *g, "--query-id", "T02", stdin=json.dumps([T1, T3]))
        b = (other / "ledger" / "trialcore.csv").read_text()
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
