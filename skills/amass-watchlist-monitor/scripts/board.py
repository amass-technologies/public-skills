#!/usr/bin/env python3
"""Watchlist board over the Amass MCP connector, no API key needed.

The board is one .xlsx file: readable sheets for people and a hidden sheet holding the state, so
the file itself is the memory between conversations. Claude calls the Amass MCP tools and pipes
what they return into this helper; the helper validates it, compares every record field by field
with the stored copy, and rewrites the board. Standard library only, Python 3.9 or newer.

    board.py new BOARD --name NAME [--title TEXT]
    board.py add BOARD --core trialcore --search "ulotaront" --limit 50 < records.json
    board.py start BOARD                      # what is due and which MCP calls to make
    board.py ingest BOARD --core trialcore --search S1 < records.json
    board.py status BOARD
    board.py review BOARD                     # the changes, before they are stored
    board.py finish BOARD                     # store them, rewrite the board, print the digest

Run `board.py <command> --help` for the options of each command.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import shlex
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

FORMAT = "amass-watchlist-board"
FORMAT_VERSION = 1
STATE_SHEET = "_state"
MAX_TEXT = 300  # transcribed text longer than this is dropped: a copy slip there costs more than it tells
CHUNK = 30000  # characters per state cell; Excel caps a cell at 32,767
SEARCH_CREDITS = 2
FETCH_CREDITS = 1
DEFAULT_FULL_EVERY = 7
DEFAULT_MAX_CREDITS = 30
LIST_CAP = 10
LABEL_MAX = 40

NOT_FOUND = "Not found in Amass, or back again"
STATUS = "Status, phase and date changes"
RESULTS = "Results posted"
LABEL = "Label and SmPC revisions"
RETRACTIONS = "Retractions and corrections"
LINKS = "New or removed cross-links"
OTHER = "Other field changes"
METADATA = "Metadata only"
CLASS_ORDER = (NOT_FOUND, STATUS, RESULTS, LABEL, RETRACTIONS, LINKS, OTHER, METADATA)
SHORT = {NOT_FOUND: "not found", STATUS: "status, phase or dates", RESULTS: "results posted", LABEL: "label or SmPC",
         RETRACTIONS: "retraction or correction", LINKS: "cross-links", OTHER: "other fields", METADATA: "metadata"}


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class BoardError(Exception):
    pass


# --------------------------------------------------------------------------
# What is tracked per Core
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    name: str
    cls: str
    kind: str  # str enum int num bool date set ids obj
    label: str
    choices: tuple = ()
    prefix: str = ""
    fetch_only: bool = False
    reduce: Callable[[Any], Any] | None = None


def F(name: str, cls: str, kind: str, label: str | None = None, **kw: Any) -> Field:
    return Field(name, cls, kind, label or name, **kw)


def _moa(value: Any) -> Any:
    if isinstance(value, list):
        return [(m.get("mechanismOfAction") or m.get("actionType")) if isinstance(m, dict) else m for m in value]
    return value


def _target_class(value: Any) -> Any:
    if isinstance(value, dict):
        path = value.get("path")
        return " > ".join(str(p) for p in path) if isinstance(path, list) else None
    return value


def _tractability(value: Any) -> Any:
    if isinstance(value, dict):
        out = []
        for modality, kinds in sorted(value.items()):
            if isinstance(kinds, dict):
                for kind, items in sorted(kinds.items()):
                    for item in items or []:
                        out.append(f"{modality} {kind}: {item}")
        return out
    return value


TRIAL_STATUSES = (
    "RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION", "ACTIVE_NOT_RECRUITING", "SUSPENDED",
    "TERMINATED", "COMPLETED", "WITHDRAWN", "UNKNOWN", "WITHHELD", "AVAILABLE", "NO_LONGER_AVAILABLE",
    "TEMPORARILY_NOT_AVAILABLE", "APPROVED_FOR_MARKETING",
)
PHASES = ("EARLY_PHASE1", "PHASE1", "PHASE1/PHASE2", "PHASE2", "PHASE2/PHASE3", "PHASE3", "PHASE4", "NA")
STUDY_TYPES = ("INTERVENTIONAL", "OBSERVATIONAL", "EXPANDED_ACCESS")


@dataclass(frozen=True)
class Core:
    key: str
    label: str
    prefix: str
    sheet: str
    noun: str
    interval: int  # days between Amass refreshes
    quick: bool  # search results carry every tracked field except cross-links
    search_tool: str
    get_tool: str
    fetch_types: tuple
    identity: tuple
    fields: tuple
    drop: frozenset

    @property
    def by_name(self) -> dict:
        return {f.name: f for f in self.fields}

    def valid(self, value: str) -> bool:
        return value.startswith(self.prefix) and re.fullmatch(r"[A-Za-z0-9]{16,32}", value[len(self.prefix):]) is not None


TRIALCORE = Core(
    "trialcore", "TrialCore", "AMTC_", "Trials", "trials", 1, True,
    "search_amass_trialcore_records", "get_amass_trialcore_record", ("nctId", "registryId"),
    ("nctId", "registryId", "sourceRegistry", "url"),
    (
        F("overallStatus", STATUS, "enum", "status", choices=TRIAL_STATUSES),
        F("whyStopped", STATUS, "str", "why stopped"),
        F("phase", STATUS, "enum", choices=PHASES),
        F("enrollment", OTHER, "int"),
        F("startDate", STATUS, "date", "start date"),
        F("completionDate", STATUS, "date", "completion date"),
        F("hasResults", RESULTS, "bool", "results posted"),
        F("resultsFirstPostDate", RESULTS, "date", "results first posted"),
        F("referencesBiomedCore", LINKS, "ids", "linked papers", prefix="AMBC_", fetch_only=True),
        F("referencesDrugCore", LINKS, "ids", "linked drugs", prefix="AMDC_", fetch_only=True),
        F("briefTitle", OTHER, "str", "title"),
        F("acronym", OTHER, "str"),
        F("sponsorName", OTHER, "str", "sponsor"),
        F("studyType", OTHER, "enum", "study type", choices=STUDY_TYPES),
        F("conditions", OTHER, "set"),
        F("interventionNames", OTHER, "set", "interventions"),
        F("interventionTypes", OTHER, "set", "intervention types"),
        F("facilityCountries", OTHER, "set", "countries"),
        F("lastUpdateDate", METADATA, "date", "Amass update date"),
    ),
    frozenset({"briefSummary", "createDate", "outcomes"}),
)

BIOMEDCORE = Core(
    "biomedcore", "BiomedCore", "AMBC_", "Papers", "papers", 1, True,
    "search_amass_biomedcore_records", "get_amass_biomedcore_record", ("pmid", "doi"),
    ("pmid", "doi", "url"),
    (
        F("isRetracted", RETRACTIONS, "bool", "retracted"),
        F("publicationTypes", RETRACTIONS, "set", "publication types"),
        F("referencesTrialCore", LINKS, "ids", "linked trials", prefix="AMTC_", fetch_only=True),
        F("title", OTHER, "str"),
        F("journal", OTHER, "str"),
        F("publicationDate", OTHER, "date", "publication date"),
        F("citationCount", METADATA, "int", "citations"),
        F("hasFulltext", METADATA, "bool", "full text available"),
        F("journalQualityJufo", METADATA, "int", "JuFo level"),
        F("lastUpdateDate", METADATA, "date", "Amass update date"),
    ),
    frozenset({"abstract", "authors", "fulltext", "references", "citedBy", "createDate"}),
)

REGULATORYCORE = Core(
    "regulatorycore", "RegulatoryCore", "AMRC_", "Authorizations", "authorizations", 7, False,
    "search_amass_regulatorycore_records", "get_amass_regulatorycore_record",
    ("fdaApplicationNumber", "emaProductNumber", "ndc", "splSetId"),
    ("agency", "applicationNumber", "productNumber", "url"),
    (
        F("authorizationStatus", STATUS, "str", "status"),
        F("isOrphan", STATUS, "bool", "orphan"),
        F("designations", STATUS, "set"),
        F("opinionStatus", STATUS, "str", "EMA opinion status"),
        F("withdrawalDate", STATUS, "date", "withdrawal date"),
        F("withdrawalReason", STATUS, "str", "withdrawal reason"),
        F("withdrawalOfApplicationDate", STATUS, "date", "application withdrawn"),
        F("refusalOfMarketingAuthorisationDate", STATUS, "date", "refusal date"),
        F("withdrawalExpiryRevocationLapseDate", STATUS, "date", "withdrawal, expiry or revocation date"),
        F("labelDate", LABEL, "date", "FDA label date"),
        F("smpcDate", LABEL, "date", "SmPC date"),
        F("revisionNumber", LABEL, "int", "SmPC revision"),
        F("latestProcedure", LABEL, "str", "latest EMA procedure"),
        F("europeanCommissionDecisionDate", LABEL, "date", "EC decision date"),
        F("sectionCount", LABEL, "int", "parsed document sections", fetch_only=True),
        F("therapeuticIndication", LABEL, "str", "indication text"),
        F("referencesDrugCore", LINKS, "ids", "linked drugs", prefix="AMDC_", fetch_only=True),
        F("name", OTHER, "str"),
        F("activeSubstance", OTHER, "str", "active substance"),
        F("marketingAuthorisationHolder", OTHER, "str", "holder"),
        F("procedureType", OTHER, "str", "procedure type"),
        F("authorizationDate", OTHER, "date", "authorization date"),
        F("additionalMonitoring", OTHER, "bool", "additional monitoring"),
        F("lastUpdateDate", METADATA, "date", "Amass update date", fetch_only=True),
    ),
    frozenset({
        "authorizationsByAgency", "createDate", "firstAuthorizationDate", "moleculeType", "smpcUrl", "labelUrl",
        "ndc", "splSetId", "prescriptionClass", "submissionClassCode", "category", "isBiosimilar",
        "isAdvancedTherapy", "isGenericOrHybrid", "pharmacotherapeuticGroup", "patientSafety",
        "firstPublishedDate", "opinionAdoptedDate", "withdrawalSourceUrl",
    }),
)

DRUGCORE = Core(
    "drugcore", "DrugCore", "AMDC_", "Drugs", "drugs", 7, False,
    "search_amass_drugcore_records", "get_amass_drugcore_record", ("chemblId",),
    ("chemblId", "url"),
    (
        F("maxClinicalStage", STATUS, "str", "highest stage"),
        F("drugType", STATUS, "str", "modality"),
        F("referencesTrialCore", LINKS, "ids", "linked trials", prefix="AMTC_", fetch_only=True),
        F("referencesBiomedCore", LINKS, "ids", "linked papers", prefix="AMBC_", fetch_only=True),
        F("referencesRegulatoryCore", LINKS, "ids", "linked authorizations", prefix="AMRC_", fetch_only=True),
        F("referencesGeneCore", LINKS, "ids", "linked genes", prefix="AMGC_", fetch_only=True),
        F("name", OTHER, "str"),
        F("synonyms", OTHER, "set"),
        F("tradeNames", OTHER, "set", "trade names"),
        F("parent", OTHER, "str", "parent drug", fetch_only=True),
        F("mechanismsOfAction", OTHER, "set", "mechanisms", fetch_only=True, reduce=_moa),
        F("lastUpdateDate", METADATA, "date", "Amass update date"),
    ),
    frozenset({"description", "inchiKey", "canonicalSmiles", "children", "createDate"}),
)

GENECORE = Core(
    "genecore", "GeneCore", "AMGC_", "Genes", "genes", 7, False,
    "search_amass_genecore_records", "get_amass_genecore_record", ("ensemblGeneId",),
    ("ensemblGeneId", "hgncId", "url"),
    (
        F("referencesDrugCore", LINKS, "ids", "linked drugs", prefix="AMDC_", fetch_only=True),
        F("symbol", OTHER, "str"),
        F("name", OTHER, "str"),
        F("geneType", OTHER, "str", "gene type"),
        F("synonyms", OTHER, "set"),
        F("targetClass", OTHER, "str", "target class", reduce=_target_class),
        F("tractability", OTHER, "set", reduce=_tractability),
        F("safetyLiabilities", OTHER, "obj", "safety liabilities"),
        F("isEssential", OTHER, "bool", "essential in DepMap"),
        F("loeuf", OTHER, "num", "LOEUF"),
        F("lastUpdateDate", METADATA, "date", "Amass update date"),
    ),
    frozenset({
        "summary", "location", "chromosome", "strand", "entrezGeneId", "uniprotIds", "hgncGeneGroups",
        "maneSelect", "omimId", "orphanet", "iuphar", "protein", "createDate",
    }),
)

CORES = {c.key: c for c in (TRIALCORE, BIOMEDCORE, REGULATORYCORE, DRUGCORE, GENECORE)}
PREFIXES = {c.prefix: c for c in CORES.values()}


def core_arg(value: str) -> Core:
    key = value.strip().lower()
    if key == "patentcore":
        raise argparse.ArgumentTypeError(
            "PatentCore cannot be watched: it has no Amass dates and no change feed. "
            "Watch TrialCore, BiomedCore, RegulatoryCore, DrugCore or GeneCore."
        )
    if key not in CORES:
        raise argparse.ArgumentTypeError(f"core must be one of {', '.join(CORES)}")
    return CORES[key]


# --------------------------------------------------------------------------
# Values
# --------------------------------------------------------------------------

DATE_RE = re.compile(r"^(\d{4}(?:-\d{2}(?:-\d{2})?)?)(?:[T ][0-9:.+\-Z]*)?$")


def canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _text(value: Any) -> str:
    return " ".join(str(value).split())


def parse_value(spec: Field, value: Any) -> Any:
    if spec.reduce is not None:
        value = spec.reduce(value)
    kind = spec.kind
    if kind in ("set", "ids"):
        if value is None:
            return []
        if isinstance(value, (str, dict)):
            value = [value]
        if not isinstance(value, list):
            raise ValueError(f"expected a list, got {value!r}")
        out = set()
        for item in value:
            if item is None:
                continue
            text = canon(item) if isinstance(item, (dict, list)) else _text(item)
            if not text:
                continue
            if kind == "ids" and not text.startswith(spec.prefix):
                raise ValueError(f"{text!r} is not a {spec.prefix}... id")
            out.add(text)
        return sorted(out)
    if value is None:
        return None
    if kind in ("str", "enum"):
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError(f"expected text, got {value!r}")
        text = _text(value)
        if not text:
            return None
        if kind == "enum" and text not in spec.choices:
            raise ValueError(f"{text!r} is not one of {', '.join(spec.choices)}")
        return text
    if kind == "int":
        if isinstance(value, bool):
            raise ValueError(f"expected a whole number, got {value!r}")
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
            return int(value.strip())
        raise ValueError(f"expected a whole number, got {value!r}")
    if kind == "num":
        if isinstance(value, bool):
            raise ValueError(f"expected a number, got {value!r}")
        if isinstance(value, (int, float)):
            return int(value) if float(value).is_integer() else float(value)
        try:
            number = float(str(value))
        except ValueError:
            raise ValueError(f"expected a number, got {value!r}") from None
        return int(number) if number.is_integer() else number
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        raise ValueError(f"expected true or false, got {value!r}")
    if kind == "date":
        match = DATE_RE.match(str(value).strip()) if isinstance(value, str) else None
        if not match:
            raise ValueError(f"expected a date as YYYY-MM-DD, got {value!r}")
        return match.group(1)
    if kind == "obj":
        return canon(value)
    raise ValueError(f"unknown kind {kind}")


def too_long(value: Any) -> bool:
    if isinstance(value, str):
        return len(value) > MAX_TEXT
    if isinstance(value, list):
        return any(isinstance(v, str) and len(v) > MAX_TEXT for v in value)
    return False


def same(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-9
        except (TypeError, ValueError):
            return False
    return a == b


def show(value: Any, n: int = 120) -> str:
    if value is None or value == []:
        return "none"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if isinstance(value, list):
        text = ", ".join(value[:LIST_CAP]) + (f" and {len(value) - LIST_CAP} more" if len(value) > LIST_CAP else "")
    else:
        text = str(value)
    return text if len(text) <= n else text[: n - 1] + "…"


def describe(spec: Field, old: Any, new: Any, human: bool = True) -> str:
    """'status: Active, not recruiting → Completed' for people; raw values when Claude verifies a change."""
    if spec.kind in ("set", "ids"):
        added = [v for v in new if v not in old]
        removed = [v for v in old if v not in new]
        parts = []
        if added:
            parts.append(f"added {len(added)} ({show(added, 160)})" if spec.kind == "ids" else f"added {show(added, 160)}")
        if removed:
            parts.append(f"removed {len(removed)} ({show(removed, 160)})" if spec.kind == "ids" else f"removed {show(removed, 160)}")
        return f"{spec.label}: " + "; ".join(parts)
    meaning = STATUS_MEANING.get(new) if human and spec.name == "overallStatus" else None
    if human and (spec.kind == "enum" or spec.name in PRETTY_FIELDS):
        old, new = (pretty(v) if isinstance(v, str) else v for v in (old, new))
    if meaning:
        return f"{spec.label}: {show(old)} → {show(new)} ({meaning})"
    if human and spec.kind == "date":
        old, new = (human_date(v) if v else v for v in (old, new))
    return f"{spec.label}: {show(old)} → {show(new)}"


def prepare(core: Core, raw: Any, strict: bool, errors: list, where: str) -> tuple | None:
    """One MCP record -> (amassId, fields, identity, dropped long fields); errors are appended."""
    if not isinstance(raw, dict):
        errors.append(f"{where}: expected a JSON object, got {type(raw).__name__}")
        return None
    rec = dict(raw)
    amass_id = rec.pop("amassId", None)
    if not isinstance(amass_id, str) or not core.valid(amass_id.strip()):
        errors.append(f"{where}: amassId {amass_id!r} is not a {core.label} Amass ID ({core.prefix}...)")
        return None
    amass_id = amass_id.strip()
    nested: set = set()
    for key in ("emaDetails", "fdaDetails"):
        sub = rec.pop(key, None)
        if isinstance(sub, dict):
            for k, v in sub.items():
                if k not in rec:
                    rec[k] = v
                    nested.add(k)
    sections = rec.pop("documentSections", None)
    if isinstance(sections, list) and "sectionCount" not in rec:
        rec["sectionCount"] = len(sections)
    essential = rec.pop("depmapEssentiality", None)
    if isinstance(essential, dict) and "isEssential" not in rec:
        rec["isEssential"] = essential.get("isEssential")
    constraint = rec.pop("gnomadConstraint", None)
    if isinstance(constraint, dict) and "loeuf" not in rec:
        rec["loeuf"] = (constraint.get("lossOfFunction") or {}).get("loeuf")
    source_url = rec.pop("sourceUrl", None)
    if source_url and not rec.get("url"):
        rec["url"] = source_url
    fields: dict = {}
    ident: dict = {}
    dropped: list = []
    specs = core.by_name
    for key, value in rec.items():
        if key in core.identity:
            if value is not None and _text(value):
                ident[key] = _text(value)
            continue
        spec = specs.get(key)
        if spec is None:
            if strict and key not in core.drop and key not in nested:
                errors.append(
                    f"{amass_id}: unknown field {key!r} for {core.label}; tracked fields are "
                    f"{', '.join(specs)}; identity fields are {', '.join(core.identity)}"
                )
            continue
        try:
            parsed = parse_value(spec, value)
        except ValueError as err:
            errors.append(f"{amass_id}: {key}: {err}")
            continue
        if strict and too_long(parsed):
            dropped.append(key)
            continue
        fields[key] = parsed
    return amass_id, fields, ident, dropped


def extract_records(core: Core, data: Any) -> list:
    """Every object carrying an amassId of this Core, wherever it sits in a saved tool result."""
    found: list = []
    seen: set = set()

    def walk(node: Any) -> None:
        if isinstance(node, str):
            text = node.strip()
            if text[:1] in "{[":
                try:
                    walk(json.loads(text))
                except ValueError:
                    pass
            return
        if isinstance(node, dict):
            aid = node.get("amassId")
            if isinstance(aid, str) and aid.startswith(core.prefix):
                if aid not in seen:
                    seen.add(aid)
                    found.append(node)
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return found


# --------------------------------------------------------------------------
# xlsx: written and read with the standard library
# --------------------------------------------------------------------------

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_X_ESC = re.compile(r"_(x[0-9A-Fa-f]{4}_)")
_X_UNESC = re.compile(r"_x([0-9A-Fa-f]{4})_")

FONTS = (
    '<font><sz val="11"/><name val="Calibri"/></font>',
    '<font><b/><sz val="11"/><name val="Calibri"/></font>',
    '<font><b/><sz val="16"/><name val="Calibri"/></font>',
    '<font><sz val="10"/><color rgb="FF6B6B6B"/><name val="Calibri"/></font>',
    '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>',
    '<font><b/><sz val="12"/><name val="Calibri"/></font>',
    '<font><u/><sz val="11"/><color rgb="FF1F5FBF"/><name val="Calibri"/></font>',
)
FILLS = {
    "header": "FFE7E6E6", "highlight": "FFFFF2CC", "today": "FF37474F",
    "ongoing": "FF43A047", "expected": "FFA5D6A7", "planned": "FFE0E0E0",
    "completed": "FFC9D3E3", "stopped": "FFE57373", "other": "FFBDBDBD",
}
# name: (font index, fill name or None, centred)
STYLE_DEFS = {
    "normal": (0, None, False), "header": (1, "header", False), "highlight": (0, "highlight", False),
    "title": (2, None, False), "muted": (3, None, False), "section": (5, None, False), "bold": (1, None, False),
    "link": (6, None, False), "today": (4, "today", True), "year": (1, "header", True),
    "highlight-link": (6, "highlight", False), "num": (0, None, True), "num-header": (1, "header", True),
    "bar-ongoing": (0, "ongoing", True), "bar-expected": (0, "expected", True), "bar-planned": (0, "planned", True),
    "bar-completed": (0, "completed", True), "bar-stopped": (0, "stopped", True), "bar-other": (0, "other", True),
}
STYLE = {name: i for i, name in enumerate(STYLE_DEFS)}


def _styles_xml() -> str:
    fill_ids = {name: i + 2 for i, name in enumerate(FILLS)}
    fills = ['<fill><patternFill patternType="none"/></fill>', '<fill><patternFill patternType="gray125"/></fill>']
    fills += [f'<fill><patternFill patternType="solid"><fgColor rgb="{rgb}"/><bgColor indexed="64"/></patternFill></fill>'
              for rgb in FILLS.values()]
    xfs = []
    for font, fill, centred in STYLE_DEFS.values():
        fid = fill_ids[fill] if fill else 0
        attrs = f'numFmtId="0" fontId="{font}" fillId="{fid}" borderId="0" xfId="0"'
        attrs += ' applyFont="1"' if font else ""
        attrs += ' applyFill="1"' if fill else ""
        if centred:
            xfs.append(f'<xf {attrs} applyAlignment="1"><alignment horizontal="center"/></xf>')
        else:
            xfs.append(f"<xf {attrs}/>")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<styleSheet xmlns="{NS_MAIN}">'
        f'<fonts count="{len(FONTS)}">{"".join(FONTS)}</fonts>'
        f'<fills count="{len(fills)}">{"".join(fills)}</fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        f'<cellXfs count="{len(xfs)}">{"".join(xfs)}</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )


@dataclass
class C:
    """A cell with a style of its own, and optionally a link."""
    value: Any = None
    style: str | None = None
    link: str | None = None


def _col(index: int) -> str:
    name, index = "", index + 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell_text(value: str) -> str:
    return xml_escape(_X_ESC.sub(r"_x005F_\1", _ILLEGAL.sub("", value)))


def _cell_xml(ref: str, value: Any, style: str | None) -> str | None:
    link = None
    if isinstance(value, C):
        style = value.style or style
        link = value.link
        value = value.value
    attr = f' s="{STYLE[style]}"' if style and STYLE[style] else ""
    if value is None or value == "":
        return f'<c r="{ref}"{attr}/>' if attr else None
    if isinstance(value, bool):
        value = "yes" if value else "no"
    if isinstance(value, (int, float)) and not link:
        return f'<c r="{ref}"{attr}><v>{value}</v></c>'
    text = str(value)
    if link:
        formula = 'HYPERLINK("{}","{}")'.format(link.replace('"', '""'), text.replace('"', '""'))
        return f'<c r="{ref}"{attr} t="str"><f>{_cell_text(formula)}</f><v>{_cell_text(text)}</v></c>'
    return f'<c r="{ref}"{attr} t="inlineStr"><is><t xml:space="preserve">{_cell_text(text)}</t></is></c>'


def _sheet_xml(sheet: dict, selected: bool) -> str:
    rows = sheet["rows"]
    header = sheet.get("header", True)
    highlight = sheet.get("highlight", set())
    out = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="{NS_MAIN}" xmlns:r="{NS_REL}">']
    view = '<sheetViews><sheetView workbookViewId="0"' + (' tabSelected="1"' if selected else "")
    view += ' showGridLines="0"' if sheet.get("gridlines") is False else ""
    view += ">"
    if header and len(rows) > 1 and sheet.get("freeze_first_column"):
        view += '<pane xSplit="1" ySplit="1" topLeftCell="B2" activePane="bottomRight" state="frozen"/>'
    elif header and len(rows) > 1:
        view += '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
    out.append(view + "</sheetView></sheetViews>")
    out.append('<sheetFormatPr defaultRowHeight="15"/>')
    widths = sheet.get("widths")
    if widths:
        out.append("<cols>" + "".join(
            f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths)
        ) + "</cols>")
    out.append("<sheetData>")
    for r, row in enumerate(rows):
        style = "header" if header and r == 0 else ("highlight" if r in highlight else None)
        cells = [_cell_xml(f"{_col(c)}{r + 1}", value, style) for c, value in enumerate(row)]
        out.append(f'<row r="{r + 1}">' + "".join(x for x in cells if x) + "</row>")
    out.append("</sheetData>")
    if header and len(rows) > 1 and rows[0]:
        out.append(f'<autoFilter ref="A1:{_col(len(rows[0]) - 1)}{len(rows)}"/>')
    out.append("</worksheet>")
    return "".join(out)


def write_xlsx(path: Path, sheets: list) -> None:
    first_visible = next(i for i, s in enumerate(sheets) if not s.get("hidden"))
    types = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(len(sheets))
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f"{types}</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Relationships xmlns="{NS_PKG}"><Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    sheet_tags = "".join(
        f'<sheet name="{xml_escape(s["name"])}" sheetId="{i + 1}"'
        + (' state="hidden"' if s.get("hidden") else "")
        + f' r:id="rId{i + 1}"/>'
        for i, s in enumerate(sheets)
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<workbook xmlns="{NS_MAIN}" xmlns:r="{NS_REL}">'
        f'<bookViews><workbookView activeTab="{first_visible}"/></bookViews>'
        f"<sheets>{sheet_tags}</sheets></workbook>"
    )
    rels = "".join(
        f'<Relationship Id="rId{i + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i + 1}.xml"/>'
        for i in range(len(sheets))
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Relationships xmlns="{NS_PKG}">{rels}'
        f'<Relationship Id="rId{len(sheets) + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/></Relationships>'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".board-", suffix=".xlsx", dir=str(path.parent))
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", content_types)
            z.writestr("_rels/.rels", root_rels)
            z.writestr("xl/workbook.xml", workbook)
            z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
            z.writestr("xl/styles.xml", _styles_xml())
            for i, sheet in enumerate(sheets):
                z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(sheet, i == first_visible))
        try:
            os.replace(tmp, path)
        except PermissionError:
            raise BoardError(f"could not save {path}: it is probably open in Excel or another program. Close it "
                             "and run the same command again; nothing was lost.") from None
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _q(tag: str) -> str:
    return f"{{{NS_MAIN}}}{tag}"


def read_sheets(path: Path, names: set) -> dict:
    """{sheet name: [ {column letter: text}, ... ]} for the named sheets that exist."""
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as err:
        raise BoardError(f"{path} is not a readable .xlsx file ({err})") from None
    with z:
        try:
            workbook = ET.fromstring(z.read("xl/workbook.xml"))
            rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        except KeyError:
            raise BoardError(f"{path} is not an .xlsx workbook") from None
        targets = {r.get("Id"): (r.get("Type") or "", r.get("Target") or "") for r in rels}

        def part(target: str) -> str:
            return target.lstrip("/") if target.startswith("/") else "xl/" + target

        shared: list = []
        for kind, target in targets.values():
            if kind.endswith("/sharedStrings"):
                sst = ET.fromstring(z.read(part(target)))
                for si in sst.findall(_q("si")):
                    pieces = [si.find(_q("t"))] + [r.find(_q("t")) for r in si.findall(_q("r"))]
                    shared.append("".join(p.text or "" for p in pieces if p is not None))
        found = {}
        for sheet in workbook.iter(_q("sheet")):
            name = sheet.get("name")
            rel = targets.get(sheet.get(f"{{{NS_REL}}}id"))
            if name in names and rel:
                found[name] = ET.fromstring(z.read(part(rel[1])))
    out = {}
    for name, sheet_xml in found.items():
        rows = []
        for row in sheet_xml.iter(_q("row")):
            values: dict = {}
            for position, cell in enumerate(row.findall(_q("c"))):
                ref = cell.get("r")
                column = re.match(r"[A-Z]+", ref).group(0) if ref else _col(position)
                kind = cell.get("t")
                if kind == "inlineStr":
                    text = "".join(t.text or "" for t in cell.iter(_q("t")))
                else:
                    v = cell.find(_q("v"))
                    text = v.text if v is not None and v.text is not None else ""
                    if kind == "s" and text:
                        text = shared[int(text)]
                values[column] = _X_UNESC.sub(lambda m: chr(int(m.group(1), 16)), text)
            rows.append(values)
        out[name] = rows
    return out


def read_state(path: Path, sheets: dict | None = None) -> dict:
    """The state JSON from the hidden sheet; survives a save from Excel, Numbers or Google Sheets."""
    rows = (sheets if sheets is not None else read_sheets(path, {STATE_SHEET})).get(STATE_SHEET)
    if rows is None:
        raise BoardError(f"{path} is not a watchlist board: it has no {STATE_SHEET} sheet")
    if not rows or rows[0].get("A") != FORMAT:
        raise BoardError(f"{path} is not a watchlist board (the {STATE_SHEET} sheet has no marker)")
    chunks = []
    for values in rows[1:]:
        try:
            chunks.append((int(float(values.get("A", ""))), values.get("B", "")))
        except ValueError:
            continue
    try:
        state = json.loads("".join(chunk for _, chunk in sorted(chunks)))
    except ValueError as err:
        raise BoardError(f"{path}: the board's state could not be read ({err})") from None
    if state.get("format") != FORMAT:
        raise BoardError(f"{path} is not a watchlist board")
    return state


# --------------------------------------------------------------------------
# The board
# --------------------------------------------------------------------------


def iso(day: dt.date) -> str:
    return day.isoformat()


def as_date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value) if value else None
    except ValueError:
        return None


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
STATUS_TEXT = {
    "RECRUITING": "Recruiting", "NOT_YET_RECRUITING": "Not yet recruiting",
    "ENROLLING_BY_INVITATION": "Enrolling by invitation", "ACTIVE_NOT_RECRUITING": "Active, not recruiting",
    "SUSPENDED": "Suspended", "TERMINATED": "Terminated", "COMPLETED": "Completed", "WITHDRAWN": "Withdrawn",
    "UNKNOWN": "Unknown status", "WITHHELD": "Withheld", "AVAILABLE": "Available",
    "NO_LONGER_AVAILABLE": "No longer available", "TEMPORARILY_NOT_AVAILABLE": "Temporarily not available",
    "APPROVED_FOR_MARKETING": "Approved for marketing",
}
PHASE_TEXT = {
    "EARLY_PHASE1": "Early phase 1", "PHASE1": "Phase 1", "PHASE1/PHASE2": "Phase 1/2", "PHASE2": "Phase 2",
    "PHASE2/PHASE3": "Phase 2/3", "PHASE3": "Phase 3", "PHASE4": "Phase 4", "NA": "Not applicable",
}
REGISTRY_TEXT = {
    "clinicaltrials_gov": "ClinicalTrials.gov", "jprn": "jRCT (Japan)", "ctis": "CTIS (EU)", "euctr": "EU CTR",
    "chictr": "ChiCTR (China)", "ctri": "CTRI (India)", "isrctn": "ISRCTN", "anzctr": "ANZCTR", "drks": "DRKS",
    "irct": "IRCT (Iran)", "kct": "CRIS (Korea)", "tctr": "TCTR (Thailand)", "pactr": "PACTR", "rebec": "ReBEC",
}
REGISTRY_SHORT = {"clinicaltrials_gov": "ClinicalTrials.gov", "jprn": "jRCT", "ctis": "CTIS", "euctr": "EU CTR",
                  "chictr": "ChiCTR", "ctri": "CTRI", "kct": "CRIS"}
PHASE_SHORT = {"EARLY_PHASE1": "P0/1", "PHASE1": "P1", "PHASE1/PHASE2": "P1/2", "PHASE2": "P2",
               "PHASE2/PHASE3": "P2/3", "PHASE3": "P3", "PHASE4": "P4"}
STATUS_MEANING = {"RECRUITING": "now enrolling", "ACTIVE_NOT_RECRUITING": "enrolment closed",
                  "COMPLETED": "results usually due within a year", "TERMINATED": "stopped early",
                  "WITHDRAWN": "withdrawn before enrolling", "SUSPENDED": "paused"}
PRETTY_FIELDS = {"authorizationStatus", "maxClinicalStage", "drugType", "geneType", "procedureType", "opinionStatus"}
ONGOING = {"RECRUITING", "ENROLLING_BY_INVITATION", "ACTIVE_NOT_RECRUITING"}
PLANNED = {"NOT_YET_RECRUITING"}
STOPPED = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}
TODAY = dt.date.today()  # main() replaces it with --today when given
ABOUT_LINES = (
    ("What this file is", "Your Amass watchlist, and the memory of the watch. Keep it. To update it, attach it to a "
     "new Claude chat and say \"check my watchlist\" (in Cowork or Claude Code, ask Claude to check the "
     "watchlist)."),
    ("Your edits", "Each check rewrites this file. What you type in the Short name and Notes columns of the record "
     "sheets is kept; everything else is regenerated."),
    ("How a check works", "Claude re-reads each record through the Amass MCP connector, and the file's helper "
     "compares it field by field with the copy stored here. Daily checks re-run the stored searches (2 credits "
     "each) and fetch only what they miss; a weekly full check fetches every record (1 credit each) and also "
     "catches new cross-links."),
    ("What it cannot see", "Results revised after their first posting, why a trial stopped, which label or SmPC "
     "section changed, errata and expressions of concern, and the exact day of a change. Amass API mode sees "
     "all of these."),
    ("Worth watching", "Prompts computed from the records' own dates and registries (a trial about to end, a "
     "missed start date, results posted only in a registry copy, results not posted a year after completion). "
     "They cost nothing and are not news from Amass."),
)


def pretty(value: Any) -> Any:
    """Registry vocabulary in plain words, for people; the state keeps the raw values."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if not isinstance(value, str):
        return value
    for table in (STATUS_TEXT, PHASE_TEXT, REGISTRY_TEXT):
        if value in table:
            return table[value]
    if re.fullmatch(r"[A-Z][A-Z0-9_]{3,}", value):
        text = value.replace("_", " ").lower()
        return text[0].upper() + text[1:]
    return value


