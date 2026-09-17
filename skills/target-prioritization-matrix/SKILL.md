---
name: target-prioritization-matrix
description: Use when a drug-discovery, biology, BD, or investment user wants to discover and prioritize drug TARGETS (genes/proteins) for an indication, pathway, gene family, or biological process and get them back as a scored, side-by-side table — NOT a single known target and NOT a list of drugs. Runs a short interview first (which therapeutic MODALITY, then which criteria — druggability/tractability, safety, genetic constraint, essentiality, competition, clinical/literature/IP activity), then discovers candidates in GeneCore and scores each across the chosen criteria using GeneCore target intelligence plus DrugCore/TrialCore/BiomedCore/PatentCore cross-links, and emits a tabular target overview in the format the user picks (markdown / CSV / Excel). Trigger on "find targets for…", "target discovery", "target landscape", "which targets are druggable/tractable by antibody/small molecule/PROTAC", "shortlist targets", "prioritize targets", "target assessment matrix", even when the user does not say "Amass" or name a Core.
license: Apache-2.0
metadata: { author: amass, version: "0.1.0" }
---

# Target prioritization matrix

A general, **interview-driven** workbench for **target discovery**. The user brings a piece of biology
— an indication, a pathway, a gene family, a process, or a seed gene to expand around — and a sense of
what matters to them (modality, druggability, safety, novelty, competition). The skill interviews them
for those criteria, discovers candidate **targets (genes/proteins)** in Amass **GeneCore**, scores each
candidate across the chosen criteria using GeneCore's target intelligence and cross-core links into
**DrugCore, TrialCore, BiomedCore, PatentCore, and RegulatoryCore**, and returns a **tabular overview**
— one row per target, one column per criterion — in the format the user asks for.

The hallucination delta is the whole point. Ask a plain model to "shortlist druggable kinase targets for
oncology, scored by essentiality and competition" and it recites a few famous genes and invents
DepMap scores, LOEUF values, tractability buckets, and "number of drugs in development" that read
authoritative and are fabricated. This skill returns the real GeneCore records — real Open Targets
tractability buckets, real gnomAD constraint, real DepMap essentiality, real curated safety liabilities —
and a real `referencesDrugCore` edge count for competition. Every cell traces to a returned field.

## When to invoke

- A user wants to **discover or prioritize targets** for a biological area and see them **compared in a
  table**: "find me targets for X," "which targets in pathway Y are druggable," "build me a target
  shortlist / assessment matrix," "what's the target landscape for Z," "score these candidate targets."
- The user cares about a **modality** ("antibody-tractable," "small-molecule targets," "PROTAC-able") or
  a **prioritization axis** (novelty vs validation, safety, essentiality, competition) — the interview
  turns those into filters and columns.
- Generalizes across biology and therapeutic area: oncology essential kinases, IBD inflammation targets,
  metabolic GPCRs, neuro ion channels, fibrosis, etc. The oncology-kinase example in this repo is an
  anchor, not a hard-coded assumption.

**Do NOT invoke when** the user already has **one specific target** and wants its full dossier (that is a
single `get_amass_genecore_record` lookup, not a discovery run), or wants a list of **drugs** rather than
targets (start in DrugCore), or wants a clinical-trial or publication landscape (use the trial/evidence
skills). This skill's unit of analysis is the **gene/protein target**, and its deliverable is a **table**.

## Step 1 — Interview the user (before any MCP calls)

Discovery is only as good as the criteria, so gather them first. Ask **modality first** (it changes which
tractability filter and which competition signal matter), then the rest. Batch the questions into **one
round** — where the host supports a structured question UI use it; otherwise ask inline as a short
numbered list. Offer the defaults so a user in a hurry can say "just use the defaults." If the user's
opening message already answers some of these, don't re-ask — confirm and fill only the gaps.

Ask for:

1. **Seed / scope** *(required)* — the biology to search from: an indication (`inflammatory bowel
   disease`), a pathway (`IL-23/Th17`), a gene family (`cyclin-dependent kinases`), a process
   (`DNA-damage checkpoint`), or one or more seed genes to expand around. This becomes the GeneCore
   query text.
2. **Modality** *(ask first)* — which therapeutic modality the user is designing for. Maps to GeneCore's
   `tractabilityModality`: `SMALL_MOLECULE`, `ANTIBODY`, `PROTAC`, or `OTHER_CLINICAL`; "any" = no
   modality filter. (Modalities GeneCore does not model as a tractability bucket — oligonucleotide, cell,
   gene therapy — cannot be filtered on tractability; say so, and fall back to target class + subcellular
   location + DrugCore precedent for those.)
