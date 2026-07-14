---
name: trial-evidence-trace
description: Use when a clinical, medical-affairs, or evidence-review user has a single clinical-trial ID (NCT or Amass AMTC) and wants the exact set of published papers that describe that trial — outputs a citation-ranked, trust-tagged evidence table plus a per-trial CSV, grounded in Amass's TrialCore→BioMedCore cross-core graph (referencesBiomedCore) so no PMID or trial→paper linkage is fabricated.
license: Apache-2.0
metadata: { author: amass, version: "0.1.0" }
---

# Trial evidence trace

For a clinician, medical-affairs lead, or evidence reviewer who needs the literature that reports a
specific clinical trial. The single input is a **clinical-trial ID** (an NCT number or an Amass
`AMTC_` id). The skill reads the trial's identity from TrialCore, walks the cross-core
`referencesBiomedCore` edge to the publications that describe it, fetches each paper for its trust
metadata and abstract, and rolls the result into a citation-ranked evidence table plus a downloadable
`.csv`. The hallucination delta: plain Claude, asked "which papers describe NCT04611802?", invents
plausible titles and PMIDs and mixes in papers that merely cite the topic; Amass returns the trial's
describing-paper IDs as a first-class graph edge, so every row is a returned record, never a guess.

## When to invoke

- A user has a trial ID and wants its describing literature ("what papers report NCT…?", "pull the
  evidence base for this trial", "trace this trial to its publications").
- Any trial→paper evidence audit where the value is the literal `referencesBiomedCore` edge — the
  papers Amass links to the trial — not a free-text literature search on the trial topic.
- Works for any trial, not just the anchor: validated on NCT04611802 (Novavax PREVENT-19, COVID
  vaccine, 7 papers) and on NCT04788511 (Novo Nordisk STEP-HFpEF, semaglutide in heart failure, a
  distinct 7-paper cluster) — different therapeutic areas, same trace.

Do **not** invoke for an exhaustive literature survey of a trial's topic (the `referencesBiomedCore`
edge is the set Amass has linked to the trial, not every paper ever written about it), nor when the
user has an author or a paper and wants the inverse direction (that is the paper→trial edge — walk
`referencesTrialCore` from a BioMedCore record instead).

## Inputs

1. `trial_id` — the trial identifier. Required. Pass as `type="nctId"` for an NCT number
   (`NCT04611802`) or `type="amassId"` for an Amass id (`AMTC_…`).

Anchor example: `trial_id = "NCT04611802"`.

## The Amass MCP calls (exact sequence)

1. **Seed (trial record — the SEED STEP).**
   `get_amass_trialcore_record(type="nctId", value=<trial_id>)` (or `type="amassId"`).
   Note the honest identity for the verdict line — `sponsorName`, `phase`, `overallStatus`,
   `enrollment`, `conditions` — and read the `referencesBiomedCore` array: the Amass ids of the
   publications that describe this trial. The length of that array is the describing-paper count;
   report it as such, never as an exhaustive census.

2. **Fan-out (per-paper cross-core fetch — the HEADLINE step).**
   For each id in `referencesBiomedCore`:
   `get_amass_biomedcore_record(type="amassId", value=<AMBC_…>)` and read `title`, `journal`,
   `publicationDate`, `citationCount`, `journalQualityJufo`, `isRetracted`, the `pmid`, and the
   `abstract`. This is the load-bearing move; the trial seed only got you the ids.
   - **Rate-limit batching.** Pace calls to roughly one every 2 seconds. For trials with many
     describing papers, batch the fan-out in chunks of ~40–55 calls and pause ~10 s between batches.
     On HTTP `429`, read `Retry-After`, back off, then resume — do not skip an id.
   - **Metadata-only recovery on overflow.** A landmark paper's record can exceed the per-call token
     budget (its `citedBy` list can run to thousands). If a fetch overflows, recover by reading just
     the identity metadata fields (`title` / `journal` / `publicationDate` / `citationCount` /
     `journalQualityJufo` / `isRetracted` / `pmid`) rather than dropping the row; mark the relevance
     cell "not in abstract" if the abstract did not come back. On the anchors this was NOT triggered —
     all 14 papers (7 + 7) fetched clean, including the STEP-HFpEF NEJM primary (PMID 37622681, 1,275
     citations). It is documented as the safety net for still-larger landmarks.

3. **(optional) Confirm the back-edge.** Each fetched paper carries `referencesTrialCore`; the trial
   id should appear in it, confirming the link is reciprocal. Keep this as a field check, not a guess.

## Output template

A header line naming the input honestly (`Trial <ID> (<sponsor>, <phase>, <status>, <enrollment>
enrolled) — N describing papers from referencesBiomedCore`), then the table sorted by
`citationCount` descending, with columns:

`Title (verbatim) | Journal | Date | Cites | JuFo | Retracted | One-line relevance (from abstract)`

The relevance cell states the paper's own headline result in its own terms — a figure that appears
verbatim in the returned abstract (e.g. an efficacy percentage or hazard ratio). If the abstract does
not state something, write "not in abstract"; never paraphrase a trial outcome that is not present in
a returned abstract, and never paraphrase a field the MCP does not surface
(`primaryOutcomeMeasures`). Close with a one-line **Verdict**: "Trial <ID> (<sponsor>, <phase>,
<status>, <enrollment> enrolled) is described by N indexed papers; highest-cited: <title>
(<citations> citations, PMID <pmid>)." Emit a `.csv` with one row per paper:
`rank, title, journal, publicationDate, citationCount, journalQualityJufo, isRetracted, relevance,
pmid, amassId`.

## Failure modes & recovery

- **Empty `referencesBiomedCore`.** The trial has no linked describing papers in Amass yet. Report
  that honestly ("no describing papers in referencesBiomedCore"); do not search by topic and present
  the results as the trial's evidence base — that is a different, free-text query.
- **Token-budget overflow on a landmark paper.** Recover with a metadata-only read; keep the row and
  mark relevance "not in abstract" if the abstract did not return. Documented fallback; not triggered
  on the anchors.
- **429 rate limit.** Read `Retry-After`, back off, resume the fan-out from the next un-fetched id.
- **Thin or multi-trial papers.** Some describing papers (pooled / cross-protocol analyses) list the
  trial alongside several others in `referencesTrialCore`; that is correct — quote only the result the
  abstract attributes, and do not over-claim a single-trial finding from a pooled paper.
- **Do not guess → "not in abstract".** Any detail absent from the returned abstract is reported as
  that literal absence, not inferred.

## Guardrail

This output makes **clinical claims** — high stakes. Render only field-grounded rows: title
(verbatim) + PMID + journal + date + `journalQualityJufo` + `isRetracted` + a relevance note quoted
from the returned abstract. **Do not synthesize an endpoint, efficacy figure, or safety conclusion
that is not stated verbatim in a returned abstract**, and do not paraphrase `primaryOutcomeMeasures`
(the MCP does not expose it). The describing-paper count is the trial's `referencesBiomedCore` length,
not a census; frame it that way. When a relevance note quotes a result, it is the paper's own reported
figure for the population the abstract names — not a generalized clinical claim.