def partial_date(value: Any) -> dt.date | None:
    if not isinstance(value, str):
        return None
    match = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", value)
    if not match:
        return None
    try:
        return dt.date(int(match.group(1)), int(match.group(2) or 1), int(match.group(3) or 1))
    except ValueError:
        return None


def human_date(value: Any) -> str:
    """'2026-10-29' -> '29 Oct 2026'; '2026-10' -> 'Oct 2026'."""
    day = partial_date(value) if not isinstance(value, dt.date) else value
    if day is None:
        return str(value or "")
    if isinstance(value, str) and len(value) == 7:
        return f"{MONTHS[day.month - 1]} {day.year}"
    if isinstance(value, str) and len(value) == 4:
        return str(day.year)
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def human_month(value: Any) -> str:
    day = partial_date(value)
    return f"{MONTHS[day.month - 1]} {day.year}" if day else ""


def registry_short(rec: dict) -> str:
    reg = rec["identity"].get("sourceRegistry") or ""
    return REGISTRY_SHORT.get(reg, pretty(reg) or "copy")


def name_of(core: "Core", rec: dict, width: int = 45, records: dict | None = None) -> str:
    """How a record is named to people: its short name and source id, or the id and a short title.
    A registry copy is named after its trial: 'Acute, US + Japan, jRCT copy (JPRN-jRCT2031250398)'."""
    source = source_of(core, rec["identity"], rec["fields"]) or ""
    main = (records or {}).get(rec.get("copyOf") or "")
    if main:
        return f"{short_name(core, main)}, {registry_short(rec)} copy ({source})"
    if rec.get("label"):
        return f"{rec['label']} ({source})" if source else rec["label"]
    title = title_of(core, rec["fields"]) or ""
    return f"{source} {show(title, width)}".strip() if title else source


