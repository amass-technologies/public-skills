---
name: submission-evidence-assembler
description: Use when a regulatory medical writer names a single drug asset and needs a citation-auditable, Module-2.5-style evidence narrative — every pivotal Phase-3 trial for the asset, the published papers that describe each trial via Amass's referencesBiomedCore cross-core edge, and a canonical Amass-ID audit trail — with journalQualityJufo, retraction flag, and citation count on every cited paper. Output: a .docx evidence narrative grouped by trial plus an .xlsx trial×paper matrix. Triggers on "assemble the evidence base for <asset>", "build a Module 2.5 evidence summary for <drug>", "what published trial evidence supports <asset>'s submission".
license: Apache-2.0
metadata: { author: amass, version: "0.1.0" }
---

# Submission evidence assembler

This skill turns one **drug-asset name** into a **submission-grade, citation-auditable evidence
narrative** for a regulatory medical writer (the Module 2.5-style clinical-overview section). It finds
the asset's pivotal Phase-3 trials in TrialCore, walks each trial's `referencesBiomedCore` edge to the
publications that describe it, and assembles two take-home files: a `.docx` narrative grouped by trial
(each cited paper stamped with PMID · journal · JuFo tier · retraction flag · citation count) and an
`.xlsx` flat trial×paper matrix for the appendix. The single input is the asset name; every cell
carries a canonical Amass ID (`AMTC_…` / `AMBC_…`) as a stable audit trail. The hallucination delta in
one line: plain Claude invents plausible trial acronyms, PMIDs, and citation counts for a submission;
Amass returns the real asset→trial→paper graph with defensible trust metadata on every paper. It
**assembles and trust-screens** evidence — it does not author regulatory conclusions.

## When to invoke

- A regulatory medical writer (or the agent acting for one) names an asset and asks to assemble or
  audit its published evidence base for a filing — e.g. "build the Module 2.5 evidence summary for
  mavacamten," "what published Phase-3 evidence supports this asset," "give me a citation-auditable
  trial→paper dossier section."
- The asset is at or near filing — its pivotal trials have read out and have describing publications.
- **Do NOT invoke** when the asset is early/ongoing and its trials carry empty `referencesBiomedCore`
  (the cross-core spine yields nothing — say so, do not fabricate a narrative), or when the request is
  for trial-design or efficacy conclusions rather than an assembled, screened evidence base.

## Inputs

1. `asset` — the single required parameter: a drug name as a search token (e.g. `mavacamten`). Use the
   bare drug name; a single rare token gives the cleanest result set (adding the indication, e.g.
   "mavacamten hypertrophic cardiomyopathy," pulls in competitor-drug noise).
2. *(optional)* `phase` — defaults to `PHASE3` (pivotal-trial scope). To cover the full program, run
   `PHASE2` and `PHASE3` as separate searches and union the results (there is no OR operator).
3. *(optional)* `min_jufo` — a trust floor to annotate, not to drop rows. The dossier reports JuFo on
   every paper; this knob only flags rows below the floor for the writer's attention.

## The Amass MCP calls (exact sequence)

1. **Find the pivotal trials.** `search_amass_trialcore_records(query=<asset>, phase="PHASE3")`. The
   search returns at most 10 results (the MCP cap; no `limit` parameter, no total count).
   **10-cap completeness gate — run this immediately:**
   - **< 10 results** → the result set is complete for this asset. State the exact count and proceed;
     do not emit a truncation banner. (Anchor: mavacamten → **6** trials, complete.)
   - **exactly 10 results** → the set may be truncated at the cap. Emit the banner verbatim: *"Result
     set may be truncated at the 10-cap; matrix completeness not guaranteed."* Tell the writer the
     dossier is a sample, not a census of the asset's Phase-3 trials, and broaden by re-running with a
     second wording or a wider phase and unioning the results.
2. **Fetch each trial's identity + edge.** `get_amass_trialcore_record(type="nctId", value=<NCT>)` per
   trial. Read the honest identity (`sponsorName`, `overallStatus`, `enrollment`, `startDate`,
   `completionDate`, `hasResults`) and the `referencesBiomedCore` array. Do not render
   `primaryOutcomeMeasures` or `whyStopped` — they are not in the MCP TrialCore projection. If asked
   for a trial's primary endpoint, quote it from a describing paper's abstract, or write "not in
   abstract."
