---
name: orcid-record-self-verify
description: Use when a researcher wants to self-audit their OWN ORCID-linked publication record for integrity before a grant, tenure, or promotion submission (or a grants office does it on the researcher's request). Input is a single ORCID iD; the skill pulls the records Amass indexes under that verified identity via the MCP-exclusive authorOrcids filter, screens each on three integrity dimensions — isRetracted, journalQualityJufo, and the referencesTrialCore cross-core edge — and emits a partitioned report plus a downloadable spreadsheet. Self-audit framing only — never a third party auditing a candidate.
license: Apache-2.0
metadata: { author: amass, version: "0.1.0" }
---

# ORCID record self-verify

A self-audit skill for a researcher (or their grants office, on request) checking their **own** ORCID-linked publication record before a grant, tenure, or promotion submission. The single input is an ORCID iD; the take-home is a per-paper integrity report — title, journal, date, citation count, JuFo tier, retraction flag, and whether the paper carries a clinical-trial link — partitioned into clean vs flagged with a one-line verdict, plus a spreadsheet. The hallucination delta: asked to list and screen the papers under an ORCID, plain Claude fabricates plausible-but-wrong citations and unbacked retraction claims; the `authorOrcids` filter returns only records Amass genuinely indexes under that verified identity, each with real `isRetracted` / `journalQualityJufo`, and the `referencesTrialCore` edge is read straight off the record.

## When to invoke

- A researcher says "check my ORCID record", "screen my publication list for retractions before my biosketch / grant / tenure / promotion packet", or pastes their own ORCID and asks what Amass holds under it and whether it is clean.
- A grants/research office runs the same check **on the researcher's own request**, against that researcher's own ORCID, to sanity-check an evidence list before submission.
- Generalises to any ORCID the persona legitimately self-audits: their own, or a co-author's / lab-member's record compiled with that person's involvement. The anchor `0000-0003-3118-6859` is an illustrative public stand-in for "your ORCID."
- **Do NOT invoke** for a third party auditing a candidate without the candidate's involvement (e.g. a hiring committee profiling an applicant). That is a different, higher-stakes use this skill deliberately does not serve.

## Inputs

1. `orcid` — a single ORCID iD, bare format `0000-0000-0000-0000` (the URL form `https://orcid.org/0000-0000-0000-0000` is also accepted). **Required.**
2. `field_token` *(optional)* — a broad field word for the `query` slot (e.g. `"research"`). The ORCID is the hard identity filter; this token only nudges relevance ranking. Defaults to a generic field word.
3. `min_jufo` *(optional)* — set `minJournalQualityJufo` to scope the sample to a journal-quality floor. The stored demo used `=2` to scope to peer-reviewed-and-above. If set, note that unevaluated (`journalQualityJufo: null`) and below-threshold papers are excluded from the sample.

## The Amass MCP calls (exact sequence)

1. **Identity-filtered search.**
   ```text
   search_amass_biomedcore_records(
     query=<field_token, default "research">,
     authorOrcids=["<orcid>"],
     minJournalQualityJufo=<min_jufo, optional>
   )
   ```
   `authorOrcids` is an MCP-exclusive BioMedCore filter that matches the verified ORCID on author metadata, not a free-text name. It is an ANY-author match — a row qualifies if at least one author carries the ORCID, and the result does not name which author matched. Confirm the ORCID-holder actually appears in each returned author list. The search returns `title`, `journal`, `publicationDate`, `citationCount`, `journalQualityJufo`, `isRetracted`, `authors`, `pmid`, `amassId` — but NOT the trial edge.
2. **Per-paper trial-edge read.** For each returned record, call:
   ```text
   get_amass_biomedcore_record(type="amassId", value="<amassId>")
   ```
   and read `referencesTrialCore` (a list of TrialCore links; `[]` means no trial linkage). This is the only field not already in the search payload, so the get-by-ID is justified — do not call it for fields search already returned.
3. **Client-side three-dimension screen.** For each row compute: retracted? (`isRetracted=true`); below peer-review? (`journalQualityJufo < 1`, i.e. JuFo 0 or null); trial-linked? (`referencesTrialCore` non-empty). A row is `clean` only if none fire; otherwise tag it with the dimension(s) that fired.
4. **Assemble the spreadsheet.** One header row + one row per paper, columns: `PMID`, `AMBC`, `title`, `journal`, `publicationDate`, `journalQualityJufo`, `citationCount`, `isRetracted`, `referencesTrialCore`, `integrityFlag`. Add a verdict sheet with the counts.

**Rate-limit batching.** This skill issues one search plus up to 10 get-by-ID calls per ORCID. Pace Amass calls at roughly one every two seconds; the run stays well inside the 60-request / 60-second envelope. On HTTP 429 read `Retry-After`, back off, then resume.

**Metadata-only overflow recovery.** A heavily-cited paper's full record can overflow the per-call token budget (the `citedBy` / `references` arrays are large). If a `get_amass_biomedcore_record` overflows, re-read only the fields this skill needs (`referencesTrialCore`, `isRetracted`, `journalQualityJufo`) — do not drop the row and do not guess the edge.

## Output template

```text
# ORCID record self-verify — <orcid>

Identity (verified): <author name as it appears in the returned author lists>.
Amass returned <N> papers under authorOrcids=["<orcid>"] (relevance-ranked SAMPLE; see caveat).

| # | Title | Journal | Date | Cites | JuFo | Retracted | Trial link | Flag |
|---|-------|---------|------|-------|------|-----------|-----------|------|
| 1 | ...   | ...     | ...  | ...   | ...  | No        | none       | clean |

Verdict: Amass indexes <N> papers under this ORCID in this sample, <R> flagged
isRetracted=true, <Q> below the peer-review JuFo floor, <T> with a non-empty
referencesTrialCore edge, as of <date>. Clean-set JuFo split: J3=<a>, J2=<b>, J1=<c>, J0=<d>.
```

The `integrityFlag` cell is `clean` when no dimension fires, else a join of the firing dimensions (`retracted` / `below-peer-review` / `trial-linked`). The artifact emitted is a CSV (hero) mirrored by an XLSX workbook with a per-paper sheet and a verdict sheet.

## Failure modes & recovery

- **Empty result set (`{"results": []}`).** No papers matched the ORCID *in Amass's indexed metadata* — NOT proof the researcher has no papers. Report it as "Amass indexes no papers under this ORCID as of <date>" and suggest a fallback `authorNames` search to check whether the record exists under a name but is missing the ORCID link.
- **10-cap on a prolific researcher.** Always frame the returned rows as a relevance-ranked sample, not the full record; full export needs raw HTTP (`limit > 10` not exposed via the MCP). Never say "all your papers."
- **Empty `referencesTrialCore`.** `[]` is the common, expected case — it means no trial linkage, which is a clean signal, not missing data. Report it as `none`.
- **Token-budget overflow on get-by-ID.** Recover by re-reading just the needed metadata fields (see above); do not drop the row.
- **429 rate limit.** Read `Retry-After`, back off, then re-issue the call.
- **Thin / ambiguous results.** If a returned row's author list does not actually contain the ORCID-holder, note it — the ANY-author match plus a shared co-author can surface a paper the holder is not on.
- **Do not guess.** Every field comes from a returned Amass value. If a value is absent, write that it is absent — never invent a PMID, a journal, a retraction status, or a trial link.

## Guardrail

**MEDIUM stakes** — this record feeds a grant / tenure / promotion submission and names a living person. Bind into every run:

- **Field-grounded rows only.** Every cell is a value Amass returned — PMID, AMBC, title, journal, publicationDate, journalQualityJufo, citationCount, isRetracted, referencesTrialCore. No subjective characterization of the researcher or the work.
- **Verdict as a checkable field claim.** "Amass indexes N papers under this ORCID in this sample, R flagged isRetracted=true, T trial-linked, as of <date>." Not "your record is excellent / weak."
- **No imputing retraction reason or intent.** If a row is `isRetracted=true`, report the flag and the identifiers only; never narrate why or assign blame.
- **Identity confirmed, not assumed.** The `authorOrcids` filter is an ANY-author match that does not name which author matched; confirm the ORCID-holder in each author list before counting the row as theirs.
- **Sample, not census.** State the 10-cap on every run.
- **Self-audit framing only.** Verifying one's own (or a consenting colleague's) record — never a committee auditing a candidate.