def trial_groups(records: dict) -> tuple:
    """(main trial ids, {main id: [copy ids]}): a registry copy is listed under the trial it copies."""
    mains, copies = [], {}
    for rid, rec in records.items():
        if rec["core"] != "trialcore":
            continue
        main = rec.get("copyOf")
        if main and main in records and main != rid:
            copies.setdefault(main, []).append(rid)
        else:
            mains.append(rid)
    return mains, copies


def _phase_of_status(status: Any) -> str | None:
    if status in ONGOING or status in PLANNED:
        return "ongoing"
    if status == "COMPLETED" or status in STOPPED:
        return "ended"
    return None


def copy_flag(main: dict, copy: dict) -> str | None:
    """A registry copy that is ahead of its trial: results only in the copy, or the copy says it ended."""
    mf, cf = main["fields"], copy["fields"]
    if not mf or not cf:
        return None
    copy_id = source_of(TRIALCORE, copy["identity"], cf)
    reg = registry_short(copy)
    if cf.get("hasResults") is True and mf.get("hasResults") is False:
        return (f"the {reg} copy ({copy_id}) is marked as having results, which for that registry can mean "
                "only a results or protocol link: worth a look")
    if _phase_of_status(cf.get("overallStatus")) == "ended" and _phase_of_status(mf.get("overallStatus")) == "ongoing":
        return (f"the {reg} copy ({copy_id}) says {pretty(cf.get('overallStatus')).lower()}; "
                f"{registry_short(main)} still says {pretty(mf.get('overallStatus')).lower()}")
    return None


def _a_year_after(day: dt.date) -> dt.date:
    try:
        return day.replace(year=day.year + 1)
    except ValueError:
        return day + dt.timedelta(days=365)


def results_state(records: dict, rid: str, copy_ids: list, today: dt.date) -> str:
    """'yes', 'CTIS only', 'due Nov 2026', 'none after 1 yr', or blank while a trial is under way."""
    f = records[rid]["fields"]
    if "hasResults" not in f:
        return ""
    if f.get("hasResults"):
        # a non-US registry's results mark can be just a results or protocol link
        return "yes" if records[rid]["identity"].get("sourceRegistry") == "clinicaltrials_gov" else "marked"
    in_copy = [registry_short(records[c]) for c in copy_ids if records[c]["fields"].get("hasResults")]
    if in_copy:
        return f"marked in {in_copy[0]}"
    if f.get("overallStatus") != "COMPLETED":
        return ""
    end = partial_date(f.get("completionDate"))
    if end is None or records[rid]["identity"].get("sourceRegistry") != "clinicaltrials_gov":
        return "no"
    due = _a_year_after(end)
    return "none after 1 yr" if today > due else f"due {MONTHS[due.month - 1]} {due.year}"


def group_trials(records: dict, mains: list) -> dict:
    """{indication: [trial ids]}, largest group first; names that differ only in case share a group."""
    groups: dict = {}
    shown: dict = {}
    for rid in mains:
        name = indication_of(records[rid])
        key = name.lower()
        shown.setdefault(key, name)
        groups.setdefault(key, []).append(rid)
    order = sorted(groups, key=lambda k: (-len(groups[k]), k))
    return {shown[k]: groups[k] for k in order}


def watch_entries(records: dict, today: dt.date) -> list:
    """[(trial id, line)], most urgent first: what the dates and registries say is worth a look (no calls)."""
    entries = []
    mains, copies = trial_groups(records)
    for rid in mains:
        rec = records[rid]
        f = rec["fields"]
        if not f:
            continue
        status = f.get("overallStatus")
        end, start = partial_date(f.get("completionDate")), partial_date(f.get("startDate"))
        reasons = []  # (urgency, date, text)
        if (status in ONGOING or status in PLANNED) and end:
            days = (end - today).days
            if 0 <= days <= 60:
                reasons.append((0, end, f"ends {human_date(f['completionDate'])}, in {plural(days, 'day')}: expect "
                                        "a status change"))
            elif days < 0:
                reasons.append((1, end, f"completion date {human_date(f['completionDate'])} has passed; the registry "
                                        f"still says {pretty(status).lower()}"))
        if status in PLANNED and start and start < today:
            reasons.append((1, start, f"was due to start {human_date(f['startDate'])}; still not yet recruiting"))
        flags = [copy_flag(rec, records[c]) for c in copies.get(rid, [])]
        flags = [x for x in flags if x]
        results_due = None
        if (status == "COMPLETED" and f.get("hasResults") is False and end
                and rec["identity"].get("sourceRegistry") == "clinicaltrials_gov"):
            due = _a_year_after(end)
            if today > due:
                results_due = f"none on ClinicalTrials.gov a year after completion ({human_month(f['completionDate'])})"
            elif (today - end).days >= 270 or flags:
                results_due = f"ClinicalTrials.gov results usually due by {MONTHS[due.month - 1]} {due.year}"
        for flag in flags:
            text = flag + (f"; {results_due}" if results_due and "marked as having results" in flag else "")
            reasons.append((1, end or dt.date.max, text))
        if results_due and not any("marked as having results" in x for x in flags):
            reasons.append((2, end, _cap(results_due) if not results_due.startswith("none") else
                            "no results " + results_due[len("none "):]))
        if reasons:
            reasons.sort(key=lambda r: (r[0], r[1]))
            line = "; ".join(text for _, _, text in reasons)
            entries.append((reasons[0][0], reasons[0][1], rid, line))
    entries.sort(key=lambda e: (e[0], e[1]))
    return [(rid, line) for _, _, rid, line in entries]


def watch_items(records: dict, today: dt.date) -> list:
    """One line per trial, for the chat."""
    return [f"{name_of(TRIALCORE, records[rid])}: {line}" for rid, line in watch_entries(records, today)]


def where_it_stands(records: dict, today: dt.date) -> list:
    """Per indication: [indication, trials, ongoing, not yet, completed, stopped, next end, with results]."""
    mains, copies = trial_groups(records)
    mains = [m for m in mains if records[m]["fields"]]
    rows = []
    for group, members in group_trials(records, mains).items():
        st = [records[m]["fields"].get("overallStatus") for m in members]
        states = [results_state(records, m, copies.get(m, []), today) for m in members]
        results = sum(1 for x in states if x == "yes")
        copy_only = sum(1 for x in states if x.startswith("marked"))
        future = sorted(d for d in (partial_date(records[m]["fields"].get("completionDate")) for m in members
                                    if records[m]["fields"].get("overallStatus") in ONGOING | PLANNED) if d and d >= today)
        rows.append([group, len(members), sum(1 for x in st if x in ONGOING), sum(1 for x in st if x in PLANNED),
                     sum(1 for x in st if x == "COMPLETED"), sum(1 for x in st if x in STOPPED),
                     f"{MONTHS[future[0].month - 1]} {future[0].year}" if future else "",
                     f"{results}" + (f" (+{copy_only} marked)" if copy_only else "")])
    return rows


def short_name(core: "Core", rec: dict, width: int = 30, records: dict | None = None) -> str:
    f = rec["fields"]
    main = (records or {}).get(rec.get("copyOf") or "")
    if main:
        return f"{short_name(core, main, width)} · {registry_short(rec)} copy"
    return rec.get("label") or f.get("acronym") or show(title_of(core, f) or "(not fetched yet)", width)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def indication_of(rec: dict) -> str:
    """The group a trial is shown under: set with annotate, or read from its conditions."""
    if rec.get("indication"):
        return rec["indication"]
    names = []
    for condition in rec["fields"].get("conditions") or []:
        if condition.lower().startswith("therapeutic area"):
            continue  # EU registry classification lines, not conditions
        condition = re.split(r"\s+MedDRA", condition)[0].strip(" .;")
        if condition and condition.lower() not in {n.lower() for n in names}:
            names.append(condition)
    if not names:
        return "Other"
    text = " / ".join(names[:2])
    text = text[0].upper() + text[1:]
    return show(text, 60)