3. **Criteria / columns** *(multi-select, defaults on)* — which of these to score and show. Each maps to a
   specific Core/field (see the map below): druggability, clinical-precedent stage, target class, **safety
   liabilities**, **genetic constraint (LOEUF)**, **essentiality (DepMap)**, **competition (# drugs
   targeting it)**, **clinical activity (# trials)**, **literature depth (# papers)**, **IP activity (#
   patents)**, biotype.
4. **Stance** — does the user want **novel / underexplored** targets (few drugs, no clinical precedent) or
   **validated / precedented** ones (approved-drug tractability, many linked drugs)? This flips the
   direction of the filters and the sort.
5. **Shortlist size** — how many rows (default ~10–15; each candidate costs a few calls to enrich).
6. **Output format** *(always ask — the deliverable is a table and format is a real choice)* — **markdown
   table**, **CSV**, **Excel `.xlsx`**, or several. Default: **markdown + CSV**.

Reflect the chosen configuration back in one line before running, so the user can correct it cheaply:
`Discovering <modality>-tractable targets for "<seed>", scored on <criteria>, favoring <stance>, top
<N>, as <format>.`

## The Cores this skill draws on (criterion → Core → field)

Point every column at a real field. This is the map from an interview answer to the Amass call that fills
it. `search_*` returns up to `limit` full records per call (1–50, default 10); `get_*` fetches one record by id.

| Criterion (column) | Core | Call | Field / filter that fills it |
|---|---|---|---|
| **Discovery seed** | **GeneCore** | `search_amass_genecore_records(query=…)` | Bag-of-tokens over gene **symbol, name, synonyms, gene family, RefSeq summary, UniProt keywords, ChEMBL class**. Concept queries work (`tyrosine kinase`, `apoptosis`). |
| **Druggable** | GeneCore | search filter | `isDruggable=true` (any small-molecule or antibody bucket satisfied) |
| **Modality tractability** | GeneCore | search filter + record | `tractabilityModality`, `tractabilityStage`; read `tractability.{smallMolecule\|antibody\|protac\|otherClinical}` → each has a `clinical` lane (real clinical precedent) and a `predictive` lane (computational) |
| **Target class** | GeneCore | search filter + record | `targetClass=[ENZYME\|MEMBRANE_RECEPTOR\|ION_CHANNEL\|…]`; read `targetClass.path` (full ChEMBL class path) |
| **Genetic constraint** | GeneCore | search filter + record | `maxConstraintLoeuf`; read `gnomadConstraint.lossOfFunction.loeuf` (lower = more LoF-intolerant; gnomAD v4 constrained ≈ < 0.6) |
| **Essentiality** | GeneCore | search filter + record | `isEssential`; read `depmapEssentiality.isEssential` and `meanGeneEffect` (more negative = stronger CRISPR dependency) |
| **Safety liabilities** | GeneCore | search filter + record | `hasSafetyLiabilities`; read `safetyLiabilities[].event` (curated Open Targets/ClinPGx signals) |
| **Biotype** | GeneCore | search filter + record | `geneType` (`PROTEIN_CODING`, `NCRNA`, …) |
| **Competition / precedent** | GeneCore → **DrugCore** | `get_amass_genecore_record` | `referencesDrugCore` **array length** = number of drugs Amass links as targeting this gene. Drill a few via DrugCore for `name` / `drugType` / `maxClinicalStage`. |
| **Clinical activity** | **TrialCore** | `search_amass_trialcore_records(query="<symbol> inhibitor <indication>", phase=…)` | Count of returned trials — a **capped sample** (at most `limit`, no total), a presence signal, not a census |
| **Literature depth** | **BiomedCore** | `search_amass_biomedcore_records(query="<symbol> <indication> target")` | Count of returned papers — a **capped sample** (at most `limit`) |
| **IP activity** | **PatentCore** | `search_amass_patentcore_records(query="<symbol> inhibitor")` | Count of returned patents — a **capped sample** (at most `limit`) |
| **Approval status** | DrugCore → **RegulatoryCore** | drug record `referencesRegulatoryCore` | Whether any linked drug carries an FDA/EMA authorization |

For a fuller field reference, see the `amass-api` skill's `AMASS.md` (GeneCore record schema and filters).

## Step 2 — Discover candidates in GeneCore

