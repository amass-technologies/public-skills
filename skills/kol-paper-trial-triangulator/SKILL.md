---
name: kol-paper-trial-triangulator
description: Use when a medical-affairs or MSL user names a KOL (key opinion leader) plus a context query and wants that author's top-relevant papers each triangulated to the clinical trial it reports — outputs a trust-tagged publication-and-trial-linkage table plus an engagement-dossier workbook, grounded in Amass's author-scoped BioMedCore search and the cross-core publication↔trial graph (referencesTrialCore) so no NCT number is fabricated.
license: Apache-2.0
metadata: { author: amass, version: "0.1.0" }
---

# KOL paper-trial triangulator

For a medical-affairs lead or MSL building a KOL engagement dossier. The single input is an
**author name plus a context query** (e.g. `Mikhail Kosiborod · cardiometabolic heart failure`).
The skill seeds an author-scoped BioMedCore search, then for each returned paper walks the
cross-core `referencesTrialCore` edge to the trial that paper reports, and rolls the result into a
per-paper publication-and-trial-linkage table plus a downloadable `.xlsx` engagement dossier. The
hallucination delta: plain Claude, asked "which trials has this KOL's work on this topic reported
on?", invents plausible NCT numbers and mis-attributes papers; Amass returns the author filter as a
search parameter and the paper→trial link as a first-class graph edge, so every NCT is a returned
field, never a guess.

## When to invoke

- A medical-affairs / MSL user names a clinician-researcher and a therapeutic-area query and wants
  their recent relevant work mapped to the trials it reports ("what trials has Dr. X's HFpEF work
  reported on?", "build me a KOL dossier for Y on Z").
- Any author-scoped literature pull where the value is the paper→trial linkage, not just the
  bibliography — i.e. you need the cross-core spine per paper, not a flat reading list.
- Works for any KOL + topic, not just the anchor: validated on Scott D. Solomon · heart failure
  preserved ejection fraction trial (a distinct trial portfolio — TOPCAT, PARAGON-HF, DELIVER).

Do **not** invoke for an exhaustive bibliometric census of an author (the 10-cap makes this a
relevance sample, not the full oeuvre — see Failure modes), nor when the user has a trial ID and
wants its describing papers (that is the inverse trial→paper direction — walk `referencesBiomedCore`
from a TrialCore record instead).

## Inputs

1. `kol` — the author name, passed verbatim to `authorNames` (e.g. `Mikhail Kosiborod`). Required.
2. `context_query` — a few distinguishing topic tokens, passed to `query` (e.g.
   `cardiometabolic heart failure`). Required — this disambiguates the author and ranks the sample.
   Strip punctuation/operators; the search is a bag-of-tokens match, no booleans/quotes/wildcards.
3. *(optional)* `min_jufo` — `minJournalQualityJufo` (0–3) to trust-gate the seed search. Default
   unset; set `2` for domain-leading-or-better. (Used on the Scott D. Solomon second-input run.)

Anchor example: `kol = "Mikhail Kosiborod"`, `context_query = "cardiometabolic heart failure"`.

## The Amass MCP calls (exact sequence)

1. **Seed (author search — the SEED STEP ONLY).**
   `search_amass_biomedcore_records(query=<context_query>, authorNames=[<kol>])`
   → up to 10 papers. `authorNames` matches any record with at least one author matching the token —
   it is an ambiguous token-match with **no prefix expansion and no identity resolution**. Verify
   the KOL actually appears in each returned record's `authors` list before keeping the row (on the
   anchor, all 10 carried Kosiborod). **10-cap = sample, not census** — this is the top-10 by
   relevance to the query, NOT the author's full bibliography. The MCP returns no total; never claim
   exhaustiveness. To trust-gate, add `minJournalQualityJufo` — but confirm the returned
   `journalQualityJufo` on each row rather than assuming it as a hard gate.