class Board:
    def __init__(self, path: Path, state: dict):
        self.path = path
        self.state = state

    # ---- load and save

    @classmethod
    def create(cls, path: Path, name: str, title: str | None, today: dt.date) -> "Board":
        state = {
            "format": FORMAT, "version": FORMAT_VERSION, "name": name, "title": title or name,
            "created": iso(today),
            "settings": {
                "intervals": {c.key: c.interval for c in CORES.values()},
                "fullEvery": DEFAULT_FULL_EVERY, "maxCredits": DEFAULT_MAX_CREDITS,
            },
            "cores": {}, "searches": [], "records": {}, "pendingIds": [], "changes": [], "checks": [],
            "credits": 0,
        }
        return cls(path, state)

    @classmethod
    def load(cls, path: Path) -> "Board":
        if not path.exists():
            raise BoardError(f"no board at {path}")
        sheets = read_sheets(path, {STATE_SHEET} | {c.sheet for c in CORES.values()})
        board = cls(path, read_state(path, sheets))
        board.absorb_edits(sheets)
        return board

    def absorb_edits(self, sheets: dict) -> None:
        """The Short name and Notes columns belong to the user: what they typed there wins."""
        for core in CORES.values():
            rows = sheets.get(core.sheet)
            if not rows:
                continue
            columns = {text.strip(): col for col, text in rows[0].items()}
            id_col = columns.get("Amass ID")
            if not id_col:
                continue
            for row in rows[1:]:
                rec = self.records.get(row.get(id_col, "").strip())
                if not rec:
                    continue
                for header, key in (("Short name", "label"), ("Notes", "note")):
                    col = columns.get(header)
                    if not col:
                        continue
                    value = row.get(col, "").strip()
                    if value:
                        rec[key] = value
                    else:
                        rec.pop(key, None)

    def save(self) -> None:
        text = json.dumps(self.state, ensure_ascii=True, separators=(",", ":"))
        chunks = [text[i: i + CHUNK] for i in range(0, len(text), CHUNK)] or [""]
        state_rows = [[FORMAT, FORMAT_VERSION]] + [[i + 1, c] for i, c in enumerate(chunks)]
        sheets = self.sheets() + [{"name": STATE_SHEET, "rows": state_rows, "header": False, "hidden": True}]
        write_xlsx(self.path, sheets)

    @property
    def check_path(self) -> Path:
        return self.path.with_name(self.path.name + ".check.json")

    def load_check(self) -> dict | None:
        if not self.check_path.exists():
            return None
        return json.loads(self.check_path.read_text(encoding="utf-8"))

    def save_check(self, check: dict) -> None:
        self.check_path.write_text(json.dumps(check, indent=1), encoding="utf-8")

    # ---- accessors

    @property
    def records(self) -> dict:
        return self.state["records"]

    def records_of(self, core: Core) -> list:
        return [rid for rid, rec in self.records.items() if rec["core"] == core.key]

    def pending_of(self, core: Core) -> list:
        return [p for p in self.state["pendingIds"] if p["core"] == core.key]

    def interval(self, core: Core) -> int:
        return int(self.state["settings"]["intervals"].get(core.key, core.interval))

    def core_state(self, core: Core) -> dict:
        return self.state["cores"].setdefault(core.key, {})

    def search(self, sid: str) -> dict | None:
        return next((s for s in self.state["searches"] if s["id"] == sid), None)

    def cores_present(self) -> list:
        keys = {rec["core"] for rec in self.records.values()} | {p["core"] for p in self.state["pendingIds"]}
        return [c for c in CORES.values() if c.key in keys]

    def next_due(self, core: Core) -> tuple:
        info = self.state["cores"].get(core.key, {})
        last = as_date(info.get("lastChecked"))
        full = as_date(info.get("lastFull"))
        due = iso(last + dt.timedelta(days=self.interval(core))) if last else "now"
        full_due = None
        if core.quick:
            full_due = iso(full + dt.timedelta(days=int(self.state["settings"]["fullEvery"]))) if full else "now"
        return due, full_due

    # ---- the readable sheets

    def sheets(self) -> list:
        latest = self.latest_check_date()
        out = [self.overview_sheet()]
        for core in self.cores_present():
            out.append(self.core_sheet(core, latest))
        out.append(self.changes_sheet())
        out.append(self.checks_sheet())
        out.append(self.settings_sheet())
        return out

    def latest_check_date(self) -> str | None:
        dates = [c["date"] for c in self.state["checks"] if set(c["kind"].split("/")) - {"setup", "baseline"}]
        return max(dates) if dates else None

    def freshness_line(self) -> str:
        last = max((self.state["cores"].get(c.key, {}).get("lastChecked") or "" for c in self.cores_present()),
                   default="")
        when = f"Checked {human_date(last)}" if last else "Not checked yet"
        return f"{when} · to update, ask Claude to check this watchlist (in a chat, attach this file)"

    def kpi_lines(self) -> list:
        """One count line per kind of record (trials get the per-indication table instead)."""
        lines = []
        for core in self.cores_present():
            rids = self.records_of(core)
            pending = len(self.pending_of(core))
            if core is TRIALCORE:
                mains, _ = trial_groups(self.records)
                text = plural(len(mains), "trial")
                if len(rids) != len(mains):
                    text += f" ({len(rids)} registry records)"
                parts = [text]
            else:
                parts = [plural(len(rids), core.noun[:-1])]
                if core is BIOMEDCORE:
                    retracted = sum(1 for rid in rids if self.records[rid]["fields"].get("isRetracted"))
                    parts.append(f"{retracted} retracted")
            if pending:
                parts.append(f"{pending} not fetched yet")
            lines.append(" · ".join(parts))
        return lines or ["No records yet."]

    def changed_lines(self) -> tuple:
        """(heading, [(record id or None, text, style)]) for the latest real check, ten records at most."""
        checks = [c for c in self.state["checks"] if set(c["kind"].split("/")) - {"setup", "baseline"}]
        if not checks:
            setup = self.state["checks"][0]["date"] if self.state["checks"] else self.state["created"]
            return "What changed", [(None, f"First look stored on {human_date(setup)}. Changes show from the next "
                                           "check.", "muted")]
        latest = checks[-1]["date"]
        earlier = [c["date"] for c in self.state["checks"] if c["date"] < latest]
        since = f" since {human_date(max(earlier))}" if earlier else ""
        changes = [c for c in self.state["changes"] if c["date"] == latest]
        news = [c for c in changes if c["cls"] != METADATA]
        by_record: dict = {}
        for c in news:
            by_record.setdefault(c["id"], []).append(c)
        n = len(by_record)
        head = (f"{plural(n, 'record')} changed{since}" if n else f"No changes{since}") + \
            f" (checked {human_date(latest)})"
        order = sorted(by_record, key=lambda rid: min(CLASS_ORDER.index(c["cls"]) for c in by_record[rid]))
        lines = [(rid if rid in self.records else None, "; ".join(c["text"] for c in by_record[rid]), "highlight")
                 for rid in order[:10]]
        if n > 10:
            lines.append((None, f"and {n - 10} more on the Changes sheet", "muted"))
        if not news:
            checked = next((c.get("records") for c in reversed(checks)), None)
            lines.append((None, f"Nothing changed on the {plural(checked or 0, 'record')} checked.", "normal"))
            older = [c for c in self.state["changes"] if c["cls"] != METADATA and c["date"] < latest]
            if older:
                last = older[-1]
                rec = self.records.get(last["id"])
                name = name_of(CORES[rec["core"]], rec, records=self.records) if rec else (last.get("source") or last["id"])
                lines.append((None, f"Last change {human_date(last['date'])}: {name}, {last['text']} (every change "
                                    "is on the Changes sheet)", "muted"))
        meta = len({c["id"] for c in changes if c["cls"] == METADATA} - set(by_record))
        if meta:
            lines.append((None, f"Also {plural(meta, 'metadata-only update')} (citation counts, Amass update dates).",
                          "muted"))
        return head, lines

    def changed_ids(self) -> set:
        latest = self.latest_check_date()
        return {c["id"] for c in self.state["changes"] if c["date"] == latest and c["cls"] != METADATA} if latest else set()

    def _record_row(self, rid: str, mark: str, text: str, style: str | None = None) -> list:
        rec = self.records[rid]
        core = CORES[rec["core"]]
        source = source_of(core, rec["identity"], rec["fields"]) or rid
        url = rec["identity"].get("url")
        link_style = "highlight-link" if style == "highlight" else "link"
        return [C(f"{mark} {short_name(core, rec, records=self.records)}", style),
                C(source, link_style, link=url) if url else C(source, style),
                C(_cap(text), style)]

    def timeline_rows(self) -> tuple:
        today = TODAY
        mains, copies = trial_groups(self.records)
        mains = [m for m in mains if self.records[m]["fields"]]
        if not mains:
            return [], []
        fields = [self.records[m]["fields"] for m in mains]
        starts = [d.year for d in (partial_date(f.get("startDate")) for f in fields) if d]
        ends = [d.year for d in (partial_date(f.get("completionDate")) for f in fields) if d]
        first = max(min(starts or [today.year]), today.year - 10)
        last = min(max(ends + [today.year]), today.year + 6)
        years = list(range(first, last + 1))
        flagged = {rid for rid, _ in watch_entries(self.records, today)}
        changed = self.changed_ids()
        key = [C("Key", "muted"), C("Ongoing", "bar-ongoing"), C("Expected", "bar-expected"), "",
               C("Not yet", "bar-planned"), C("Completed", "bar-completed"), C("Stopped", "bar-stopped"),
               C("▲ results posted · △ marked as having results by a non-US registry (can be just a link) · "
                 "✕ stopped early · ◆ ends within 60 days · ⚠ see Worth watching · dark year: this year · yellow: "
                 "changed in the latest check", "muted")]
        head = [C(h, "header") for h in ("Short name", "Trial", "Status", "Phase", "Results", "Ends", "Also in")]
        head += [C(str(y), "today" if y == today.year else "year") for y in years]
        rows = [key, head]
        for group, members in group_trials(self.records, mains).items():
            rows.append([C(group, "bold")])
            for rid in sorted(members, key=lambda r: self.records[r]["fields"].get("startDate") or "9999"):
                rec = self.records[rid]
                f = rec["fields"]
                copy_ids = copies.get(rid, [])
                also = ", ".join(registry_short(self.records[c]) + (" ⚠" if copy_flag(rec, self.records[c]) else "")
                                 for c in copy_ids)
                source = source_of(TRIALCORE, rec["identity"], f)
                url = rec["identity"].get("url")
                hl = "highlight" if rid in changed else None
                cells = [C(short_name(TRIALCORE, rec) + (" ⚠" if rid in flagged else ""), hl),
                         C(source, "highlight-link" if hl else "link", link=url) if url else C(source, hl),
                         C(pretty(f.get("overallStatus")), hl), C(PHASE_SHORT.get(f.get("phase"), ""), hl),
                         C(results_state(self.records, rid, copy_ids, today), hl),
                         C(human_month(f.get("completionDate")), hl), C(also, hl)]
                cells += self._bar(f, years, today, rec["identity"].get("sourceRegistry") == "clinicaltrials_gov")
                rows.append(cells)
        return rows, years

    @staticmethod
    def _bar(f: dict, years: list, today: dt.date, us: bool = True) -> list:
        status = f.get("overallStatus")
        start, end = partial_date(f.get("startDate")), partial_date(f.get("completionDate"))
        if start is None:
            return [""] * len(years)
        kind = ("ongoing" if status in ONGOING else "planned" if status in PLANNED else
                "completed" if status == "COMPLETED" else "stopped" if status in STOPPED else "other")
        last_year = end.year if end else (today.year if kind in ("ongoing", "planned", "other") else start.year)
        soon = end is not None and 0 <= (end - today).days <= 60 and kind in ("ongoing", "planned")
        cells = []
        for y in years:
            if not start.year <= y <= last_year:
                cells.append("")
                continue
            style = "bar-expected" if kind == "ongoing" and y > today.year else f"bar-{kind}"
            mark = ""
            if y == last_year:
                mark = (("▲" if us else "△") if f.get("hasResults") else "✕" if kind == "stopped" else
                        "◆" if soon else "…" if end is None and kind == "ongoing" else "")
            if y == years[0] and start.year < y:
                mark = "◀" + mark
            cells.append(C(mark, style))
        return cells

    def overview_sheet(self) -> dict:
        s = self.state
        rows: list = [[C(s["title"], "title")], [C(self.freshness_line(), "muted")]]
        trials_present = any(c is TRIALCORE for c in self.cores_present())
        rows += [[C(line, "bold")] for line in self.kpi_lines()]
        if trials_present:
            stands = where_it_stands(self.records, TODAY)
            if stands:
                rows.append([])
                rows.append([C("Where it stands", "section")])
                rows.append([C("Indication", "header")] + [C(h, "num-header") for h in (
                    "Trials", "Ongoing", "Not yet", "Completed", "Stopped", "Next end", "With results")])
                rows += [[C(r[0], "bold")] + [C(v if v != 0 else "–", "num") for v in r[1:]] for r in stands]
        rows.append([])
        head, lines = self.changed_lines()
        rows.append([C(head, "section")])
        for rid, text, style in lines:
            rows.append(self._record_row(rid, "●", text, style) if rid else [C(text, style)])
        rows.append([])
        rows.append([C("Worth watching", "section")])
        watch = watch_entries(self.records, TODAY)
        if watch:
            for rid, line in watch[:10]:
                rows.append(self._record_row(rid, "⚠", line))
            if len(watch) > 10:
                rows.append([C(f"and {len(watch) - 10} more", "muted")])
            rows.append([C("Read from the records' own dates and registries; prompts to look, not news from Amass.",
                           "muted")])
        else:
            rows.append([C("Nothing stands out in the dates or the registries.", "muted")])
        timeline, years = self.timeline_rows()
        if timeline:
            rows += [[], [C("Timeline", "section")]] + timeline
        for core in self.cores_present():
            if core is TRIALCORE:
                continue
            rids = self.records_of(core)
            rows += [[], [C(f"{core.sheet} ({len(rids)})", "section")]]
            rows.append([C(h, "header") for h in ("Short name", "Id", "Name", "Status", "Last changed")]
                        + [C("Latest change", "header")])
            changed = self.changed_ids()
            for rid in rids:
                rec = self.records[rid]
                f = rec["fields"]
                status = {
                    "biomedcore": "Retracted" if f.get("isRetracted") else "",
                    "regulatorycore": pretty(f.get("authorizationStatus")),
                    "drugcore": pretty(f.get("maxClinicalStage")),
                    "genecore": pretty(f.get("geneType")),
                }[core.key]
                url = rec["identity"].get("url")
                source = source_of(core, rec["identity"], f) or rid
                hl = "highlight" if rid in changed else None
                rows.append([C(rec.get("label") or "", hl),
                             C(source, "highlight-link" if hl else "link", link=url) if url else C(source, hl),
                             C(show(title_of(core, f) or "", 60), hl), C(status or "", hl),
                             C(rec.get("lastChanged") or "", hl), C(rec.get("latestChange") or "", hl)])
        rows += [[], [C("What the watch cannot see, and how this file works: see the Settings sheet.", "muted")]]
        widths = [30, 16, 22, 8, 14, 10, 12] + [6] * len(years)
        if not years:
            widths = [30, 16, 22, 10, 12, 12, 12, 12]
        return {"name": "Overview", "rows": rows, "widths": widths, "header": False, "gridlines": False}

    def core_sheet(self, core: Core, latest: str | None) -> dict:
        columns = {
            "trialcore": (
                ["Source id", "Registry", "Copy of", "Title", "Indication", "Status", "Phase", "Results posted",
                 "Enrollment", "Start", "Completion", "Sponsor", "Linked papers"],
                [24, 18, 16, 60, 30, 22, 12, 10, 10, 11, 11, 32, 8],
            ),
            "biomedcore": (
                ["PMID", "DOI", "Title", "Journal", "Published", "Retracted", "Citations", "Linked trials"],
                [11, 26, 60, 28, 11, 9, 9, 8],
            ),
            "regulatorycore": (
                ["Agency", "Number", "Name", "Substance", "Status", "Orphan", "Label or SmPC date", "SmPC revision",
                 "Holder"],
                [8, 18, 28, 28, 14, 8, 12, 9, 32],
            ),
            "drugcore": (
                ["ChEMBL", "Name", "Modality", "Highest stage", "Trade names", "Linked trials",
                 "Linked authorizations"],
                [16, 28, 18, 16, 32, 8, 8],
            ),
            "genecore": (
                ["Symbol", "Ensembl", "Name", "Type", "Target class", "Linked drugs"],
                [10, 18, 40, 16, 50, 8],
            ),
        }[core.key]
        status_col = {"trialcore": "Status", "biomedcore": "Retracted", "regulatorycore": "Status",
                      "drugcore": "Highest stage", "genecore": "Type"}[core.key]
        head, widths = columns
        at = head.index(status_col) + 1
        head = ["Short name"] + head[:at] + ["Last changed", "Latest change"] + head[at:] + \
            ["Last checked", "Notes", "Link", "Amass ID"]
        widths = [26] + widths[:at] + [12, 50] + widths[at:] + [12, 40, 40, 36]
        title_at = head.index({"trialcore": "Title", "biomedcore": "Title"}.get(core.key, "Name"))
        rows = [head]
        highlight = set()
        order = self.records_of(core)
        if core is TRIALCORE:  # by indication and start date, each registry copy right under its trial
            mains, copies = trial_groups(self.records)
            order = []
            for members in group_trials(self.records, mains).values():
                for rid in sorted(members, key=lambda r: self.records[r]["fields"].get("startDate") or "9999"):
                    order += [rid] + copies.get(rid, [])
        for rid in order:
            rec = self.records[rid]
            f, ident = rec["fields"], rec["identity"]
            count = (lambda name: len(f[name]) if name in f else None)
            if core is TRIALCORE:
                main = self.records.get(rec.get("copyOf") or "")
                source = ident.get("nctId") or ident.get("registryId")
                cells = [f"↳ {source}" if main else source, pretty(ident.get("sourceRegistry")),
                         source_of(TRIALCORE, main["identity"], main["fields"]) if main else None,
                         f.get("briefTitle"), "; ".join(f.get("conditions") or []), pretty(f.get("overallStatus")),
                         pretty(f.get("phase")), f.get("hasResults"), f.get("enrollment"), f.get("startDate"),
                         f.get("completionDate"), f.get("sponsorName"), count("referencesBiomedCore")]
            elif core is BIOMEDCORE:
                cells = [ident.get("pmid"), ident.get("doi"), f.get("title"), f.get("journal"),
                         f.get("publicationDate"), f.get("isRetracted"), f.get("citationCount"),
                         count("referencesTrialCore")]
            elif core is REGULATORYCORE:
                cells = [ident.get("agency"), ident.get("applicationNumber") or ident.get("productNumber"),
                         f.get("name"), f.get("activeSubstance"), pretty(f.get("authorizationStatus")),
                         f.get("isOrphan"),
                         f.get("labelDate") or f.get("smpcDate") or f.get("europeanCommissionDecisionDate"),
                         f.get("revisionNumber"), f.get("marketingAuthorisationHolder")]
            elif core is DRUGCORE:
                cells = [ident.get("chemblId"), f.get("name"), pretty(f.get("drugType")),
                         pretty(f.get("maxClinicalStage")), ", ".join(f.get("tradeNames") or []),
                         count("referencesTrialCore"), count("referencesRegulatoryCore")]
            else:
                cells = [f.get("symbol"), ident.get("ensemblGeneId"), f.get("name"), pretty(f.get("geneType")),
                         f.get("targetClass"), count("referencesDrugCore")]
            cells = [rec.get("label")] + cells[:at] + [rec.get("lastChanged"), rec.get("latestChange")] + cells[at:]
            if rec.get("notFound"):
                cells[title_at] = f"[not found in Amass] {cells[title_at] or ''}".strip()
            if not f:
                cells[title_at] = cells[title_at] or "(not fetched yet)"
            cells += [rec.get("lastChecked"), rec.get("note"), ident.get("url"), rid]
            if latest and rec.get("lastChanged") == latest:
                highlight.add(len(rows))
            rows.append(cells)
        for pending in self.pending_of(core):
            cells = [None] * len(head)
            cells[1] = pending["value"]
            cells[title_at] = "(not fetched yet)" + (" [Amass does not know this id]" if pending.get("notFound") else "")
            rows.append(cells)
        return {"name": core.sheet, "rows": rows, "widths": widths, "highlight": highlight,
                "freeze_first_column": True}

    def changes_sheet(self) -> dict:
        rows = [["Checked on", "Core", "Record", "Title", "Kind", "Change", "Amass ID"]]
        for c in reversed(self.state["changes"]):
            rec = self.records.get(c["id"])
            rows.append([c["date"], CORES[c["core"]].label, (rec or {}).get("label") or c.get("source"),
                         c.get("title"), c["cls"], c["text"], c["id"]])
        return {"name": "Changes", "rows": rows, "widths": [11, 14, 24, 50, 26, 70, 36]}

    def checks_sheet(self) -> dict:
        rows = [["Date", "Kind", "Checked", "Not due", "Records checked", "Changes", "Credits", "Note"]]
        for c in reversed(self.state["checks"]):
            rows.append([c["date"], c["kind"], c.get("checked"), c.get("notDue"), c.get("records"),
                         c.get("changes"), c.get("credits"), c.get("note")])
        return {"name": "Checks", "rows": rows, "widths": [11, 10, 40, 40, 10, 9, 8, 50]}

    def settings_sheet(self) -> dict:
        s = self.state
        rows = [["Setting", "Value"]] + [[k, v] for k, v in ABOUT_LINES] + [
            ["Board", s["title"]],
            ["Name", s["name"]],
            ["Created", s["created"]],
            ["Full check every", f"{s['settings']['fullEvery']} days"],
            ["Credit limit per check", f"{s['settings']['maxCredits']} MCP credits before Claude asks"],
        ]
        for core in self.cores_present():
            info = s["cores"].get(core.key, {})
            due, full_due = self.next_due(core)
            text = (f"Amass refreshes it every {self.interval(core)} day(s). Last checked "
                    f"{info.get('lastChecked') or 'never'}; next check due {due}.")
            if full_due:
                text += f" Next full check due {full_due}."
            rows.append([core.label, text])
        for search in s["searches"]:
            covers = sum(1 for rec in self.records.values() if search["id"] in rec.get("seenBy", []))
            rows.append([f"Search {search['id']}", f"{CORES[search['core']].label}: {search_label(search)} "
                                                   f"(covers {plural(covers, 'record')})"])
        rows.append(["Credits so far", f"{s['credits']} MCP credits (nominal: 2 per search, 1 per fetch; "
                     "your plan balance shows the exact charge)"])
        return {"name": "Settings", "rows": rows, "widths": [24, 110]}