3. **Fan out to the describing papers.** For each `AMBC_` ID in every trial's `referencesBiomedCore`,
   call `get_amass_biomedcore_record(type="amassId", value=<AMBC>)` and read
   title · journal · publicationDate · citationCount · journalQualityJufo · isRetracted.
   - **Rate-limit batching:** the fan-out can be dozens of fetches (37 here). Pace calls to stay under
     the per-user/per-org limit of 60 requests / 60 seconds — batch in chunks then pause; on an HTTP
     429, read `Retry-After`, back off, and resume.
   - **Token-overflow recovery:** a landmark paper's get-by-ID can overflow the per-call token budget
     (its citation list runs to thousands of IDs). On overflow, recover by re-reading just the metadata
     fields you need (title/journal/date/citationCount/JuFo/retraction) — never drop the row.
4. **Union and dedupe** the papers across trials by `amassId` (here: 37 links, 37 unique papers, no
   cross-trial duplicates — but always dedupe). Sort within each trial by `citationCount` descending.
5. **Assemble the two artifacts** (Output template).

Client-side post-processing forced by wrapper gaps: there is no `minCitationCount` search filter
(filter/sort on the returned `citationCount` field); sponsor *type* is neither a search filter nor a
returned field (read sponsor class from `sponsorName`); JuFo and retraction come from the returned
get-by-ID record fields, not a separate search step.

## Output template

**Header (honest identity + completeness):** asset name, the Phase-3 trial count returned, and the
completeness verdict — either "search returned N < 10 ⇒ complete matrix for this asset" or the
truncation banner. State the total describing-paper count and the retracted count.

**Narrative grouped by trial (the .docx):** one section per trial, ordered by describing-paper count
descending. Each section header carries the trial acronym, NCT, `AMTC_` ID, sponsor, status,
enrollment, and link count. Under it, one bullet per describing paper:

```
<Title>. <Journal>, <Date>. PMID <pmid> · AMBC <amassId> · JuFo <0–3> · cites <n> · retracted <true|false>
```

**Flat matrix (the .xlsx)** — one row per trial×paper link, columns exactly:
`trial_NCT | trial_acronym | sponsor | trial_status | paper_PMID | paper_AMBC | title | journal | date | JuFo | citationCount | isRetracted`.

**Completeness banner in both artifacts:** for a complete asset, state "N/N trials complete (search
returned N < 10-cap)." For a truncated asset, state the truncation banner instead.

**Verdict line:** "<Asset>: <N> pivotal Phase-3 trials (complete / truncated), <M> describing papers,
<K> retracted; highest-cited: <title> (<cites> citations, PMID <pmid>, JuFo <tier>)."

## Failure modes & recovery

- **Empty `referencesBiomedCore` on a trial.** Not a failure — the trial genuinely has no describing
  publication yet (ongoing/unpublished). List the trial with "0 describing papers (no
  `referencesBiomedCore` edge as of the run date)." (Anchor: HORIZON-HCM → 0.) Do not invent a citation
  to fill the row.
- **Asset whose every trial returns an empty edge.** The whole cross-core spine yields nothing —
  typical of an early/ongoing asset. Say so plainly: "No describing publications are linked to this
  asset's Phase-3 trials in Amass as of <date>; an evidence narrative cannot be assembled." Do not
  fabricate one.
- **Token-budget overflow on a landmark paper.** Recover with a metadata-only read; keep the row.
- **429 rate-limit.** Read `Retry-After`, back off, resume the fan-out.
- **Thin results.** If the Phase-3 search returns 0–1 trials, report the count honestly and offer to
  widen the phase (PHASE2/PHASE3) or re-word the query; do not pad the dossier.
- **Do not guess → "not in abstract".** Any abstract-derived field absent from the returned text is
  written with that literal sentinel. Never paraphrase an MCP-omitted field into a claim.

## Guardrail

This is a **regulatory-stakes** artifact (a submission dossier section) that names living investigators
and clinical claims, so the field-grounded discipline is bound into the skill, not merely mentioned:

- **Field-grounded rows only.** Every cell — PMID, journal, JuFo, citationCount, isRetracted, sponsor,
  status, enrollment — is copied verbatim from the returned Amass record. No value is rounded,
  "improved," or inferred.
- **No subjective characterization.** Do not call a paper "weak/strong," a journal "predatory," or a
  trial "failed." `isRetracted` and `journalQualityJufo` are reported as the field values Amass
  returns; the writer draws conclusions.
- **No paraphrased omitted fields.** Never render `primaryOutcomeMeasures`, `whyStopped`, or any other
  field absent from the MCP projection. Trial-outcome figures, if needed, are quoted verbatim from a
  describing paper's abstract, or "not in abstract." Do not assert efficacy beyond what a returned
  abstract states.
- **Verdict is a checkable field claim.** "0 of 37 cited papers carry `isRetracted=true`" is a claim
  the reader can re-run against the same records — not an assurance of quality. The canonical Amass IDs
  in every row make the whole narrative independently re-fetchable. The skill assembles and
  trust-screens the evidence; the human writer authors the regulatory conclusions.
