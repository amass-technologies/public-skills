#!/usr/bin/env python3
"""Amass landscape monitor: deterministic bookkeeping for a field mapped through the Amass MCP.

    python3 ledger.py <command> --field landscapes/<field> [options]

Commands
  validate      check field.yaml and print what it holds
  plan          print the query plan with a run-cap and context estimate
  start-run     open a baseline or update run (update applies the interval table)
  ingest        read JSON rows from stdin: upsert the ledger, log the call, diff, classify
  add-query     append a query to the plan (rewrites field.yaml)
  saturation    new in-scope records per query and facet, with the stopping rule
  terms         values seen on in-scope records that no query mentions yet
  anchors       which anchors the plan reached
  crosscheck    compare Amass ids from stdin with the ledger; unknown ones become candidates
  status        where things stand: open run, plan versus log, budget, anchors, candidates
  finish-core   close one Core of the open run (this sets that Core's "last checked")
  finish-run    close the run, write log/summary-<run>.md and <field>.xlsx
  abandon-run   drop an open run without closing its Cores
  export        rebuild the xlsx and the latest summary from the files
  watchlist     write a Core's in-scope records as a watchlist for amass-watchlist-monitor
  dismiss       mark a candidate as out of scope by design
  entities      the map by entity, and the in-scope records no entity matches
  add-entity    add an entity (name, aliases, kind) or extend its aliases
  allowance     set the searches each Core may plan (by breadth: narrow, medium, broad)
  intervals     print the default update intervals

Standard library only, Python 3.11 or newer. The field folder is the memory: nothing is
kept in process between commands, so a run can be resumed from the files alone.
Exit codes: 0 done, 1 error (nothing written), 2 done but the run is over budget.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

VERSION = "0.1.0"

CREDITS = {"search": 2, "fetch": 1}  # nominal MCP credits; multiplied by budget.factor
DEFAULT_BUDGET = {"baseline": 300, "update": 60, "factor": 1}
DEFAULT_STOPPING = {"consecutive": 2, "minNew": 2}
# Searches per Core a baseline may plan, by the field's breadth. Set after the roster round with
# `allowance --breadth`; `add-query` refuses to go past it without --force.
ALLOWANCE = {
    "narrow": {"trialcore": 10, "biomedcore": 8, "patentcore": 6, "drugcore": 6, "genecore": 3, "regulatorycore": 3},
    "medium": {"trialcore": 16, "biomedcore": 12, "patentcore": 8, "drugcore": 8, "genecore": 4, "regulatorycore": 4},
    "broad": {"trialcore": 24, "biomedcore": 16, "patentcore": 10, "drugcore": 10, "genecore": 4, "regulatorycore": 4},
}
# Update intervals in days. One table: change a Core's cadence here (and in SKILL.md).
DEFAULT_INTERVALS = {
    "biomedcore": 1,
    "trialcore": 1,
    "regulatorycore": 7,
    "genecore": 7,
    "drugcore": 90,
    "patentcore": 90,
}
TOKENS_PER_RESULT = {  # rough context cost of one search result, for the plan estimate
    "trialcore": 220,
    "biomedcore": 450,
    "regulatorycore": 260,
    "drugcore": 160,
    "genecore": 2500,
    "patentcore": 320,
}
DECISIONS = ("in", "out", "unsure")
PASSES = ("created", "updated", "published")
PASS_FILTER = {"created": "minCreateDate", "updated": "minLastUpdateDate", "published": "minPublicationDate"}
UPDATE_LOOKBACK_DAYS = 1

NEW_TO_AMASS = "new-to-amass"
NEWLY_MATCHED = "newly-matched"
CHANGED = "changed"
DECISION_CHANGED = "decision-changed"
NEW_CLASSES = (NEW_TO_AMASS, NEWLY_MATCHED)

LEDGER_META = ("decision", "reason", "source", "firstSeen", "firstQuery", "lastSeen", "lastQuery", "seenCount", "observed")
QUERY_LOG_COLUMNS = (
    "timestamp", "runId", "mode", "core", "queryId", "kind", "tool", "pass", "since", "params", "limit",
    "returned", "capHit", "newIds", "inCount", "outCount", "unsureCount", "changedRows", "unchangedKnown",
    "credits", "note",
)
RUN_COLUMNS = ("runId", "mode", "core", "startedAt", "finishedAt", "since", "queries", "credits", "newRecords",
               "changes", "unchangedKnown", "note")
CHANGE_COLUMNS = ("runId", "date", "core", "amassId", "label", "class", "field", "old", "new", "group", "queryId")
CANDIDATE_COLUMNS = ("date", "runId", "core", "amassId", "source", "status", "note")


class LedgerError(Exception):
    """A problem the caller must see. Nothing is written when it is raised."""


# --------------------------------------------------------------------------
# Per-Core specification
# --------------------------------------------------------------------------

PHASES = ("EARLY_PHASE1", "PHASE1", "PHASE1/PHASE2", "PHASE2", "PHASE2/PHASE3", "PHASE3", "PHASE4", "NA")
TRIAL_STATUSES = ("RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION", "ACTIVE_NOT_RECRUITING", "SUSPENDED",
                  "TERMINATED", "COMPLETED", "WITHDRAWN", "UNKNOWN", "WITHHELD", "AVAILABLE", "NO_LONGER_AVAILABLE",
                  "TEMPORARILY_NOT_AVAILABLE", "APPROVED_FOR_MARKETING")
STUDY_TYPES = ("INTERVENTIONAL", "OBSERVATIONAL", "EXPANDED_ACCESS")
INTERVENTION_TYPES = ("DRUG", "DEVICE", "BIOLOGICAL", "COMBINATION_PRODUCT", "PROCEDURE", "RADIATION",
                      "DIETARY_SUPPLEMENT", "GENETIC", "BEHAVIORAL", "DIAGNOSTIC_TEST", "OTHER")
AGENCIES = ("FDA", "EMA")
AUTH_STATUSES = ("ACTIVE", "APPROVED_NOT_MARKETED", "CONDITIONAL", "SUSPENDED", "WITHDRAWN_VOLUNTARY",
                 "WITHDRAWN_FORCED", "REVOKED", "LAPSED_SUNSET", "REFUSED", "WITHDRAWN_DURING_REVIEW", "EXPIRED",
                 "UNKNOWN")
DESIGNATIONS = ("PRIORITY_REVIEW", "BREAKTHROUGH_THERAPY", "FAST_TRACK", "RMAT", "ACCELERATED_APPROVAL",
                "ACCELERATED_ASSESSMENT", "PRIME", "CONDITIONAL_MA", "EXCEPTIONAL_CIRCUMSTANCES")
MOLECULE_TYPES = ("SMALL_MOLECULE", "ANTIBODY", "PROTEIN", "ENZYME", "OLIGONUCLEOTIDE", "GENE", "CELL",
                  "ANTIBODY_DRUG_CONJUGATE", "VACCINE_COMPONENT", "VACCINE", "OLIGOSACCHARIDE", "UNKNOWN")
STAGES = ("EARLY_PHASE1", "PHASE1", "PHASE1/PHASE2", "PHASE2", "PHASE2/PHASE3", "PHASE3", "PRECLINICAL", "IND",
          "PREAPPROVAL", "APPROVAL", "UNKNOWN")
GENE_TYPES = ("PROTEIN_CODING", "NCRNA", "PSEUDO", "TRNA", "RRNA", "SNRNA", "SCRNA", "SNORNA", "MISCRNA",
              "BIOLOGICAL_REGION", "TRANSPOSON", "OTHER")
TARGET_CLASSES = ("ENZYME", "MEMBRANE_RECEPTOR", "ION_CHANNEL", "TRANSPORTER", "TRANSCRIPTION_FACTOR",
                  "EPIGENETIC_REGULATOR", "SECRETED_PROTEIN", "SURFACE_ANTIGEN", "STRUCTURAL_PROTEIN", "ADHESION",
                  "OTHER_CYTOSOLIC_PROTEIN", "OTHER_NUCLEAR_PROTEIN", "AUXILIARY_TRANSPORT_PROTEIN",
                  "UNCLASSIFIED_PROTEIN")
TRACT_MODALITIES = ("SMALL_MOLECULE", "ANTIBODY", "PROTAC", "OTHER_CLINICAL")
TRACT_STAGES = ("APPROVED_DRUG", "ADVANCED_CLINICAL", "PHASE_1_CLINICAL")


class Col:
    """One tracked column. kind drives validation, normalisation and diffing; group labels a change."""

    __slots__ = ("name", "kind", "values", "group", "fetch_only")

    def __init__(self, name: str, kind: str, values: tuple = (), group: str = "other", fetch_only: bool = False):
        self.name, self.kind, self.values, self.group, self.fetch_only = name, kind, values, group, fetch_only

    @property
    def is_set(self) -> bool:
        return self.kind in ("list", "listenum", "byagency", "safety", "mechanisms", "designations", "ids")


class Core:
    __slots__ = ("name", "label", "prefix", "search_tool", "fetch_tool", "columns", "id_columns", "title_column",
                 "term_columns", "breakdown_columns", "filters", "cadence", "watchlist")

    def __init__(self, name, label, prefix, search_tool, fetch_tool, columns, id_columns, title_column, term_columns,
                 breakdown_columns, filters, cadence, watchlist=True):
        self.name, self.label, self.prefix = name, label, prefix
        self.search_tool, self.fetch_tool = search_tool, fetch_tool
        self.columns = {c.name: c for c in columns}
        self.id_columns, self.title_column = id_columns, title_column
        self.term_columns, self.breakdown_columns = term_columns, breakdown_columns
        self.filters, self.cadence, self.watchlist = filters, cadence, watchlist

    def valid_id(self, value: Any) -> bool:
        return isinstance(value, str) and re.fullmatch(re.escape(self.prefix) + r"[0-9A-Za-z]{8,64}", value) is not None

    @property
    def ledger_columns(self) -> list[str]:
        return ["amassId", *self.columns, *LEDGER_META]


# filter name -> ("bool" | "date" | "int" | "float" | "str" | tuple of enum values)
_DATE_FILTERS_COMMON = {"minCreateDate": "date", "minLastUpdateDate": "date"}

CORES: dict[str, Core] = {}
for _core in (
    Core(
        "trialcore", "TrialCore", "AMTC_", "search_amass_trialcore_records", "get_amass_trialcore_record",
        [
            Col("nctId", "str"), Col("registryId", "str"), Col("sourceRegistry", "str"), Col("sourceUrl", "str"),
            Col("briefTitle", "text", group="text"), Col("acronym", "str"), Col("sponsorName", "str"),
            Col("phase", "enum", PHASES, group="status"), Col("overallStatus", "enum", TRIAL_STATUSES, group="status"),
            Col("studyType", "enum", STUDY_TYPES), Col("startDate", "date", group="dates"),
            Col("completionDate", "date", group="dates"), Col("enrollment", "int", group="enrollment"),
            Col("conditions", "list"), Col("interventionNames", "list"),
            Col("interventionTypes", "listenum", INTERVENTION_TYPES), Col("facilityCountries", "list"),
            Col("hasResults", "bool", group="results"), Col("links", "links", group="links", fetch_only=True),
        ],
        ["nctId", "registryId"], "briefTitle", ["interventionNames", "sponsorName", "conditions"],
        ["overallStatus", "phase"],
        {**_DATE_FILTERS_COMMON, "minStartDate": "date", "hasResults": "bool", "phase": PHASES,
         "overallStatus": TRIAL_STATUSES, "studyType": STUDY_TYPES, "interventionType": INTERVENTION_TYPES},
        "daily",
    ),
    Core(
        "biomedcore", "BiomedCore", "AMBC_", "search_amass_biomedcore_records", "get_amass_biomedcore_record",
        [
            Col("pmid", "str"), Col("doi", "str"), Col("url", "str"), Col("title", "text", group="text"),
            Col("journal", "str"), Col("publicationDate", "date", group="dates"), Col("authors", "authors"),
            Col("citationCount", "int", group="metadata"), Col("journalQualityJufo", "int"),
            Col("hasFulltext", "bool"), Col("isRetracted", "bool", group="retraction"),
            Col("links", "links", group="links", fetch_only=True),
        ],
        ["pmid", "doi"], "title", [], ["publicationYear"],
        {**_DATE_FILTERS_COMMON, "minPublicationDate": "date", "isRetracted": "bool", "minJournalQualityJufo": "int",
         "authorNames": "list", "authorOrcids": "list", "institutionNames": "list", "institutionRors": "list"},
        "daily",
    ),
    Core(
        "regulatorycore", "RegulatoryCore", "AMRC_", "search_amass_regulatorycore_records",
        "get_amass_regulatorycore_record",
        [
            Col("agency", "enum", AGENCIES), Col("name", "text", group="text"), Col("activeSubstance", "str"),
            Col("moleculeType", "enum", MOLECULE_TYPES), Col("authorizationStatus", "enum", AUTH_STATUSES, group="status"),
            Col("procedureType", "str"), Col("therapeuticIndication", "text", group="text"),
            Col("marketingAuthorisationHolder", "str"), Col("authorizationDate", "date", group="dates"),
            Col("sourceUrl", "str"), Col("isOrphan", "bool", group="status"),
            Col("authorizationsByAgency", "byagency", group="links"),
            Col("applicationNumber", "str", fetch_only=True), Col("productNumber", "str", fetch_only=True),
            Col("designations", "designations", group="status", fetch_only=True),
            Col("labelDate", "date", group="dates", fetch_only=True), Col("smpcDate", "date", group="dates", fetch_only=True),
            Col("revisionNumber", "int", group="dates", fetch_only=True),
            Col("sectionCount", "int", group="metadata", fetch_only=True),
            Col("lastUpdateDate", "date", group="metadata", fetch_only=True),
            Col("createDate", "date", group="metadata", fetch_only=True),
            Col("links", "links", group="links", fetch_only=True),
        ],
        ["applicationNumber", "productNumber"], "name", ["activeSubstance", "marketingAuthorisationHolder"],
        ["agency", "authorizationStatus"],
        {**_DATE_FILTERS_COMMON, "minAuthorizationDate": "date", "maxAuthorizationDate": "date", "agency": AGENCIES,
         "authorizationStatus": AUTH_STATUSES, "hasDesignation": DESIGNATIONS, "isOrphan": "bool",
         "moleculeType": MOLECULE_TYPES},
        "weekly",
    ),
    Core(
        "drugcore", "DrugCore", "AMDC_", "search_amass_drugcore_records", "get_amass_drugcore_record",
        [
            Col("chemblId", "str"), Col("url", "str"), Col("name", "text", group="text"), Col("synonyms", "list"),
            Col("tradeNames", "list"), Col("drugType", "enum", MOLECULE_TYPES, group="status"),
            Col("maxClinicalStage", "enum", STAGES, group="status"), Col("description", "text", group="text"),
            Col("mechanisms", "mechanisms", fetch_only=True), Col("parent", "str", fetch_only=True),
            Col("links", "links", group="links", fetch_only=True),
        ],
        ["chemblId"], "name", ["name", "synonyms", "tradeNames"], ["maxClinicalStage", "drugType"],
        {**_DATE_FILTERS_COMMON, "drugType": MOLECULE_TYPES, "maxClinicalStage": STAGES},
        "weekly",
    ),
    Core(
        "genecore", "GeneCore", "AMGC_", "search_amass_genecore_records", "get_amass_genecore_record",
        [
            Col("ensemblGeneId", "str"), Col("symbol", "str", group="text"), Col("name", "str", group="text"),
            Col("synonyms", "list"), Col("geneType", "enum", GENE_TYPES), Col("hgncId", "str"),
            Col("targetClass", "targetclass"), Col("tractability", "tractability"), Col("safetyEvents", "safety"),
            Col("loeuf", "loeuf"), Col("isEssential", "essential"),
            Col("lastUpdateDate", "date", group="metadata"), Col("createDate", "date", group="metadata"),
            Col("links", "links", group="links", fetch_only=True),
        ],
        ["ensemblGeneId", "symbol", "hgncId"], "symbol", ["symbol", "synonyms"], ["geneType"],
        {**_DATE_FILTERS_COMMON, "geneType": GENE_TYPES, "hasSafetyLiabilities": "bool", "isDruggable": "bool",
         "isEssential": "bool", "maxConstraintLoeuf": "float", "targetClass": TARGET_CLASSES,
         "tractabilityModality": TRACT_MODALITIES, "tractabilityStage": TRACT_STAGES},
        "weekly",
    ),
    Core(
        "patentcore", "PatentCore", "AMPC_", "search_amass_patentcore_records", "get_amass_patentcore_record",
        [
            Col("publicationNumber", "str"), Col("countryCode", "str"), Col("kindCode", "str"), Col("familyId", "str"),
            Col("title", "text", group="text"), Col("assignees", "list"), Col("cpcCodes", "list"),
            Col("publicationDate", "date", group="dates"), Col("filingDate", "date", group="dates"),
            Col("grantDate", "date", group="dates"), Col("priorityDate", "date", group="dates"),
            Col("citedByCount", "int", group="metadata"), Col("links", "links", group="links", fetch_only=True),
        ],
        ["publicationNumber", "familyId"], "title", ["assignees"], ["countryCode", "publicationYear"],
        {"minPublicationDate": "date", "assignee": "str", "countryCode": "str", "cpcCodes": "str",
         "minCitedByCount": "int"},
        "quarterly", watchlist=False,
    ),
):
    CORES[_core.name] = _core

PREFIX_TO_CORE = {c.prefix: c.name for c in CORES.values()}

# Where each tracked column comes from in a raw tool result, when its name differs from the column.
# A string is a dotted path; a dict names the cross-link fields that feed the `links` column.
RAW_SOURCES: dict[str, dict[str, Any]] = {
    "trialcore": {"links": {"biomedcore": "referencesBiomedCore", "drugcore": "referencesDrugCore"}},
    "biomedcore": {"links": {"trialcore": "referencesTrialCore"}},
    "regulatorycore": {
        "applicationNumber": "fdaDetails.applicationNumber", "productNumber": "emaDetails.productNumber",
        "labelDate": "fdaDetails.labelDate", "smpcDate": "emaDetails.smpcDate",
        "revisionNumber": "emaDetails.revisionNumber", "sectionCount": "len:documentSections",
        "links": {"drugcore": "referencesDrugCore"},
    },
    "drugcore": {"mechanisms": "mechanismsOfAction",
                 "links": {"trialcore": "referencesTrialCore", "biomedcore": "referencesBiomedCore",
                           "regulatorycore": "referencesRegulatoryCore", "genecore": "referencesGeneCore"}},
    "genecore": {"loeuf": "gnomadConstraint", "isEssential": "depmapEssentiality", "safetyEvents": "safetyLiabilities",
                 "links": {"drugcore": "referencesDrugCore"}},
    "patentcore": {"links": {"drugcore": "referencesDrugCore", "biomedcore": "referencesBiomedCore"}},
}


def _raw_get(record: dict, path: str) -> Any:
    if path.startswith("len:"):
        value = record.get(path[4:])
        return len(value) if isinstance(value, list) else None
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def rows_from_raw(core: Core, raw: Any, decisions: dict, default: tuple[str, str] | None) -> list[dict]:
    """Turn a raw MCP tool result ({"results": [...]} or {"data": {...}}) into ingest rows."""
    if isinstance(raw, dict) and "results" in raw:
        records = raw["results"]
    elif isinstance(raw, dict) and "data" in raw:
        records = raw["data"] if isinstance(raw["data"], list) else [raw["data"]]
    elif isinstance(raw, list):
        records = raw
    else:
        raise LedgerError('the raw file must hold a tool result: {"results": [...]} for a search or {"data": {...}} for a fetch')
    sources = RAW_SOURCES.get(core.name, {})
    rows = []
    for rec in records:
        if not isinstance(rec, dict):
            raise LedgerError("every raw record must be an object")
        row: dict[str, Any] = {"amassId": rec.get("amassId")}
        for name in core.columns:
            src = sources.get(name, name)
            if isinstance(src, dict):
                links = {c: rec.get(f) for c, f in src.items() if isinstance(rec.get(f), list)}
                if links:
                    row["links"] = links
            elif src in rec or "." in src or src.startswith("len:"):
                value = _raw_get(rec, src)
                if value is not None or src in rec:
                    row[name] = value
        verdict = decisions.get(row["amassId"]) if isinstance(row["amassId"], str) else None
        if verdict is None and default:
            verdict = list(default)
            if default[0] == "drop":
                row["decision"] = "drop"
                rows.append(row)
                continue
        if isinstance(verdict, str):
            row["decision"] = verdict
        elif isinstance(verdict, (list, tuple)) and verdict:
            row["decision"] = verdict[0]
            if len(verdict) > 1 and verdict[1]:
                row["reason"] = verdict[1]
        elif isinstance(verdict, dict):
            row["decision"] = verdict.get("decision")
            if verdict.get("reason"):
                row["reason"] = verdict["reason"]
        rows.append(row)
    unknown = sorted(set(decisions) - {r["amassId"] for r in rows})
    if unknown:
        raise LedgerError(f"decisions name ids that are not in the raw result: {unknown[:5]}")
    return rows


def core_for_id(amass_id: str) -> Core | None:
    for prefix, name in PREFIX_TO_CORE.items():
        if isinstance(amass_id, str) and amass_id.startswith(prefix):
            return CORES[name]
    return None


# --------------------------------------------------------------------------
# YAML subset: enough for field.yaml, strict about everything else
# --------------------------------------------------------------------------

_SPECIAL_START = "-?:,[]{}#&*!|>'\"%@`"
_NUMBER_RE = re.compile(r"[-+]?(\d+\.?\d*([eE][-+]?\d+)?|\.\d+)")


def _strip_comment(line: str) -> str:
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i]
    return line


def _parse_scalar(text: str, where: str) -> Any:
    t = text.strip()
    if t == "" or t in ("~", "null"):
        return None
    if t[0] == '"':
        try:
            return json.loads(t)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"{where}: bad double-quoted string: {exc}") from None
    if t[0] == "'":
        if len(t) < 2 or not t.endswith("'"):
            raise LedgerError(f"{where}: unterminated single-quoted string")
        return t[1:-1].replace("''", "'")
    if t in ("true", "True"):
        return True
    if t in ("false", "False"):
        return False
    if re.fullmatch(r"[-+]?\d+", t):
        return int(t)
    if _NUMBER_RE.fullmatch(t):
        return float(t)
    return t


def _split_flow(inner: str, where: str) -> list[str]:
    parts, buf, quote, depth = [], "", None, 0
    for ch in inner:
        if quote:
            buf += ch
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            buf += ch
        elif ch in "[{":
            depth += 1
            buf += ch
        elif ch in "]}":
            depth -= 1
            buf += ch
        elif ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    if quote or depth:
        raise LedgerError(f"{where}: unbalanced flow collection")
    if buf.strip():
        parts.append(buf)
    return parts


def _parse_inline(text: str, where: str) -> Any:
    t = text.strip()
    if t.startswith("["):
        if not t.endswith("]"):
            raise LedgerError(f"{where}: unterminated list")
        return [_parse_inline(p, where) for p in _split_flow(t[1:-1], where)]
    if t.startswith("{"):
        if not t.endswith("}"):
            raise LedgerError(f"{where}: unterminated map")
        out = {}
        for part in _split_flow(t[1:-1], where):
            m = re.fullmatch(r"\s*([A-Za-z_][\w.-]*)\s*:\s*(.*)", part, re.S)
            if not m:
                raise LedgerError(f"{where}: expected `key: value` inside {{}}, got {part.strip()!r}")
            out[m.group(1)] = _parse_inline(m.group(2), where)
        return out
    return _parse_scalar(t, where)


_KEY_RE = re.compile(r"([A-Za-z_][\w.-]*)\s*:(?:\s+(.*))?")


class _Line:
    __slots__ = ("indent", "text", "where")

    def __init__(self, indent: int, text: str, where: str):
        self.indent, self.text, self.where = indent, text, where


def _parse_block(lines: list[_Line], i: int, indent: int) -> tuple[Any, int]:
    if lines[i].text == "-" or lines[i].text.startswith("- "):
        return _parse_seq(lines, i, indent)
    return _parse_map(lines, i, indent)


def _parse_map(lines: list[_Line], i: int, indent: int) -> tuple[dict, int]:
    data: dict[str, Any] = {}
    while i < len(lines) and lines[i].indent == indent:
        line = lines[i]
        if line.text == "-" or line.text.startswith("- "):
            raise LedgerError(f"{line.where}: a list item where a key was expected")
        m = _KEY_RE.fullmatch(line.text)
        if not m:
            raise LedgerError(f"{line.where}: expected `key: value`, got {line.text!r} (quote strings that contain ': ')")
        key, rest = m.group(1), m.group(2)
        if key in data:
            raise LedgerError(f"{line.where}: duplicate key {key!r}")
        if rest is None or rest.strip() == "":
            if i + 1 < len(lines) and lines[i + 1].indent > indent:
                value, i = _parse_block(lines, i + 1, lines[i + 1].indent)
            else:
                value, i = None, i + 1
        else:
            value, i = _parse_inline(rest, line.where), i + 1
        data[key] = value
    if i < len(lines) and lines[i].indent > indent:
        raise LedgerError(f"{lines[i].where}: unexpected indentation")
    return data, i


def _parse_seq(lines: list[_Line], i: int, indent: int) -> tuple[list, int]:
    items: list[Any] = []
    while i < len(lines) and lines[i].indent == indent and (lines[i].text == "-" or lines[i].text.startswith("- ")):
        line = lines[i]
        rest = line.text[1:].strip()
        if rest == "":
            if i + 1 < len(lines) and lines[i + 1].indent > indent:
                value, i = _parse_block(lines, i + 1, lines[i + 1].indent)
            else:
                value, i = None, i + 1
        elif rest[0] not in "'\"[{" and _KEY_RE.fullmatch(rest):
            lines[i] = _Line(indent + 2, rest, line.where)
            value, i = _parse_map(lines, i, indent + 2)
        else:
            value, i = _parse_inline(rest, line.where), i + 1
        items.append(value)
    if i < len(lines) and lines[i].indent > indent:
        raise LedgerError(f"{lines[i].where}: unexpected indentation")
    return items, i


def parse_yaml(text: str, source: str = "field.yaml") -> Any:
    lines: list[_Line] = []
    for n, raw in enumerate(text.splitlines(), 1):
        stripped = _strip_comment(raw).rstrip()
        if not stripped.strip() or stripped.strip() in ("---", "..."):
            continue
        lead = len(stripped) - len(stripped.lstrip(" "))
        if stripped[:lead].count("\t") or stripped.lstrip(" ").startswith("\t"):
            raise LedgerError(f"{source}:{n}: use spaces, not tabs, for indentation")
        lines.append(_Line(lead, stripped.strip(), f"{source}:{n}"))
    if not lines:
        return {}
    value, i = _parse_block(lines, 0, lines[0].indent)
    if i != len(lines):
        raise LedgerError(f"{lines[i].where}: unexpected content")
    return value


def _fmt_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    s = str(value)
    needs_quote = (
        s == "" or s != s.strip() or "\n" in s or "\t" in s or s[0] in _SPECIAL_START or ": " in s or " #" in s
        or s.endswith(":") or s.lower() in ("null", "~", "true", "false", "yes", "no", "on", "off")
        or re.fullmatch(r"[-+]?\d+", s) is not None or _NUMBER_RE.fullmatch(s) is not None
    )
    return json.dumps(s, ensure_ascii=False) if needs_quote else s


def _flow_ok(items: list) -> bool:
    if not items or not all(isinstance(x, (str, int, float, bool)) or x is None for x in items):
        return False
    rendered = [_fmt_scalar(x) for x in items]
    return all("," not in r and len(r) < 40 for r in rendered) and sum(len(r) for r in rendered) < 80


def dump_yaml(data: Any, indent: int = 0) -> list[str]:
    pad = " " * indent
    out: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict):
                if value:
                    out.append(f"{pad}{key}:")
                    out.extend(dump_yaml(value, indent + 2))
                else:
                    out.append(f"{pad}{key}: {{}}")
            elif isinstance(value, list):
                if not value:
                    out.append(f"{pad}{key}: []")
                elif _flow_ok(value):
                    out.append(f"{pad}{key}: [" + ", ".join(_fmt_scalar(x) for x in value) + "]")
                else:
                    out.append(f"{pad}{key}:")
                    out.extend(dump_yaml(value, indent + 2))
            else:
                out.append(f"{pad}{key}: {_fmt_scalar(value)}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item:
                inner = dump_yaml(item, indent + 2)
                out.append(f"{pad}- " + inner[0][indent + 2:])
                out.extend(inner[1:])
            elif isinstance(item, list):
                out.append(f"{pad}- [" + ", ".join(_fmt_scalar(x) for x in item) + "]")
            else:
                out.append(f"{pad}- {_fmt_scalar(item)}")
    else:
        out.append(f"{pad}{_fmt_scalar(data)}")
    return out


# --------------------------------------------------------------------------
# Field file
# --------------------------------------------------------------------------

FIELD_KEYS = {"name", "question", "created", "cores", "include", "exclude", "anchors", "entities", "budget",
              "intervals", "stopping", "allowance", "queries", "notes"}
ENTITY_KINDS = ("drug", "target", "class", "other")
QUERY_KEYS = {"id", "core", "facet", "query", "filters", "limit", "update", "note"}
CORE_LETTER = {"trialcore": "T", "biomedcore": "B", "regulatorycore": "R", "drugcore": "D", "genecore": "G",
               "patentcore": "P"}


def _as_str_list(value: Any, what: str, errors: list[str], required: bool = False) -> list[str]:
    if value is None:
        if required:
            errors.append(f"{what}: required")
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        errors.append(f"{what}: must be a list of non-empty strings")
        return []
    return [x.strip() for x in value]


def _check_filter_value(core: Core, name: str, value: Any, where: str, errors: list[str]) -> Any:
    spec = core.filters.get(name)
    if spec is None:
        errors.append(f"{where}: {core.label} search has no filter {name!r}; it takes {', '.join(sorted(core.filters))}")
        return value
    if name in ("minCreateDate", "minLastUpdateDate"):
        errors.append(f"{where}: {name} is set by the update pass, not by the plan")
        return value
    # minPublicationDate is allowed as a plan filter on BiomedCore and PatentCore; on a PatentCore
    # update pass the pass date replaces it.
    if spec == "bool":
        if not isinstance(value, bool):
            errors.append(f"{where}: {name} must be true or false")
    elif spec == "date":
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            errors.append(f"{where}: {name} must be YYYY-MM-DD")
    elif spec == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{where}: {name} must be an integer")
    elif spec == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{where}: {name} must be a number")
    elif spec == "str":
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            value = ",".join(x.strip() for x in value)  # comma-separated "match any" filters (assignee, countryCode)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{where}: {name} must be a string")
    elif spec == "list":
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            errors.append(f"{where}: {name} must be a list of strings")
    else:  # enum tuple
        items = value if isinstance(value, list) else [value]
        bad = [x for x in items if not isinstance(x, str) or x.upper() not in spec]
        if bad:
            errors.append(f"{where}: {name} accepts {', '.join(spec)}; got {bad}")
        else:
            value = [x.upper() for x in items] if isinstance(value, list) else str(value).upper()
    return value


def normalise_field(data: Any, source: str = "field.yaml") -> tuple[dict, list[str]]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return {}, [f"{source}: the file must be a mapping"]
    unknown = sorted(set(data) - FIELD_KEYS)
    if unknown:
        errors.append(f"{source}: unknown keys {unknown}")
    field: dict[str, Any] = {}
    name = data.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name):
        errors.append("name: required; lowercase letters, digits, . _ - (e.g. taar1-schizophrenia)")
    field["name"] = name if isinstance(name, str) else ""
    question = data.get("question")
    if not isinstance(question, str) or not question.strip():
        errors.append("question: required, one sentence")
    field["question"] = question.strip() if isinstance(question, str) else ""
    created = data.get("created")
    field["created"] = str(created) if created else utc_today().isoformat()
    cores = _as_str_list(data.get("cores"), "cores", errors, required=True)
    bad = [c for c in cores if c not in CORES]
    if bad:
        errors.append(f"cores: unknown {bad}; choose from {', '.join(CORES)}")
    field["cores"] = [c for c in cores if c in CORES]
    field["include"] = _as_str_list(data.get("include"), "include", errors, required=True)
    field["exclude"] = _as_str_list(data.get("exclude"), "exclude", errors)
    field["notes"] = _as_str_list(data.get("notes"), "notes", errors)

    anchors: list[dict] = []
    raw_anchors = data.get("anchors") or []
    if not isinstance(raw_anchors, list):
        errors.append("anchors: must be a list of {core, id, note}")
        raw_anchors = []
    for n, a in enumerate(raw_anchors, 1):
        if not isinstance(a, dict) or not isinstance(a.get("core"), str) or a.get("id") in (None, ""):
            errors.append(f"anchors[{n}]: needs core and id")
            continue
        if a["core"] not in CORES:
            errors.append(f"anchors[{n}]: unknown core {a['core']!r}")
            continue
        anchors.append({"core": a["core"], "id": str(a["id"]).strip(), "note": str(a.get("note") or "")})
    field["anchors"] = anchors

    entities: list[dict] = []
    raw_entities = data.get("entities") or []
    if not isinstance(raw_entities, list):
        errors.append("entities: must be a list of {name, aliases, kind}")
        raw_entities = []
    seen_names: set[str] = set()
    for n, e in enumerate(raw_entities, 1):
        if not isinstance(e, dict) or not isinstance(e.get("name"), str) or not e["name"].strip():
            errors.append(f"entities[{n}]: needs a name")
            continue
        name_ = e["name"].strip()
        if name_.lower() in seen_names:
            errors.append(f"entities[{n}]: duplicate entity {name_!r}")
            continue
        seen_names.add(name_.lower())
        kind = str(e.get("kind") or "drug")
        if kind not in ENTITY_KINDS:
            errors.append(f"entities[{n}] ({name_}): kind must be one of {', '.join(ENTITY_KINDS)}")
            kind = "other"
        aliases = _as_str_list(e.get("aliases"), f"entities[{n}] ({name_}).aliases", errors)
        entities.append({"name": name_, "aliases": aliases, "kind": kind, "note": str(e.get("note") or "")})
    field["entities"] = entities

    budget = dict(DEFAULT_BUDGET)
    raw_budget = data.get("budget") or {}
    if not isinstance(raw_budget, dict):
        errors.append("budget: must be a mapping with baseline, update, factor")
        raw_budget = {}
    for key in ("baseline", "update"):
        if key in raw_budget:
            v = raw_budget[key]
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                errors.append(f"budget.{key}: a positive integer")
            else:
                budget[key] = v
    if "factor" in raw_budget:
        v = raw_budget["factor"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            errors.append("budget.factor: positive number (a multiplier on the nominal credit counts, default 1)")
        else:
            budget["factor"] = v
    unknown_budget = sorted(set(raw_budget) - {"baseline", "update", "factor"})
    if unknown_budget:
        errors.append(f"budget: unknown keys {unknown_budget}")
    field["budget"] = budget

    intervals = {c: DEFAULT_INTERVALS[c] for c in field["cores"]}
    raw_intervals = data.get("intervals") or {}
    if not isinstance(raw_intervals, dict):
        errors.append("intervals: must map core -> days")
        raw_intervals = {}
    for core_name, days in raw_intervals.items():
        if core_name not in CORES:
            errors.append(f"intervals: unknown core {core_name!r}")
        elif isinstance(days, bool) or not isinstance(days, int) or days < 1:
            errors.append(f"intervals.{core_name}: whole number of days, at least 1")
        elif core_name in intervals:
            intervals[core_name] = days
    field["intervals"] = intervals

    stopping = dict(DEFAULT_STOPPING)
    raw_stopping = data.get("stopping") or {}
    if not isinstance(raw_stopping, dict):
        errors.append("stopping: must be a mapping with consecutive and minNew")
        raw_stopping = {}
    for key, minimum in (("consecutive", 1), ("minNew", 0)):
        if key in raw_stopping:
            v = raw_stopping[key]
            if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
                errors.append(f"stopping.{key}: integer, at least {minimum}")
            else:
                stopping[key] = v
    field["stopping"] = stopping

    allowance = {c: ALLOWANCE["medium"][c] for c in field["cores"]}
    raw_allow = data.get("allowance") or {}
    if not isinstance(raw_allow, dict):
        errors.append("allowance: must map core -> searches")
        raw_allow = {}
    for core_name, n in raw_allow.items():
        if core_name not in CORES:
            errors.append(f"allowance: unknown core {core_name!r}")
        elif isinstance(n, bool) or not isinstance(n, int) or n < 1:
            errors.append(f"allowance.{core_name}: whole number of searches, at least 1")
        elif core_name in allowance:
            allowance[core_name] = n
    field["allowance"] = allowance

    queries: list[dict] = []
    seen_ids: set[str] = set()
    raw_queries = data.get("queries") or []
    if not isinstance(raw_queries, list):
        errors.append("queries: must be a list")
        raw_queries = []
    for n, q in enumerate(raw_queries, 1):
        where = f"queries[{n}]"
        if not isinstance(q, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        unknown_q = sorted(set(q) - QUERY_KEYS)
        if unknown_q:
            errors.append(f"{where}: unknown keys {unknown_q}")
        qid = q.get("id")
        if not isinstance(qid, str) or not re.fullmatch(r"[A-Za-z][\w-]*", qid):
            errors.append(f"{where}: id required (e.g. T01)")
            qid = f"?{n}"
        elif qid in seen_ids:
            errors.append(f"{where}: duplicate id {qid!r}")
        seen_ids.add(qid)
        core_name = q.get("core")
        if core_name not in CORES:
            errors.append(f"{where} ({qid}): core must be one of {', '.join(CORES)}")
            continue
        if core_name not in field["cores"]:
            errors.append(f"{where} ({qid}): core {core_name} is not in the field's cores list")
        core = CORES[core_name]
        text = q.get("query")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{where} ({qid}): query text required")
            text = ""
        facet = q.get("facet")
        if not isinstance(facet, str) or not facet.strip():
            errors.append(f"{where} ({qid}): facet required (e.g. drug, mechanism, indication, sponsor, code)")
            facet = ""
        filters = q.get("filters") or {}
        if not isinstance(filters, dict):
            errors.append(f"{where} ({qid}): filters must be a mapping")
            filters = {}
        filters = {k: _check_filter_value(core, k, v, f"{where} ({qid})", errors) for k, v in filters.items()}
        limit = q.get("limit", 50)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            errors.append(f"{where} ({qid}): limit must be 1 to 50")
            limit = 50
        update = q.get("update", False)
        if not isinstance(update, bool):
            errors.append(f"{where} ({qid}): update must be true or false")
            update = False
        queries.append({"id": qid, "core": core_name, "facet": facet.strip(), "query": text.strip(),
                        "filters": filters, "limit": limit, "update": update, "note": str(q.get("note") or "")})
    field["queries"] = queries
    return field, errors


def field_to_yaml(field: dict) -> str:
    ordered = {
        "name": field["name"], "question": field["question"], "created": field["created"], "cores": field["cores"],
        "include": field["include"], "exclude": field["exclude"], "anchors": field["anchors"],
        "entities": [{k: v for k, v in e.items() if v} for e in field.get("entities", [])],
        "budget": field["budget"], "intervals": field["intervals"], "stopping": field["stopping"],
        "allowance": field["allowance"],
    }
    if field.get("notes"):
        ordered["notes"] = field["notes"]
    queries = []
    for q in field["queries"]:
        item = {"id": q["id"], "core": q["core"], "facet": q["facet"], "query": q["query"]}
        if q["filters"]:
            item["filters"] = q["filters"]
        item["limit"] = q["limit"]
        item["update"] = q["update"]
        if q.get("note"):
            item["note"] = q["note"]
        queries.append(item)
    ordered["queries"] = queries
    return "\n".join(dump_yaml(ordered)) + "\n"


class Field:
    def __init__(self, directory: Path):
        self.dir = directory
        self.path = directory / "field.yaml"
        if not self.path.is_file():
            raise LedgerError(f"no field.yaml in {directory}")
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LedgerError(f"cannot read {self.path}: {exc}") from None
        data = parse_yaml(text, str(self.path))
        self.data, errors = normalise_field(data, str(self.path))
        if errors:
            raise LedgerError("field.yaml has problems:\n  " + "\n  ".join(errors))
        for sub in ("ledger", "log"):
            (directory / sub).mkdir(parents=True, exist_ok=True)

    def save(self) -> None:
        self.path.write_text(field_to_yaml(self.data), encoding="utf-8")

    @property
    def name(self) -> str:
        return self.data["name"]

    @property
    def cores(self) -> list[str]:
        return self.data["cores"]

    @property
    def queries(self) -> list[dict]:
        return self.data["queries"]

    def query(self, qid: str) -> dict:
        for q in self.queries:
            if q["id"] == qid:
                return q
        raise LedgerError(f"query {qid!r} is not in the plan; add it with add-query first")

    def queries_for(self, core_name: str, update_only: bool = False) -> list[dict]:
        return [q for q in self.queries if q["core"] == core_name and (q["update"] or not update_only)]

    def next_query_id(self, core_name: str) -> str:
        letter = CORE_LETTER[core_name]
        numbers = [int(m.group(1)) for q in self.queries if (m := re.fullmatch(letter + r"(\d+)", q["id"]))]
        return f"{letter}{(max(numbers) + 1) if numbers else 1:02d}"

    # ---- files ----
    def ledger_path(self, core_name: str) -> Path:
        return self.dir / "ledger" / f"{core_name}.csv"

    def log_path(self, name: str) -> Path:
        return self.dir / "log" / name

    def xlsx_path(self) -> Path:
        return self.dir / f"{self.name}.xlsx"

    def html_path(self) -> Path:
        return self.dir / f"{self.name}-landscape.html"

    def briefing_path(self) -> Path:
        return self.dir / f"{self.name}-briefing.md"


# --------------------------------------------------------------------------
# CSV files with a lock
# --------------------------------------------------------------------------

class Lock:
    """An exclusive lock on <field>/.lock, so parallel sessions never interleave writes."""

    def __init__(self, directory: Path):
        self.path = directory / ".lock"
        self.handle = None

    def __enter__(self) -> "Lock":
        self.handle = open(self.path, "a+")
        try:
            import fcntl
        except ImportError:  # Windows
            import msvcrt
            import time
            while True:
                try:
                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
        else:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: Any) -> None:
        if not self.handle:
            return
        try:
            try:
                import fcntl
            except ImportError:
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def read_csv(path: Path, columns: tuple | list) -> list[dict]:
    if not path.is_file():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = []
        for row in reader:
            rows.append({c: (row.get(c) or "") for c in columns})
        return rows


def write_csv(path: Path, columns: tuple | list, rows: list[dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})
    os.replace(tmp, path)


def append_csv(path: Path, columns: tuple | list, row: dict) -> None:
    new = not path.is_file()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow({c: row.get(c, "") for c in columns})


def load_ledger(field: Field, core: Core) -> dict[str, dict]:
    rows = read_csv(field.ledger_path(core.name), core.ledger_columns)
    ledger = {}
    for r in rows:
        if not r["amassId"]:
            continue
        if not r.get("observed"):  # ledgers written before this column existed: non-empty columns count as observed
            r["observed"] = SEP.join(c for c in core.columns if r.get(c))
        ledger[r["amassId"]] = r
    return ledger


def save_ledger(field: Field, core: Core, ledger: dict[str, dict]) -> None:
    write_csv(field.ledger_path(core.name), core.ledger_columns, [ledger[k] for k in sorted(ledger)])


def load_runs(field: Field) -> list[dict]:
    return read_csv(field.log_path("runs.csv"), RUN_COLUMNS)


def save_runs(field: Field, rows: list[dict]) -> None:
    write_csv(field.log_path("runs.csv"), RUN_COLUMNS, rows)


def load_queries_log(field: Field) -> list[dict]:
    return read_csv(field.log_path("queries.csv"), QUERY_LOG_COLUMNS)


def load_changes(field: Field) -> list[dict]:
    return read_csv(field.log_path("changes.csv"), CHANGE_COLUMNS)


def load_candidates(field: Field) -> list[dict]:
    return read_csv(field.log_path("candidates.csv"), CANDIDATE_COLUMNS)


def save_candidates(field: Field, rows: list[dict]) -> None:
    write_csv(field.log_path("candidates.csv"), CANDIDATE_COLUMNS, rows)


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------

def utc_today() -> dt.date:
    override = os.environ.get("LEDGER_TODAY")  # tests and replays
    if override:
        return dt.date.fromisoformat(override)
    return dt.datetime.now(dt.timezone.utc).date()


def now_iso() -> str:
    override = os.environ.get("LEDGER_TODAY")
    if override:
        return f"{override}T00:00:00Z"
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date(text: str) -> dt.date:
    return dt.date.fromisoformat(text[:10])


def credits_of(field: Field, kind: str, n: int = 1) -> float:
    return CREDITS[kind] * n * field.data["budget"]["factor"]


def fmt_credits(value: float) -> str:
    return f"{value:g}"


# --------------------------------------------------------------------------
# Row normalisation
# --------------------------------------------------------------------------

SEP = " | "


def _join(items: list[str]) -> str:
    return SEP.join(x for x in items if x)


def _split(text: str) -> list[str]:
    return [x for x in text.split(SEP) if x] if text else []


def _str_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value).strip()


def normalise_value(col: Col, value: Any, where: str, errors: list[str]) -> str:
    """Turn a JSON value into the string stored in the ledger, validating it on the way."""
    if value is None:
        return ""
    kind = col.kind
    if kind in ("str", "text"):
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            errors.append(f"{where}: {col.name} must be a string")
            return ""
        return re.sub(r"\s+", " ", str(value)).strip()
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            errors.append(f"{where}: {col.name} must be an integer")
            return ""
        try:
            return str(int(float(value)))
        except ValueError:
            errors.append(f"{where}: {col.name} must be an integer, got {value!r}")
            return ""
    if kind in ("float", "loeuf"):
        if isinstance(value, dict):  # the whole gnomadConstraint object
            value = ((value.get("lossOfFunction") or {}).get("loeuf"))
            if value is None:
                return ""
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            errors.append(f"{where}: {col.name} must be a number")
            return ""
        try:
            return f"{float(value):.3g}"
        except ValueError:
            errors.append(f"{where}: {col.name} must be a number, got {value!r}")
            return ""
    if kind in ("bool", "essential"):
        if isinstance(value, dict):  # the whole depmapEssentiality object
            value = value.get("isEssential")
            if value is None:
                return ""
        if isinstance(value, str) and value.lower() in ("true", "false"):
            value = value.lower() == "true"
        if not isinstance(value, bool):
            errors.append(f"{where}: {col.name} must be true or false")
            return ""
        return "true" if value else "false"
    if kind == "date":
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", value.strip()[:10]):
            errors.append(f"{where}: {col.name} must be an ISO date, got {value!r}")
            return ""
        return value.strip()[:10]
    if kind == "enum":
        if not isinstance(value, str) or value.strip().upper() not in col.values:
            errors.append(f"{where}: {col.name} must be one of {', '.join(col.values)}; got {value!r}")
            return ""
        return value.strip().upper()
    if kind in ("list", "listenum", "ids"):
        items = value if isinstance(value, list) else [value]
        out = []
        for item in items:
            if item is None:
                continue
            if not isinstance(item, (str, int, float)) or isinstance(item, bool):
                errors.append(f"{where}: {col.name} must be a list of strings")
                return ""
            s = re.sub(r"\s+", " ", str(item)).strip()
            if kind == "listenum" and s.upper() not in col.values:
                errors.append(f"{where}: {col.name} values must be in {', '.join(col.values)}; got {s!r}")
                return ""
            out.append(s.upper() if kind == "listenum" else s)
        return _join(out)
    if kind == "authors":
        if isinstance(value, str) and ";" in value:
            value = value.split(";")  # a pre-joined list; names may hold commas, never semicolons
        items = value if isinstance(value, list) else [value]
        names = [re.sub(r"\s+", " ", str(x)).strip() for x in items if x]
        names = [n for n in names if n and not re.fullmatch(r"\+\d+", n)]
        head = names[:5]
        if len(names) > 5:
            head.append(f"+{len(names) - 5}")
        return "; ".join(head)
    if kind == "byagency":
        items = value if isinstance(value, list) else [value]
        out = []
        for item in items:
            if isinstance(item, dict):
                out.append(" ".join(_str_of(item.get(k)) for k in ("amassId", "agency", "authorizationStatus") if item.get(k)))
            elif isinstance(item, str):
                out.append(item.strip())
            else:
                errors.append(f"{where}: {col.name} items must be objects or strings")
                return ""
        return _join(out)
    if kind == "designations":
        items = value if isinstance(value, list) else [value]
        out = []
        for item in items:
            if isinstance(item, dict):
                out.append(" ".join(_str_of(item.get(k)) for k in ("agency", "type") if item.get(k)))
            elif isinstance(item, str):
                out.append(item.strip())
            else:
                errors.append(f"{where}: {col.name} items must be objects or strings")
                return ""
        return _join(out)
    if kind == "safety":
        items = value if isinstance(value, list) else [value]
        out = []
        for item in items:
            if isinstance(item, dict):
                out.append(_str_of(item.get("event")))
            elif isinstance(item, str):
                out.append(item.strip())
        return _join(sorted(set(x for x in out if x)))
    if kind == "mechanisms":
        items = value if isinstance(value, list) else [value]
        out = []
        for item in items:
            if isinstance(item, dict):
                symbols = ", ".join(_str_of(t.get("symbol") or t.get("ensemblId")) for t in (item.get("targets") or [])
                                    if isinstance(t, dict))
                out.append(f"{_str_of(item.get('actionType')) or 'UNKNOWN'} on {symbols or 'no target'}")
            elif isinstance(item, str):
                out.append(item.strip())
        return _join(out)
    if kind == "tractability":
        if isinstance(value, str):
            return value.strip()
        if not isinstance(value, dict):
            errors.append(f"{where}: {col.name} must be the tractability object or a string")
            return ""
        parts = []
        for modality in ("smallMolecule", "antibody", "protac", "otherClinical"):
            lane = value.get(modality) or {}
            clinical = lane.get("clinical") if isinstance(lane, dict) else None
            if clinical:
                parts.append(f"{modality}: {', '.join(str(x) for x in clinical)}")
        return _join(parts)
    if kind == "targetclass":
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            value = value.get("path") or []
        if isinstance(value, list):
            return " > ".join(str(x) for x in value)
        errors.append(f"{where}: {col.name} must be the targetClass object, its path list or a string")
        return ""
    if kind == "links":
        if not isinstance(value, dict):
            errors.append(f"{where}: links must be an object mapping core -> list of Amass ids")
            return ""
        parts = []
        for core_name in sorted(value):
            ids = value[core_name]
            if core_name not in CORES or not isinstance(ids, list):
                errors.append(f"{where}: links.{core_name} must be a list of ids of a known core")
                return ""
            parts.append(f"{core_name}:{len(ids)}")
        return _join(parts)
    errors.append(f"{where}: unsupported column kind {kind}")
    return ""


def normalise_row(core: Core, raw: Any, where: str, errors: list[str]) -> dict:
    """Validate one incoming row. Returns {"amassId", "decision", "reason", "fields": {...}, "links": {...}}."""
    if not isinstance(raw, dict):
        errors.append(f"{where}: each row must be an object")
        return {}
    amass_id = raw.get("amassId")
    if not core.valid_id(amass_id):
        errors.append(f"{where}: amassId must be a {core.label} id starting with {core.prefix}; got {amass_id!r}")
        return {}
    decision = raw.get("decision")
    if decision is not None and decision not in DECISIONS:
        errors.append(f"{where} ({amass_id}): decision must be in, out or unsure")
    reason = raw.get("reason")
    if reason is not None and not isinstance(reason, str):
        errors.append(f"{where} ({amass_id}): reason must be a string")
        reason = None
    if decision in ("out", "unsure") and not (reason and reason.strip()):
        errors.append(f"{where} ({amass_id}): decision {decision} needs a short reason")
    unknown = sorted(set(raw) - set(core.columns) - {"amassId", "decision", "reason"})
    if unknown:
        errors.append(f"{where} ({amass_id}): unknown keys {unknown}; {core.label} columns are "
                      f"{', '.join(core.columns)}")
    fields: dict[str, str] = {}
    links: dict[str, list[str]] = {}
    for name, value in raw.items():
        col = core.columns.get(name)
        if col is None:
            continue
        fields[name] = normalise_value(col, value, f"{where} ({amass_id})", errors)
        if col.kind == "links" and isinstance(value, dict):
            for core_name, ids in value.items():
                if core_name in CORES and isinstance(ids, list):
                    good = [x for x in ids if CORES[core_name].valid_id(x)]
                    if len(good) != len(ids):
                        errors.append(f"{where} ({amass_id}): links.{core_name} holds ids that are not {CORES[core_name].prefix} ids")
                    links[core_name] = good
    return {"amassId": amass_id, "decision": decision, "reason": (reason or "").strip()[:300], "fields": fields,
            "links": links}


def set_delta(old: str, new: str) -> str:
    before, after = set(_split(old)), set(_split(new))
    added = sorted(after - before)
    removed = sorted(before - after)
    parts = []
    if added:
        parts.append("+" + ", ".join(added))
    if removed:
        parts.append("-" + ", ".join(removed))
    return "; ".join(parts)


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------

def open_run(field: Field) -> tuple[str, list[dict]] | tuple[None, list]:
    runs = load_runs(field)
    open_rows = [r for r in runs if not r["finishedAt"]]
    if not open_rows:
        return None, []
    return open_rows[0]["runId"], open_rows


def last_checked(field: Field) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in load_runs(field):
        if r["finishedAt"]:
            out[r["core"]] = max(out.get(r["core"], ""), r["finishedAt"][:10])
    return out


def credits_spent(field: Field) -> dict[str, float]:
    out = {"baseline": 0.0, "update": 0.0, "total": 0.0}
    for r in load_runs(field):
        c = float(r["credits"] or 0)
        out[r["mode"]] = out.get(r["mode"], 0.0) + c
        out["total"] += c
    return out


def due_table(field: Field, force: bool = False) -> list[dict]:
    today = utc_today()
    checked = last_checked(field)
    rows = []
    for core_name in field.cores:
        interval = field.data["intervals"][core_name]
        last = checked.get(core_name)
        if last:
            age = (today - parse_date(last)).days
            due = age >= interval
            reason = f"last checked {last}, {age} day(s) ago, interval {interval}"
        else:
            age, due = None, True
            reason = f"never checked (no finished run), interval {interval}"
        since = (parse_date(last) if last else today - dt.timedelta(days=interval)) - dt.timedelta(days=UPDATE_LOOKBACK_DAYS)
        rows.append({"core": core_name, "interval": interval, "last": last or "", "age": age, "due": due or force,
                     "forced": force and not due, "reason": reason, "since": since.isoformat()})
    return rows


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_validate(args: argparse.Namespace) -> int:
    field = Field(Path(args.field))
    d = field.data
    print(f"field {d['name']}: {d['question']}")
    print(f"cores: {', '.join(d['cores'])}")
    print(f"include: {len(d['include'])} criteria, exclude: {len(d['exclude'])}, anchors: {len(d['anchors'])}")
    print(f"budget: baseline {d['budget']['baseline']}, update {d['budget']['update']}" + (f", multiplier {d['budget']['factor']}" if d["budget"]["factor"] != 1 else ""))
    print("intervals: " + ", ".join(f"{c} {n}d" for c, n in d["intervals"].items()))
    print(f"stopping: {d['stopping']['consecutive']} consecutive queries adding < {d['stopping']['minNew']} new in-scope")
    per_core = {c: len(field.queries_for(c)) for c in d["cores"]}
    upd = {c: len(field.queries_for(c, update_only=True)) for c in d["cores"]}
    print("queries: " + ", ".join(f"{c} {n} ({upd[c]} in update subset)" for c, n in per_core.items()))
    missing = [c for c, n in per_core.items() if n == 0 and c != "genecore"]
    if missing:
        print(f"note: no queries yet for {', '.join(missing)}")
    over = [c for c, n in per_core.items() if n > d["allowance"][c]]
    print("allowance (planned searches / allowed): " + ", ".join(f"{c} {per_core[c]}/{d['allowance'][c]}" for c in d["cores"]))
    if over:
        print(f"warning: the plan exceeds the allowance for {', '.join(over)}; raise it with `allowance --breadth` only if the field is broader than assumed")
    print("ok")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    field = Field(Path(args.field))
    factor = field.data["budget"]["factor"]
    rows = []
    total_credits = 0.0
    total_tokens = 0
    print(f"{'id':6} {'core':15} {'facet':12} {'limit':>5} {'upd':3} query / filters")
    for q in field.queries:
        filt = " ".join(f"{k}={v}" for k, v in q["filters"].items())
        print(f"{q['id']:6} {q['core']:15} {q['facet'][:12]:12} {q['limit']:>5} {'y' if q['update'] else '-':3} {q['query']}"
              + (f"  [{filt}]" if filt else ""))
        total_credits += CREDITS["search"] * factor
        total_tokens += q["limit"] * TOKENS_PER_RESULT[q["core"]]
        rows.append(q)
    anchors_fetch = len(field.data["anchors"])
    snowball = 5 * len([c for c in field.cores if c in ("drugcore", "trialcore")])
    reserve = CREDITS["fetch"] * (anchors_fetch + snowball) * factor
    print()
    print(f"planned searches: {len(rows)} -> {fmt_credits(total_credits)} of the run cap")
    print(f"fetch reserve (anchors {anchors_fetch} + snowball {snowball}): {fmt_credits(reserve)}")
    expansion = round(total_credits * 0.3)
    print(f"expansion reserve (30% of searches): {expansion}")
    estimate = total_credits + reserve + expansion
    print(f"estimate: about {fmt_credits(estimate)} of the run cap of {field.data['budget']['baseline']} (search 2, fetch 1)")
    print(f"context: about {total_tokens // 1000}k tokens of search results if run in one context; fan out per Core or work in rounds")
    if estimate > field.data["budget"]["baseline"]:
        print("warning: the estimate exceeds the run cap (budget.baseline); trim the plan")
    return 0


def cmd_start_run(args: argparse.Namespace) -> int:
    field = Field(Path(args.field))
    with Lock(field.dir):
        run_id, open_rows = open_run(field)
        if run_id:
            cores_open = ", ".join(r["core"] for r in open_rows)
            raise LedgerError(f"run {run_id} is still open for {cores_open}: finish-core / finish-run it, or abandon-run")
        runs = load_runs(field)
        n = len({r["runId"] for r in runs if r["mode"] == args.mode}) + 1
        run_id = f"{args.mode}-{n}"
        wanted = args.core or field.cores
        bad = [c for c in wanted if c not in field.cores]
        if bad:
            raise LedgerError(f"not in this field's cores: {bad}")
        new_rows = []
        if args.mode == "baseline":
            for core_name in wanted:
                new_rows.append({"runId": run_id, "mode": "baseline", "core": core_name, "startedAt": now_iso(),
                                 "finishedAt": "", "since": "", "queries": "0", "credits": "0", "newRecords": "0",
                                 "changes": "0", "unchangedKnown": "0", "note": ""})
            print(f"opened {run_id} for {', '.join(wanted)}")
            for core_name in wanted:
                ids = [q["id"] for q in field.queries_for(core_name)]
                print(f"  {core_name}: {len(ids)} planned queries" + (f": {' '.join(ids)}" if ids else ""))
        else:
            table = due_table(field, force=args.force)
            print(f"{'core':15} {'interval':>8} {'last checked':13} verdict")
            for row in table:
                if row["core"] not in wanted:
                    continue
                verdict = "DUE" if row["due"] else "skip"
                if row["forced"]:
                    verdict = "DUE (forced)"
                print(f"{row['core']:15} {row['interval']:>7}d {row['last'] or '-':13} {verdict}: {row['reason']}")
                if row["due"]:
                    new_rows.append({"runId": run_id, "mode": "update", "core": row["core"], "startedAt": now_iso(),
                                     "finishedAt": "", "since": row["since"], "queries": "0", "credits": "0",
                                     "newRecords": "0", "changes": "0", "unchangedKnown": "0", "note": ""})
            if not new_rows:
                print("nothing is due; use --force to run anyway")
                return 0
            print(f"opened {run_id} for {', '.join(r['core'] for r in new_rows)}")
            factor = field.data["budget"]["factor"]
            for row in new_rows:
                core_name = row["core"]
                core = CORES[core_name]
                qs = field.queries_for(core_name, update_only=True)
                ledger = load_ledger(field, core)
                in_scope = sum(1 for r in ledger.values() if r["decision"] == "in")
                if core_name == "patentcore":
                    print(f"  {core_name}: since {row['since']}, one pass (published) over {len(qs)} queries: "
                          f"{fmt_credits(CREDITS['search'] * len(qs) * factor)} of the run cap. No Amass dates on PatentCore search: "
                          "this finds newly published patents only.")
                    continue
                by_search = CREDITS["search"] * len(qs) * factor
                by_fetch = CREDITS["fetch"] * in_scope * factor
                print(f"  {core_name}: since {row['since']}; pass created over {len(qs)} queries = {fmt_credits(by_search)} of the run cap; "
                      f"pass updated by search = {fmt_credits(by_search)} or by re-fetching {in_scope} in-scope records = "
                      f"{fmt_credits(by_fetch)} -> " + ("re-fetch" if by_fetch < by_search else "search"))
        save_runs(field, runs + new_rows)
    return 0


def cmd_abandon_run(args: argparse.Namespace) -> int:
    field = Field(Path(args.field))
    with Lock(field.dir):
        run_id, open_rows = open_run(field)
        if not run_id:
            print("no open run")
            return 0
        runs = [r for r in load_runs(field) if not (r["runId"] == run_id and not r["finishedAt"])]
        save_runs(field, runs)
        print(f"abandoned {run_id} ({', '.join(r['core'] for r in open_rows)}); its queries stay in the log")
    return 0


def _read_rows_from_stdin() -> list:
    text = sys.stdin.read()
    if not text.strip():
        raise LedgerError("no JSON on stdin")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LedgerError(f"stdin is not valid JSON: {exc}") from None
    if isinstance(data, dict):
        if "rows" in data:
            data = data["rows"]
        elif "results" in data:
            raise LedgerError("pass extracted rows, not the raw tool result: one object per record with amassId, "
                              "decision, reason and the tracked columns")
        else:
            data = [data]
    if not isinstance(data, list):
        raise LedgerError("stdin must be a JSON list of rows")
    return data


def _read_json_stdin(what: str) -> Any:
    text = sys.stdin.read()
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LedgerError(f"{what} on stdin is not valid JSON: {exc}") from None


def cmd_ingest(args: argparse.Namespace) -> int:
    field = Field(Path(args.field))
    if args.raw:
        raw_path = Path(args.raw)
        try:
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LedgerError(f"cannot read the raw tool result {raw_path}: {exc}") from None
        decisions = _read_json_stdin("decisions")
        if not isinstance(decisions, dict):
            raise LedgerError("with --raw, stdin holds a JSON object mapping amassId -> decision, [decision, reason] or {decision, reason}")
        default = None
        if args.default:
            if args.default[0] not in DECISIONS and args.default[0] != "drop":
                raise LedgerError("--default takes a decision (in, out, unsure or drop) and a reason")
            default = (args.default[0], args.default[1])
        if args.query_id:
            raw_core = CORES[field.query(args.query_id)["core"]]
        else:
            if isinstance(raw, dict) and "data" in raw:
                first = raw["data"]
            elif isinstance(raw, dict):
                first = (raw.get("results") or [None])[0]
            else:
                first = None
            first_id = first.get("amassId") if isinstance(first, dict) else None
            raw_core = core_for_id(first_id or "")
            if raw_core is None:
                raise LedgerError("cannot tell the Core of the raw result; pass --query-id or check the file")
        raw_rows = rows_from_raw(raw_core, raw, decisions, default)
        returned_override = len(raw_rows)
        if default and default[0] == "drop":
            known_ids = set(load_ledger(field, raw_core))
            kept = []
            for r in raw_rows:
                if r.get("decision") == "drop":
                    if r["amassId"] in known_ids:
                        r.pop("decision")  # known record: update its columns, keep its decision
                        kept.append(r)
                    continue  # unknown filler: counted in returned, not stored
                kept.append(r)
            raw_rows = kept
    else:
        raw_rows = _read_rows_from_stdin()
        returned_override = None
    if args.returned is not None:
        returned_override = args.returned
    with Lock(field.dir):
        run_id, open_rows = open_run(field)
        if not run_id:
            raise LedgerError("no open run: start-run first")
        if args.run and args.run != run_id:
            raise LedgerError(f"the open run is {run_id}, not {args.run}")
        mode = open_rows[0]["mode"]
        if args.query_id:
            q = field.query(args.query_id)
            core = CORES[q["core"]]
            kind, tool, query_id, limit = "search", core.search_tool, q["id"], q["limit"]
            params = dict(q["filters"])
        else:
            kind, query_id, limit, params = "fetch", f"fetch", 0, {}
            core = None
            for r in raw_rows:
                if isinstance(r, dict) and core_for_id(r.get("amassId", "")):
                    core = core_for_id(r["amassId"])
                    break
            if core is None:
                raise LedgerError("fetch rows need Amass ids with a known prefix")
            tool = core.fetch_tool
        run_row = next((r for r in open_rows if r["core"] == core.name), None)
        if run_row is None:
            raise LedgerError(f"run {run_id} is not open for {core.name} (open: {', '.join(r['core'] for r in open_rows)})")
        pass_name = args.pass_name or ""
        since = ""
        if pass_name:
            if mode != "update":
                raise LedgerError("--pass is for update runs")
            if pass_name == "published" and core.name != "patentcore":
                raise LedgerError("--pass published is for PatentCore only")
            if pass_name != "published" and core.name == "patentcore":
                raise LedgerError("PatentCore takes --pass published")
            since = run_row["since"]
            if kind == "search":
                params[PASS_FILTER[pass_name]] = since
        elif mode == "update" and kind == "search":
            print("note: no --pass given in an update run; new records will be classed newly-matched", file=sys.stderr)

        errors: list[str] = []
        rows = []
        seen: set[str] = set()
        for n, raw in enumerate(raw_rows, 1):
            row = normalise_row(core, raw, f"row {n}", errors)
            if row and row["amassId"] in seen:
                errors.append(f"row {n}: {row['amassId']} appears twice")
            if row:
                seen.add(row["amassId"])
                rows.append(row)
        if errors:
            raise LedgerError("rows rejected, nothing written:\n  " + "\n  ".join(errors[:40]))

        ledger = load_ledger(field, core)
        today = utc_today().isoformat()
        query_ref = f"{query_id}@{pass_name}" if pass_name else query_id  # rows remember the pass that found them
        changes: list[dict] = []
        candidates = load_candidates(field)
        candidate_ids = {(c["core"], c["amassId"]): c for c in candidates}
        counts = {"new": 0, "in": 0, "out": 0, "unsure": 0, "changed": 0, "unchanged": 0, "newInScope": 0}
        for row in rows:
            amass_id = row["amassId"]
            existing = ledger.get(amass_id)
            label = row["fields"].get(core.title_column) or (existing or {}).get(core.title_column, "") if core.title_column else ""
            if existing is None:
                if row["decision"] is None:
                    raise LedgerError(f"{amass_id} is new to the ledger and needs a decision (in, out or unsure)")
                new_row = {c: "" for c in core.ledger_columns}
                new_row.update({"amassId": amass_id, "decision": row["decision"], "reason": row["reason"],
                                "source": kind, "firstSeen": today, "firstQuery": query_ref, "lastSeen": today,
                                "lastQuery": query_ref, "seenCount": "1",
                                "observed": SEP.join(c for c in core.columns if c in row["fields"])})
                new_row.update(row["fields"])
                ledger[amass_id] = new_row
                counts["new"] += 1
                counts[row["decision"]] += 1
                if row["decision"] == "in":
                    counts["newInScope"] += 1
                if mode == "update" and row["decision"] != "out":  # filler is not news about the field
                    cls = NEW_TO_AMASS if pass_name == "created" else NEWLY_MATCHED
                    changes.append({"runId": run_id, "date": today, "core": core.name, "amassId": amass_id,
                                    "label": label[:160], "class": cls, "field": "", "old": "",
                                    "new": row["decision"], "group": "new", "queryId": query_id})
            else:
                diffs = []
                observed = set(_split(existing.get("observed", "")))
                for name, new_value in row["fields"].items():
                    col = core.columns[name]
                    old_value = existing.get(name, "")
                    first_time = name not in observed
                    observed.add(name)
                    if old_value == new_value:
                        continue
                    if first_time:
                        existing[name] = new_value  # first observation of a column is not a change
                        continue
                    if col.is_set:
                        same = set(_split(old_value)) == set(_split(new_value))
                        if same:
                            continue
                        shown_old, shown_new = old_value, new_value + "  [" + set_delta(old_value, new_value) + "]"
                    else:
                        shown_old, shown_new = old_value, new_value
                    diffs.append((name, shown_old, shown_new, col.group))
                    existing[name] = new_value
                if diffs:
                    counts["changed"] += 1
                    for name, old_value, new_value, group in diffs:
                        changes.append({"runId": run_id, "date": today, "core": core.name, "amassId": amass_id,
                                        "label": label[:160], "class": CHANGED, "field": name, "old": old_value[:500],
                                        "new": new_value[:500], "group": group, "queryId": query_id})
                else:
                    counts["unchanged"] += 1
                if row["decision"] and row["decision"] != existing["decision"]:
                    changes.append({"runId": run_id, "date": today, "core": core.name, "amassId": amass_id,
                                    "label": label[:160], "class": DECISION_CHANGED, "field": "decision",
                                    "old": existing["decision"], "new": row["decision"], "group": "decision",
                                    "queryId": query_id})
                    existing["decision"] = row["decision"]
                    if row["reason"]:
                        existing["reason"] = row["reason"]
                elif row["reason"] and not existing["reason"]:
                    existing["reason"] = row["reason"]
                existing["observed"] = SEP.join(c for c in core.columns if c in observed)
                if kind == "search" and existing["source"] == "fetch":
                    existing["source"] = "search"
                existing["lastSeen"] = today
                existing["lastQuery"] = query_ref
                existing["seenCount"] = str(int(existing["seenCount"] or 0) + 1)
            # candidates resolved by this ingest
            key = (core.name, amass_id)
            if key in candidate_ids and candidate_ids[key]["status"] == "open":
                candidate_ids[key]["status"] = "resolved"
            # links -> candidates for other cores
            for core_name, ids in row["links"].items():
                other = CORES[core_name]
                other_ledger = ledger if core_name == core.name else load_ledger(field, other)
                for linked in ids:
                    if linked in other_ledger:
                        continue
                    k2 = (core_name, linked)
                    if k2 not in candidate_ids:
                        cand = {"date": today, "runId": run_id, "core": core_name, "amassId": linked,
                                "source": amass_id, "status": "open", "note": f"linked from {core.name} {amass_id}"}
                        candidates.append(cand)
                        candidate_ids[k2] = cand

        save_ledger(field, core, ledger)
        if candidates:
            save_candidates(field, candidates)
        for ch in changes:
            append_csv(field.log_path("changes.csv"), CHANGE_COLUMNS, ch)
        returned = returned_override if returned_override is not None else len(rows)
        cap_hit = kind == "search" and returned >= limit
        credits = credits_of(field, kind, 1 if kind == "search" else returned)
        append_csv(field.log_path("queries.csv"), QUERY_LOG_COLUMNS, {
            "timestamp": now_iso(), "runId": run_id, "mode": mode, "core": core.name, "queryId": query_id, "kind": kind,
            "tool": tool, "pass": pass_name, "since": since, "params": json.dumps(params, ensure_ascii=False),
            "limit": limit, "returned": returned, "capHit": "true" if cap_hit else "false", "newIds": counts["new"],
            "inCount": counts["in"], "outCount": counts["out"], "unsureCount": counts["unsure"],
            "changedRows": counts["changed"], "unchangedKnown": counts["unchanged"], "credits": fmt_credits(credits),
            "note": args.note or "",
        })
        runs = load_runs(field)
        for r in runs:
            if r["runId"] == run_id and r["core"] == core.name:
                r["queries"] = str(int(r["queries"] or 0) + 1)
                r["credits"] = fmt_credits(float(r["credits"] or 0) + credits)
                r["newRecords"] = str(int(r["newRecords"] or 0) + counts["new"])
                r["changes"] = str(int(r["changes"] or 0) + len(changes))
                r["unchangedKnown"] = str(int(r["unchangedKnown"] or 0) + counts["unchanged"])
        save_runs(field, runs)

    run_credits = sum(float(r["credits"] or 0) for r in runs if r["runId"] == run_id)
    budget = field.data["budget"][mode]
    head = f"{query_id} {core.name}"
    if kind == "search":
        head += f" ({pass_name} pass)" if pass_name else ""
        head += f": returned {returned}" + (" (CAP HIT)" if cap_hit else "")
    else:
        head += f": {returned} record(s) fetched"
    print(f"{head}; new {counts['new']} (in {counts['in']}, out {counts['out']}, unsure {counts['unsure']}); "
          f"known {counts['changed'] + counts['unchanged']} (changed {counts['changed']}, unchanged {counts['unchanged']})")
    if mode == "update" and changes:
        classes: dict[str, int] = {}
        for ch in changes:
            classes[ch["class"]] = classes.get(ch["class"], 0) + 1
        print("  changes: " + ", ".join(f"{k} {v}" for k, v in classes.items()))
    if cap_hit and pass_name:
        print("  cap hit on a dated pass: the window holds more than one page of records matching loosely; in-scope ones "
              "rank first, the tail is filler. If many KNOWN in-scope records came back rewritten, that is a bulk "
              "refresh: re-fetch the in-scope records when there are few, otherwise split the query by facet "
              "(SKILL.md, Update), and say which you did.")
    elif cap_hit:
        print("  cap hit: the field is wider than this query; add narrower queries for this facet")
    if kind == "search" and args.query_id:
        q = field.query(args.query_id)
        verdict = facet_verdict(field, core.name, q["facet"])
        if verdict:
            print(f"  facet {q['facet']}: {verdict}")
        if mode == "baseline":
            used = sum(1 for e in load_queries_log(field) if e["runId"] == run_id and e["core"] == core.name and e["kind"] == "search")
            allowed = field.data["allowance"].get(core.name, 0)
            print(f"  searches this run for {core.name}: {used} of {allowed} allowed" + ("; allowance reached, finish the Core" if used >= allowed else ""))
    open_cands = sum(1 for c in candidates if c["status"] == "open" and c["core"] in field.cores)
    if open_cands:
        print(f"  open candidates (linked ids not in any ledger): {open_cands}")
    remaining = budget - run_credits
    print(f"  run cap: {fmt_credits(run_credits)} of {budget} used ({mode})")
    if remaining < 0:
        print("  RUN CAP REACHED: make no more calls. Close the Cores (finish-core, then finish-run), write the "
              "briefing and report what is mapped; the field can be extended in a later run.")
        return 2
    return 0


def facet_history(field: Field, core_name: str, facet: str) -> list[dict]:
    ids = {q["id"]: q for q in field.queries if q["core"] == core_name and q["facet"] == facet}
    out = []
    for entry in load_queries_log(field):
        if entry["kind"] == "search" and entry["queryId"] in ids and not entry["pass"]:
            out.append({"id": entry["queryId"], "returned": int(entry["returned"] or 0),
                        "new": int(entry["newIds"] or 0), "newInScope": int(entry["inCount"] or 0),
                        "capHit": entry["capHit"] == "true"})
    return out


def facet_verdict(field: Field, core_name: str, facet: str) -> str:
    hist = facet_history(field, core_name, facet)
    if not hist:
        return ""
    k, n = field.data["stopping"]["consecutive"], field.data["stopping"]["minNew"]
    recent = [h["newInScope"] for h in hist][-k:]
    text = "new in-scope per query " + ", ".join(str(h["newInScope"]) for h in hist)
    if len(hist) >= k and all(x < n for x in recent):
        return text + f" -> saturated (last {k} added < {n})"
    return text + " -> still producing"


def cmd_add_query(args: argparse.Namespace) -> int:
    with Lock(Path(args.field)):  # load inside the lock: two sessions may add queries at once
        field = Field(Path(args.field))
        core_name = args.core
        if core_name not in CORES:
            raise LedgerError(f"unknown core {core_name}")
        qid = args.id or field.next_query_id(core_name)
        filters: dict[str, Any] = {}
        for item in args.filter or []:
            if "=" not in item:
                raise LedgerError(f"--filter takes name=value, got {item!r}")
            name, value = item.split("=", 1)
            if "," in value:
                parsed: Any = [v.strip() for v in value.split(",")]
            elif value.lower() in ("true", "false"):
                parsed = value.lower() == "true"
            elif re.fullmatch(r"-?\d+", value):
                parsed = int(value)
            elif re.fullmatch(r"-?\d+\.\d+", value):
                parsed = float(value)
            else:
                parsed = value
            filters[name] = parsed
        planned = len(field.queries_for(core_name))
        allowed = field.data["allowance"].get(core_name, ALLOWANCE["medium"].get(core_name, 8))
        if planned >= allowed and not args.force:
            raise LedgerError(f"{core_name} already has {planned} planned searches, its allowance for this field ({allowed}). "
                              "Finish the Core with what it has; if the field is genuinely broader, set `allowance --breadth broad` "
                              "or pass --force for one more query.")
        new_q = {"id": qid, "core": core_name, "facet": args.facet, "query": args.query, "filters": filters,
                 "limit": args.limit, "update": bool(args.update), "note": args.note or ""}
        data = dict(field.data)
        data["queries"] = field.queries + [new_q]
        normalised, errors = normalise_field(data)
        if errors:
            raise LedgerError("query rejected:\n  " + "\n  ".join(errors))
        field.data = normalised
        field.save()
    print(f"added {qid} ({core_name}, facet {args.facet}): {args.query}" + (f" {filters}" if filters else ""))
    return 0


def cmd_saturation(args: argparse.Namespace) -> int:
    field = Field(Path(args.field))
    k, n = field.data["stopping"]["consecutive"], field.data["stopping"]["minNew"]
    print(f"stopping rule: a facet is saturated when its last {k} queries each added fewer than {n} new in-scope records")
    for core_name in field.cores:
        facets = []
        for q in field.queries_for(core_name):
            if q["facet"] not in facets:
                facets.append(q["facet"])
        if not facets:
            continue
        print(f"\n{core_name}")
        for facet in facets:
            hist = facet_history(field, core_name, facet)
            if not hist:
                print(f"  {facet}: not run yet")
                continue
            line = "  " + " ".join(f"{h['id']}:{h['returned']}/{h['new']}/{h['newInScope']}{'!' if h['capHit'] else ''}" for h in hist)
            print(f"  {facet}: {facet_verdict(field, core_name, facet)}")
            print(line + "   (query:returned/new/newInScope, ! = cap hit)")
    return 0


def _term_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())  # "AMG-510", "AMG 510" and "amg510" are one term


def cmd_terms(args: argparse.Namespace) -> int:
    field = Field(Path(args.field))
    all_query_text = " ".join(_term_key(q["query"]) for q in field.queries)
    found_any = False
    for core_name in field.cores:
        core = CORES[core_name]
        if not core.term_columns:
            continue
        ledger = load_ledger(field, core)
        counts: dict[str, int] = {}
        for row in ledger.values():
            if row["decision"] != "in":
                continue
            for name in core.term_columns:
                col = core.columns[name]
                values = _split(row.get(name, "")) if col.is_set else ([row.get(name, "")] if row.get(name) else [])
                for v in values:
                    v = v.strip()
                    if len(v) < 3 or v.lower() in ("placebo", "saline", "standard of care"):
                        continue
                    counts[v] = counts.get(v, 0) + 1
        uncovered = [(v, c) for v, c in counts.items() if _term_key(v) and _term_key(v) not in all_query_text]
        uncovered.sort(key=lambda x: (-x[1], x[0].lower()))
        if uncovered:
            found_any = True
            print(f"{core_name}: values on in-scope records that no query mentions (count)")
            for v, c in uncovered[: args.top]:
                print(f"  {c:>3}  {v}")
    if not found_any:
        print("every value seen on in-scope records is already mentioned by some query")
    return 0


def anchor_status(field: Field) -> list[dict]:
    out = []
    ledgers: dict[str, dict] = {}
    for a in field.data["anchors"]:
        core = CORES[a["core"]]
        if a["core"] not in ledgers:
            ledgers[a["core"]] = load_ledger(field, core)
        ledger = ledgers[a["core"]]
        wanted = a["id"].strip().lower()
        hit = None
        if a["id"] in ledger:
            hit = ledger[a["id"]]
        else:
            for row in ledger.values():
                if any((row.get(c) or "").strip().lower() == wanted for c in core.id_columns):
                    hit = row
                    break
        if hit is None:
            status = "missing"
        elif hit["source"] == "search":
            status = "found by search"
        else:
            status = "fetched only (plan did not reach it)"
        out.append({**a, "status": status, "amassId": hit["amassId"] if hit else "",
                    "decision": hit["decision"] if hit else ""})
    return out


def cmd_anchors(args: argparse.Namespace) -> int:
    field = Field(Path(args.field))
    rows = anchor_status(field)
    if not rows:
        print("no anchors in field.yaml; add 5 to 10 records that must be in the field before calling the baseline done")
        return 0
    for r in rows:
        print(f"{r['core']:15} {r['id']:28} {r['status']:38} {r['amassId']} {r['decision']}  {r['note']}")
    found = sum(1 for r in rows if r["status"] == "found by search")
    print(f"{found} of {len(rows)} anchors found by search")
    return 0


def cmd_crosscheck(args: argparse.Namespace) -> int:
    field = Field(Path(args.field))
    text = sys.stdin.read()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LedgerError(f"stdin is not valid JSON: {exc}") from None
    if isinstance(data, list):
        if not args.core:
            raise LedgerError("--core is required when stdin is a plain list of ids")
        data = {args.core: data}
    if not isinstance(data, dict):
        raise LedgerError("stdin must be a list of ids or an object mapping core -> ids")
    with Lock(field.dir):
        run_id, _ = open_run(field)
        candidates = load_candidates(field)
        known = {(c["core"], c["amassId"]) for c in candidates}
        today = utc_today().isoformat()
        for core_name, ids in data.items():
            if core_name not in CORES or not isinstance(ids, list):
                raise LedgerError(f"{core_name}: unknown core or ids not a list")
            core = CORES[core_name]
            bad = [x for x in ids if not core.valid_id(x)]
            if bad:
                raise LedgerError(f"{core_name}: not {core.prefix} ids: {bad[:5]}")
            ledger = load_ledger(field, core)
            tally = {"in": 0, "out": 0, "unsure": 0}
            unknown = []
            for amass_id in ids:
                row = ledger.get(amass_id)
                if row:
                    tally[row["decision"]] += 1
                else:
                    unknown.append(amass_id)
                    if (core_name, amass_id) not in known:
                        candidates.append({"date": today, "runId": run_id or "", "core": core_name, "amassId": amass_id,
                                           "source": args.source or "", "status": "open", "note": args.note or ""})
                        known.add((core_name, amass_id))
            total = len(ids)
            covered = total - len(unknown)
            pct = f"{100 * covered / total:.0f}%" if total else "n/a"
            print(f"{core_name}: {total} linked ids, {covered} in the ledger ({pct}; in {tally['in']}, out {tally['out']}, "
                  f"unsure {tally['unsure']}), {len(unknown)} unknown -> candidates")
        save_candidates(field, candidates)
    return 0


def plan_progress(field: Field, run_id: str | None, mode: str | None) -> dict[str, dict]:
    """Per core: planned query ids and which are logged in the given run (baseline) or which passes are done (update)."""
    log = load_queries_log(field)
    out: dict[str, dict] = {}
    for core_name in field.cores:
        if mode == "update":
            planned = [q["id"] for q in field.queries_for(core_name, update_only=True)]
            done: dict[str, set] = {}
            fetches = 0
            refetched = False
            for e in log:
                if e["runId"] == run_id and e["core"] == core_name:
                    if e["kind"] == "search":
                        done.setdefault(e["queryId"], set()).add(e["pass"] or "none")
                    else:
                        fetches += int(e["returned"] or 0)
                        refetched = refetched or e["pass"] == "updated"
            need = ["published"] if core_name == "patentcore" else ["created", "updated"]
            remaining = [f"{qid} ({p})" for qid in planned for p in need
                         if p not in done.get(qid, set()) and not (p == "updated" and refetched)]
            out[core_name] = {"planned": planned, "remaining": remaining, "fetches": fetches}
        else:
            planned = [q["id"] for q in field.queries_for(core_name)]
            logged = {e["queryId"] for e in log if e["kind"] == "search" and (run_id is None or e["runId"] == run_id)}
            out[core_name] = {"planned": planned, "remaining": [q for q in planned if q not in logged], "fetches": 0}
    return out


def ledger_counts(field: Field, core_name: str) -> dict[str, int]:
    ledger = load_ledger(field, CORES[core_name])
    out = {"records": len(ledger), "in": 0, "out": 0, "unsure": 0, "fetched": 0}
    for r in ledger.values():
        out[r["decision"]] = out.get(r["decision"], 0) + 1
        if r["source"] == "fetch":
            out["fetched"] += 1
    return out


def cmd_status(args: argparse.Namespace) -> int:
    field = Field(Path(args.field))
    d = field.data
    print(f"field {d['name']}: {d['question']}")
    spent = credits_spent(field)
    print(f"run cap: baseline {fmt_credits(spent['baseline'])} of {d['budget']['baseline']} used, "
          f"updates {fmt_credits(spent['update'])} (cap {d['budget']['update']} per run), total {fmt_credits(spent['total'])}")
    run_id, open_rows = open_run(field)
    checked = last_checked(field)
    print(f"{'core':15} {'records':>7} {'in':>4} {'out':>4} {'unsure':>6} {'queries':>7} {'last checked':13}")
    for core_name in field.cores:
        c = ledger_counts(field, core_name)
        nq = len(field.queries_for(core_name))
        print(f"{core_name:15} {c['records']:>7} {c['in']:>4} {c['out']:>4} {c['unsure']:>6} {nq:>7} {checked.get(core_name, '-'):13}")
    if run_id:
        mode = open_rows[0]["mode"]
        run_credits = sum(float(r["credits"] or 0) for r in load_runs(field) if r["runId"] == run_id)
        print(f"\nopen run {run_id} ({mode}) for {', '.join(r['core'] for r in open_rows)}; "
              f"{fmt_credits(run_credits)} of its run cap of {d['budget'][mode]} used")
        progress = plan_progress(field, run_id, mode)
        log_now = load_queries_log(field)
        for r in open_rows:
            p = progress[r["core"]]
            rem = p["remaining"]
            extra = f"; {p['fetches']} records re-fetched" if p["fetches"] else ""
            if mode == "baseline":
                used = sum(1 for e in log_now if e["runId"] == run_id and e["core"] == r["core"] and e["kind"] == "search")
                extra += f"; searches {used} of {d['allowance'].get(r['core'], 0)} allowed"
            if rem:
                print(f"  {r['core']}: {len(p['planned'])} planned, next: {' '.join(rem[:12])}" + (" ..." if len(rem) > 12 else "") + extra)
            else:
                print(f"  {r['core']}: all {len(p['planned'])} planned queries logged{extra}; run saturation/terms/anchors, then finish-core")
    else:
        print("\nno open run")
    anchors = anchor_status(field)
    if anchors:
        found = sum(1 for a in anchors if a["status"] == "found by search")
        missing = [a["id"] for a in anchors if a["status"] == "missing"]
        print(f"anchors: {found} of {len(anchors)} found by search" + (f"; missing: {', '.join(missing)}" if missing else ""))
    cands = [c for c in load_candidates(field) if c["status"] == "open"]
    if cands:
        by_core: dict[str, int] = {}
        for c in cands:
            by_core[c["core"]] = by_core.get(c["core"], 0) + 1
        print("open candidates: " + ", ".join(f"{k} {v}" for k, v in by_core.items()) + " (see log/candidates.csv)")
    return 0


def cmd_finish_core(args: argparse.Namespace) -> int:
    field = Field(Path(args.field))
    with Lock(field.dir):
        run_id, open_rows = open_run(field)
        if not run_id:
            raise LedgerError("no open run")
        if args.core not in [r["core"] for r in open_rows]:
            raise LedgerError(f"{args.core} is not open in {run_id}")
        mode = open_rows[0]["mode"]
        remaining = plan_progress(field, run_id, mode)[args.core]["remaining"]
        if remaining and not args.force:
            raise LedgerError(f"{args.core} still has unlogged work in {run_id}: {' '.join(remaining[:10])}. "
                              "Run it, or pass --force to close the Core anyway (its last-checked date will advance).")
        open_cands = [c for c in load_candidates(field) if c["core"] == args.core and c["status"] == "open"]
        runs = load_runs(field)
        for r in runs:
            if r["runId"] == run_id and r["core"] == args.core:
                r["finishedAt"] = now_iso()
                if args.note:
                    r["note"] = args.note
        save_runs(field, runs)
    print(f"{args.core} closed in {run_id}; last checked is now {utc_today().isoformat()}")
    if open_cands:
        print(f"  note: {len(open_cands)} open candidate(s) for {args.core} remain; a closed Core cannot take fetch rows "
              "in this run, so snowball before closing, or leave them for the next run")
    return 0


def cmd_finish_run(args: argparse.Namespace) -> int:
    field = Field(Path(args.field))
    with Lock(field.dir):
        run_id, open_rows = open_run(field)
        if run_id:
            if not args.force:
                raise LedgerError(f"{run_id} is still open for {', '.join(r['core'] for r in open_rows)}: finish-core each, "
                                  "or pass --force to close them all now")
            runs = load_runs(field)
            for r in runs:
                if r["runId"] == run_id and not r["finishedAt"]:
                    r["finishedAt"] = now_iso()
            save_runs(field, runs)
        else:
            run_id = _latest_run_id(field)
            if not run_id:
                raise LedgerError("no run to finish: start-run first")
    return _export(field, run_id)


def _latest_run_id(field: Field) -> str | None:
    runs = load_runs(field)
    return runs[-1]["runId"] if runs else None


def cmd_export(args: argparse.Namespace) -> int:
    field = Field(Path(args.field))
    return _export(field, args.run or _latest_run_id(field))


def _export(field: Field, run_id: str | None) -> int:
    summary = render_summary(field, run_id)
    if run_id:
        path = field.log_path(f"summary-{run_id}.md")
        path.write_text(summary, encoding="utf-8")
        print(f"wrote {path}")
    write_workbook(field, run_id)
    print(f"wrote {field.xlsx_path()}")
    write_landscape_html(field, run_id)
    print(f"wrote {field.html_path()}")
    print(summary.splitlines()[0])
    for line in summary.splitlines():
        if line.startswith("One line:"):
            print(line)
    return 0


def cmd_watchlist(args: argparse.Namespace) -> int:
    field = Field(Path(args.field))
    core = CORES[args.core]
    if not core.watchlist:
        raise LedgerError(f"{core.label} has no change feed; the watchlist monitor cannot watch it")
    ledger = load_ledger(field, core)
    rows = [r for r in ledger.values() if r["decision"] == "in"]
    if not rows:
        raise LedgerError(f"no in-scope {core.label} records in the ledger")
    cadence = "daily" if core.cadence == "daily" else "weekly"
    lines = [f"name: {field.name}-{core.name}", f"core: {core.name}",
             f"description: in-scope {core.label} records of the {field.name} landscape, exported {utc_today().isoformat()}",
             "ids:"]
    for r in sorted(rows, key=lambda x: x["amassId"]):
        ident = next((r.get(c) for c in core.id_columns if r.get(c)), "")
        title = (r.get(core.title_column) or "")[:70]
        comment = " ".join(x for x in (ident, title) if x)
        lines.append(f"  - {r['amassId']}" + (f"   # {comment}" if comment else ""))
    lines.append(f"cadence: {cadence}")
    out_dir = field.dir / "watchlists"
    out_dir.mkdir(exist_ok=True)
    path = Path(args.out) if args.out else out_dir / f"{field.name}-{core.name}.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {path} with {len(rows)} ids for amass-watchlist-monitor")
    return 0


def cmd_dismiss(args: argparse.Namespace) -> int:
    """Mark an open candidate as out of scope by design (a linked target gene, say), so it stops counting as a gap."""
    field = Field(Path(args.field))
    with Lock(field.dir):
        candidates = load_candidates(field)
        hit = [c for c in candidates if c["core"] == args.core and c["amassId"] == args.id]
        if not hit:
            raise LedgerError(f"{args.id} is not a candidate for {args.core}")
        for c in hit:
            c["status"] = "dismissed"
            if args.note:
                c["note"] = (c["note"] + "; " if c["note"] else "") + args.note
        save_candidates(field, candidates)
    print(f"dismissed {args.core} {args.id}")
    return 0


def cmd_allowance(args: argparse.Namespace) -> int:
    """Set the per-Core search allowance from the field's breadth, or a single Core's value."""
    with Lock(Path(args.field)):
        field = Field(Path(args.field))
        if args.breadth:
            for c in field.cores:
                field.data["allowance"][c] = ALLOWANCE[args.breadth][c]
        if args.core and args.searches:
            if args.core not in field.cores:
                raise LedgerError(f"{args.core} is not in this field's cores")
            field.data["allowance"][args.core] = args.searches
        field.save()
    print("allowance (searches per Core for a baseline): " + ", ".join(f"{c} {n}" for c, n in field.data["allowance"].items()))
    return 0


def cmd_intervals(args: argparse.Namespace) -> int:
    print("default update intervals (days):")
    for core_name, days in DEFAULT_INTERVALS.items():
        print(f"  {core_name:15} {days}")
    return 0


# --------------------------------------------------------------------------
# Entities: the roster the landscape is grouped by
# --------------------------------------------------------------------------

ENTITY_TEXT = {  # which columns of a record an entity alias is matched against
    "trialcore": ("interventionNames", "briefTitle", "acronym"),
    "biomedcore": ("title",),
    "patentcore": ("title",),
    "regulatorycore": ("name", "activeSubstance"),
    "drugcore": ("name", "synonyms", "tradeNames"),
    "genecore": ("symbol", "synonyms", "name"),
}
P3 = ("PHASE3", "PHASE2/PHASE3")
P2 = ("PHASE2", "PHASE1/PHASE2")
P1 = ("PHASE1", "EARLY_PHASE1")
ACTIVE = ("RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION", "ACTIVE_NOT_RECRUITING")
STOPPED = ("TERMINATED", "WITHDRAWN", "SUSPENDED")


def _spaced(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


# Which Cores an entity kind is matched against. A drug is named on records of every Core. A target
# names its gene record and the target-level papers and patents, but not trials (a trial is about a
# drug, and its title naming the gene would put every trial under the target). A class entity is a
# fallback bucket: it takes the records no drug or target matched, so counts stay a partition.
ENTITY_SCOPE = {
    "drug": frozenset(CORES),
    "other": frozenset(CORES),
    "target": frozenset({"genecore", "biomedcore", "patentcore"}),
    "class": frozenset(CORES),
}


class EntityMatcher:
    """Assigns records to entities by alias. Long aliases (6+ characters once punctuation is removed)
    match anywhere in the compacted text, so "AMG 510", "AMG-510" and "AMG510" are one alias; shorter
    aliases must appear as whole tokens, so "KBP" does not fire on "kbp042" by accident."""

    def __init__(self, entities: list[dict]):
        self.entities = entities
        self.prepared = []
        for e in entities:
            forms = []
            for alias in [e["name"], *e["aliases"]]:
                c, sp = _compact(alias), _spaced(alias).strip()
                if c:
                    forms.append((c, sp))
            self.prepared.append((e["name"], e.get("kind", "drug"), forms))

    @staticmethod
    def _hit(forms: list[tuple[str, str]], spaced: str, compact: str) -> bool:
        return any((len(c) >= 6 and c in compact) or f" {sp} " in spaced or f" {c} " in spaced for c, sp in forms)

    def match(self, texts: list[str], core_name: str | None = None) -> list[str]:
        joined = " | ".join(t for t in texts if t)
        if not joined:
            return []
        spaced, compact = _spaced(joined), _compact(joined)
        hits, fallback = [], []
        for name, kind, forms in self.prepared:
            if core_name and core_name not in ENTITY_SCOPE.get(kind, ENTITY_SCOPE["other"]):
                continue
            if self._hit(forms, spaced, compact):
                (fallback if kind == "class" else hits).append(name)
        return hits or fallback

    def for_row(self, core: Core, row: dict) -> list[str]:
        return self.match([row.get(c, "") for c in ENTITY_TEXT.get(core.name, ())], core.name)


def entity_table(field: Field, ledgers: dict[str, dict[str, dict]]) -> tuple[list[dict], dict[str, int]]:
    """One row per entity with in-scope counts per Core, plus an Unassigned row; and the per-Core unassigned counts."""
    matcher = EntityMatcher(field.data["entities"])
    today = utc_today()
    cutoff = (today - dt.timedelta(days=730)).isoformat()
    names = [e["name"] for e in field.data["entities"]] + ["Unassigned"]
    stats = {n: {"kind": "", "trials": 0, "p3": 0, "p2": 0, "p1": 0, "active": 0, "completed": 0, "stopped": 0,
                 "results": 0, "papers": 0, "recent": 0, "patents": set(), "authorizations": 0, "drugs": 0} for n in names}
    for e in field.data["entities"]:
        stats[e["name"]]["kind"] = e["kind"]
    unassigned: dict[str, int] = {}
    for core_name, ledger in ledgers.items():
        core = CORES[core_name]
        for row in ledger.values():
            if row["decision"] != "in":
                continue
            hits = matcher.for_row(core, row) or ["Unassigned"]
            if hits == ["Unassigned"]:
                unassigned[core_name] = unassigned.get(core_name, 0) + 1
            for name in hits:
                st = stats[name]
                if core_name == "trialcore":
                    st["trials"] += 1
                    ph, status = row.get("phase", ""), row.get("overallStatus", "")
                    st["p3"] += ph in P3
                    st["p2"] += ph in P2
                    st["p1"] += ph in P1
                    st["active"] += status in ACTIVE
                    st["completed"] += status == "COMPLETED"
                    st["stopped"] += status in STOPPED
                    st["results"] += row.get("hasResults") == "true"
                elif core_name == "biomedcore":
                    st["papers"] += 1
                    st["recent"] += (row.get("publicationDate") or "") >= cutoff
                elif core_name == "patentcore":
                    st["patents"].add(row.get("familyId") or row["amassId"])
                elif core_name == "regulatorycore":
                    st["authorizations"] += 1
                elif core_name == "drugcore":
                    st["drugs"] += 1
    rows = []
    for name in names:
        st = stats[name]
        if name == "Unassigned" and not any(v for k, v in st.items() if k not in ("kind", "patents")) and not st["patents"]:
            continue
        rows.append({"entity": name, "kind": st["kind"], "trials": st["trials"], "p3": st["p3"], "p2": st["p2"],
                     "p1": st["p1"], "active": st["active"], "completed": st["completed"], "stopped": st["stopped"],
                     "results": st["results"], "papers": st["papers"], "recent": st["recent"],
                     "patents": len(st["patents"]), "authorizations": st["authorizations"], "drugs": st["drugs"]})
    return rows, unassigned


ENTITY_COLUMNS = [("entity", "Entity"), ("kind", "Kind"), ("trials", "Trials"), ("p3", "Phase 3"), ("p2", "Phase 2"),
                  ("p1", "Phase 1"), ("active", "Active"), ("completed", "Completed"), ("stopped", "Stopped"),
                  ("results", "With results"), ("papers", "Papers"), ("recent", "Papers, last 24 months"),
                  ("patents", "Patent families"), ("authorizations", "Authorizations"), ("drugs", "Drug records")]


def cmd_entities(args: argparse.Namespace) -> int:
    field = Field(Path(args.field))
    if not field.data["entities"]:
        print("no entities in field.yaml yet; add the roster with add-entity (name, aliases, kind)")
        return 0
    ledgers = {c: load_ledger(field, CORES[c]) for c in field.cores}
    rows, unassigned = entity_table(field, ledgers)
    head = [h for _, h in ENTITY_COLUMNS]
    print(" | ".join(head))
    for r in rows:
        print(" | ".join(str(r[k]) for k, _ in ENTITY_COLUMNS))
    matcher = EntityMatcher(field.data["entities"])
    shown = 0
    for core_name, n in unassigned.items():
        core = CORES[core_name]
        print(f"\n{core_name}: {n} in-scope record(s) match no entity; add an alias or accept them as class-level:")
        for row in load_ledger(field, core).values():
            if row["decision"] == "in" and not matcher.for_row(core, row) and shown < args.top:
                ident = next((row.get(c) for c in core.id_columns if row.get(c)), row["amassId"])
                extra = row.get("interventionNames") or row.get("sponsorName") or ""
                print(f"  {ident}: {(row.get(core.title_column) or '')[:70]}" + (f"  [{extra[:60]}]" if extra else ""))
                shown += 1
    zero = [r["entity"] for r in rows if r["kind"] == "drug" and r["trials"] == 0]
    if zero:
        print(f"\ndrug entities with no trial in scope: {', '.join(zero)}. If the drug is in development, search its sponsor's "
              "name and its codes separately; registry text often carries the code only.")
    return 0


def cmd_add_entity(args: argparse.Namespace) -> int:
    with Lock(Path(args.field)):
        field = Field(Path(args.field))
        entities = field.data["entities"]
        existing = next((e for e in entities if e["name"].lower() == args.name.strip().lower()), None)
        aliases = [a.strip() for a in (args.alias or []) if a.strip()]
        if existing:
            for a in aliases:
                if a.lower() not in [x.lower() for x in existing["aliases"]]:
                    existing["aliases"].append(a)
            if args.kind:
                existing["kind"] = args.kind
            if args.note:
                existing["note"] = args.note
            action = "updated"
        else:
            entities.append({"name": args.name.strip(), "aliases": aliases, "kind": args.kind or "drug", "note": args.note or ""})
            action = "added"
        data = dict(field.data)
        data["entities"] = entities
        normalised, errors = normalise_field(data)
        if errors:
            raise LedgerError("entity rejected:\n  " + "\n  ".join(errors))
        field.data = normalised
        field.save()
    print(f"{action} entity {args.name.strip()}" + (f" with aliases {aliases}" if aliases else ""))
    return 0


# --------------------------------------------------------------------------
# Summary and workbook
# --------------------------------------------------------------------------

CURATED: dict[str, list[str]] = {  # column order on the per-Core sheets; everything else follows
    "trialcore": ["entities", "nctId", "registryId", "briefTitle", "phase", "overallStatus", "sponsorName", "startDate",
                  "completionDate", "enrollment", "hasResults", "conditions", "interventionNames", "facilityCountries",
                  "decision", "reason", "sourceUrl", "acronym", "studyType", "interventionTypes", "sourceRegistry", "links"],
    "biomedcore": ["entities", "pmid", "doi", "title", "journal", "publicationDate", "authors", "citationCount",
                   "journalQualityJufo", "hasFulltext", "isRetracted", "decision", "reason", "url", "links"],
    "patentcore": ["entities", "publicationNumber", "title", "assignees", "publicationDate", "priorityDate", "grantDate",
                   "countryCode", "kindCode", "familyId", "cpcCodes", "citedByCount", "decision", "reason", "filingDate", "links"],
    "regulatorycore": ["entities", "agency", "name", "activeSubstance", "authorizationStatus", "authorizationDate",
                       "marketingAuthorisationHolder", "therapeuticIndication", "isOrphan", "moleculeType", "procedureType",
                       "decision", "reason", "sourceUrl"],
    "drugcore": ["entities", "name", "chemblId", "drugType", "maxClinicalStage", "synonyms", "tradeNames", "mechanisms",
                 "description", "decision", "reason", "url", "parent", "links"],
    "genecore": ["entities", "symbol", "name", "ensemblGeneId", "hgncId", "geneType", "targetClass", "tractability",
                 "safetyEvents", "loeuf", "isEssential", "synonyms", "decision", "reason", "links"],
}
HOW_TO_USE = [
    "How to use this workbook",
    "Landscape (this sheet): the map at a glance, grouped by entity. An entity is a drug, target or class from the plan; a record naming two entities counts under both; Unassigned rows match no entity yet.",
    "Key records: the Phase 2 and later trials and every authorization in scope, one line each.",
    "One sheet per Core (TrialCore, BiomedCore, ...): every in-scope and unsure record with its decision and reason. Filter on the decision column; unsure rows are the ones to settle.",
    "Excluded: every record a search returned that was screened out, with the reason. They stay so later runs do not propose them again.",
    "Queries, Candidates, Changes, New: the run log. Changes and New fill on update runs.",
    "This workbook is regenerated from the CSV ledgers on every run; edit the field definition or tell the skill, not the sheets.",
    "Search is relevance-ranked and capped per call, so the map is as complete as the plan and its saturation say. The coverage box states what was checked.",
]


def _ident(core: Core, row: dict) -> str:
    return next((row.get(c) for c in core.id_columns if row.get(c)), "")


def _ledgers(field: Field) -> dict[str, dict[str, dict]]:
    return {c: load_ledger(field, CORES[c]) for c in field.cores}


def _crosslink_recall(field: Field, ledgers: dict[str, dict[str, dict]]) -> dict[str, dict[str, int]]:
    """Per target Core: ids linked from in-scope records, and how each was reached. `search`: a search returned
    it, before or after the link was seen (drug records are fetched before the searches run, so most links are
    candidates first and searches resolve them). `fetch`: only a fetch reached it, the plan's known gap. `open`:
    not reached yet. `dismissed`: out of scope by design."""
    out: dict[str, dict[str, int]] = {}
    cands = load_candidates(field)
    for core_name, ledger in ledgers.items():
        for row in ledger.values():
            if row["decision"] != "in" or not row.get("links"):
                continue
            for part in _split(row["links"]):
                target, _, n = part.partition(":")
                if target in CORES and n.isdigit():
                    o = out.setdefault(target, {"linked": 0, "search": 0, "fetch": 0, "open": 0, "dismissed": 0})
                    o["linked"] += int(n)
    for c in cands:
        if c["core"] in out and c["source"]:
            o = out[c["core"]]
            if c["status"] == "resolved":
                row = ledgers.get(c["core"], {}).get(c["amassId"])
                if row is None or row.get("source") != "search":
                    o["fetch"] += 1
            elif c["status"] == "dismissed":
                o["dismissed"] += 1
            else:
                o["open"] += 1
    for o in out.values():
        o["search"] = max(o["linked"] - o["fetch"] - o["open"] - o["dismissed"], 0)
    return out


def _crosslink_sentence(label: str, o: dict[str, int]) -> str:
    return (f"Cross-links: of {o['linked']} {label} ids linked from in-scope records: {o['search']} reached by search, "
            f"{o['fetch']} only by fetch, {o['open']} still open, {o['dismissed']} out of scope by design.")


def _saturation_words(field: Field) -> tuple[list[str], list[str]]:
    saturated, producing = [], []
    for core_name in field.cores:
        facets: list[str] = []
        for q in field.queries_for(core_name):
            if q["facet"] not in facets:
                facets.append(q["facet"])
        for facet in facets:
            if not facet_history(field, core_name, facet):
                continue
            label = f"{CORES[core_name].label} {facet}"
            (saturated if "saturated" in facet_verdict(field, core_name, facet) else producing).append(label)
    return saturated, producing


def _changes_by_entity(field: Field, run_id: str, ledgers: dict[str, dict[str, dict]]) -> dict[str, dict[str, int]]:
    matcher = EntityMatcher(field.data["entities"])
    out: dict[str, dict[str, int]] = {}
    for ch in load_changes(field):
        if ch["runId"] != run_id:
            continue
        row = ledgers.get(ch["core"], {}).get(ch["amassId"])
        names = (matcher.for_row(CORES[ch["core"]], row) if row else []) or ["Unassigned"]
        for n in names:
            d = out.setdefault(n, {})
            d[ch["class"]] = d.get(ch["class"], 0) + 1
    return out


NOUNS = {"trialcore": ("trial", "trials"), "biomedcore": ("paper", "papers"), "drugcore": ("drug record", "drug records"),
         "genecore": ("gene record", "gene records"), "regulatorycore": ("authorization", "authorizations"),
         "patentcore": ("patent record", "patent records")}


def _count_words(core_name: str, n: int) -> str:
    one, many = NOUNS[core_name]
    return f"{n} {one if n == 1 else many}"


def render_summary(field: Field, run_id: str | None) -> str:
    d = field.data
    runs = load_runs(field)
    run_rows = [r for r in runs if r["runId"] == run_id] if run_id else []
    mode = run_rows[0]["mode"] if run_rows else "none"
    changes = [c for c in load_changes(field) if c["runId"] == run_id] if run_id else []
    log = load_queries_log(field)
    checked = last_checked(field)
    ledgers = _ledgers(field)
    counts = {c: ledger_counts(field, c) for c in field.cores}
    lines = [f"# {d['name']}: {mode} run {run_id or '(none)'}, {utc_today().isoformat()}", "",
             f"Question: {d['question']}", ""]
    in_words = ", ".join(_count_words(c, counts[c]["in"]) for c in field.cores)
    if mode == "update":
        by_class: dict[str, int] = {}
        for ch in changes:
            by_class[ch["class"]] = by_class.get(ch["class"], 0) + 1
        cores_run = ", ".join(CORES[r["core"]].label for r in run_rows)
        if by_class:
            lines.append(f"One line: update over {cores_run}; " + ", ".join(f"{v} {k}" for k, v in by_class.items()) + ".")
        else:
            lines.append(f"One line: update over {cores_run}; nothing new in the field and no change on a tracked column.")
    else:
        lines.append(f"One line: baseline with {in_words} in scope.")
    lines += ["", "## Scope", ""]
    lines += [f"- In: {x}" for x in d["include"]]
    lines += [f"- Out: {x}" for x in d["exclude"]]
    lines.append("")
    if d["entities"]:
        rows, unassigned = entity_table(field, ledgers)
        lines += ["## By entity", "", "| " + " | ".join(h for _, h in ENTITY_COLUMNS) + " |",
                  "| " + " | ".join("---" for _ in ENTITY_COLUMNS) + " |"]
        for r in rows:
            lines.append("| " + " | ".join(str(r[k]) for k, _ in ENTITY_COLUMNS) + " |")
        lines.append("")
        lines.append("A record naming two drugs counts under both; a target entity counts gene records, papers and patents, not trials; a class entity takes the records that name no drug or target. Unassigned records match no alias yet.")
        lines.append("")
    if mode == "update":
        lines += ["## This run", "", "| Core | Since | Queries | New records | Changes | Rewritten unchanged |", "| --- | --- | --- | --- | --- | --- |"]
        for r in run_rows:
            lines.append(f"| {CORES[r['core']].label} | {r['since']} | {r['queries']} | {r['newRecords']} | {r['changes']} | {r['unchangedKnown']} |")
        skipped = [CORES[c].label for c in field.cores if c not in [r["core"] for r in run_rows]]
        if skipped:
            lines += ["", "Skipped (not due): " + ", ".join(skipped)]
        lines.append("")
        by_entity = _changes_by_entity(field, run_id, ledgers) if d["entities"] else {}
        if by_entity:
            lines += ["### Changes by entity", ""]
            for name, cls in by_entity.items():
                lines.append(f"- {name}: " + ", ".join(f"{v} {k}" for k, v in cls.items()))
            lines.append("")
        if changes:
            lines += ["### Changes", ""]
            for cls in (NEW_TO_AMASS, NEWLY_MATCHED, CHANGED, DECISION_CHANGED):
                items = [c for c in changes if c["class"] == cls]
                if not items:
                    continue
                lines.append(f"**{cls}** ({len(items)})")
                lines.append("")
                for c in items[:200]:
                    if cls in NEW_CLASSES:
                        lines.append(f"- {CORES[c['core']].label} {c['amassId']} [{c['new']}] {c['label']}")
                    else:
                        lines.append(f"- {CORES[c['core']].label} {c['amassId']} {c['label']}: {c['field']} {c['old']!r} -> {c['new']!r} ({c['group']})")
                if len(items) > 200:
                    lines.append(f"- ... {len(items) - 200} more in log/changes.csv")
                lines.append("")
    lines += ["## Coverage", ""]
    lines.append("| Core | In scope | Unsure | Screened out | Queries | Last checked | Interval |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for c in field.cores:
        nq = sum(1 for e in log if e["core"] == c and e["kind"] == "search")
        lines.append(f"| {CORES[c].label} | {counts[c]['in']} | {counts[c]['unsure']} | {counts[c]['out']} | {nq} | {checked.get(c, '-')} | {d['intervals'][c]}d |")
    lines.append("")
    anchors = anchor_status(field)
    if anchors:
        found = sum(1 for a in anchors if a["status"] == "found by search")
        lines.append(f"Anchors: {found} of {len(anchors)} found by search." + ("" if found == len(anchors) else " " + "; ".join(f"{a['id']} {a['status']}" for a in anchors if a["status"] != "found by search")))
    recall = _crosslink_recall(field, ledgers)
    for target, o in recall.items():
        if o["linked"]:
            lines.append(_crosslink_sentence(CORES[target].label, o))
    saturated, producing = _saturation_words(field)
    if saturated or producing:
        lines.append("Saturated facets: " + (", ".join(saturated) or "none") + ". Still producing: " + (", ".join(producing) or "none") + ".")
    total_unsure = sum(counts[c]["unsure"] for c in field.cores)
    if total_unsure:
        lines.append(f"Unsure records to settle: {total_unsure} (" + ", ".join(f"{counts[c]['unsure']} {CORES[c].label}" for c in field.cores if counts[c]["unsure"]) + ").")
    lines.append("")
    lines += ["## Files", "", f"- `{d['name']}.xlsx`: Landscape and Key records first, then one sheet per Core, Excluded, and the run log.",
              "- `ledger/<core>.csv`: the source of truth the workbook is built from.",
              "- `field.yaml`: the field definition, entities and plan; `log/`: every call, run and change.", ""]
    lines += ["## What this cannot see", "",
              "Search is relevance-ranked and capped per call, so the map is as complete as the plan and its saturation say. "
              "Date-filtered searches miss deletions, results-only revisions on trials, label and SmPC section-only revisions, "
              "and in-place publicationDate corrections; the REST change feed used by amass-watchlist-monitor sees those. "
              "PatentCore carries no Amass dates on MCP, so its updates find newly published patents only. A record rewritten "
              "by Amass is not a change; only a differing tracked column is.", ""]
    return "\n".join(lines) + "\n"


def _col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


_XML_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _xml_text(value: Any) -> str:
    s = _XML_BAD.sub("", str(value))[:32000]
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _sheet_xml(header: list[str], rows: list[list[Any]], widths: list[int], bold_rows: set[int] = frozenset(),
               freeze: bool = True, autofilter: bool = True) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
    if freeze:
        parts.append('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
                     'state="frozen"/></sheetView></sheetViews>')
    parts.append("<cols>" + "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, 1)) + "</cols>")
    parts.append("<sheetData>")

    def cell(ref: str, value: Any, style: int) -> str:
        s = f' s="{style}"' if style else ""
        if value is None or value == "":
            return ""
        if isinstance(value, bool):
            return f'<c r="{ref}" t="inlineStr"{s}><is><t>{"true" if value else "false"}</t></is></c>'
        if isinstance(value, (int, float)):
            return f'<c r="{ref}"{s}><v>{value}</v></c>'
        text = _xml_text(value)
        space = ' xml:space="preserve"' if text != text.strip() else ""
        return f'<c r="{ref}" t="inlineStr"{s}><is><t{space}>{text}</t></is></c>'

    r = 1
    if header:
        parts.append('<row r="1">' + "".join(cell(f"{_col_letter(i)}1", h, 1) for i, h in enumerate(header, 1)) + "</row>")
        r = 2
    for row in rows:
        style = 1 if (r in bold_rows) else 0
        parts.append(f'<row r="{r}">' + "".join(cell(f"{_col_letter(i)}{r}", v, style) for i, v in enumerate(row, 1)) + "</row>")
        r += 1
    parts.append("</sheetData>")
    if header and autofilter:
        parts.append(f'<autoFilter ref="A1:{_col_letter(len(header))}{max(len(rows) + 1, 1)}"/>')
    parts.append("</worksheet>")
    return "".join(parts)


class Sheet:
    __slots__ = ("name", "header", "rows", "bold_rows", "freeze", "autofilter", "widths")

    def __init__(self, name: str, header: list[str], rows: list[list[Any]], bold_rows: set[int] | None = None,
                 freeze: bool = True, autofilter: bool = True, widths: list[int] | None = None):
        self.name, self.header, self.rows = name, header, rows
        self.bold_rows, self.freeze, self.autofilter, self.widths = bold_rows or set(), freeze, autofilter, widths


def write_xlsx(path: Path, sheets: list[Sheet]) -> None:
    """Write a workbook with the standard library: zipped OOXML with inline strings."""
    used: set[str] = set()
    names = []
    for sh in sheets:
        clean = re.sub(r"[\[\]:*?/\\]", " ", sh.name)[:31] or "Sheet"
        while clean in used:
            clean = clean[:29] + "_2"
        used.add(clean)
        names.append(clean)
    content_types = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
                     '<Default Extension="xml" ContentType="application/xml"/>',
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
                     '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
    for i in range(1, len(sheets) + 1):
        content_types.append(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                             'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    content_types.append("</Types>")
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>")
    wb = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>']
    for i, name in enumerate(names, 1):
        wb.append(f'<sheet name="{_xml_text(name)}" sheetId="{i}" r:id="rId{i}"/>')
    wb.append("</sheets><definedNames>")
    for i, (name, sh) in enumerate(zip(names, sheets)):
        if sh.header and sh.autofilter:
            ref = f"'{name.replace(chr(39), chr(39) * 2)}'!$A$1:${_col_letter(len(sh.header))}${max(len(sh.rows) + 1, 1)}"
            wb.append(f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">{_xml_text(ref)}</definedName>')
    wb.append("</definedNames></workbook>")
    wb_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for i in range(1, len(sheets) + 1):
        wb_rels.append(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>')
    wb_rels.append(f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
    wb_rels.append("</Relationships>")
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
              "</styleSheet>")
    tmp = path.with_suffix(".xlsx.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "".join(content_types))
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", "".join(wb))
        zf.writestr("xl/_rels/workbook.xml.rels", "".join(wb_rels))
        zf.writestr("xl/styles.xml", styles)
        for i, sh in enumerate(sheets, 1):
            if sh.widths:
                widths = sh.widths
            else:
                ncols = max([len(sh.header)] + [len(r) for r in sh.rows]) if (sh.header or sh.rows) else 1
                widths = []
                for c in range(ncols):
                    longest = max([len(str(sh.header[c])) if c < len(sh.header) else 0]
                                  + [len(str(r[c])) for r in sh.rows[:500] if c < len(r) and r[c] is not None])
                    widths.append(min(max(longest + 2, 8), 60))
            zf.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(sh.header, sh.rows, widths, sh.bold_rows, sh.freeze, sh.autofilter))
    os.replace(tmp, path)


def _typed(core: Core, name: str, value: str) -> Any:
    col = core.columns.get(name)
    if col and value != "":
        if col.kind == "int":
            try:
                return int(value)
            except ValueError:
                return value
        if col.kind in ("float", "loeuf"):
            try:
                return float(value)
            except ValueError:
                return value
    if name == "seenCount" and value:
        return int(value)
    return value


def write_workbook(field: Field, run_id: str | None) -> None:
    d = field.data
    runs = load_runs(field)
    log = load_queries_log(field)
    checked = last_checked(field)
    ledgers = _ledgers(field)
    counts = {c: ledger_counts(field, c) for c in field.cores}
    matcher = EntityMatcher(d["entities"])
    today = utc_today()
    sheets: list[Sheet] = []

    # ---- Landscape ----
    L: list[list[Any]] = []
    bold: set[int] = set()

    def title(text: str) -> None:
        L.append([])
        bold.add(len(L) + 1)
        L.append([text])

    L.append([d["question"]])
    bold.add(1)
    L.append([f"Field {d['name']}, mapped through the Amass MCP. Workbook generated {today.isoformat()}" + (f" after run {run_id}" if run_id else "") + "."])
    title("In scope")
    L.append(["Core", "In scope", "Unsure", "Screened out", "Searches", "Last checked", "Next due"])
    for c in field.cores:
        nq = sum(1 for e in log if e["core"] == c and e["kind"] == "search")
        last = checked.get(c, "")
        nxt = (parse_date(last) + dt.timedelta(days=d["intervals"][c])).isoformat() if last else "now"
        L.append([CORES[c].label, counts[c]["in"], counts[c]["unsure"], counts[c]["out"], nq, last, nxt])
    if d["entities"]:
        title("By entity")
        L.append([h for _, h in ENTITY_COLUMNS])
        rows, unassigned = entity_table(field, ledgers)
        for r in rows:
            L.append([r[k] for k, _ in ENTITY_COLUMNS])
        L.append(["A record naming two drugs counts under both. A target entity counts gene records, papers and patents, not trials. A class entity takes the records that name no drug or target. Active = recruiting, not yet recruiting, enrolling by invitation or active not recruiting; Stopped = terminated, withdrawn or suspended."])
    title("Scope")
    for x in d["include"]:
        L.append(["In", x])
    for x in d["exclude"]:
        L.append(["Out", x])
    title("Coverage")
    anchors = anchor_status(field)
    if anchors:
        found = sum(1 for a in anchors if a["status"] == "found by search")
        L.append([f"Anchors: {found} of {len(anchors)} found by search."])
        for a in anchors:
            L.append(["", a["core"], a["id"], a["status"], a["note"]])
    for target, o in _crosslink_recall(field, ledgers).items():
        if o["linked"]:
            L.append([_crosslink_sentence(CORES[target].label, o)])
    saturated, producing = _saturation_words(field)
    if saturated or producing:
        L.append(["Saturated facets: " + (", ".join(saturated) or "none") + "."])
        L.append(["Facets still producing when the run ended: " + (", ".join(producing) or "none") + "."])
    total_unsure = sum(counts[c]["unsure"] for c in field.cores)
    L.append([f"Unsure records to settle: {total_unsure}." if total_unsure else "No unsure records."])
    L.append(["Blind spots: date-filtered searches cannot see deletions, results-only revisions, label or SmPC section-only revisions, or in-place publication-date corrections; PatentCore updates find newly published patents only."])
    title(HOW_TO_USE[0])
    for line in HOW_TO_USE[1:]:
        L.append([line])
    title("Update intervals (days)")
    for c in field.cores:
        L.append([CORES[c].label, d["intervals"][c]])
    sheets.append(Sheet("Landscape", [], L, bold_rows=bold, freeze=False, autofilter=False, widths=[70, 18, 14, 14, 12, 14, 14, 12, 12, 14, 12, 22, 16, 16, 14]))

    # ---- Key records ----
    K: list[list[Any]] = []
    kbold: set[int] = set()
    trials = ledgers.get("trialcore", {})
    key_trials = [r for r in trials.values() if r["decision"] == "in" and r.get("phase") in P3 + P2 + ("PHASE4",)]
    order = {"PHASE3": 0, "PHASE2/PHASE3": 1, "PHASE2": 2, "PHASE4": 3, "PHASE1/PHASE2": 4}
    key_trials.sort(key=lambda r: (order.get(r.get("phase"), 9), r.get("overallStatus") not in ACTIVE, r.get("startDate") or "", r["amassId"]))
    K.append(["Entities", "Identifier", "Title", "Phase", "Status", "Sponsor", "Start", "Completion", "Enrollment", "Results posted", "Registry", "Link", "Amass id"])
    kbold.add(1)
    for r in key_trials:
        K.append([", ".join(matcher.for_row(CORES["trialcore"], r)), _ident(CORES["trialcore"], r), r.get("briefTitle", ""), r.get("phase", ""),
                  r.get("overallStatus", ""), r.get("sponsorName", ""), r.get("startDate", ""), r.get("completionDate", ""),
                  _typed(CORES["trialcore"], "enrollment", r.get("enrollment", "")), r.get("hasResults", ""), r.get("sourceRegistry", ""),
                  r.get("sourceUrl", ""), r["amassId"]])
    auths = [r for r in ledgers.get("regulatorycore", {}).values() if r["decision"] == "in"]
    if auths:
        K.append([])
        kbold.add(len(K) + 1)
        K.append(["Entities", "Agency", "Product", "Active substance", "Status", "Authorized", "Holder", "Indication", "", "", "", "Link", "Amass id"])
        for r in sorted(auths, key=lambda x: (x.get("agency", ""), x.get("name", ""))):
            K.append([", ".join(matcher.for_row(CORES["regulatorycore"], r)), r.get("agency", ""), r.get("name", ""), r.get("activeSubstance", ""),
                      r.get("authorizationStatus", ""), r.get("authorizationDate", ""), r.get("marketingAuthorisationHolder", ""),
                      (r.get("therapeuticIndication") or "")[:300], "", "", "", r.get("sourceUrl", ""), r["amassId"]])
    sheets.append(Sheet("Key records", [], K, bold_rows=kbold, freeze=False, autofilter=False))

    # ---- one sheet per Core: in-scope and unsure ----
    excluded: list[list[Any]] = []
    for core_name in field.cores:
        core = CORES[core_name]
        ledger = ledgers[core_name]
        ordered = CURATED.get(core_name, []) + [c for c in core.ledger_columns if c not in CURATED.get(core_name, []) and c != "amassId"] + ["amassId"]
        header = ordered
        rows = []
        for r in sorted(ledger.values(), key=lambda x: (x["decision"] != "in", x["amassId"])):
            if r["decision"] == "out":
                excluded.append([core.label, _ident(core, r), (r.get(core.title_column) or "")[:200], r["reason"], r["firstQuery"], r["amassId"]])
                continue
            ents = ", ".join(matcher.for_row(core, r))
            rows.append([ents if c == "entities" else _typed(core, c, r.get(c, "")) for c in header])
        sheets.append(Sheet(core.label, header, rows))
    sheets.append(Sheet("Excluded", ["Core", "Identifier", "Title", "Reason", "Found by", "Amass id"], excluded))

    # ---- logs ----
    changes = load_changes(field)
    shown = [k for k in QUERY_LOG_COLUMNS if k != "credits"]  # the call count stays in log/queries.csv, out of the workbook
    sheets.append(Sheet("Queries", shown, [[e[k] for k in shown] for e in log]))
    cands = load_candidates(field)
    sheets.append(Sheet("Candidates", list(CANDIDATE_COLUMNS), [[c[k] for k in CANDIDATE_COLUMNS] for c in cands]))
    sheets.append(Sheet("Changes", list(CHANGE_COLUMNS), [[c[k] for k in CHANGE_COLUMNS] for c in changes if c["class"] not in NEW_CLASSES]))
    new_rows: list[list[Any]] = []
    for c in changes:
        if c["class"] in NEW_CLASSES:
            core = CORES[c["core"]]
            row = ledgers.get(c["core"], {}).get(c["amassId"], {})
            status = " ".join(row.get(x, "") for x in core.breakdown_columns if x != "publicationYear" and row.get(x))
            new_rows.append([c["runId"], c["date"], core.label, ", ".join(matcher.for_row(core, row)) if row else "", c["amassId"], c["class"],
                             row.get("decision", c["new"]), row.get(core.title_column, c["label"]), _ident(core, row) if row else "", status])
    sheets.append(Sheet("New", ["runId", "date", "core", "entities", "amassId", "class", "decision", "label", "identifier", "status"], new_rows))
    write_xlsx(field.xlsx_path(), sheets)


# --------------------------------------------------------------------------
# The landscape page: one self-contained HTML file with inline SVG charts
# --------------------------------------------------------------------------
# Colours follow the validated reference palette of the data-visualisation method: one ordinal blue
# ramp for phases, three fixed categorical slots for activity, one hue per single-series chart,
# ink tokens for every piece of text. Both modes were run through the palette validator.

PAGE_CSS = """
.viz-root{color-scheme:light;--surface:#fcfcfb;--plane:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--p1:#86b6ef;--p2:#3987e5;--p3:#1c5cab;--pother:#898781}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;--surface:#1a1a19;--plane:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--s3:#199e70;--p1:#86b6ef;--p2:#3987e5;--p3:#184f95;--pother:#898781}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--surface:#1a1a19;--plane:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--s3:#199e70;--p1:#86b6ef;--p2:#3987e5;--p3:#184f95;--pother:#898781}
*{box-sizing:border-box}html,body{margin:0;padding:0}
body.viz-root{background:var(--plane);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.page{max-width:1100px;margin:0 auto;padding:24px 16px 48px}
header h1{font-size:26px;line-height:1.25;margin:0 0 6px;font-weight:650}
header .sub{color:var(--ink2);margin:0 0 20px}
h2{font-size:18px;margin:32px 0 10px;font-weight:650}
h3{font-size:15px;margin:18px 0 6px;font-weight:650}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:14px 0 6px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.tile .label{color:var(--ink2);font-size:13px}.tile .value{font-size:28px;font-weight:600;line-height:1.2}
.tile .note{color:var(--muted);font-size:12px;margin-top:2px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media (max-width:760px){.grid2{grid-template-columns:1fr}}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin:12px 0}
.card h3{margin-top:0}.card .desc{color:var(--ink2);font-size:13px;margin:0 0 8px}
svg{width:100%;height:auto;display:block}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:13px;color:var(--ink2);margin:6px 0 2px}
.legend span{display:inline-flex;align-items:center;gap:6px}.legend i{display:inline-block;width:12px;height:12px;border-radius:3px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top}
th{color:var(--ink2);font-weight:600;position:sticky;top:0;background:var(--surface)}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.tablewrap{overflow-x:auto;max-height:520px;overflow-y:auto;border:1px solid var(--border);border-radius:8px}
details summary{cursor:pointer;color:var(--ink2);font-size:14px;margin:6px 0}
.narrative p{margin:8px 0}.narrative ul{padding-left:20px}.narrative li{margin:3px 0}.narrative h2{font-size:16px;margin:18px 0 6px}.narrative h3{font-size:15px}
.narrative table{font-size:12px}
.muted{color:var(--muted);font-size:13px}.scope li{margin:4px 0}
footer{margin-top:36px;color:var(--muted);font-size:12px;border-top:1px solid var(--grid);padding-top:12px}
a{color:inherit}
@media print{.page{max-width:none}.tablewrap{max-height:none;overflow:visible}details{display:block}details>summary{display:none}details>*{display:block}.card,.tile,tr,svg{break-inside:avoid}h2,h3{break-after:avoid}th{position:static}}
"""


def _h(value: Any) -> str:
    return (str(value) if value is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _nice_step(maximum: int, target_ticks: int = 5) -> int:
    if maximum <= 0:
        return 1
    raw = maximum / target_ticks
    mag = 10 ** int(math.floor(math.log10(raw))) if raw >= 1 else 1
    for m in (1, 2, 5, 10):
        if raw <= m * mag:
            return int(m * mag)
    return int(10 * mag)


def _svg_stacked_bars(rows: list[tuple[str, list[int]]], colors: list[str], series_names: list[str],
                      label_w: int = 170, bar_h: int = 20, row_h: int = 30, width: int = 760) -> str:
    """Horizontal stacked bars, one per row. colors are CSS variable names (p1, s1, ...)."""
    if not rows:
        return '<p class="muted">Nothing to plot yet.</p>'
    totals = [sum(v) for _, v in rows]
    maximum = max(totals) or 1
    step = _nice_step(maximum)
    axis_max = step * math.ceil(maximum / step)
    plot_w = width - label_w - 48
    height = row_h * len(rows) + 28
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="stacked bars">']
    # gridlines and ticks
    tick = 0
    while tick <= axis_max:
        x = label_w + plot_w * tick / axis_max
        out.append(f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height - 24}" stroke="var(--grid)" stroke-width="1"/>')
        out.append(f'<text x="{x:.1f}" y="{height - 8}" text-anchor="middle" font-size="11" fill="var(--muted)">{tick}</text>')
        tick += step
    for i, (label, values) in enumerate(rows):
        y = i * row_h + 5
        out.append(f'<text x="{label_w - 10}" y="{y + bar_h * 0.72:.1f}" text-anchor="end" font-size="13" fill="var(--ink)">{_h(label[:28])}</text>')
        x = label_w
        segs = [(k, v) for k, v in enumerate(values) if v > 0]
        for n, (k, v) in enumerate(segs):
            w = plot_w * v / axis_max
            w_draw = max(w - (2 if n < len(segs) - 1 else 0), 0.5)
            title = f"{_h(label)}: {_h(series_names[k])} {v}"
            if n == len(segs) - 1 and w_draw >= 4:
                r = 4
                d = (f"M{x:.1f},{y} H{x + w_draw - r:.1f} Q{x + w_draw:.1f},{y} {x + w_draw:.1f},{y + r} V{y + bar_h - r} "
                     f"Q{x + w_draw:.1f},{y + bar_h} {x + w_draw - r:.1f},{y + bar_h} H{x:.1f} Z")
                out.append(f'<path d="{d}" fill="var(--{colors[k]})"><title>{title}</title></path>')
            else:
                out.append(f'<rect x="{x:.1f}" y="{y}" width="{w_draw:.1f}" height="{bar_h}" fill="var(--{colors[k]})"><title>{title}</title></rect>')
            x += w
        out.append(f'<text x="{x + 6:.1f}" y="{y + bar_h * 0.72:.1f}" font-size="12" fill="var(--ink2)">{totals[i]}</text>')
    out.append(f'<line x1="{label_w}" y1="0" x2="{label_w}" y2="{height - 24}" stroke="var(--axis)" stroke-width="1"/>')
    out.append("</svg>")
    return "".join(out)


def _svg_columns(points: list[tuple[str, int]], color: str, width: int = 760, height: int = 220) -> str:
    """One series of columns (years on x). Labels the maximum and the last column only."""
    if not points or not any(v for _, v in points):
        return '<p class="muted">Nothing to plot yet.</p>'
    maximum = max(v for _, v in points) or 1
    step = _nice_step(maximum, 4)
    axis_max = step * math.ceil(maximum / step)
    left, bottom, top = 40, 28, 14
    plot_w, plot_h = width - left - 12, height - bottom - top
    n = len(points)
    slot = plot_w / n
    bar_w = min(24, max(slot - 4, 3))
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="columns by year">']
    tick = 0
    while tick <= axis_max:
        y = top + plot_h - plot_h * tick / axis_max
        out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - 12}" y2="{y:.1f}" stroke="var(--grid)" stroke-width="1"/>')
        out.append(f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="var(--muted)">{tick}</text>')
        tick += step
    max_i = max(range(n), key=lambda i: points[i][1])
    label_every = 1 if n <= 12 else 2 if n <= 24 else 5
    for i, (label, v) in enumerate(points):
        x = left + slot * i + (slot - bar_w) / 2
        h = plot_h * v / axis_max
        y = top + plot_h - h
        if v > 0:
            r = min(4, h / 2, bar_w / 2)
            d = (f"M{x:.1f},{top + plot_h} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} H{x + bar_w - r:.1f} "
                 f"Q{x + bar_w:.1f},{y:.1f} {x + bar_w:.1f},{y + r:.1f} V{top + plot_h} Z")
            out.append(f'<path d="{d}" fill="var(--{color})"><title>{_h(label)}: {v}</title></path>')
        if i % label_every == 0 or i == n - 1:
            out.append(f'<text x="{x + bar_w / 2:.1f}" y="{height - 8}" text-anchor="middle" font-size="11" fill="var(--muted)">{_h(label)}</text>')
        if v > 0 and (i == max_i or i == n - 1):
            out.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 4:.1f}" text-anchor="middle" font-size="11" fill="var(--ink2)">{v}</text>')
    out.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{width - 12}" y2="{top + plot_h}" stroke="var(--axis)" stroke-width="1"/>')
    out.append("</svg>")
    return "".join(out)


def _md_inline(text: str) -> str:
    text = _h(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = re.sub(r"\[(.+?)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


_NUMERIC = re.compile(r"-?\d+(\.\d+)?")


def _num_class(cell: str) -> str:
    return "n" if _NUMERIC.fullmatch(cell or "x") else ""


def md_to_html(text: str) -> str:
    """A small Markdown subset: headings, paragraphs, bullet lists, tables, bold, italic, code, links."""
    out: list[str] = []
    para: list[str] = []
    list_open = False
    table: list[str] = []

    def flush_para() -> None:
        if para:
            out.append("<p>" + _md_inline(" ".join(para)) + "</p>")
            para.clear()

    def flush_list() -> None:
        nonlocal list_open
        if list_open:
            out.append("</ul>")
            list_open = False

    def flush_table() -> None:
        if not table:
            return
        rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in table]
        rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c or "") for c in r)]
        if rows:
            out.append('<div class="tablewrap"><table><thead><tr>' + "".join(f"<th>{_md_inline(c)}</th>" for c in rows[0]) + "</tr></thead><tbody>")
            for r in rows[1:]:
                out.append("<tr>" + "".join(f'<td class="{_num_class(c)}">{_md_inline(c)}</td>' for c in r) + "</tr>")
            out.append("</tbody></table></div>")
        table.clear()

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("|"):
            flush_para(); flush_list()
            table.append(line)
            continue
        flush_table()
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            flush_para(); flush_list()
            level = min(len(m.group(1)) + 1, 4)
            out.append(f"<h{level}>{_md_inline(m.group(2))}</h{level}>")
            continue
        m = re.match(r"^\s*[-*]\s+(.*)", line)
        if m:
            flush_para()
            if not list_open:
                out.append("<ul>")
                list_open = True
            out.append(f"<li>{_md_inline(m.group(1))}</li>")
            continue
        if not line.strip():
            flush_para(); flush_list()
            continue
        if list_open and line.startswith("  "):
            out[-1] = out[-1][:-5] + " " + _md_inline(line.strip()) + "</li>"
            continue
        flush_list()
        para.append(line.strip())
    flush_para(); flush_list(); flush_table()
    return "\n".join(out)


def _year_counts(rows: list[dict], column: str, years: int = 15) -> list[tuple[str, int]]:
    today_year = utc_today().year
    counts: dict[int, int] = {}
    for r in rows:
        y = (r.get(column) or "")[:4]
        if y.isdigit():
            counts[int(y)] = counts.get(int(y), 0) + 1
    if not counts:
        return []
    start = max(min(counts), today_year - years + 1)
    return [(str(y), counts.get(y, 0)) for y in range(start, today_year + 1)]


def write_landscape_html(field: Field, run_id: str | None) -> None:
    d = field.data
    ledgers = _ledgers(field)
    counts = {c: ledger_counts(field, c) for c in field.cores}
    matcher = EntityMatcher(d["entities"])
    today = utc_today().isoformat()
    runs = load_runs(field)
    run_rows = [r for r in runs if r["runId"] == run_id] if run_id else []
    mode = run_rows[0]["mode"] if run_rows else "baseline"
    H: list[str] = []
    H.append('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">')
    H.append(f"<title>{_h(d['name'])} landscape</title><style>{PAGE_CSS}</style></head><body class=\"viz-root\"><div class=\"page\">")
    H.append(f"<header><h1>{_h(d['question'])}</h1><p class=\"sub\">Landscape of the field <code>{_h(d['name'])}</code>, mapped through the Amass MCP. "
             f"Generated {today}" + (f" after run {_h(run_id)}" if run_id else "") + ". Every figure on this page is computed from the ledgers.</p></header>")

    # ---- headline tiles ----
    H.append('<div class="tiles">')
    for c in field.cores:
        one, many = NOUNS[c]
        H.append(f'<div class="tile"><div class="label">{_h(many.capitalize())} in scope</div><div class="value">{counts[c]["in"]}</div>'
                 f'<div class="note">{counts[c]["unsure"]} unsure, {counts[c]["out"]} screened out</div></div>')
    H.append("</div>")

    # ---- update: what changed ----
    if mode == "update" and run_id:
        changes = [c for c in load_changes(field) if c["runId"] == run_id]
        by_class: dict[str, int] = {}
        for ch in changes:
            by_class[ch["class"]] = by_class.get(ch["class"], 0) + 1
        H.append(f"<h2>What changed in run {_h(run_id)}</h2>")
        if by_class:
            H.append("<p>" + ", ".join(f"{v} {_h(k)}" for k, v in by_class.items()) + ".</p>")
            by_entity = _changes_by_entity(field, run_id, ledgers)
            if by_entity:
                H.append("<ul>" + "".join(f"<li><strong>{_h(n)}</strong>: " + ", ".join(f"{v} {_h(k)}" for k, v in cls.items()) + "</li>" for n, cls in by_entity.items()) + "</ul>")
        else:
            H.append("<p>Nothing new in the field and no change on a tracked column.</p>")
        skipped = [CORES[c].label for c in field.cores if c not in [r["core"] for r in run_rows]]
        if skipped:
            H.append(f'<p class="muted">Not due this run: {_h(", ".join(skipped))}.</p>')

    # ---- charts ----
    trials = [r for r in ledgers.get("trialcore", {}).values() if r["decision"] == "in"]
    papers = [r for r in ledgers.get("biomedcore", {}).values() if r["decision"] == "in"]
    patents = [r for r in ledgers.get("patentcore", {}).values() if r["decision"] == "in"]
    rows, unassigned = entity_table(field, ledgers) if d["entities"] else ([], {})
    drug_rows = [r for r in rows if r["kind"] == "drug" and r["trials"] > 0]
    drug_rows.sort(key=lambda r: (-r["trials"], r["entity"]))
    top = drug_rows[:14]
    H.append("<h2>Pipeline</h2>")
    H.append('<div class="grid2">')
    if top:
        phase_rows = [(r["entity"], [r["p1"], r["p2"], r["p3"], r["trials"] - r["p1"] - r["p2"] - r["p3"]]) for r in top]
        H.append('<div class="card"><h3>Trials by phase, per drug</h3><p class="desc">Registry records in scope; a trial registered twice counts twice, a record naming two drugs counts under both.</p>')
        H.append('<div class="legend"><span><i style="background:var(--p1)"></i>Phase 1</span><span><i style="background:var(--p2)"></i>Phase 2</span><span><i style="background:var(--p3)"></i>Phase 3</span><span><i style="background:var(--pother)"></i>Other or not stated</span></div>')
        H.append(_svg_stacked_bars(phase_rows, ["p1", "p2", "p3", "pother"], ["Phase 1", "Phase 2", "Phase 3", "other"]))
        H.append("</div>")
        act_rows = [(r["entity"], [r["active"], r["completed"], r["stopped"]]) for r in top]
        H.append('<div class="card"><h3>Trial activity, per drug</h3><p class="desc">Active: recruiting, not yet recruiting, enrolling by invitation, active not recruiting. Stopped: terminated, withdrawn, suspended. Other statuses are not plotted.</p>')
        H.append('<div class="legend"><span><i style="background:var(--s1)"></i>Active</span><span><i style="background:var(--s3)"></i>Completed</span><span><i style="background:var(--s2)"></i>Stopped</span></div>')
        H.append(_svg_stacked_bars(act_rows, ["s1", "s3", "s2"], ["active", "completed", "stopped"]))
        H.append("</div>")
    else:
        H.append('<div class="card"><p class="muted">No drug entity has a trial in scope yet; add entities with their aliases to see the pipeline by drug.</p></div>')
    H.append("</div>")
    H.append('<div class="grid2">')
    py = _year_counts(papers, "publicationDate")
    H.append('<div class="card"><h3>Papers per year</h3><p class="desc">In-scope papers by publication year; the current year is partial.</p>' + _svg_columns(py, "s1") + "</div>")
    fam_rows: dict[str, dict] = {}
    for r in patents:
        key = r.get("familyId") or r["amassId"]
        if key not in fam_rows or (r.get("priorityDate") or "9") < (fam_rows[key].get("priorityDate") or "9"):
            fam_rows[key] = r
    pat = _year_counts(list(fam_rows.values()), "priorityDate")
    H.append('<div class="card"><h3>Patent families per priority year</h3><p class="desc">One count per family, by the earliest priority date in scope.</p>' + _svg_columns(pat, "s2") + "</div>")
    H.append("</div>")
    if py or pat:
        H.append("<details><summary>Table view of the two charts above</summary><div class=\"grid2\">")
        for title_, pts in (("Papers per year", py), ("Patent families per priority year", pat)):
            if pts:
                H.append(f'<div class="tablewrap"><table><thead><tr><th>{title_}</th><th class="n">Count</th></tr></thead><tbody>' + "".join(f'<tr><td>{_h(y)}</td><td class="n">{v}</td></tr>' for y, v in pts) + "</tbody></table></div>")
        H.append("</div></details>")

    # ---- by entity table ----
    if rows:
        H.append("<h2>By entity</h2>")
        H.append('<p class="muted">A record naming two drugs counts under both. A target entity counts gene records, papers and patents, not trials. A class entity takes the records that name no drug or target.</p>')
        H.append('<div class="tablewrap"><table><thead><tr>' + "".join(f'<th class="{"n" if k not in ("entity", "kind") else ""}">{_h(h)}</th>' for k, h in ENTITY_COLUMNS) + "</tr></thead><tbody>")
        for r in rows:
            H.append("<tr>" + "".join(f'<td class="{"n" if k not in ("entity", "kind") else ""}">{_h(r[k])}</td>' for k, _ in ENTITY_COLUMNS) + "</tr>")
        H.append("</tbody></table></div>")

    # ---- narrative ----
    brief = field.briefing_path()
    H.append("<h2>Briefing</h2>")
    if brief.is_file():
        H.append('<div class="card narrative">' + md_to_html(brief.read_text(encoding="utf-8")) + "</div>")
    else:
        H.append('<p class="muted">No briefing has been written for this field yet.</p>')

    # ---- key records ----
    key_trials = [r for r in trials if r.get("phase") in P3 + P2 + ("PHASE4",)]
    order = {"PHASE3": 0, "PHASE2/PHASE3": 1, "PHASE2": 2, "PHASE4": 3, "PHASE1/PHASE2": 4}
    key_trials.sort(key=lambda r: (order.get(r.get("phase"), 9), r.get("overallStatus") not in ACTIVE, r.get("startDate") or "", r["amassId"]))
    auths = [r for r in ledgers.get("regulatorycore", {}).values() if r["decision"] == "in"]
    H.append(f"<h2>Key records</h2><details open><summary>{len(key_trials)} Phase 2 and later trials, {len(auths)} authorizations</summary>")
    if key_trials:
        H.append('<div class="tablewrap"><table><thead><tr><th>Drug</th><th>Trial</th><th>Phase</th><th>Status</th><th>Sponsor</th><th>Start</th><th class="n">Enrolment</th><th>Results</th></tr></thead><tbody>')
        core_t = CORES["trialcore"]
        for r in key_trials:
            ident = _ident(core_t, r)
            link = f'<a href="{_h(r.get("sourceUrl"))}">{_h(ident)}</a>' if r.get("sourceUrl") else _h(ident)
            H.append(f'<tr><td>{_h(", ".join(matcher.for_row(core_t, r)))}</td><td>{link}<br><span class="muted">{_h((r.get("briefTitle") or "")[:110])}</span></td>'
                     f'<td>{_h(r.get("phase"))}</td><td>{_h(r.get("overallStatus"))}</td><td>{_h((r.get("sponsorName") or "")[:40])}</td><td>{_h(r.get("startDate"))}</td>'
                     f'<td class="n">{_h(r.get("enrollment"))}</td><td>{"yes" if r.get("hasResults") == "true" else ""}</td></tr>')
        H.append("</tbody></table></div>")
    if auths:
        core_r = CORES["regulatorycore"]
        H.append('<h3>Authorizations</h3><div class="tablewrap"><table><thead><tr><th>Drug</th><th>Agency</th><th>Product</th><th>Substance</th><th>Status</th><th>Authorized</th><th>Holder</th></tr></thead><tbody>')
        for r in sorted(auths, key=lambda x: (x.get("agency", ""), x.get("name", ""))):
            link = f'<a href="{_h(r.get("sourceUrl"))}">{_h(r.get("name"))}</a>' if r.get("sourceUrl") else _h(r.get("name"))
            H.append(f'<tr><td>{_h(", ".join(matcher.for_row(core_r, r)))}</td><td>{_h(r.get("agency"))}</td><td>{link}</td><td>{_h(r.get("activeSubstance"))}</td>'
                     f'<td>{_h(r.get("authorizationStatus"))}</td><td>{_h(r.get("authorizationDate"))}</td><td>{_h((r.get("marketingAuthorisationHolder") or "")[:40])}</td></tr>')
        H.append("</tbody></table></div>")
    elif "regulatorycore" in field.cores:
        H.append('<p class="muted">No authorization in scope: no drug of this field holds an FDA or EMA authorization in Amass.</p>')
    H.append("</details>")

    # ---- coverage ----
    H.append("<h2>Coverage</h2>")
    anchors = anchor_status(field)
    recall = _crosslink_recall(field, ledgers)
    saturated, producing = _saturation_words(field)
    total_unsure = sum(counts[c]["unsure"] for c in field.cores)
    H.append('<div class="tiles">')
    if anchors:
        found = sum(1 for a in anchors if a["status"] == "found by search")
        H.append(f'<div class="tile"><div class="label">Anchor records found by search</div><div class="value">{found} of {len(anchors)}</div><div class="note">records that had to be in the field</div></div>')
    for target, o in recall.items():
        if o["linked"]:
            H.append(f'<div class="tile"><div class="label">Linked {_h(CORES[target].label)} reached by search</div><div class="value">{o["search"]} of {o["linked"]}</div>'
                     f'<div class="note">{o["fetch"]} only by fetch, {o["open"]} open, {o["dismissed"]} out of scope by design</div></div>')
    H.append(f'<div class="tile"><div class="label">Search facets</div><div class="value">{len(saturated)} / {len(saturated) + len(producing)}</div><div class="note">saturated, of those run</div></div>')
    H.append(f'<div class="tile"><div class="label">Unsure records</div><div class="value">{total_unsure}</div><div class="note">' + _h(", ".join(f"{counts[c]['unsure']} {CORES[c].label}" for c in field.cores if counts[c]["unsure"]) or "none to settle") + "</div></div>")
    H.append("</div>")
    if anchors:
        H.append("<ul>" + "".join(f'<li>{_h(a["id"])}: {_h(a["status"])}' + (f' ({_h(a["note"])})' if a["note"] else "") + "</li>" for a in anchors) + "</ul>")
    if saturated or producing:
        H.append(f"<p><strong>Saturated:</strong> {_h(', '.join(saturated) or 'none')}. <strong>Still producing when the run ended:</strong> {_h(', '.join(producing) or 'none')}.</p>")
    H.append('<p class="muted">Search is relevance-ranked and capped per call, so the map is as complete as the plan and its saturation say. Date-filtered searches cannot see deletions, results-only revisions, label or SmPC section-only revisions, or in-place publication-date corrections; PatentCore updates find newly published patents only.</p>')

    # ---- scope and how to use ----
    H.append('<h2>Scope</h2><ul class="scope">' + "".join(f"<li><strong>In:</strong> {_h(x)}</li>" for x in d["include"]) + "".join(f"<li><strong>Out:</strong> {_h(x)}</li>" for x in d["exclude"]) + "</ul>")
    H.append("<h2>How to use the files</h2><ul>")
    H.append(f"<li><strong>{_h(d['name'])}.xlsx</strong> opens on the Landscape sheet (the tables on this page), then Key records, one sheet per Core with every in-scope and unsure record and its reason, Excluded, and the run log. Filter the decision column to see the unsure records.</li>")
    H.append("<li><strong>The field folder</strong> (field.yaml, ledger/, log/) is the memory for updates. Keep it, and say \"run the update\" to get what is new and what changed, by drug and by Core; this page then opens with the changes.</li>")
    H.append("<li>The in-scope trials can be exported as a watchlist for amass-watchlist-monitor, which tracks those records exactly.</li></ul>")
    H.append(f'<footer>Field {_h(d["name"])}, {today}. Built by amass-landscape-monitor from the Amass Cores: ' + _h(", ".join(CORES[c].label for c in field.cores)) + ".</footer>")
    H.append("</div></body></html>")
    field.html_path().write_text("".join(H), encoding="utf-8")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ledger.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="command", required=True)

    def add(name: str, func, help_text: str, field: bool = True):
        sp = sub.add_parser(name, help=help_text)
        if field:
            sp.add_argument("--field", required=True, help="the field folder (holds field.yaml)")
        sp.set_defaults(func=func)
        return sp

    add("validate", cmd_validate, "check field.yaml")
    add("plan", cmd_plan, "print the plan with a run-cap and context estimate")
    sp = add("start-run", cmd_start_run, "open a baseline or update run")
    sp.add_argument("--mode", choices=("baseline", "update"), required=True)
    sp.add_argument("--core", action="append", help="restrict to these cores (repeatable)")
    sp.add_argument("--force", action="store_true", help="update: run every core whether due or not")
    sp = add("ingest", cmd_ingest, "upsert JSON rows from stdin into the ledger and log the call")
    sp.add_argument("--run", help="the open run id (checked)")
    sp.add_argument("--query-id", help="plan query id for a search; omit for a fetch")
    sp.add_argument("--pass", dest="pass_name", choices=PASSES, help="update pass this search belongs to")
    sp.add_argument("--raw", help="path to the saved raw tool result; stdin then holds decisions keyed by amassId")
    sp.add_argument("--default", nargs=2, metavar=("DECISION", "REASON"),
                    help="with --raw: decision and reason for every record not named on stdin; 'drop' counts them but stores nothing")
    sp.add_argument("--returned", type=int, help="the number of results the search returned, when stdin omits filler rows")
    sp.add_argument("--note")
    sp = add("allowance", cmd_allowance, "set the searches each Core may plan, by breadth or per Core")
    sp.add_argument("--breadth", choices=("narrow", "medium", "broad"))
    sp.add_argument("--core")
    sp.add_argument("--searches", type=int)
    sp = add("add-query", cmd_add_query, "append a query to the plan")
    sp.add_argument("--core", required=True)
    sp.add_argument("--facet", required=True)
    sp.add_argument("--query", required=True)
    sp.add_argument("--id")
    sp.add_argument("--filter", action="append", help="name=value (comma-separated for several values)")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--update", action="store_true", help="include in the update subset")
    sp.add_argument("--force", action="store_true", help="add one query past the Core's allowance")
    sp.add_argument("--note")
    add("saturation", cmd_saturation, "new in-scope records per query and facet")
    sp = add("terms", cmd_terms, "values on in-scope records that no query mentions")
    sp.add_argument("--top", type=int, default=25)
    add("anchors", cmd_anchors, "which anchors the plan reached")
    sp = add("crosscheck", cmd_crosscheck, "compare linked ids from stdin with the ledger")
    sp.add_argument("--core", help="core of the ids when stdin is a plain list")
    sp.add_argument("--source", help="the record the ids came from")
    sp.add_argument("--note")
    add("status", cmd_status, "where things stand")
    sp = add("finish-core", cmd_finish_core, "close one core of the open run")
    sp.add_argument("--core", required=True)
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--note")
    sp = add("finish-run", cmd_finish_run, "close the run, write summary and xlsx")
    sp.add_argument("--force", action="store_true", help="close cores that are still open")
    add("abandon-run", cmd_abandon_run, "drop the open run")
    sp = add("export", cmd_export, "rebuild the xlsx and summary")
    sp.add_argument("--run")
    sp = add("watchlist", cmd_watchlist, "export in-scope ids as a watchlist")
    sp.add_argument("--core", required=True)
    sp.add_argument("--out")
    sp = add("entities", cmd_entities, "the map by entity, and the in-scope records no entity matches")
    sp.add_argument("--top", type=int, default=12)
    sp = add("add-entity", cmd_add_entity, "add an entity to the plan or extend its aliases")
    sp.add_argument("--name", required=True)
    sp.add_argument("--alias", action="append")
    sp.add_argument("--kind", choices=ENTITY_KINDS)
    sp.add_argument("--note")
    sp = add("dismiss", cmd_dismiss, "mark a candidate as out of scope by design")
    sp.add_argument("--core", required=True)
    sp.add_argument("--id", required=True)
    sp.add_argument("--note")
    add("intervals", cmd_intervals, "print the default update intervals", field=False)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except LedgerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