def search_label(search: dict) -> str:
    args = search_args(search)
    return ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())


def search_args(search: dict) -> dict:
    args = {"query": search["query"]}
    args.update(search.get("filters") or {})
    if search.get("limit"):
        args["limit"] = search["limit"]
    return args


def source_of(core: Core, ident: dict, fields: dict) -> str | None:
    if core is TRIALCORE:
        return ident.get("nctId") or ident.get("registryId")
    if core is BIOMEDCORE:
        return f"PMID {ident['pmid']}" if ident.get("pmid") else ident.get("doi")
    if core is REGULATORYCORE:
        number = ident.get("applicationNumber") or ident.get("productNumber")
        return f"{ident.get('agency', '')} {number or ''}".strip() or None
    if core is DRUGCORE:
        return ident.get("chemblId")
    return fields.get("symbol") or ident.get("ensemblGeneId")


def title_of(core: Core, fields: dict) -> str | None:
    if core is TRIALCORE:
        return fields.get("briefTitle")
    if core is BIOMEDCORE:
        return fields.get("title")
    if core is REGULATORYCORE:
        name, substance = fields.get("name"), fields.get("activeSubstance")
        return f"{name} ({substance})" if name and substance else name or substance
    if core is DRUGCORE:
        return fields.get("name")
    return fields.get("name")


def parse_filters(items: list | None) -> dict:
    out: dict = {}
    for item in items or []:
        if "=" not in item:
            raise BoardError(f"--filter takes key=value, got {item!r}")
        key, value = item.split("=", 1)
        key, value = key.strip(), value.strip()
        if value.lower() in ("true", "false"):
            out[key] = value.lower() == "true"
        elif re.fullmatch(r"-?\d+", value):
            out[key] = int(value)
        elif "," in value:
            out[key] = [v.strip() for v in value.split(",") if v.strip()]
        else:
            out[key] = value
    return out