2. **Triangulate (per-paper cross-core fan-out — the HEADLINE step).**
   For each returned paper: `get_amass_biomedcore_record(type="amassId", value=<AMBC_…>)` and read
   its `referencesTrialCore` array (the Amass trial ids of the trial(s) that paper reports). This is
   the load-bearing move; the `authorNames` seed only got you here.
   - **Rate-limit batching.** Pace calls to roughly one every 2 seconds; for larger author corpora,
     batch in chunks of ~40–55 calls and pause ~10 s between batches. On HTTP `429`, read
     `Retry-After`, back off, then resume.
   - **Metadata-only recovery on overflow.** A landmark paper's record can exceed the per-call token
     budget (its citing-paper list can run to thousands). If a fetch overflows, recover by reading
     just the identity metadata + `referencesTrialCore` rather than dropping the row. **On the anchor
     this was NOT triggered** — all 10 fetched clean, including the DELIVER NEJM primary
     (PMID 36027570, 2,390 citations). It is documented as the safety net for still-larger landmarks.

3. **(optional) Resolve the trial title.** For each non-empty `referencesTrialCore` id, you may
   `get_amass_trialcore_record(type="amassId", value=<AMTC_…>)` (or `type="nctId"`) to attach the
   trial's `briefTitle` / `acronym` / sponsor / phase / status. Keep it to field-grounded identity;
   the reciprocal `referencesBiomedCore` edge on the trial should list the paper back, confirming
   the link.

## Output template

A header line naming the input honestly (`Author X · query "…" — top-10 by relevance, not the full
oeuvre`), then one table sorted however the persona prefers (the anchor keeps the seed-search
relevance order), with columns:

`Title (verbatim) | Journal | Date | Cites | JuFo | Retracted | referencesTrialCore (NCT)`

Each row's NCT cell is the literal `referencesTrialCore` value (or "— none (meta-analysis / review)"
when the edge is empty). Then a one-line **Verdict** stated as a checkable field claim:
"N of 10 papers carry exactly one `referencesTrialCore` link; M carry none." Close with the
artifact emitted: an `.xlsx` engagement dossier — one row per paper with columns
`paper_PMID, paper_AMBC, title, journal, date, journalQualityJufo, citationCount, isRetracted,
referencesTrialCore_NCTs`.

## Failure modes & recovery

- **Empty `referencesTrialCore` on a paper.** Not a failure — meta-analyses and reviews legitimately
  carry zero (they aggregate many trials, name none as a single primary). Render "— none
  (meta-analysis / review)"; never invent a trial to fill the cell.
- **Token-budget overflow on a landmark paper.** Recover with a metadata-only read; keep the row.
  Documented fallback; not triggered on the anchor.
- **Author returns thin or off-topic results.** `authorNames` is a token-match with no identity
  resolution; a common name can pull a homonym's papers. Verify the KOL in each `authors` list; if
  the corpus is review/consensus-heavy, triangulation will be thin — report that honestly rather
  than forcing trial links.
- **JuFo gate under weak token match.** If you trust-gate the seed with `minJournalQualityJufo` and
  the query has no strong token match, results may not all honor the filter — confirm the returned
  `journalQualityJufo` on each row before relying on it as a gate.
- **429 rate limit.** Read `Retry-After`, back off, resume the fan-out.
- **Do not guess → "not in abstract".** Any abstract-derived detail absent from the returned text is
  reported as that literal absence. Do not paraphrase fields the MCP does not surface
  (`primaryOutcomeMeasures`, `whyStopped`).

## Guardrail

This dossier names a **living physician** — medium stakes. Render **only field-grounded rows** —
title (verbatim) + PMID + journal + date + JuFo + `isRetracted` + the `referencesTrialCore` NCT
id(s). **Do NOT synthesize a subjective characterization** of the named person: no "thought leader",
"sponsor-aligned", "influential", or any inferred standing or intent. Frame the output strictly as a
**"publication + trial-linkage record for author X (top-10 by relevance to the context query; not
the full oeuvre)"**. The verdict is a checkable field claim — a count of linked vs. unlinked papers —
not an opinion about the person.
