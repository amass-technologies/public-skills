"""Offline tests for the MCP-mode board helper (skills/amass-watchlist-monitor/scripts/board.py).

The fixtures in tests/fixtures/mcp are trimmed copies of real Amass MCP results from 2026-10-08.
Each test drives the helper as Claude would, with stdin JSON and a fixed --today, in a temporary
folder; no network and no credits.

    python3 tests/board/test_board.py
"""

import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "amass-watchlist-monitor" / "scripts"))
import board  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "mcp"
NCT06894212 = "AMTC_Ei4FXnKwSDUoC3ZmYS5TjFYbqv2"
CTIS_MDD = "AMTC_7qM01giQzCRk4s8oS0ew41dE33j"
CTIS_GAD = "AMTC_9Pais5zfpFtubYGzVvbICi0xv4M"
NCT07759128 = "AMTC_UOVYXZ2dY42Kn663vgBL0221KY4"


def fixture(name):
    data = json.loads((FIXTURES / name).read_text())
    return data.get("results") or data.get("data")


def search_rows(ids, edit=None):
    rows = []
    for r in fixture("search_ulotaront.json"):
        if r["amassId"] in ids:
            r.pop("briefSummary", None)
            if edit:
                edit(r)
            rows.append(r)
    return rows


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = str(self.dir / "t.xlsx")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def run_cmd(self, *args, stdin=None, code=0):
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdin
        sys.stdin = io.StringIO("" if stdin is None else json.dumps(stdin))
        try:
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    got = board.main(list(args))
                except SystemExit as exc:
                    got = exc.code
        finally:
            sys.stdin = old
        self.assertEqual(got, code, f"{args}: {out.getvalue()}{err.getvalue()}")
        return out.getvalue() + err.getvalue()

    def state(self):
        return board.read_state(Path(self.path))

    def setup_board(self):
        self.run_cmd("new", self.path, "--name", "t", "--today", "2026-10-08")
        self.run_cmd("add", self.path, "--core", "trialcore", "--search", "ulotaront", "--limit", "50",
                     "--today", "2026-10-08", stdin=search_rows({NCT06894212, CTIS_MDD, CTIS_GAD}))
        self.run_cmd("add", self.path, "--core", "trialcore", "--id", "nctId:NCT07759128", "--today", "2026-10-08")
        out = self.run_cmd("start", self.path, "--today", "2026-10-08")
        self.assertIn('"type": "nctId", "value": "NCT07759128"', out)
        self.run_cmd("ingest", self.path, "--core", "trialcore", "--fetch", stdin=fixture("get_trial.json"))
        out = self.run_cmd("finish", self.path)
        self.assertIn("baseline captured for 1", out)

    def test_setup_and_baseline(self):
        self.setup_board()
        s = self.state()
        self.assertEqual(len(s["records"]), 4)
        self.assertEqual(s["pendingIds"], [])
        self.assertEqual(s["credits"], 3)  # one search and one fetch
        self.assertNotIn("interventionNames", s["records"][CTIS_MDD]["fields"])  # long registry text dropped
        self.assertIn("S1", s["records"][NCT06894212]["seenBy"])
        self.assertIn("Nothing is due", self.run_cmd("start", self.path, "--today", "2026-10-08"))

    def test_quick_check_reports_changes(self):
        self.setup_board()
        out = self.run_cmd("start", self.path, "--today", "2026-10-09")
        self.assertIn("quick check", out)
        self.assertIn("estimated 3 MCP credits", out)

        def edit(r):
            if r["amassId"] == NCT06894212:
                r.update(overallStatus="COMPLETED", hasResults=True)
            if r["amassId"] == CTIS_GAD:
                r["facilityCountries"].append("DE")

        rows = search_rows({NCT06894212, CTIS_MDD, CTIS_GAD, "AMTC_PZvjIuCj5263tY8QHIEQxGJndux"}, edit)
        out = self.run_cmd("ingest", self.path, "--core", "trialcore", "--search", "S1", stdin=rows)
        self.assertIn("1 ignored (not on the board)", out)
        self.assertIn("1 still to fetch", out)
        self.assertIn("1 planned records are not in yet", self.run_cmd("review", self.path))
        self.run_cmd("finish", self.path, code=1)  # a planned record is missing
        self.run_cmd("ingest", self.path, "--core", "trialcore", "--fetch", stdin=fixture("get_trial.json"))
        out = self.run_cmd("finish", self.path)
        self.assertIn("status: Active, not recruiting → Completed", out)
        self.assertIn("results posted: no → yes", out)
        self.assertIn("countries: added DE", out)
        self.assertIn("Changes on 2 of 4 records", out)
        s = self.state()
        self.assertEqual(s["credits"], 6)
        self.assertEqual(s["records"][NCT06894212]["lastChanged"], "2026-10-09")

    def test_full_check_after_a_week_and_links(self):
        self.setup_board()
        out = self.run_cmd("start", self.path, "--today", "2026-10-15")
        self.assertIn("full check", out)
        rows = []
        for r in search_rows({NCT06894212, CTIS_GAD}):
            r.update(referencesBiomedCore=["AMBC_ZhnA9SkzGj1A1jSNxIJY0WAAm3P"], referencesDrugCore=[])
            rows.append(r)
        self.run_cmd("ingest", self.path, "--core", "trialcore", "--fetch", "--not-found", CTIS_MDD, stdin=rows)
        trial = fixture("get_trial.json")
        trial["referencesBiomedCore"] = ["AMBC_ZhnA9SkzGj1A1jSNxIJY0WAAm3P"]
        self.run_cmd("ingest", self.path, "--core", "trialcore", "--fetch", stdin=trial)
        out = self.run_cmd("finish", self.path)
        # links seen for the first time on search-added records are a baseline, not a change
        self.assertNotIn("NCT06894212", out.split("## Worth watching")[0])
        self.assertIn("linked papers: added 1", out)  # NCT07759128 was fetched before, with no links
        self.assertIn("not found when fetched", out)
        self.assertEqual(self.state()["cores"]["trialcore"]["lastFull"], "2026-10-15")

    def test_add_only_keeps_the_named_records_from_a_saved_result(self):
        self.run_cmd("new", self.path, "--name", "t", "--today", "2026-10-08")
        raw = self.dir / "search.json"
        raw.write_text(json.dumps({"results": fixture("search_ulotaront.json")}))
        total = len(fixture("search_ulotaront.json"))
        self.assertGreater(total, 3)
        nct = next(r["nctId"] for r in fixture("search_ulotaront.json") if r["amassId"] == NCT06894212)
        out = self.run_cmd("add", self.path, "--core", "trialcore", "--search", "ulotaront", "--limit", "50",
                           "--raw", str(raw), "--only", nct, CTIS_MDD, "--today", "2026-10-08")
        self.assertIn("2 added", out)
        s = self.state()
        self.assertEqual(set(s["records"]), {NCT06894212, CTIS_MDD})
        self.assertEqual(s["credits"], 2)  # one search, whatever --only kept
        # an id that is not in the result is refused and nothing is stored
        out = self.run_cmd("add", self.path, "--core", "trialcore", "--search", "ulotaront", "--limit", "50",
                           "--raw", str(raw), "--only", "NCT00000000", "--today", "2026-10-08", code=1)
        self.assertIn("NCT00000000", out)
        self.assertEqual(set(self.state()["records"]), {NCT06894212, CTIS_MDD})

    def test_validation_rejects_the_whole_batch(self):
        self.setup_board()
        self.run_cmd("start", self.path, "--today", "2026-10-16")
        bad = [{"amassId": NCT06894212, "overallStatus": "Completed", "phse": "PHASE3"},
               {"amassId": "AMBC_ZhnA9SkzGj1A1jSNxIJY0WAAm3P"}]
        out = self.run_cmd("ingest", self.path, "--core", "trialcore", "--fetch", stdin=bad, code=1)
        self.assertIn("'Completed' is not one of", out)
        self.assertIn("unknown field 'phse'", out)
        self.assertIn("is not a TrialCore Amass ID", out)
        self.assertIn("4 still to fetch", self.run_cmd("status", self.path))

    def test_correction_counts_no_credits(self):
        self.setup_board()
        self.run_cmd("start", self.path, "--today", "2026-10-16", "--full")

        def slip(r):
            if r["amassId"] == NCT06894212:
                r["enrollment"] = 583  # a copy slip: the tool said 538

        self.run_cmd("ingest", self.path, "--core", "trialcore", "--fetch",
                     stdin=search_rows({NCT06894212, CTIS_MDD, CTIS_GAD}, slip))
        self.run_cmd("ingest", self.path, "--core", "trialcore", "--fetch", stdin=fixture("get_trial.json"))
        self.assertIn("enrollment: 538 → 583", self.run_cmd("review", self.path))
        self.run_cmd("ingest", self.path, "--core", "trialcore", "--fetch", "--correction",
                     stdin=search_rows({NCT06894212}))
        self.assertNotIn("538 → 583", self.run_cmd("review", self.path))
        self.assertIn("No changes on the 4 records checked", self.run_cmd("finish", self.path))
        self.assertEqual(self.state()["credits"], 3 + 4)

    def test_two_copies_of_one_record_must_agree(self):
        self.setup_board()
        self.run_cmd("add", self.path, "--core", "trialcore", "--search", "SEP-363856", "--limit", "50",
                     stdin=search_rows({NCT06894212}))
        self.run_cmd("start", self.path, "--today", "2026-10-09", "--force")
        changed = search_rows({NCT06894212}, lambda r: r.update(overallStatus="COMPLETED"))
        self.run_cmd("ingest", self.path, "--core", "trialcore", "--search", "S1", stdin=changed)
        out = self.run_cmd("ingest", self.path, "--core", "trialcore", "--search", "S2",
                           stdin=search_rows({NCT06894212}), code=1)
        self.assertIn("earlier copy COMPLETED, this copy ACTIVE_NOT_RECRUITING", out)
        self.run_cmd("ingest", self.path, "--core", "trialcore", "--search", "S2", stdin=changed)
        self.assertIn("status: ACTIVE_NOT_RECRUITING → COMPLETED", self.run_cmd("review", self.path))  # raw for checking

    def test_unknown_ids_are_retried_on_full_checks_only(self):
        self.setup_board()
        self.run_cmd("add", self.path, "--core", "trialcore", "--id", "nctId:NCT09999999", "--today", "2026-10-08")
        self.run_cmd("start", self.path, "--today", "2026-10-08")
        self.run_cmd("ingest", self.path, "--core", "trialcore", "--fetch", "--not-found", "nctId:NCT09999999")
        self.assertIn("Ids that Amass does not know", self.run_cmd("finish", self.path))
        self.assertNotIn("NCT09999999", self.run_cmd("start", self.path, "--today", "2026-10-09"))
        self.run_cmd("start", self.path, "--discard")
        self.assertIn("NCT09999999", self.run_cmd("start", self.path, "--today", "2026-10-15"))

    def test_open_stdin_does_not_hang(self):
        import subprocess
        self.setup_board()
        self.run_cmd("start", self.path, "--today", "2026-10-16", "--full")
        helper = ROOT / "skills" / "amass-watchlist-monitor" / "scripts" / "board.py"
        proc = subprocess.Popen([sys.executable, str(helper), "ingest", self.path, "--core", "trialcore", "--fetch",
                                 "--not-found", CTIS_MDD], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        try:
            self.assertEqual(proc.wait(timeout=30), 0)
        finally:
            proc.stdin.close()
        self.assertIn("1 marked not found", proc.stdout.read())
        proc.stdout.close()

    def sheet_text(self, name):
        rows = board.read_sheets(Path(self.path), {name})[name]
        return "\n".join(" | ".join(v for _, v in sorted(row.items(), key=lambda kv: (len(kv[0]), kv[0])))
                         for row in rows)

    def test_overview_page(self):
        self.setup_board()
        self.run_cmd("annotate", self.path, "--today", "2026-10-09", stdin=[
            {"id": "NCT06894212", "label": "Acute schizophrenia US+JP"},
            {"id": "CTIS2022-500538-27-00", "copyOf": "NCT06894212"},
        ])
        text = self.sheet_text("Overview")
        names = [n for n in board.read_sheets(Path(self.path), {"Overview", "Trials"})]
        self.assertIn("Overview", names)
        self.assertIn("3 trials (4 registry records)", text)
        self.assertIn("First look stored on 8 Oct 2026", text)
        self.assertIn("⚠ Acute schizophrenia US+JP | NCT06894212 | Ends 29 Oct 2026, in 20 days: expect a status "
                      "change; the CTIS copy (CTIS2022-500538-27-00) is marked as having results", text)
        self.assertIn("Schizophrenia | 1 | 1 | – | – | – | Oct 2026 | 0 (+1 marked)", text)
        self.assertIn("CTIS ⚠", text)
        self.assertIn("2026", text)
        trials = self.sheet_text("Trials")
        self.assertIn("Active, not recruiting", trials)
        self.assertIn("Phase 3", trials)
        self.assertIn("ClinicalTrials.gov", trials)
        # the first sheet is the Overview, and it is the one that opens
        with zipfile.ZipFile(self.path) as z:
            workbook = z.read("xl/workbook.xml").decode()
            self.assertLess(workbook.index('name="Overview"'), workbook.index('name="Trials"'))
            self.assertIn("HYPERLINK(", z.read("xl/worksheets/sheet1.xml").decode())

    def test_overview_after_a_change(self):
        self.setup_board()
        self.run_cmd("start", self.path, "--today", "2026-10-09")
        changed = search_rows({NCT06894212, CTIS_MDD, CTIS_GAD},
                              lambda r: r.update(overallStatus="COMPLETED") if r["amassId"] == NCT06894212 else None)
        self.run_cmd("ingest", self.path, "--core", "trialcore", "--search", "S1", stdin=changed)
        self.run_cmd("ingest", self.path, "--core", "trialcore", "--fetch", stdin=fixture("get_trial.json"))
        out = self.run_cmd("finish", self.path, "--today", "2026-10-09")
        self.assertNotIn("## Worth watching", out)  # the trial that was ending has completed
        text = self.sheet_text("Overview")
        self.assertIn("1 record changed since 8 Oct 2026 (checked 9 Oct 2026)", text)
        self.assertIn("● A Trial of the Efficacy and S… | NCT06894212 | Status: Active, not recruiting → Completed "
                      "(results usually due within a year)", text)
        shown = self.run_cmd("show", self.path, "--today", "2026-10-09")
        self.assertIn("| Short name | Trial | Status | Phase | Results | Ends | Also in |", shown)
        self.assertIn("status: Active, not recruiting → Completed", shown)

    def test_short_names_and_notes_typed_in_excel_are_kept(self):
        self.setup_board()
        self.run_cmd("annotate", self.path, "--id", "NCT07759128", "--label", "GAD fixed dose")
        with zipfile.ZipFile(self.path) as z:
            parts = {n: z.read(n) for n in z.namelist()}
        rows = board.read_sheets(Path(self.path), {"Trials"})["Trials"]
        header = {v: k for k, v in rows[0].items()}
        sheet_name = next(n for n in parts if n.startswith("xl/worksheets/")
                          and b"Short name" in parts[n] and b"Linked papers" in parts[n])
        xml = parts[sheet_name].decode()
        row_no = next(i + 1 for i, row in enumerate(rows) if row.get(header["Amass ID"]) == NCT06894212)
        cell = (f'<c r="{header["Notes"]}{row_no}" t="inlineStr"><is><t>ask medical affairs</t></is></c>'
                f'<c r="{header["Short name"]}{row_no}" t="inlineStr"><is><t>Acute US+JP</t></is></c>')
        xml = re.sub(rf'(<row r="{row_no}">)', rf"\1{cell}", xml)
        # and the user clears the label Claude set on NCT07759128
        label_row = next(i + 1 for i, row in enumerate(rows) if row.get(header["Amass ID"]) == NCT07759128)
        xml = re.sub(rf'<c r="{header["Short name"]}{label_row}"[^>]*>.*?</c>', "", xml, count=1)
        parts[sheet_name] = xml.encode()
        with zipfile.ZipFile(self.path, "w") as z:
            for n, data in parts.items():
                z.writestr(n, data)
        self.run_cmd("set", self.path, "--max-credits", "30")  # any command that rewrites the board
        records = self.state()["records"]
        self.assertEqual(records[NCT06894212]["label"], "Acute US+JP")
        self.assertEqual(records[NCT06894212]["note"], "ask medical affairs")
        self.assertNotIn("label", records[NCT07759128])
        self.assertIn("ask medical affairs", self.sheet_text("Trials"))

    def test_annotate_rejects_bad_links(self):
        self.setup_board()
        out = self.run_cmd("annotate", self.path, stdin=[{"id": "NCT06894212", "copyOf": "NCT06894212"},
                                                         {"id": "NCT00000000", "label": "x"}], code=1)
        self.assertIn("cannot be a copy of itself", out)
        self.assertIn("NCT00000000 is not on the board", out)
        self.run_cmd("annotate", self.path, stdin=[{"id": CTIS_MDD, "copyOf": "NCT06894212"}])
        out = self.run_cmd("annotate", self.path, stdin=[{"id": "NCT06894212", "copyOf": "NCT07759128"},
                                                         {"id": CTIS_GAD, "copyOf": CTIS_MDD}], code=1)
        self.assertIn("both ClinicalTrials.gov records", out)
        self.assertIn("is itself a registry copy", out)
        self.assertEqual(self.state()["records"][CTIS_MDD]["copyOf"], NCT06894212)
        digest_name = board.name_of(board.TRIALCORE, self.state()["records"][CTIS_MDD], records=self.state()["records"])
        self.assertTrue(digest_name.endswith("CTIS copy (CTIS2022-500538-27-00)"))
        self.run_cmd("annotate", self.path, stdin=[{"id": CTIS_MDD, "copyOf": None}])
        self.assertNotIn("copyOf", self.state()["records"][CTIS_MDD])
        out = self.run_cmd("annotate", self.path, "--id", "NCT06894212", "--label", "x" * 45)
        self.assertIn("cut to 40 characters", out)
        out_dir = self.dir / "csv"
        self.run_cmd("export", self.path, "csv", "--out", str(out_dir))
        self.assertTrue((out_dir / "t-trials.csv").read_text().startswith("Short name,Source id"))

    def test_other_cores(self):
        self.run_cmd("new", self.path, "--name", "mixed", "--today", "2026-10-08")
        reg = fixture("get_reg.json")
        search_fields = {k: reg[k] for k in ("amassId", "agency", "name", "activeSubstance", "authorizationStatus",
                                             "procedureType", "marketingAuthorisationHolder", "authorizationDate",
                                             "url", "isOrphan", "authorizationsByAgency", "moleculeType")}
        self.run_cmd("add", self.path, "--core", "regulatorycore", "--search", "maribavir", "--filter", "agency=EMA",
                     "--limit", "10", "--today", "2026-10-08", stdin=[search_fields])
        for core, name in (("drugcore", "get_drug.json"), ("genecore", "get_gene.json"),
                           ("biomedcore", "get_paper.json")):
            self.run_cmd("add", self.path, "--core", core, "--fetch", "--today", "2026-10-08", stdin=fixture(name))
        out = self.run_cmd("start", self.path, "--today", "2026-10-08")
        self.assertIn("get_amass_regulatorycore_record", out)  # search fields only: one fetch for a full first look
        raw = self.dir / "reg.json"
        raw.write_text(json.dumps({"data": reg}))
        self.run_cmd("ingest", self.path, "--core", "regulatorycore", "--fetch", "--raw", str(raw))
        self.run_cmd("finish", self.path)
        fields = self.state()["records"][reg["amassId"]]["fields"]
        self.assertEqual(fields["sectionCount"], 3)
        self.assertEqual(fields["revisionNumber"], 8)
        gene = self.state()["records"]["AMGC_LarihaWey2C7yMgkHZIIxL5ceV9"]["fields"]
        self.assertEqual(gene["loeuf"], 3.8574)
        self.assertIn("smallMolecule clinical: Advanced Clinical", gene["tractability"])

        self.run_cmd("start", self.path, "--today", "2026-10-15")
        reg["emaDetails"]["revisionNumber"] = 9
        raw.write_text(json.dumps({"data": reg}))
        self.run_cmd("ingest", self.path, "--core", "regulatorycore", "--fetch", "--raw", str(raw))
        drug = fixture("get_drug.json")
        drug["maxClinicalStage"] = "WITHDRAWN"
        self.run_cmd("ingest", self.path, "--core", "drugcore", "--fetch", stdin=drug)
        self.run_cmd("ingest", self.path, "--core", "genecore", "--fetch", stdin=fixture("get_gene.json"))
        paper = fixture("get_paper.json")
        paper["citationCount"] = 161
        self.run_cmd("ingest", self.path, "--core", "biomedcore", "--fetch", stdin=paper)
        out = self.run_cmd("finish", self.path)
        self.assertIn("SmPC revision: 8 → 9", out)
        self.assertIn("highest stage: Approval → Withdrawn", out)
        self.assertIn("## Metadata only", out)
        self.assertIn("citations: 158 → 161", out)

    def test_credit_limit_and_discard(self):
        self.run_cmd("new", self.path, "--name", "big", "--today", "2026-10-08")
        ids = [f"nctId:NCT0{4000000 + i}" for i in range(31)]
        self.run_cmd("add", self.path, "--core", "trialcore", "--id", *ids, "--today", "2026-10-08")
        self.assertIn("OVER LIMIT", self.run_cmd("start", self.path, "--today", "2026-10-08", code=2))
        self.assertIn("Discarded", self.run_cmd("start", self.path, "--discard"))

    def test_rejections(self):
        self.run_cmd("new", self.path, "--name", "r", "--today", "2026-10-08")
        self.assertIn("PatentCore cannot be watched", self.run_cmd("add", self.path, "--core", "patentcore",
                                                                  "--id", "x", code=2))
        self.assertIn("by Amass ID or nctId, registryId", self.run_cmd("add", self.path, "--core", "trialcore",
                                                                      "--id", "symbol:EGFR", code=1))

    def test_survives_an_excel_style_save(self):
        self.setup_board()
        src = zipfile.ZipFile(self.path)
        shared, index, parts = [], {}, {}
        for name in src.namelist():
            data = src.read(name).decode()
            if name.startswith("xl/worksheets/"):
                def to_shared(m):
                    text = m.group(2).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
                    if text not in index:
                        index[text] = len(shared)
                        shared.append(text)
                    return f'<c{m.group(1)} t="s"><v>{index[text]}</v></c>'
                data = re.sub(r'<c([^>]*?) t="inlineStr"><is><t xml:space="preserve">(.*?)</t></is></c>',
                              to_shared, data, flags=re.S)
                name = name.replace("worksheets/sheet", "worksheets/Sheet_")
            if name == "xl/_rels/workbook.xml.rels":
                data = data.replace('Target="worksheets/sheet', 'Target="/xl/worksheets/Sheet_').replace(
                    "</Relationships>", '<Relationship Id="rId99" Type="http://schemas.openxmlformats.org/'
                    'officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>')
            parts[name] = data
        from xml.sax.saxutils import escape
        parts["xl/sharedStrings.xml"] = (
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + "".join(f"<si><r><t>{escape(t[:2])}</t></r><r><t>{escape(t[2:])}</t></r></si>" for t in shared)
            + "</sst>")
        copy = self.dir / "excel.xlsx"
        with zipfile.ZipFile(copy, "w") as z:
            for name, data in parts.items():
                z.writestr(name, data)
        self.assertEqual(board.read_state(copy), self.state())

    def test_watchlist_round_trip(self):
        self.setup_board()
        out_dir = self.dir / "out"
        self.run_cmd("export", self.path, "watchlist", "--out", str(out_dir))
        exported = out_dir / "t-trialcore.yaml"
        import monitor
        wl = monitor.load_watchlist(exported)
        self.assertEqual(len(wl["entries"]), 4)
        other = str(self.dir / "u.xlsx")
        self.run_cmd("new", other, "--name", "u", "--today", "2026-10-08")
        self.assertIn("4 added by id", self.run_cmd("add", other, "--core", "trialcore", "--watchlist", str(exported)))


class PlatformTest(unittest.TestCase):
    def test_board_open_in_another_program_gives_a_clear_error(self):
        folder = Path(tempfile.mkdtemp())
        try:
            path = str(folder / "t.xlsx")
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(out):
                board.main(["new", path, "--name", "t"])
            real = os.replace

            def locked(src, dst):
                raise PermissionError(13, "The process cannot access the file")

            os.replace = locked
            try:
                with redirect_stdout(out), redirect_stderr(out):
                    code = board.main(["set", path, "--max-credits", "20"])
            finally:
                os.replace = real
            self.assertEqual(code, 1)
            self.assertIn("probably open in Excel", out.getvalue())
            self.assertEqual(board.read_state(Path(path))["settings"]["maxCredits"], 30)  # the old file is intact
            self.assertEqual([f.name for f in folder.iterdir() if f.name.startswith(".board-")], [])
        finally:
            shutil.rmtree(folder)

    def test_lock_probe_never_signals_a_process_on_windows(self):
        import monitor
        calls = []
        real_name, real_kill, real_probe = os.name, os.kill, monitor._pid_alive_windows
        try:
            os.name = "nt"
            os.kill = lambda *a: calls.append(a)
            monitor._pid_alive_windows = lambda pid: True
            self.assertTrue(monitor._pid_alive(1234))
        finally:
            os.name, os.kill, monitor._pid_alive_windows = real_name, real_kill, real_probe
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