def read_items(core: Core, args: argparse.Namespace, allow_empty: bool) -> tuple:
    """Records from --raw FILE or stdin JSON; returns (items, strict)."""
    if getattr(args, "raw", None):
        try:
            data = json.loads(Path(args.raw).read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            raise BoardError(f"cannot read {args.raw} as JSON: {err}") from None
        return extract_records(core, data), False
    text = _stdin_text()
    if not text.strip():
        if allow_empty:
            return [], True
        raise BoardError("no records on stdin: pipe a JSON list of records (or one record), or pass --raw FILE")
    try:
        data = json.loads(text)
    except ValueError as err:
        raise BoardError(f"stdin is not valid JSON: {err}") from None
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        data = data["results"]
    elif isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    return (data if isinstance(data, list) else [data]), True


def _stdin_text(wait: float = 5.0) -> str:
    """Whatever was piped in; an open stdin with nothing on it reads as empty instead of hanging."""
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    try:
        import select

        ready, _, _ = select.select([sys.stdin], [], [], wait)
        if not ready:
            return ""
    except (ValueError, OSError, TypeError):
        pass  # not selectable (Windows pipes, test doubles): read it
    return sys.stdin.read()


def prepare_all(core: Core, items: list, strict: bool) -> list:
    errors: list = []
    out = []
    for i, raw in enumerate(items):
        prepared = prepare(core, raw, strict, errors, f"item {i + 1}")
        if prepared:
            out.append(prepared)
    if errors:
        raise BoardError("nothing was stored; fix these and send the batch again:\n  " + "\n  ".join(errors))
    return out


def pending_key(p: dict) -> str:
    return f"{p['type']}:{p['value']}"


def parse_id(core: Core, text: str) -> tuple:
    """'AMTC_…' or 'nctId:NCT…' -> ('amassId', value) or (type, value)."""
    text = text.strip()
    if ":" in text and not text.startswith(core.prefix):
        kind, value = text.split(":", 1)
        kind, value = kind.strip(), value.strip()
        if kind not in core.fetch_types:
            raise BoardError(
                f"{core.label} records can be fetched over MCP by Amass ID or {', '.join(core.fetch_types)}; "
                f"got {kind!r}. Find the record with {core.search_tool} and add it from the result instead."
            )
        if not value:
            raise BoardError(f"empty {kind}")
        return kind, value
    if not core.valid(text):
        raise BoardError(f"{text!r} is not a {core.label} Amass ID ({core.prefix}...) or a type:value id")
    return "amassId", text


def match_pending(board: Board, core: Core, ident: dict, resolved: dict, explicit: str | None) -> str | None:
    open_keys = [pending_key(p) for p in board.pending_of(core) if pending_key(p) not in resolved]
    if explicit:
        if explicit not in open_keys:
            raise BoardError(f"--for {explicit!r} is not an unresolved id on this board")
        return explicit
    values = {v.upper() for v in ident.values()}
    for key in open_keys:
        if key.split(":", 1)[1].upper() in values:
            return key
    return None


# --------------------------------------------------------------------------
# Comparing a check with the board
# --------------------------------------------------------------------------


def needs_baseline(rec: dict) -> bool:
    return not rec["fields"] or bool(rec.get("needsFetch"))


def compute(board: Board, check: dict) -> dict:
    """What finish would store; nothing is written here."""
    today = check["date"]
    records = json.loads(json.dumps(board.records))
    changes, baseline, not_found, still_missing, metadata = [], [], [], [], []
    obs = check["obs"]
    resolved = check.get("resolved", {})
    pending_by_key = {pending_key(p): p for p in board.state["pendingIds"]}
    for key, aid in resolved.items():
        if aid not in records:
            p = pending_by_key.get(key, {})
            records[aid] = {"core": p.get("core") or PREFIXES[aid[:5]].key, "added": p.get("added", today),
                            "fields": {}, "identity": {}, "seenBy": []}
    for aid, o in obs.items():
        rec = records[aid]
        core = CORES[rec["core"]]
        specs = core.by_name
        rec_changes = []
        if not rec["fields"]:
            baseline.append(aid)
        else:
            for name, new in o["fields"].items():
                if name not in rec["fields"]:
                    continue
                old = rec["fields"][name]
                if not same(old, new):
                    spec = specs[name]
                    rec_changes.append((spec, old, new))
        if rec.get("notFound"):
            rec_changes.insert(0, (None, "not found", "found again"))
        rec["fields"].update(o["fields"])
        rec["identity"].update(o["identity"])
        rec["lastChecked"] = today
        rec["notFound"] = False
        for via in o["vias"]:
            if via == "fetch":
                rec["needsFetch"] = False
            elif via not in rec.setdefault("seenBy", []):
                rec["seenBy"].append(via)
        if rec_changes:
            source = source_of(core, rec["identity"], rec["fields"])
            title = title_of(core, rec["fields"])
            texts = []
            for spec, old, new in rec_changes:
                if spec is None:
                    entry = {"cls": NOT_FOUND, "text": "found again in Amass", "raw": "found again in Amass"}
                else:
                    entry = {"cls": spec.cls, "text": describe(spec, old, new), "field": spec.name,
                             "raw": describe(spec, old, new, human=False)}
                entry.update({"date": today, "core": core.key, "id": aid, "source": source, "title": title})
                changes.append(entry)
                texts.append(entry["text"])
            substantive = [e for e in changes if e["id"] == aid and e["cls"] != METADATA]
            rec["lastChanged"] = today
            rec["latestChange"] = "; ".join(e["text"] for e in (substantive or [c for c in changes if c["id"] == aid]))
            if not substantive:
                metadata.append(aid)
    for item in check.get("notFound", []):
        if item in records:
            rec = records[item]
            core = CORES[rec["core"]]
            if not rec.get("notFound"):
                changes.append({"date": today, "core": core.key, "id": item, "cls": NOT_FOUND,
                                "text": "not found when fetched", "source": source_of(core, rec["identity"], rec["fields"]),
                                "title": title_of(core, rec["fields"])})
                rec["lastChanged"] = today
                rec["latestChange"] = "not found in Amass when fetched"
            rec["notFound"] = True
            rec["lastChecked"] = today
        not_found.append(item)
    for core_key, plan in check["plan"].items():
        core = CORES[core_key]
        for target in plan["expect"]:
            aid = resolved.get(target, target)
            if aid not in obs and target not in check.get("notFound", []) and aid not in check.get("notFound", []):
                still_missing.append(target)
    return {"records": records, "changes": changes, "baseline": baseline, "notFound": not_found,
            "missing": still_missing, "metadata": metadata}


def digest(board: Board, check: dict, result: dict, final: bool) -> str:
    title = board.state["title"]
    today = check["date"]
    records = result["records"]
    changes = result["changes"]
    by_cls: dict = {}
    for c in changes:
        by_cls.setdefault(c["cls"], []).append(c)
    substantive = [c for c in changes if c["cls"] != METADATA]
    checked = sum(1 for aid in check["obs"]) + len(result["notFound"])
    lines = [f"# {title}: check of {human_date(today)}", ""]
    key = "text" if final else "raw"
    if substantive:
        counts = []
        for cls in CLASS_ORDER:
            n = len({c["id"] for c in by_cls.get(cls, [])})
            if n and cls != METADATA:
                counts.append(f"{SHORT[cls]} ({n})")
        changed = len({c["id"] for c in substantive})
        one = f"changes on {changed} of {plural(checked, 'record')} checked: " + ", ".join(counts)
    else:
        one = f"no changes on the {plural(checked, 'record')} checked"
    meta_ids = {c["id"] for c in by_cls.get(METADATA, [])} - {c["id"] for c in substantive}
    if meta_ids:
        one += f"; {len(meta_ids)} with metadata-only changes"
    if result["baseline"]:
        one += f"; baseline captured for {len(result['baseline'])}"
    lines += [f"**One line:** {one[0].upper() + one[1:]}. {plural(check['credits'], 'MCP credit')}.", ""]
    if not final:
        lines.insert(0, "(Review: nothing is stored until `finish`.)\n")
    for cls in CLASS_ORDER:
        entries = by_cls.get(cls, [])
        if cls == METADATA:
            entries = [c for c in entries if c["id"] in meta_ids]
        if not entries:
            continue
        lines.append(f"## {cls}")
        grouped: dict = {}
        for c in entries:
            grouped.setdefault(c["id"], []).append(c)
        items = list(grouped.items())
        for aid, cs in items[: (LIST_CAP if cls == METADATA else len(items))]:
            rec = records.get(aid)
            head = f"**{name_of(CORES[rec['core']], rec, 60, records) if rec else (cs[0].get('source') or aid)}**"
            head += "" if final else f" (`{aid}`)"
            lines.append(f"- {head}: " + "; ".join(c.get(key) or c["text"] for c in cs))
        if cls == METADATA and len(items) > LIST_CAP:
            lines.append(f"- and {len(items) - LIST_CAP} more")
        lines.append("")
    if result["baseline"]:
        lines.append("## Baseline captured")
        for aid in result["baseline"][:LIST_CAP]:
            rec = records[aid]
            core = CORES[rec["core"]]
            lines.append(f"- **{name_of(core, rec, 60, records) or aid}**: first look stored")
        if len(result["baseline"]) > LIST_CAP:
            lines.append(f"- and {len(result['baseline']) - LIST_CAP} more")
        lines.append("")
    pending_not_found = [x for x in result["notFound"] if x not in records]
    if pending_not_found:
        lines.append("## Ids that Amass does not know")
        lines += [f"- {x}" for x in pending_not_found]
        lines.append("- Retried at each full check, in case Amass adds them later; remove any that are wrong.")
        lines.append("")
    if result["missing"]:
        lines.append("## Not checked this time")
        lines += [f"- {x}" for x in result["missing"][:LIST_CAP]]
        if len(result["missing"]) > LIST_CAP:
            lines.append(f"- and {len(result['missing']) - LIST_CAP} more")
        lines.append("")
    if not substantive and not meta_ids and not result["baseline"]:
        lines += ["Nothing changed.", ""]
    if final:
        watch = watch_items(records, partial_date(today) or TODAY)
        if watch:
            lines.append("## Worth watching")
            lines += [f"- {w}" for w in watch[:8]]
            if len(watch) > 8:
                lines.append(f"- and {len(watch) - 8} more on the board's Overview sheet")
            lines.append("- (read from the records' own dates and registries: prompts to look, not news from Amass)")
            lines.append("")
        stands = where_it_stands(records, partial_date(today) or TODAY)
        if len(stands) > 0:
            lines += ["## Where it stands", "",
                      "| Indication | Trials | Ongoing | Not yet | Completed | Stopped | Next end | With results |",
                      "|---|---|---|---|---|---|---|---|"]
            lines += ["| " + " | ".join(str(v) if v != 0 else "–" for v in row) + " |" for row in stands[:10]]
            lines.append("")
    for core_key, plan in check["plan"].items():
        core = CORES[core_key]
        what = {"quick": f"quick check, {len(plan['searches'])} search{'es' if len(plan['searches']) != 1 else ''} "
                         f"and the records they miss",
                "full": "full check, every record fetched",
                "baseline": "first look at new records only"}[plan["mode"]]
        lines.append(f"Checked {core.label}: {plural(len(plan['expect']), 'record')} ({what}).")
    for note in check.get("notDue", []):
        lines.append(f"Not due: {note}")
    if final:
        for core in board.cores_present():
            due, full_due = board.next_due(core)
            due_text = "now" if due == "now" else human_date(due)
            full_text = ("now" if full_due == "now" else human_date(full_due)) if full_due else None
            lines.append(f"Next {core.label} check due {due_text}" + (f" (full check {full_text})" if full_text else "")
                         + ".")
    lines.append(f"Credits: {plural(check['credits'], 'MCP credit')} (nominal: 2 per search, 1 per fetch; your plan "
                 "balance shows the exact charge).")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def today_of(args: argparse.Namespace) -> dt.date:
    return dt.date.fromisoformat(args.today) if getattr(args, "today", None) else dt.date.today()


def helper() -> str:
    return "python3 " + shlex.quote(str(Path(__file__).resolve()))


def cmd_new(args: argparse.Namespace) -> int:
    path = Path(args.board)
    if path.suffix.lower() != ".xlsx":
        raise BoardError("the board file must end in .xlsx")
    if path.exists():
        raise BoardError(f"{path} already exists; pick another name or use the existing board")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,59}", args.name):
        raise BoardError("--name is short, lowercase, letters, digits and hyphens (ulotaront-phase3)")
    board = Board.create(path, args.name, args.title, today_of(args))
    board.save()
    print(f"Created {path} ({args.title or args.name}). Add records with `add`.")
    return 0


def _log_setup(board: Board, today: str, credits: int, searches: int = 0, fetches: int = 0) -> None:
    checks = board.state["checks"]
    if checks and checks[-1]["date"] == today and checks[-1]["kind"] == "setup":
        entry = checks[-1]
    else:
        entry = {"date": today, "kind": "setup", "credits": 0, "records": 0, "changes": 0, "searches": 0,
                 "fetches": 0}
        checks.append(entry)
    entry["credits"] += credits
    entry["searches"] = entry.get("searches", 0) + searches
    entry["fetches"] = entry.get("fetches", 0) + fetches
    entry["records"] = len(board.records) + len(board.state["pendingIds"])
    entry["checked"] = ", ".join(f"{c.label} ({len(board.records_of(c)) + len(board.pending_of(c))})"
                                 for c in board.cores_present())
    entry["note"] = (f"list built: {plural(entry['searches'], 'search')}, {plural(entry['fetches'], 'fetch')}"
                     .replace("searchs", "searches").replace("fetchs", "fetches"))
    board.state["credits"] += credits


def keep_only(prepared: list, wanted: list) -> list:
    """The records among `prepared` that `wanted` names, by Amass ID or any source id (a `type:` prefix is
    allowed). An id that matches nothing is an error, so a typo never leaves a record silently off the board."""
    keys = {w.split(":", 1)[1].strip().upper() if ":" in w and not w.upper().startswith("AM") else w.strip().upper()
            for w in wanted if w.strip()}
    kept, seen = [], set()
    for item in prepared:
        aid, _fields, ident, _dropped = item
        names = {aid.upper()} | {str(v).upper() for v in ident.values() if v}
        hit = names & keys
        if hit:
            kept.append(item)
            seen |= hit
    missing = sorted(keys - seen)
    if missing:
        raise BoardError(f"--only names ids that are not in these records: {', '.join(missing)}")
    return kept


def cmd_add(args: argparse.Namespace) -> int:
    board = Board.load(Path(args.board))
    if board.load_check():
        raise BoardError("a check is open on this board; finish it (or `start --discard`) before adding records")
    core: Core = args.core
    today = iso(today_of(args))
    if args.watchlist:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import monitor  # noqa: E402  the API-mode engine reads the same watchlist files

        try:
            wl = monitor.load_watchlist(Path(args.watchlist))
        except monitor.MonitorError as err:
            raise BoardError(str(err)) from None
        if wl["core"].name != core.key:
            raise BoardError(f"{args.watchlist} watches {wl['core'].name}, not {core.key}")
        ids = [e if isinstance(e, str) else f"{e[0]}:{e[1]}" for e in wl["entries"]]
        return _add_ids(board, core, ids, today, f"imported {args.watchlist}")
    if args.id:
        return _add_ids(board, core, args.id, today, "added by id")
    if not args.search and not args.fetch:
        raise BoardError("say where the records came from: --search QUERY (with its --filter and --limit), "
                         "--fetch, --id, or --watchlist FILE")
    items, strict = read_items(core, args, allow_empty=False)
    prepared = prepare_all(core, items, strict)
    fetched = len(prepared)  # every record a fetch returned was paid for, whatever --only keeps
    if args.only:
        prepared = keep_only(prepared, args.only)
    sid = None
    credits = 0
    if args.search:
        filters = parse_filters(args.filter)
        key = (core.key, args.search.strip().lower(), canon(filters), args.limit)
        for s in board.state["searches"]:
            if (s["core"], s["query"].strip().lower(), canon(s.get("filters") or {}), s.get("limit")) == key:
                sid = s["id"]
        if sid is None:
            sid = f"S{len(board.state['searches']) + 1}"
            board.state["searches"].append({"id": sid, "core": core.key, "query": args.search.strip(),
                                            "filters": filters, "limit": args.limit, "added": today})
        if not args.correction:
            credits = SEARCH_CREDITS
    elif not args.correction:
        credits = FETCH_CREDITS * fetched
    added, merged, dropped_notes = 0, 0, []
    for aid, fields, ident, dropped in prepared:
        if dropped:
            dropped_notes.append(f"{aid}: {', '.join(dropped)}")
        rec = board.records.get(aid)
        if rec is None:
            rec = {"core": core.key, "added": today, "fields": {}, "identity": {}, "seenBy": [],
                   "lastChecked": today, "lastChanged": None, "latestChange": None, "notFound": False,
                   "needsFetch": bool(args.search) and not core.quick}
            board.records[aid] = rec
            added += 1
        else:
            merged += 1
            if args.fetch:
                rec["needsFetch"] = False
        for name, value in fields.items():
            rec["fields"].setdefault(name, value)
        rec["identity"].update(ident)
        if sid and sid not in rec["seenBy"]:
            rec["seenBy"].append(sid)
        board.state["pendingIds"] = [
            p for p in board.state["pendingIds"]
            if not (p["core"] == core.key and p["value"].upper() in {v.upper() for v in ident.values()})
        ]
    info = board.core_state(core)
    info.setdefault("lastChecked", today)
    if info.get("lastChecked") is None:
        info["lastChecked"] = today
    if core.quick and not info.get("lastFull"):
        info["lastFull"] = today
    _log_setup(board, today, credits, searches=1 if sid and credits else 0,
               fetches=fetched if args.fetch and credits else 0)
    board.save()
    total = len(board.records_of(core)) + len(board.pending_of(core))
    print(f"{added} added, {merged} already on the board. {core.label}: {total} records. Board saved to {board.path}.")
    if sid:
        print(f"Stored as search {sid} for quick checks: {search_label(board.search(sid))}")
    if dropped_notes:
        print("Text over 300 characters is not tracked (too long to copy reliably): " + "; ".join(dropped_notes))
    if not core.quick and args.search and added:
        print(f"{core.label} search results lack fields a check compares; `start` will fetch these records once.")
    return 0


def _add_ids(board: Board, core: Core, ids: list, today: str, note: str) -> int:
    added, known = 0, 0
    for text in ids:
        kind, value = parse_id(core, text)
        if kind == "amassId":
            if value in board.records:
                known += 1
                continue
            board.records[value] = {"core": core.key, "added": today, "fields": {}, "identity": {}, "seenBy": [],
                                    "lastChecked": None, "lastChanged": None, "latestChange": None,
                                    "notFound": False, "needsFetch": True}
            added += 1
        else:
            exists = any(p["core"] == core.key and p["type"] == kind and p["value"].upper() == value.upper()
                         for p in board.state["pendingIds"])
            exists = exists or any(
                rec["core"] == core.key and value.upper() in {v.upper() for v in rec["identity"].values()}
                for rec in board.records.values()
            )
            if exists:
                known += 1
                continue
            board.state["pendingIds"].append({"core": core.key, "type": kind, "value": value, "added": today})
            added += 1
    _log_setup(board, today, 0)
    board.save()
    print(f"{added} added by id, {known} already on the board. They are fetched in the next check "
          f"(1 credit each); run `start`.")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    board = Board.load(Path(args.board))
    if board.load_check():
        raise BoardError("a check is open on this board; finish it (or `start --discard`) first")
    removed = []
    for item in args.id or []:
        if item in board.records:
            del board.records[item]
            removed.append(item)
            continue
        before = len(board.state["pendingIds"])
        board.state["pendingIds"] = [p for p in board.state["pendingIds"]
                                     if pending_key(p) != item and p["value"] != item]
        if len(board.state["pendingIds"]) < before:
            removed.append(item)
        else:
            match = [rid for rid, rec in board.records.items()
                     if item.upper() in {v.upper() for v in rec["identity"].values()}]
            if len(match) == 1:
                del board.records[match[0]]
                removed.append(f"{item} ({match[0]})")
            else:
                raise BoardError(f"{item} is not on the board")
    for sid in args.search_id or []:
        if not board.search(sid):
            raise BoardError(f"no search {sid}")
        board.state["searches"] = [s for s in board.state["searches"] if s["id"] != sid]
        for rec in board.records.values():
            if sid in rec.get("seenBy", []):
                rec["seenBy"].remove(sid)
        removed.append(f"search {sid}")
    board.save()
    print(f"Removed {', '.join(removed) or 'nothing'}. Board saved to {board.path}.")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    board = Board.load(Path(args.board))
    settings = board.state["settings"]
    for item in args.interval or []:
        key, _, days = item.partition("=")
        core = core_arg(key)
        if not days.strip().isdigit() or int(days) < 1:
            raise BoardError(f"--interval takes core=days, got {item!r}")
        settings["intervals"][core.key] = int(days)
    if args.full_every is not None:
        settings["fullEvery"] = max(1, args.full_every)
    if args.max_credits is not None:
        settings["maxCredits"] = max(1, args.max_credits)
    if args.title:
        board.state["title"] = args.title
    board.save()
    print(f"Settings: intervals {settings['intervals']}, full check every {settings['fullEvery']} days, "
          f"{settings['maxCredits']} credits per check before asking.")
    return 0