1. **Build 1–3 query angles from the seed.** The engine is bag-of-tokens with no stemming, so distinct
   spellings and synonyms are distinct tokens — unioning a couple of angles (e.g. `cyclin dependent
   kinase mitotic checkpoint` and `aurora polo like kinase spindle`) broadens coverage past any one
   query's relevance ranking.
   Strip punctuation (quotes, hyphens, parentheses) — the engine treats it as garbage tokens.
2. **Apply the interview's filters on every angle.** Push as much as possible into the enum filters
   (`targetClass`, `tractabilityModality`/`tractabilityStage`, `isDruggable`, `isEssential`,
   `hasSafetyLiabilities`, `maxConstraintLoeuf`, `geneType`) rather than filtering client-side. Filters do
   double duty: they sharpen relevance **and** cut token load (see overflow note below).
3. **Union + dedupe on `ensemblGeneId`.** Report the honest count: `A=<n>, B=<n>, overlap=k → N unique`.
   Search takes a `limit` of 1–50 (default 10) but never returns a total — the union is a **broadened
   sample, not a census** at any limit. Say so.
4. **Handle the heavy-record overflow.** A GeneCore search returns **full records** (target intelligence,
   long PDB lists in `protein.structure`), so a page of them can exceed a single call's token budget and
   get saved to a file. When that happens, do **not** try to read the whole file into context — extract
   just the fields you need with `jq` (`.results[] | {symbol, targetClass, tractability, depmapEssentiality,
   gnomadConstraint, safetyLiabilities}`). Filtering hard in step 2 is the first defense.

## Step 3 — Read target intelligence (free, from the search response)

The criteria that live on the gene record come back **in the search response** — no extra call. For each
candidate extract only the selected columns:

- `symbol`, `name`, `targetClass.path` (show the leaf class)
- `tractability.<chosen modality>`: the `clinical` lane is clinical-precedent tractability (`Approved
  Drug` / `Advanced Clinical` / `Phase 1 Clinical`); the `predictive` lane is computational. Report the
  clinical lane as the headline and note when only predictive evidence exists.
- `safetyLiabilities`: count + the `event` strings (verbatim)
- `gnomadConstraint.lossOfFunction.loeuf`
- `depmapEssentiality.isEssential` and `meanGeneEffect`

## Step 4 — Enrich cross-core (only the columns the interview selected)

Fan out per candidate for the columns that need another Core. Keep this bounded — enrich only what was
asked for.

- **Competition (clean edge):** `get_amass_genecore_record(type="ensemblGeneId", value=<id>)` → the
  length of `referencesDrugCore` is the number of drugs Amass links as targeting the gene. This is a
  first-class graph edge, not a search — the trustworthy competition signal. To characterize the field
  (crowded-with-approved vs early), fetch a few of those `AMDC_` ids from DrugCore for `drugType` and
  `maxClinicalStage`.
- **Clinical / literature / IP activity (capped-sample proxies):** one TrialCore / BiomedCore /
  PatentCore search per candidate, as in the map. These return at most the `limit` you passed (1–50,
  default 10) and never a total — treat them as presence/intensity flags (`0`, `3`, `10+`), never as
  exact counts. Mark a cell `<limit>+` when the search comes back full. Keep the same `limit` across
  every candidate, or the column compares nothing.
  - **Watch for saturation.** For a mature, heavily-studied target class, a broad per-symbol query
    (`"<symbol> inhibitor cancer"`) comes back full for *every* candidate — the column goes uniformly
    `<limit>+` and stops differentiating. Raising `limit` pushes the saturation point out but does not
    remove it. (Observed live on the oncology essential-kinase
    anchor: all 12 kinases saturated all three of TrialCore/BiomedCore/PatentCore.) When that happens,
    don't present three dead columns as if they ranked anything — say the class is saturated, lead with
    the `referencesDrugCore` count (the one signal that *does* separate the field), and, if you need real
    activity differentiation, narrow the query with the specific indication or mechanism instead of the
    bare symbol.
- **Rate-limit batching:** keep each batch to **~8–10 calls, then continue**; the platform allows ~60
  requests / 60 s per user+org. On HTTP `429`, read `Retry-After`, back off exponentially, and resume
  where you left off — never silently drop a candidate.

## Step 5 — Assemble the tabular overview

Build the table: **one row per target, one column per selected criterion**, plus `symbol` and
`targetClass` always. Sort by the interview's priority axis (for a novelty stance, ascending competition
then ascending clinical stage; for a validation stance, the reverse; for oncology, often
`meanGeneEffect` ascending = most essential first). Then:

- **A `Read` line** — the pattern in the numbers: how many candidates clear each bar, where the
  crowding/novelty sits, which targets trade off (e.g. "most essential" often also "most toxic —
  pan-essential = narrow therapeutic window"; call that tension out honestly).
- **A one-line `Verdict`** — a checkable, field-grounded summary: `<N> <modality>-tractable targets for
  "<seed>"; top by <axis>: <symbol> (<value>); most novel: <symbol> (<n_drugs> drugs).`
- **A `Scope note` blockquote** (see Guardrail).
- **Emit in the chosen format.** Markdown renders inline. For CSV/Excel, write the file
  (`<seed-slug>-target-matrix.csv` / `.xlsx`) with one row per target and every selected column, and tell
  the user the path. Keep the CSV columns identical to the markdown so the two never drift.

## Step 6 — Recommend a reusable skill (do this every run)

This skill is a **workbench** — a general interview each time. Once a user has run it and likes the shape,
that exact configuration is worth freezing into a **dedicated, named skill** so they never re-interview.
So after delivering the table, close with a concrete offer:

> You just ran: seed pattern `<seed>`, modality `<modality>`, filters `<filters>`, columns `<columns>`,
> sorted by `<axis>`, as `<format>`. If you'll run this shape again — same criteria, new biology — I can
> turn it into a one-line reusable skill (e.g. `ibd-antibody-target-matrix`) via **skill-creator**, so
> next time you just say the seed. Want me to?

If they say yes, hand skill-creator a spec built from their answers: a fixed name, a description keyed to
their domain, the frozen GeneCore filter set, the fixed column list and sort, and the output format — i.e.
a specialized descendant of this general skill (the way `indication-pipeline-landscape` is a frozen
TrialCore recipe). This is how the general workbench spawns the narrow, repeatable tools.

## Failure modes & recovery

- **A query angle returns 0.** The seed terms are too rare or misspelled for the index, or the filters are
  too tight together (e.g. `PROTAC` + `isEssential` + a narrow class). Loosen one filter or try another
  terminology angle before concluding the space is empty — don't report an empty landscape from one
  over-constrained search.
- **`null` target-intelligence fields.** `tractability`, `safetyLiabilities`, `gnomadConstraint`,
  `depmapEssentiality` are `null` when Open Targets has no data — common for non-coding genes. `null` means
  **"no data recorded," not "not a target" and not "zero."** Render it as `—`, never as `0`.
- **Empty `referencesDrugCore`.** Genuine "no linked drug yet" — the novel end of the spectrum. Record `0`
  and read it as *unprecedented*, not *undruggable*.
- **Search overflow.** Extract fields with `jq` from the saved file; never drop a candidate or fabricate a
  value because the raw page was large.
- **429 rate limit.** Read `Retry-After`, back off, resume the fan-out from the next un-enriched candidate.
- **Do not guess.** Any value not present in a returned field is `—` ("no data"), never an estimate.

## Guardrail & honest scope

This output ranks targets and names how crowded each is, so it carries weight — bind these into every run:

- **Field-grounded cells only.** Every value traces to a returned Amass field. Tractability buckets, LOEUF,
  DepMap effect, safety events, and the `referencesDrugCore` count are rendered from the record — never
  characterized or estimated.
- **The `clinical` vs `predictive` tractability distinction is load-bearing.** Report the `clinical` lane
  (real clinical precedent) as the headline; label predictive-only tractability as computational, not as
  evidence a drug exists.
- **Trial / paper / patent counts are capped samples, not censuses.** Each underlying search returns at
  most its `limit` and never a total. Present them as intensity flags and mark `<limit>+` when a search
  comes back full. The one true count is `referencesDrugCore` (a graph edge).
- **`isEssential` is a double-edged read.** A DepMap-essential gene is a strong dependency **and** often a
  pan-lethal target with a narrow therapeutic window — flag it as a caveat, not an unqualified plus.
- **The verdict is a field claim, not an opinion** about which target is "best" — it states counts, stages,
  and edge lengths that trace to the returned records.

> **Scope note (paste into the output).** Candidates come from a union of relevance-ranked GeneCore
> searches — a **broadened sample, not a census**; more query angles surface more. Target-intelligence
> fields are verbatim from Open Targets / gnomAD / DepMap / UniProt as harmonized in GeneCore; `null` =
> "no data recorded," not zero. `n_drugs` is the length of each gene's `referencesDrugCore` edge (drugs
> Amass links as targeting it), **0 = unprecedented, not undruggable**. Trial/paper/patent columns are
> capped samples (presence/intensity flags), not exact counts. `isEssential` (DepMap) is a
> cell-line dependency signal — strong biology, but pan-essential targets carry therapeutic-window risk.