def choose_searches(board: Board, core: Core, rids: list) -> tuple:
    candidates = [s for s in board.state["searches"] if s["core"] == core.key]
    cover = {s["id"]: {r for r in rids if s["id"] in board.records[r].get("seenBy", [])} for s in candidates}
    chosen, uncovered = [], set(rids)
    while True:
        best = max(candidates, key=lambda s: len(cover[s["id"]] & uncovered), default=None)
        if best is None or not cover[best["id"]] & uncovered:
            break
        chosen.append(best["id"])
        uncovered -= cover[best["id"]]
        candidates = [s for s in candidates if s["id"] != best["id"]]
    return chosen, [r for r in rids if r in uncovered]


def fetch_call(core: Core, target: str) -> str:
    kind, value = target.split(":", 1) if ":" in target and not target.startswith(core.prefix) else ("amassId", target)
    return f"{core.get_tool} {json.dumps({'type': kind, 'value': value})}"


def cmd_start(args: argparse.Namespace) -> int:
    board = Board.load(Path(args.board))
    existing = board.load_check()
    if existing and args.discard:
        board.check_path.unlink()
        print(f"Discarded the open check of {existing['date']}; nothing from it was stored.")
        return 0
    if existing:
        raise BoardError(f"a check of {existing['date']} is already open: continue it (`status`) or drop it "
                         "(`start --discard`)")
    if args.discard:
        print("No open check to discard.")
        return 0
    today = today_of(args)
    settings = board.state["settings"]
    full_every = int(settings["fullEvery"])
    only = {c.key for c in args.core or []}
    plan, not_due, out = {}, [], []
    for core in board.cores_present():
        if only and core.key not in only:
            continue
        rids = board.records_of(core)
        pend_all = [pending_key(p) for p in board.pending_of(core)]
        pend = [pending_key(p) for p in board.pending_of(core) if not p.get("notFound")]
        info = board.core_state(core)
        last = as_date(info.get("lastChecked"))
        interval = board.interval(core)
        due = args.force or last is None or (today - last).days >= interval
        fresh = [r for r in rids if needs_baseline(board.records[r])]
        if not due:
            if not fresh and not pend:
                next_day = iso(last + dt.timedelta(days=interval))
                not_due.append(f"{core.label}, checked {iso(last)}; Amass refreshes it every {interval} day"
                               f"{'s' if interval != 1 else ''}, so the next check is due {next_day}")
                continue
            plan[core.key] = {"mode": "baseline", "searches": [], "fetch": fresh + pend, "expect": fresh + pend}
            continue
        last_full = as_date(info.get("lastFull"))
        full = (args.full or not core.quick or last_full is None or (today - last_full).days >= full_every)
        searches, uncovered = ([], rids) if full else choose_searches(board, core, rids)
        fetch_quick = [r for r in rids if r in set(uncovered) | set(fresh)] + pend
        if full or not searches or SEARCH_CREDITS * len(searches) + len(fetch_quick) >= len(rids) + len(pend):
            plan[core.key] = {"mode": "full", "searches": [], "fetch": rids + pend_all, "expect": rids + pend_all}
        else:
            plan[core.key] = {"mode": "quick", "searches": searches, "fetch": fetch_quick, "expect": rids + pend}
    if not plan:
        print("Nothing is due; no credits needed.")
        for note in not_due:
            print(f"- {note}")
        print(f"This board has used {plural(board.state['credits'], 'MCP credit')} so far.")
        return 0
    estimate = sum(SEARCH_CREDITS * len(p["searches"]) + FETCH_CREDITS * len(p["fetch"]) for p in plan.values())
    check = {"board": str(board.path), "date": iso(today), "plan": plan, "notDue": not_due, "obs": {},
             "notFound": [], "searchesRun": [], "credits": 0, "resolved": {}, "estimate": estimate}
    board.save_check(check)
    b = shlex.quote(str(board.path))
    out.append(f"Check of {iso(today)} for {board.state['title']}: estimated {plural(estimate, 'MCP credit')} "
               "(2 per search, 1 per fetch).")
    for core_key, p in plan.items():
        core = CORES[core_key]
        last = board.core_state(core).get("lastChecked")
        why = {"quick": "quick check: the stored searches, then a fetch of whatever they miss",
               "full": "full check: every record fetched, cross-links included",
               "baseline": "not due, but new records need a first look"}[p["mode"]]
        out.append("")
        out.append(f"## {core.label}: {plural(len(p['expect']), 'record')}, {why} (last checked {last or 'never'})")
        step = 1
        for sid in p["searches"]:
            s = board.search(sid)
            expected = sum(1 for r in p["expect"] if sid in board.records.get(r, {}).get("seenBy", []))
            out.append(f"{step}. {core.search_tool} {json.dumps(search_args(s), ensure_ascii=False)}")
            out.append(f"   then: {helper()} ingest {b} --core {core.key} --search {sid}   "
                       f"(every watched record in the result; {expected} expected)")
            step += 1
        if p["fetch"]:
            out.append(f"{step}. Fetch {'this record' if len(p['fetch']) == 1 else 'each of these ' + str(len(p['fetch'])) + ', ingesting every few results'}:")
            for target in p["fetch"]:
                rec = board.records.get(target)
                hint = source_of(core, rec["identity"], rec["fields"]) if rec else None
                out.append(f"   {fetch_call(core, target)}" + (f"   # {hint}" if hint else ""))
            out.append(f"   then: {helper()} ingest {b} --core {core.key} --fetch   "
                       "(a record the tool cannot find: add --not-found ID)")
            step += 1
        if p["mode"] == "quick":
            out.append(f"{step}. Run `status`: it lists any watched record a search did not return this time; "
                       "fetch those too.")
        if p["searches"]:
            out.append("Watched ids, to pick them out of search results:")
            for rid in board.records_of(core):
                rec = board.records[rid]
                out.append(f"   {rid}  {source_of(core, rec['identity'], rec['fields']) or ''}")
    if not_due:
        out.append("")
        out += [f"Not due: {note}" for note in not_due]
    out.append("")
    out.append(f"When every record is in: {helper()} review {b}, verify, then finish.")
    print("\n".join(out))
    if estimate > int(settings["maxCredits"]):
        print(f"\nOVER LIMIT: {estimate} credits is above this board's {settings['maxCredits']} per check. "
              "Ask the user before making any call. If they decline: `start --discard`.")
        return 2
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    board = Board.load(Path(args.board))
    check = board.load_check()
    if not check:
        raise BoardError("no open check: run `start` first (to add records to the board, use `add`)")
    core: Core = args.core
    plan = check["plan"].get(core.key)
    if not plan:
        raise BoardError(f"{core.label} is not part of this check (planned: {', '.join(check['plan'])})")
    if bool(args.search) == bool(args.fetch):
        raise BoardError("say where the records came from: --search SID or --fetch")
    if args.search:
        s = board.search(args.search)
        if not s or s["core"] != core.key:
            raise BoardError(f"{args.search} is not a stored {core.label} search")
    items, strict = read_items(core, args, allow_empty=bool(args.search) or bool(args.not_found))
    prepared = prepare_all(core, items, strict)
    resolved = check.setdefault("resolved", {})
    via = args.search or "fetch"
    if not args.correction:
        clashes = []
        for aid, fields, _, _ in prepared:
            prev = check["obs"].get(aid)
            for name in sorted(set(fields) & set(prev["fields"]) if prev else []):
                if not same(prev["fields"][name], fields[name]):
                    clashes.append(f"{aid} {name}: earlier copy {show(prev['fields'][name])}, this copy "
                                   f"{show(fields[name])}")
        if clashes:
            raise BoardError("nothing was stored: these records were already ingested in this check with other "
                             "values. Look at the tool results again and resend the right values with "
                             "--correction:\n  " + "\n  ".join(clashes))
    accepted, ignored, dropped_notes = 0, [], []
    for aid, fields, ident, dropped in prepared:
        if aid not in board.records and aid not in resolved.values():
            key = match_pending(board, core, ident, resolved, args.for_id if not args.search else None)
            if key:
                resolved[key] = aid
            elif args.search:
                ignored.append(aid)
                continue
            else:
                raise BoardError(f"{aid} is not on this board and matches none of its unresolved ids. If you "
                                 "fetched it for an id given as type:value, ingest it alone with --for type:value.")
        if dropped:
            dropped_notes.append(f"{aid}: {', '.join(dropped)}")
        prev = check["obs"].get(aid)
        if prev:
            merged = dict(prev["fields"])
            merged.update(fields)
            fields = merged
            ident = dict(prev["identity"], **ident)
        vias = (prev or {}).get("vias", [])
        check["obs"][aid] = {"core": core.key, "fields": fields, "identity": ident,
                             "vias": vias + ([via] if via not in vias else [])}
        if aid in check["notFound"]:
            check["notFound"].remove(aid)
        accepted += 1
    for item in args.not_found or []:
        target = item if item in board.records else None
        if target is None:
            target = next((pending_key(p) for p in board.pending_of(core)
                           if item in (pending_key(p), p["value"])), None)
        if target is None:
            raise BoardError(f"--not-found {item}: not on this board")
        if target not in check["notFound"]:
            check["notFound"].append(target)
    if not args.correction:
        if args.search and args.search not in check["searchesRun"]:
            check["searchesRun"].append(args.search)
            check["credits"] += SEARCH_CREDITS
        if args.fetch:
            check["credits"] += FETCH_CREDITS * (accepted + len(args.not_found or []))
    board.save_check(check)
    parts = [f"{accepted} ingested"]
    if ignored:
        parts.append(f"{len(ignored)} ignored (not on the board)")
    if args.not_found:
        parts.append(f"{len(args.not_found)} marked not found")
    print(f"{core.label}: " + ", ".join(parts) + f". Credits so far: {check['credits']}.")
    if dropped_notes:
        print("Text over 300 characters is not tracked: " + "; ".join(dropped_notes))
    print(_remaining(board, check, core))
    return 0


def _remaining(board: Board, check: dict, core: Core) -> str:
    plan = check["plan"][core.key]
    resolved = check.get("resolved", {})
    todo_searches = [s for s in plan["searches"] if s not in check["searchesRun"]]
    observed = set(check["obs"]) | set(check["notFound"])
    missing = [t for t in plan["expect"] if resolved.get(t, t) not in observed and t not in observed]
    if todo_searches:
        pending_search = {r for r in missing for s in todo_searches if s in board.records.get(r, {}).get("seenBy", [])}
        missing = [m for m in missing if m not in pending_search]
        lines = [f"Still to run: {', '.join(todo_searches)}."]
        if missing:
            lines.append(f"Still to fetch ({len(missing)}): " + ", ".join(missing))
        return "\n".join(lines)
    if not missing:
        return f"{core.label} is complete. When every Core is, run `review`."
    lines = [f"{core.label}: {len(missing)} still to fetch:"]
    for target in missing:
        rec = board.records.get(target)
        hint = source_of(core, rec["identity"], rec["fields"]) if rec else None
        lines.append(f"   {fetch_call(core, target)}" + (f"   # {hint}" if hint else ""))
    return "\n".join(lines)


def cmd_status(args: argparse.Namespace) -> int:
    board = Board.load(Path(args.board))
    check = board.load_check()
    if check:
        print(f"Open check of {check['date']} ({check['credits']} credits so far, {check['estimate']} estimated).")
        for core_key in check["plan"]:
            print(_remaining(board, check, CORES[core_key]))
        return 0
    s = board.state
    print(f"{s['title']} ({board.path}), created {s['created']}, {s['credits']} MCP credits so far.")
    today = today_of(args)
    for core in board.cores_present():
        info = s["cores"].get(core.key, {})
        due, full_due = board.next_due(core)
        is_due = due == "now" or as_date(due) <= today
        line = (f"- {core.label}: {len(board.records_of(core)) + len(board.pending_of(core))} records, last checked "
                f"{info.get('lastChecked') or 'never'}, {'due now' if is_due else 'next due ' + due}")
        if full_due:
            line += f"; full check {'due now' if full_due == 'now' or as_date(full_due) <= today else 'due ' + full_due}"
        print(line)
    for search in s["searches"]:
        covers = sum(1 for rec in board.records.values() if search["id"] in rec.get("seenBy", []))
        print(f"- search {search['id']} ({CORES[search['core']].label}): {search_label(search)}; covers {covers}")
    if s["changes"]:
        print(f"Last change recorded {s['changes'][-1]['date']}; {len(s['changes'])} in all.")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    board = Board.load(Path(args.board))
    check = board.load_check()
    if not check:
        raise BoardError("no open check to review")
    result = compute(board, check)
    print(digest(board, check, result, final=False))
    substantive = [c for c in result["changes"] if c["cls"] != METADATA]
    if substantive:
        print("Before finishing, confirm each change above against the tool result in this conversation. A copy "
              "slip is fixed by ingesting that record again with the exact values and --correction.")
    if result["missing"]:
        print(f"{len(result['missing'])} planned records are not in yet; `status` lists the calls. Finishing "
              "without them needs --allow-missing.")
    return 0


def cmd_finish(args: argparse.Namespace) -> int:
    board = Board.load(Path(args.board))
    check = board.load_check()
    if not check:
        raise BoardError("no open check to finish")
    result = compute(board, check)
    if result["missing"] and not args.allow_missing:
        raise BoardError(f"{len(result['missing'])} planned records were not observed: "
                         f"{', '.join(result['missing'][:LIST_CAP])}. Fetch them (`status` lists the calls) or "
                         "pass --allow-missing.")
    today = check["date"]
    resolved = check.get("resolved", {})
    board.state["records"] = result["records"]
    board.state["pendingIds"] = [
        dict(p, notFound=True) if pending_key(p) in result["notFound"] else p
        for p in board.state["pendingIds"] if pending_key(p) not in resolved
    ]
    board.state["changes"].extend(result["changes"])
    for core_key, plan in check["plan"].items():
        info = board.core_state(CORES[core_key])
        if plan["mode"] != "baseline":
            info["lastChecked"] = today
        if plan["mode"] == "full" and not result["missing"]:
            info["lastFull"] = today
    board.state["credits"] += check["credits"]
    substantive = [c for c in result["changes"] if c["cls"] != METADATA]
    board.state["checks"].append({
        "date": today, "kind": "/".join(sorted({p["mode"] for p in check["plan"].values()})),
        "checked": ", ".join(f"{CORES[k].label} ({len(p['expect'])})" for k, p in check["plan"].items()),
        "notDue": "; ".join(check.get("notDue", [])), "records": len(check["obs"]) + len(result["notFound"]),
        "changes": len(substantive), "credits": check["credits"],
        "note": f"{len(result['missing'])} not checked" if result["missing"] else "",
    })
    board.save()
    board.check_path.unlink()
    text = digest(board, check, result, final=True)
    if args.digest:
        Path(args.digest).write_text(text, encoding="utf-8")
    print(text)
    print(f"Board saved to {board.path}.")
    hint = unlinked_hint(board)
    if hint:
        print(hint)
    return 0


def _resolve_record(board: Board, text: str) -> str:
    """An Amass ID, or a source id such as an NCT or registry id, to the Amass ID on the board."""
    text = text.strip()
    if text in board.records:
        return text
    wanted = text.split(":", 1)[1] if ":" in text and not text[:5] in PREFIXES else text
    matches = [rid for rid, rec in board.records.items()
               if wanted.upper() in {str(v).upper() for v in rec["identity"].values()}]
    if len(matches) == 1:
        return matches[0]
    raise BoardError(f"{text} is not on the board" if not matches else f"{text} matches several records")


def unlinked_hint(board: Board) -> str | None:
    mains, _ = trial_groups(board.records)
    loose = [rid for rid in mains if board.records[rid]["identity"].get("sourceRegistry") not in (None, "clinicaltrials_gov")]
    if not loose:
        return None
    ids = ", ".join(source_of(TRIALCORE, board.records[r]["identity"], board.records[r]["fields"]) or r for r in loose[:6])
    return (f"Hint: {plural(len(loose), 'non-US registry record')} ({ids}{', …' if len(loose) > 6 else ''}) "
            "not linked to a trial. If they copy trials on the board, link them with annotate so the board counts "
            "trials, not registrations.")


def cmd_annotate(args: argparse.Namespace) -> int:
    board = Board.load(Path(args.board))
    if args.id:
        items = [{"id": args.id, "label": args.label, "copyOf": args.copy_of, "indication": args.indication,
                  "note": args.note}]
        if args.clear_copy:
            items[0]["copyOf"] = None
        elif args.copy_of is None:
            items[0].pop("copyOf")
    else:
        text = _stdin_text()
        if not text.strip():
            raise BoardError("say which record: --id ID with --label, --copy-of, --indication or --note, or a "
                             "JSON list of {id, label, copyOf, indication, note} on stdin")
        try:
            items = json.loads(text)
        except ValueError as err:
            raise BoardError(f"stdin is not valid JSON: {err}") from None
        items = items if isinstance(items, list) else [items]
    errors, plans = [], []
    for item in items:
        if not isinstance(item, dict) or "id" not in item:
            errors.append(f"{item!r}: each entry needs an id")
            continue
        unknown = set(item) - {"id", "label", "copyOf", "indication", "note"}
        if unknown:
            errors.append(f"{item['id']}: unknown key(s) {', '.join(sorted(unknown))}")
            continue
        try:
            rid = _resolve_record(board, str(item["id"]))
            main = None
            if item.get("copyOf"):
                main = _resolve_record(board, str(item["copyOf"]))
                if main == rid:
                    raise BoardError(f"{item['id']} cannot be a copy of itself")
                if board.records[main].get("copyOf"):
                    raise BoardError(f"{item['copyOf']} is itself a registry copy; link {item['id']} to the trial "
                                     "it copies instead")
                if board.records[main]["core"] != board.records[rid]["core"]:
                    raise BoardError(f"{item['id']} and {item['copyOf']} are different kinds of record")
                registries = {board.records[x]["identity"].get("sourceRegistry") for x in (rid, main)}
                if registries == {"clinicaltrials_gov"}:
                    raise BoardError(f"{item['id']} and {item['copyOf']} are both ClinicalTrials.gov records, so "
                                     "two trials, not a trial and its copy")
            plans.append((rid, item, main))
        except BoardError as err:
            errors.append(str(err))
    if errors:
        raise BoardError("nothing was changed:\n  " + "\n  ".join(errors))
    warnings = []
    for rid, item, main in plans:
        rec = board.records[rid]
        for key in ("label", "indication", "note"):
            if item.get(key) is not None:
                value = " ".join(str(item[key]).split()) if key != "note" else str(item[key]).strip()
                if key == "label" and len(value) > LABEL_MAX:
                    warnings.append(f"short name for {item['id']} cut to {LABEL_MAX} characters: "
                                    f"{value[:LABEL_MAX]!r}")
                    value = value[:LABEL_MAX].rstrip()
                if value:
                    rec[key] = value
                else:
                    rec.pop(key, None)
        if "copyOf" in item:
            if main:
                rec["copyOf"] = main
                for other in board.records.values():  # a trial's copies follow it
                    if other.get("copyOf") == rid:
                        other["copyOf"] = main
            else:
                rec.pop("copyOf", None)
    labels: dict = {}
    for rid, rec in board.records.items():
        if rec.get("label") and not rec.get("copyOf"):
            labels.setdefault(rec["label"].lower(), []).append(rid)
    for label, rids in labels.items():
        if len(rids) > 1:
            warnings.append(f"{len(rids)} records share the short name {board.records[rids[0]]['label']!r}; make "
                            "short names unique (add the indication when the board spans several)")
    board.save()
    print(f"Updated {plural(len(plans), 'record')}. Board saved to {board.path}.")
    for warning in warnings:
        print(f"Warning: {warning}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    board = Board.load(Path(args.board))
    s = board.state
    out = [f"# {s['title']}", "", board.freshness_line(), ""]
    out += [f"**{line}**" for line in board.kpi_lines()] + [""]
    stands = where_it_stands(board.records, TODAY)
    if stands:
        out += ["| Indication | Trials | Ongoing | Not yet | Completed | Stopped | Next end | With results |",
                "|---|---|---|---|---|---|---|---|"]
        out += ["| " + " | ".join(str(v) if v != 0 else "–" for v in row) + " |" for row in stands] + [""]
    head, lines = board.changed_lines()
    out.append(f"## {head}")
    for rid, text, _ in lines:
        rec = board.records.get(rid) if rid else None
        out.append(f"- **{name_of(CORES[rec['core']], rec, records=board.records)}**: {text}" if rec else f"- {text}")
    out.append("")
    watch = watch_items(board.records, TODAY)
    if watch:
        out += ["## Worth watching"] + [f"- {w}" for w in watch[:10]]
        out += ([f"- and {len(watch) - 10} more"] if len(watch) > 10 else []) + [""]
    if args.brief:
        print("\n".join(out).rstrip())
        return 0
    for core in board.cores_present():
        if args.core and core is not args.core:
            continue
        if core is TRIALCORE:
            mains, copies = trial_groups(board.records)
            groups = group_trials(board.records, mains)
            for group in groups:
                out += [f"## {group}", "", "| Short name | Trial | Status | Phase | Results | Ends | Also in |",
                        "|---|---|---|---|---|---|---|"]
                for rid in sorted(groups[group], key=lambda r: board.records[r]["fields"].get("startDate") or "9999"):
                    rec = board.records[rid]
                    f = rec["fields"]
                    also = ", ".join(
                        f"{registry_short(board.records[c])} {source_of(TRIALCORE, board.records[c]['identity'], board.records[c]['fields'])}"
                        + (" ⚠" if copy_flag(rec, board.records[c]) else "") for c in copies.get(rid, []))
                    short = rec.get("label") or f.get("acronym") or show(f.get("briefTitle") or "(not fetched yet)", 40)
                    cells = [short, source_of(core, rec["identity"], f) or rid, pretty(f.get("overallStatus")) or "",
                             pretty(f.get("phase")) or "", results_state(board.records, rid, copies.get(rid, []), TODAY),
                             human_month(f.get("completionDate")), also]
                    out.append("| " + " | ".join(str(c).replace("|", "/") for c in cells) + " |")
                out.append("")
            pend = board.pending_of(core)
            if pend:
                out += ["Not fetched yet: " + ", ".join(p["value"] for p in pend)]
            continue
        sheet = board.core_sheet(core, None)
        head_row = sheet["rows"][0]
        keep = {
            "biomedcore": ["Short name", "PMID", "Title", "Journal", "Retracted", "Last changed"],
            "regulatorycore": ["Short name", "Agency", "Name", "Status", "Label or SmPC date", "Last changed"],
            "drugcore": ["Short name", "ChEMBL", "Name", "Highest stage", "Last changed"],
            "genecore": ["Short name", "Symbol", "Name", "Type", "Last changed"],
        }[core.key]
        index = [head_row.index(k) for k in keep]
        out += [f"## {core.sheet} ({len(sheet['rows']) - 1})", "", "| " + " | ".join(keep) + " |",
                "|" + "---|" * len(keep)]
        for row in sheet["rows"][1:]:
            cells = []
            for i in index:
                value = row[i]
                text = "yes" if value is True else "no" if value is False else "" if value is None else str(value)
                width = {"Title": 60, "Name": 40}.get(head_row[i], 40)
                cells.append(show(text, width).replace("|", "/") if text else "")
            out.append("| " + " | ".join(cells) + " |")
        out.append("")
    latest = board.latest_check_date()
    recent = [c for c in s["changes"] if c["cls"] != METADATA and c["date"] != latest]
    recent = recent[-args.changes:] if args.changes else []
    if recent:
        out.append("## Earlier changes")
        for c in reversed(recent):
            rec = board.records.get(c["id"])
            name = name_of(CORES[rec["core"]], rec, records=board.records) if rec else (c.get("source") or c["id"])
            out.append(f"- {human_date(c['date'])} **{name}**: {c['text']}")
    hint = unlinked_hint(board)
    if hint:
        out += ["", hint]
    print("\n".join(out).rstrip())
    return 0


def _plain(value: Any) -> Any:
    if isinstance(value, C):
        value = value.value
    return "yes" if value is True else "no" if value is False else "" if value is None else value


def cmd_export(args: argparse.Namespace) -> int:
    board = Board.load(Path(args.board))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = board.state["name"]
    written = []
    if args.what == "csv":
        for sheet in board.sheets():
            if sheet["name"] in ("Overview", "Settings"):
                continue
            path = out / f"{name}-{sheet['name'].lower()}.csv"
            with path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                for row in sheet["rows"]:
                    writer.writerow([_plain(v) for v in row])
            written.append(path)
    else:
        for core in board.cores_present():
            path = out / f"{name}-{core.key}.yaml"
            lines = [f"# Exported from the board {board.state['title']} on {iso(TODAY)}.",
                     "# For API mode (scripts/monitor.py), which needs AMASS_API_KEY.",
                     f"name: {name}-{core.key}", f"core: {core.key}", "ids:"]
            for rid in board.records_of(core):
                rec = board.records[rid]
                note = name_of(core, rec, 50).replace("#", "")
                lines.append(f"  - {rid}" + (f"   # {note}" if note else ""))
            for pnd in board.pending_of(core):
                lines.append(f"  - {pnd['type']}: {json.dumps(pnd['value'])}")
            lines.append(f"cadence: {'daily' if board.interval(core) <= 1 else 'weekly'}")
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            written.append(path)
    print("Wrote " + ", ".join(str(p) for p in written))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("board", help="the board file (.xlsx)")
    common.add_argument("--today", help=argparse.SUPPRESS)

    p = sub.add_parser("new", parents=[common], help="create an empty board")
    p.add_argument("--name", required=True, help="short, lowercase, hyphenated (ulotaront-phase3)")
    p.add_argument("--title", help="a readable title")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("add", parents=[common], help="put records on the board (their first look is the baseline)")
    p.add_argument("--core", type=core_arg, required=True)
    p.add_argument("--search", metavar="QUERY", help="the records came from this search; it is stored for quick checks")
    p.add_argument("--filter", action="append", metavar="KEY=VALUE", help="a filter that search used (repeat)")
    p.add_argument("--limit", type=int, help="the limit that search used")
    p.add_argument("--fetch", action="store_true", help="the records came from fetch calls")
    p.add_argument("--id", nargs="+", metavar="ID", help="add by id only, fetched in the next check: AMTC_... or nctId:NCT...")
    p.add_argument("--watchlist", metavar="FILE", help="import the ids of an API-mode watchlist file")
    p.add_argument("--raw", metavar="FILE", help="read the records from a saved tool result instead of stdin")
    p.add_argument("--only", nargs="+", metavar="ID",
                   help="keep only these records (Amass IDs or source ids such as NCT ids); for a saved result that "
                        "holds more than the board should")
    p.add_argument("--correction", action="store_true", help="re-sending records already paid for: no credits counted")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("remove", parents=[common], help="take records or stored searches off the board")
    p.add_argument("--id", nargs="+", metavar="ID", help="Amass IDs, source ids or type:value ids")
    p.add_argument("--search-id", nargs="+", metavar="SID")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("set", parents=[common], help="change intervals, the full-check cadence or the credit limit")
    p.add_argument("--interval", action="append", metavar="CORE=DAYS")
    p.add_argument("--full-every", type=int, metavar="DAYS")
    p.add_argument("--max-credits", type=int, metavar="N")
    p.add_argument("--title")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("start", parents=[common], help="open a check: what is due and which MCP calls to make")
    p.add_argument("--force", action="store_true", help="check every Core, due or not")
    p.add_argument("--full", action="store_true", help="fetch every record instead of a quick check")
    p.add_argument("--core", type=core_arg, action="append", help="only this Core (repeat)")
    p.add_argument("--discard", action="store_true", help="drop the open check without storing anything")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("ingest", parents=[common], help="hand over what a search or fetch returned")
    p.add_argument("--core", type=core_arg, required=True)
    p.add_argument("--search", metavar="SID", help="the stored search the records came from (S1)")
    p.add_argument("--fetch", action="store_true", help="the records came from fetch calls")
    p.add_argument("--not-found", nargs="+", metavar="ID", help="ids the fetch tool could not find")
    p.add_argument("--for", dest="for_id", metavar="TYPE:VALUE", help="the unresolved id a single fetched record answers")
    p.add_argument("--raw", metavar="FILE", help="read the records from a saved tool result instead of stdin")
    p.add_argument("--correction", action="store_true", help="fixing a copy slip: no credits counted")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("status", parents=[common], help="the open check's progress, or the board's schedule")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("review", parents=[common], help="the changes the open check found, before storing them")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("finish", parents=[common], help="store the check, rewrite the board, print the digest")
    p.add_argument("--allow-missing", action="store_true", help="finish although some planned records were not observed")
    p.add_argument("--digest", metavar="FILE", help="also write the digest to this file")
    p.set_defaults(func=cmd_finish)

    p = sub.add_parser("annotate", parents=[common],
                       help="short names, registry copies, indication groups and notes (one record, or a JSON list)")
    p.add_argument("--id", metavar="ID", help="the record: Amass ID, NCT id or registry id")
    p.add_argument("--label", help="a short name, about 30 characters, 40 at most (empty to clear)")
    p.add_argument("--copy-of", metavar="ID", help="the main trial this record is a registry copy of")
    p.add_argument("--clear-copy", action="store_true", help="this record is not a copy after all")
    p.add_argument("--indication", help="the group it is shown under on the Overview (default: its conditions)")
    p.add_argument("--note", help="a note (empty to clear)")
    p.set_defaults(func=cmd_annotate)

    p = sub.add_parser("show", parents=[common], help="the board as Markdown tables, for the chat")
    p.add_argument("--core", type=core_arg)
    p.add_argument("--changes", type=int, default=5, metavar="N", help="earlier changes to list (default 5)")
    p.add_argument("--brief", action="store_true", help="the summary only, without the full tables")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("export", parents=[common], help="CSV files, or watchlist files for API mode")
    p.add_argument("what", choices=["csv", "watchlist"])
    p.add_argument("--out", required=True, metavar="DIR")
    p.set_defaults(func=cmd_export)
    return parser


def main(argv: list | None = None) -> int:
    global TODAY
    args = build_parser().parse_args(argv)
    TODAY = today_of(args)
    try:
        return args.func(args)
    except BoardError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
