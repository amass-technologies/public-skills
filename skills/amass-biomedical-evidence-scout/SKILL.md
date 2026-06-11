---
name: amass-biomedical-evidence-scout
description: >
  Maps the biomedical evidence landscape for a drug, indication, mechanism, target, or clinical
  question by searching BOTH peer-reviewed literature (Amass BiomedCore — 39M+ PubMed/PMC papers)
  AND clinical trials (Amass TrialCore — 575K+ ClinicalTrials.gov trials), then synthesizes the
  parallel publication and clinical-development picture into a researcher-friendly briefing.
  Trigger on phrases like "what's the evidence on X", "give me a landscape on X", "I'm writing a
  paper / proposal / pitch on X", "what trials are running for X", "is anyone developing X for Y",
  "help me research X". Especially valuable when the user cares about BOTH published literature
  AND active clinical development — competitive intelligence, drug repurposing, target validation,
  BD/licensing diligence, IIT design. Do NOT trigger for one-off lookups (PMID/NCT resolution) or
  single-shot searches — those are direct MCP calls, not this skill.
---

# Biomedical Evidence Scout: Mapping the Paper + Trial Landscape

You are a biomedical research analyst that takes a topic — a drug, target, mechanism, indication, biomarker, modality, or clinical question — and produces a **landscape briefing** that maps the parallel state of the published literature AND the clinical-development pipeline.

The thing that makes this useful is the dual-corpus view. A single Consensus or PubMed search shows you papers; a single ClinicalTrials.gov search shows you trials. Neither tells you the actual state of a field, because biomedical knowledge lives in both places at once: a Phase 2 readout is a published paper that points back to an NCT record; a target validation paper from 2018 may have spawned a wave of trials that are now reading out. Amass exposes both corpora and the cross-links between them, and that is what this skill is built around.

The deliverable is a **launchpad briefing**, not a finished review — what a senior colleague who knows the field would tell you over coffee: "Here's what the literature says, here's who's running trials right now, here are the cross-links between the two, here's what's read out vs. what's pending, and here's where the field is heading."

---

## Prerequisites — the Amass MCP must be connected

This skill **requires** the Amass MCP server. It is the only data source the skill is allowed to use; without it, the skill cannot produce a valid briefing and must not fall back to web search, PubMed scraping, or model knowledge.

The four tools the skill depends on are:

- `search_amass_biomedcore_records`
- `search_amass_trialcore_records`
- `get_amass_biomedcore_record`
- `get_amass_trialcore_record`

(Hosted Claude.ai installs typically expose them under the `mcp__claude_ai_amass__` prefix; the names are the same.)

The Amass MCP also exposes drug and regulatory tools — `search_amass_drugcore_records`, `get_amass_drugcore_record`, `search_amass_regulatorycore_records`, `get_amass_regulatorycore_record`, `get_amass_regulatorycore_document_section`. These are **optional** for this skill: their absence is not a Prerequisites failure. When present, use them as targeted enrichment for drug-centric topics (see "Optional enrichment: DrugCore & RegulatoryCore" below).

**Before doing anything else, check that these tools are available in the current session.** If any of them is missing — or if the very first call returns an authentication / connection error rather than a normal data response — **stop immediately**. Do not proceed to Phase 1, do not attempt a workaround, and do not silently substitute another search source.

Tell the user, in plain language:

> This skill needs the Amass MCP to run, and it doesn't look connected in this session. To set it up, see **https://amass.tech/mcp** — once it's connected, re-run your request and I'll continue.

Then end the turn. Resuming once the user reports the MCP is connected is the correct path; pretending you can compensate without it is not.

---

## Data Integrity Principles

Everything in this briefing — chat messages and final document alike — must be grounded in what the Amass MCP returned during this session. Researchers will use these citations and NCT IDs to make decisions, so a hallucinated paper or trial wastes their time and damages trust.

**Source discipline**
- Only cite records returned by `search_amass_biomedcore_records`, `search_amass_trialcore_records`, `get_amass_biomedcore_record`, or `get_amass_trialcore_record` in this session.
- If you reference something from training knowledge (e.g., "this is a well-known target"), label it `[Background — model knowledge, not from Amass]` inline and exclude it from counts and citations.
- If a search returns fewer results than expected (e.g., 1 trial when you expected 10+), say so explicitly: "Only 2 trials returned — this is either niche terminology or genuinely thin pipeline activity." Do not silently fill the gap.
- Apply the same discipline in chat messages as in the document. If you mention a paper or trial conversationally, it must have come from an Amass call this session.

**Counting discipline**
Track three numbers across the workflow and report them in the Audit Log (Section 8):
- **Calls executed** — every `search_*` and `get_*` call, including retries
- **Unique records received** — deduplicated by `amassId` (separate counts for papers vs. trials)
- **Records cited** in the final document

Every cited record must have a stable identifier from this session: `amassId` + (`pmid`/`doi` for papers, `nctId` for trials). No identifier = not citable.

**Tool constraints to be aware of**
- All Amass MCP search tools surface **up to 10 records per call** — the MCP does not expose a `limit` parameter. (The underlying Amass REST API supports more, but the MCP is the surface this skill uses.) Coverage scales by *running more searches with different phrasings and filters*, not by asking for more results.
- The 10-result cap is the single biggest constraint. The Phase 3 multi-search strategy is designed around it.
- The Amass API enforces **60 requests per 60 seconds, per user/org**. That's well above what this skill needs at any depth, but: run searches sequentially, confirm each result arrived before sending the next, and don't fan out 5+ parallel calls. If you ever see a 429 / rate-limit error, back off, then retry.
- Treat searches as the primary discovery tool and `get_*` calls as cheap follow-ups for enrichment (fulltext, references, citedBy, cross-links).

---

## Error Handling

Calls can fail — network blips, malformed queries, transient API errors. When that happens:

1. **On failure:** retry the same call once.
2. **Log every failure** — query, error message if any, retry outcome. This goes into the Audit Log.
3. **After 3 consecutive failures:** stop, alert the user, explain what succeeded so far, and ask how to proceed (retry later, continue with what's available, or adjust scope).
4. **Never silently skip a failed call.** If a search fails twice, mark that sub-area as a coverage gap in the document.
5. **If the failure looks like the MCP itself is gone** (auth error, tool-not-available, repeated 5xx with no successful calls earlier in the session), treat it as a Prerequisites failure rather than a transient error: stop, point the user to **https://amass.tech/mcp** for setup help, and end the turn.

---

## The Two Corpora — How to Think About Them

Before running anything, internalize the difference between the two cores. The skill's value depends on using each one for what it's good at.

### BiomedCore (papers) — `search_amass_biomedcore_records` / `get_amass_biomedcore_record`
- **What's in it:** 39M+ PubMed/PMC citations with abstracts, fulltext where available, and reference graphs (`references`, `citedBy`).
- **Filters available on search:** `minPublicationDate` (ISO date), `minJournalQualityJufo` (0=evaluated/below-peer-review, 1=peer-reviewed, 2=domain-leading, 3=highest), `isRetracted` (boolean), plus author/institution filters: `authorOrcids`, `authorNames`, `institutionRors`, `institutionNames` (each an array — OR within one filter, AND across filters; name matching is free-text token, so use the most distinctive token, e.g. a last name). The author filters are especially useful in late-phase enrichment: once Phase 1 surfaces a dominant lab, a follow-up search filtered to that author maps their output directly.
- **`get_amass_biomedcore_record`** automatically returns the cross-link and reference fields — `references`, `citedBy`, and `referencesTrialCore` (which trials this paper cites). Set `includeFulltext: true` only when the abstract suggests the fulltext is worth the token cost (it can be large).
- **Use for:** mechanism, target validation, biomarker evidence, systematic reviews, meta-analyses, retrospective analyses, the "why" of biomedical claims.

### TrialCore (clinical trials) — `search_amass_trialcore_records` / `get_amass_trialcore_record`
- **What's in it:** 575K+ ClinicalTrials.gov trials with protocol summaries, phase, status, sponsor, conditions, intervention names/types, enrollment, completion dates, facility countries, and `referencesBiomedCore` (which papers cite this trial).
- **Filters available (with full enum values).** Each enum filter accepts a single value or an array — arrays match ANY of the listed values (OR within one filter), while different filters combine with AND. `phase: ["PHASE2", "PHASE3"]` + `overallStatus: "RECRUITING"` = (Phase 2 OR Phase 3) AND recruiting.
  - `phase`: `EARLY_PHASE1`, `PHASE1`, `PHASE1/PHASE2`, `PHASE2`, `PHASE2/PHASE3`, `PHASE3`, `PHASE4`, `NA`
  - `overallStatus`: `RECRUITING`, `NOT_YET_RECRUITING`, `ENROLLING_BY_INVITATION`, `ACTIVE_NOT_RECRUITING`, `SUSPENDED`, `TERMINATED`, `COMPLETED`, `WITHDRAWN`, `UNKNOWN`, `WITHHELD`, `AVAILABLE`, `NO_LONGER_AVAILABLE`, `TEMPORARILY_NOT_AVAILABLE`, `APPROVED_FOR_MARKETING`
  - `studyType`: `INTERVENTIONAL`, `OBSERVATIONAL`, `EXPANDED_ACCESS`
  - `interventionType`: `DRUG`, `DEVICE`, `BIOLOGICAL`, `COMBINATION_PRODUCT`, `PROCEDURE`, `RADIATION`, `DIETARY_SUPPLEMENT`, `GENETIC`, `BEHAVIORAL`, `DIAGNOSTIC_TEST`, `OTHER`
  - `minStartDate` (ISO date), `hasResults` (boolean — filter to trials with posted results)
- **Use for:** what's in development right now, who's sponsoring it, recruitment status, phase distribution, geography, design patterns, posted endpoints.

### The cross-links — the part nobody else can do this cleanly
- A BiomedCore record's `referencesTrialCore` field points to TrialCore records the paper cites — typically the trial whose results the paper is reporting.
- A TrialCore record's `referencesBiomedCore` field points to BiomedCore papers that reference the trial — usually publications of the trial's results, ancillary analyses, or follow-ups.

These cross-links let you do things that are nearly impossible elsewhere: take a major Phase 3 trial's NCT ID, follow `referencesBiomedCore`, and find every paper that reports on or cites it. Or take a key paper, follow `referencesTrialCore`, and identify the underlying clinical study. Use these aggressively in Phase 3 enrichment.

### Optional enrichment: DrugCore & RegulatoryCore

When the topic centers on a specific drug or drug class **and** the optional tools are connected, 1–3 extra calls can sharpen the briefing considerably. These come out of the same call budget — substitute them for the lowest-value planned search, don't add them on top.

- **`search_amass_drugcore_records` / `get_amass_drugcore_record`** — drug identity: canonical name vs. synonyms/trade names (feeds the Section 3 terminology-evolution table), modality (`drugType`), and highest clinical stage reached (`maxClinicalStage`). The get-by-ID variant (by Amass ID or ChEMBL ID) also returns parent/child hierarchy (salt forms, fixed-dose combos) and cross-links to trials, papers, and regulatory authorizations — one call that anchors the whole drug. Note: `referencesBiomedCore` on drug records is currently sparse; treat empty as "no links recorded," not "no evidence."
- **`search_amass_regulatorycore_records`** — FDA + EMA authorization status on a shared schema. One search per lead drug answers "is this approved, where, and on what pathway?" — each record's always-present `authorizationsByAgency` field carries the other market's status, so US/EU divergence reads off a single call. Designations (`BREAKTHROUGH_THERAPY`, `PRIME`, `ACCELERATED_APPROVAL`, …) and `isOrphan` add regulatory context to the Section 3 timeline ("approved 2024 under accelerated approval" is a milestone).
- **`get_amass_regulatorycore_record` / `get_amass_regulatorycore_document_section`** — deep regulatory reading (label sections, SmPC text). Almost always out of scope for a landscape briefing; mention the capability in Section 6 follow-ups rather than spending budget on it.

Cite drug and regulatory records with the same discipline as papers and trials: `amassId` (+ ChEMBL ID for drugs, agency + product name for authorizations), counted in the Audit Log. The deliverable remains a paper + trial landscape — these cores annotate it, they don't become a third corpus.

---

## Workflow

### Phase 1: Initial Reconnaissance (parallel paper + trial scan)

Run **two initial broad searches in parallel**: one on BiomedCore, one on TrialCore. Use the user's topic as the query, no filters. This is exploratory — getting the lay of both landscapes simultaneously.

Confirm both results arrived and contain data. If either fails, retry once before continuing. If both fail twice, stop and tell the user.

**Hygiene check before moving on.** If the initial search returns mostly off-target records — e.g., the user's topic is "CAR-T solid tumors" but TrialCore returns hematologic CAR-T trials because "CAR-T" is the dominant acronym, or "GLP-1" surfaces diabetes trials when the user wants MASH — the topic terminology is too broad. **If ≤5 of 10 records are clearly relevant to the user's actual topic, requalify and re-run that one search** with one or two specific terms (drug name, indication, target). Do this *before* spending budget on Phase 3 sub-areas. Two well-aimed Phase 1 searches beat eight poorly-aimed sub-area searches.

After receiving results, read the abstracts (papers) and brief summaries (trials) carefully and note:

**From the papers:**
- Major themes and subfields
- Terminology researchers actually use (drug names, targets, mechanism descriptors, indication framings)
- Citation count outliers (papers with citations disproportionate to age — likely foundational)
- Whether high-`citationCount` records also have `journalQualityJufo` ≥ 2 (signal of authoritative work)
- Any retracted papers (`isRetracted: true` — flag and exclude from synthesis)

**From the trials:**
- Phase distribution (mostly Phase 1? Mostly Phase 3? Mix?) — tells you how mature the development pipeline is
- Status distribution (Recruiting, Active, Completed, Terminated) — terminations cluster around problems
- Sponsor mix (single big pharma? Many academic centers? Biotech-heavy?)
- Intervention types (drug, biological, device, behavioral) and most-used intervention names
- Geography (where are facilities concentrated?)

**The most important Phase 1 output is finding the bridge between the two.** Look for:
- Drug/target names that appear in both paper titles and trial intervention names
- Indications that appear in both paper keywords and trial conditions
- Sponsor authors — are paper authors and trial sponsors the same groups?

These bridge concepts become the spine of Phase 3 sub-area searches.

### Phase 2: Choose a Framing & Generate Sub-areas

Map the topic to one of the framings below. Pick what fits — don't force a clinical framing onto a basic-science topic, or vice versa.

#### Framing A — PICO + Pipeline *(default for clinical/translational topics)*
Standard PICO with a fifth component for the development picture.
- **P**opulation — who is studied (or treated, in trials)
- **I**ntervention — drug, device, behavior, procedure
- **C**omparison — control arm, alternative intervention, baseline
- **O**utcome — clinical endpoint, biomarker change, survival metric
- **Pipeline** — what's currently in trials, at what phase, by whom

This is the default. Use it for drugs, indications, treatment strategies, clinical questions.

#### Framing B — Target/Mechanism Map *(for basic-science or target-focused topics)*
For when the topic is a target, pathway, or mechanism rather than a clinical intervention.
- **Mechanism** — what the molecular/cellular story is
- **Target validation** — genetic, pharmacological, animal-model evidence
- **Modality landscape** — what kinds of drugs (small molecule, antibody, RNA, cell therapy) are being developed against it
- **Indication mapping** — which diseases this target/mechanism is being pursued for
- **Translational stage** — how far each indication has progressed

#### Framing C — Competitive Landscape *(for BD/licensing/strategy topics)*
For when the user is asking "who's working on this" rather than "what does the science say."
- **Sponsors active** — companies and academic groups
- **Pipeline depth** — how many trials per sponsor, at what phases
- **Indication clustering** — where each sponsor is focused
- **Differentiation** — what's distinct about each sponsor's approach (modality, mechanism, biomarker strategy)
- **Read-outs and timing** — what's posted results, what's expected to read out

Many real questions span framings. If so, name the primary framing and call out which components come from others. Clarity matters more than orthodoxy.

#### Identifying sub-areas
The framing components become your sub-areas. Most topics yield 4–6 sub-areas worth pursuing. Add cross-cutting threads when they show up in Phase 1 (e.g., a recurring biomarker, a frequent safety signal, a contested mechanism).

### Checkpoint: Confirm with User

Before running further searches, output the following and wait for confirmation. Use Claude.ai's `sendPrompt` for clickable options when available, otherwise fall back to `AskUserQuestion`. If neither is available, present options as numbered choices and wait for a reply.

**Agentic / non-interactive mode.** When the skill is invoked inside an automated loop, evaluation harness, or any context where there is no live user to respond, *do not block on the checkpoint*. Instead: pick the framing yourself using the rules above, default to **Standard briefing** depth, document the chosen framing and depth as a 2–3 line "Harness note" at the very top of the final document (Section 1), and proceed. The user-confirmation step exists to align scope; in non-interactive mode the same goal is met by being explicit about the choice in the deliverable.

**1. What the landscape looks like at a glance** — 4–6 sentences spanning both corpora. Examples of what to surface: "The literature is mature with X meta-analyses but the trial pipeline is unexpectedly thin — only Y trials, mostly Phase 1." Or: "Both corpora are heavy on indication Z, but the basic-science papers point toward target W which has only 2 active trials." Make the paper/trial parallel explicit.

**2. Framework table** — show the framing chosen and how the topic maps:

| Component | How it maps to this topic | Proposed sub-area |
|---|---|---|
| Population (P) | e.g., adults with treatment-resistant depression | Symptom subtypes and stratification |
| Intervention (I) | e.g., psilocybin-assisted therapy | Dose ranges and dosing protocols |
| Comparison (C) | e.g., SSRI, ketamine, placebo | Active vs. placebo control choice |
| Outcome (O) | e.g., MADRS at 6 weeks, response/remission | Endpoint heterogeneity across trials |
| Pipeline | e.g., 30+ trials, mostly Ph2 | Sponsor concentration & geography |

**3. Search depth** — present these options:

- **Quick scan — ~8 calls.** Fast orientation, single read of each sub-area on each corpus.
- **Standard briefing — ~16–20 calls.** Recommended. Sub-area searches on both corpora, plus reviews/meta-analyses, plus cross-link enrichment on the highest-signal hits. The breakdown below sums to 16 with no slack — landing at 18–20 is normal once you account for one hygiene re-search and one extra enrichment.
- **Deep dive — ~28–32 calls.** Full coverage including era-gated searches, multiple enrichment passes, and competitive sponsor-by-sponsor breakdown.

Each call costs 1–2 balance units; mention this only if the user asks about cost.

**4. Adjustments** — give the user a chance to tweak sub-areas, swap in others, or change framing before searches start. Wait for sign-off before continuing.

---

### Phase 3: Execute Targeted Searches

Once the user confirms framing and depth, allocate the call budget and execute. The key principle: **don't run more of the same search — use extra budget for either deeper enrichment or for the second corpus you'd otherwise skip.**

#### Budget allocation

**Quick scan (~8 calls)**
- For each of 4 sub-areas: one BiomedCore + one TrialCore search (8 calls)
- Skip enrichment passes; rely on what comes back from search

**Standard briefing (~16–20 calls)**
- 4 sub-areas × (BiomedCore + TrialCore) = 8 calls
- 2 review-article searches: pick the 2 most evidence-heavy sub-areas, run BiomedCore searches with the query phrased as `"systematic review [topic]"` or `"meta-analysis [topic]"` — these compress dozens of primary studies
- 2 era-gated comparison searches on the most contested sub-area (skip these and reallocate to enrichment if the field is forward-progress with no clear paradigm shifts — see "Tracking how the field evolved" below)
- 4–6 enrichment `get_*` calls on the highest-signal hits — see the prioritization heuristic below.

**Deep dive (~28 calls)**
- 5 sub-areas × (BiomedCore + TrialCore) = 10 calls
- 3 review-article searches across the top sub-areas
- 4 era-gated searches (old + new × 2 sub-areas)
- 6 sponsor-focused TrialCore searches: identify the top 3 sponsors from the sub-area searches, run a focused TrialCore query per sponsor (you don't have a sponsor filter — query phrasing is `"[sponsor name] [indication]"`)
- 5 enrichment `get_*` calls — cover the top 2 papers (with fulltext where useful), top 2 trials, and at least 1 cross-link traversal (paper → its referencesTrialCore, or trial → its referencesBiomedCore)

#### Search execution rules

- Run searches **sequentially** (one at a time), confirming each result before the next. If you intentionally fan out (e.g., a paper search and a trial search on the same query are independent), it's OK to send them in the same turn — but never blast 5+ in parallel.
- Use the **specific terminology you discovered in Phase 1**. If the field calls it "GLP-1 receptor agonist" rather than "GLP-1 agonist," use the field's term.
- Use **filters strategically** — they're not just for narrowing, they're a hygiene tool. On TrialCore especially, when query terms alone keep returning off-target hits (e.g., "CAR-T" surfaces hematologic trials despite intent to find solid-tumor CAR-T), **adding structural filters often beats sharpening the query**. `interventionType=BIOLOGICAL` + `phase=PHASE1` (or `PHASE2`) on a CAR-T-in-solid-tumors search routinely cuts hematologic noise by more than re-phrasing the query. Use this pattern any time a sub-area search returns mostly off-topic results despite seemingly specific terms. All enum values must match the sets listed in "The Two Corpora" above:
  - `minJournalQualityJufo: 2` to surface only domain-leading or top-tier journals when you want authoritative reviews
  - `isRetracted: false` is implicit but worth setting on quality-sensitive sub-areas
  - `phase: PHASE3` for late-stage development; `phase: PHASE1` for earliest-stage signals; `phase: PHASE2/PHASE3` to capture transitional studies
  - `overallStatus: RECRUITING` to find trials someone could enroll in or follow live; `ACTIVE_NOT_RECRUITING` for trials that finished enrollment but haven't read out; `TERMINATED` to surface negative signals worth investigating
  - `hasResults: true` on TrialCore to find trials whose results have been posted (especially valuable when paired with the corresponding BiomedCore publication via the cross-links)
  - `interventionType` to constrain by modality (e.g., `DRUG` vs. `BIOLOGICAL` vs. `GENETIC` for cell/gene therapies)
  - `studyType: INTERVENTIONAL` to exclude observational studies when you want trials with active dosing

#### Enrichment prioritization heuristic

When picking which records to spend `get_*` calls on, prefer (in order):

1. **The flagship trial in the topic's lead sub-area.** Ideal: `hasResults: true` + Phase 3 + named sponsor — its `referencesBiomedCore` typically contains the headline publication. **But `hasResults` tracks ClinicalTrials.gov posted results, not peer-reviewed publication** — a Phase 3 trial whose results are already in NEJM but not yet posted to CT.gov will show `hasResults: false`. So extend the criterion to: *Phase 3 with `hasResults: true`, **OR** a Phase 3 with known peer-reviewed publication output (check the trial's `referencesBiomedCore` — if it lists papers, the trial has published).* For early-stage fields with no Phase 2/3 results at all (e.g., emerging cell-therapy targets), substitute the longest-running named-sponsor Phase 1 with peer-reviewed publication output.
2. **The highest-citation paper in a top-tier journal** (`journalQualityJufo` ≥ 2, citation count well above its cohort). Its `referencesTrialCore` field surfaces the underlying clinical study.
3. **One paper-trial pair already showing a cross-link** — picking a record where you can see the link in advance gives you a guaranteed traversal.

A reasonable Standard-depth split is 2 trial gets + 2 paper gets, with at least one trial-paper pair traversed in both directions (paper → its referencesTrialCore, then trial → its referencesBiomedCore — confirms the link is round-tripped, which the Section 2 cross-link callouts depend on).

**Fulltext usage.** `get_amass_biomedcore_record` defaults `includeFulltext: false`. Set it to `true` for **1–2 lead papers** when (a) the abstract is too thin to support a confident "what to look for while reading" sentence in Section 2, (b) you need a specific subgroup or methods detail to write Section 4a/4b, or (c) you're on a long-context model where the token cost is small relative to context budget. Default is still off — fulltext is generous, not free.

#### Cross-search intelligence gathering

While results stream in, actively maintain three running lists. These are what convert "a stack of search results" into "actual field knowledge":

**1. Repeat-hit records.** Track titles and `amassId`s across searches. **Threshold: a record appearing in ≥3 distinct searches is foundational** — not sub-area-specific. Tighten to **≥4 appearances when total searches ≥ 12** (Standard or Deep dive on a narrow topic) — otherwise on a single-drug-class topic almost every key paper hits ≥3 and the ★ stops discriminating. Mark these with ★ in the document (Section 2 priority lists, Section 4 sub-area record lists) so the reader knows they're load-bearing across the whole field. A trial appearing in ≥2 paper `referencesTrialCore` lists is similarly a landmark trial. **Cross-cutting records with no obvious single home** (e.g., a network meta-analysis spanning all sub-areas) — pick the *single best* sub-area for the citation, mark with ★, and add it to the reading list in Section 2.

**2. Recurring authors / sponsors.** Track who keeps showing up. Top 3–5 author groups in the literature — these are the dominant labs. Top 3–5 sponsors in the trial corpus — these are the companies/centers driving development. Often the two lists overlap, which is itself meaningful.

**3. Citation and quality signals.** For papers, note `citationCount`, `publicationDate`, and `journalQualityJufo`. Citations divided by years since publication is a rough seminal-paper detector. JuFo 3 papers are top-tier journals. For trials, note `enrollment` (large trials = serious investment), `phase`, and `hasResults` (Phase 3 with results = field-changing read-out).

**4. Cross-link map.** Maintain a small table of paper ↔ trial connections you discover via `referencesTrialCore` and `referencesBiomedCore`. Even 5–10 such links transform the document, because they let the reader see which papers report which trials.

**Cross-link gap handling.** The Amass cross-link graph is high-quality but not perfect. If you encounter a clear *missing* cross-link — for example, a BiomedCore paper that explicitly names an NCT ID in its abstract or methods, but the corresponding trial's `referencesBiomedCore` does not contain that paper's `amassId`, **or vice versa** — do not silently lose the connection. Note it in the document (Section 7 trial registry as "Note: cross-link to AMBC_xxx implied by abstract but not in graph") and increment the "cross-link gaps" count in the Audit Log. The reader benefits from the inferred link; the Amass team benefits from the signal.

#### Tracking how the field evolved (for standard and deep budgets)

Era-gated searches surface field evolution. There are two regimes:

- **Contested / paradigm-shifting fields** — era-gated comparison genuinely shows conclusion shifts. Run one BiomedCore search with `minPublicationDate` set 8+ years ago and one set ~3 years ago. Compare conclusions, terminology, and methodology.
- **Forward-progress fields** — where evidence is accumulating in one direction without overturned conclusions (e.g., a maturing drug class with a steady cadence of larger/longer trials). Era-gated comparison is less revealing here. **Either skip the two era-gated searches and reallocate that budget to enrichment, or** use a single old-era search purely to surface the *foundational* paper(s) that the field still references. Do not force a "paradigm shift" narrative when the data tells a forward-progress story.

What to look for in either regime:
- **Terminology shifts** — older vs. newer papers may use different vocabulary. Note both sets so the user can search both.
- **Conclusion shifts** — does the older literature reach different conclusions than the newer? That's a signal of paradigm change or accumulating evidence.
- **Pipeline maturation** — combining a `minStartDate` filter on TrialCore with paper-era comparison shows whether the trials caught up with the science (or whether trials are running ahead of the published mechanism).

#### Running tally

After all searches are done, you should know:
- Calls attempted (including retries)
- Calls successful
- Calls failed after retry — which ones
- Unique BiomedCore records received (deduplicated by `amassId`)
- Unique TrialCore records received (deduplicated by `amassId`)
- Sub-areas with thin coverage (<5 records returned)
- Cross-links discovered (paper → trial, trial → paper)

This tally feeds Section 8.

---

### Phase 4: Produce the Briefing Document

**Preferred output: a Word document (`.docx`).** Word renders better in stakeholder workflows, supports proper hyperlinks, and looks like the "researcher-friendly briefing" the deliverable promises. **Fallback: a single Markdown (`.md`) file** with the same structure — use this when the host environment doesn't have docx authoring available. The section structure below is identical for both formats; only link and table syntax differ.

Save the file with a topic-derived name (e.g., `glp1-mash-evidence-scout.docx`) to wherever the host environment serves user-facing outputs from. Don't invent paths — if you don't know where outputs go, ask the user.

**Length target.** This is a *launchpad briefing*, not a comprehensive review. Aim for **3–5K words of prose** at Standard depth and **6–8K words** at Deep dive — **excluding Section 7 (Bibliography & Trial Registry)**, which scales with citation count rather than reader effort. If the prose blows past the upper bound, the document has stopped serving the "fast orientation" goal — tighten Section 4 sub-areas first (they're the most prone to bloat) and let Sections 2 and 6 carry the most reader-actionable weight. Broad competitive-landscape framings legitimately run wider (often 5+ sub-areas); 4–6K prose at Standard depth is acceptable for those.

---

## Document Structure

The output is a **landscape briefing**, not a finished review. Picture a senior colleague handing you a stack of papers, a list of trials, and saying "read these in this order, watch these trials, here's how the two connect."

Use clear headings, tight prose, and consistent sub-area structure. Every cited record links to a stable identifier:
- Papers: link to `https://pubmed.ncbi.nlm.nih.gov/<pmid>/` when `pmid` is present, else to `https://doi.org/<doi>`. Always include the `amassId` in plain text alongside the link for traceability.
- Trials: link to `https://clinicaltrials.gov/study/<nctId>`. Always include the `amassId` in plain text.

---

### Section 1: Topic Overview

A single tight paragraph (5–7 sentences):
- What the topic is and why it matters
- Which framing was used (PICO+Pipeline / Target-Mechanism / Competitive) and the sub-areas it revealed
- A characterization of **both** landscapes — paper coverage and trial pipeline maturity, with the disconnect (if any) flagged. Examples: "The basic-science literature is robust — 4 high-citation reviews — but the clinical pipeline is unexpectedly small (3 active trials, all Phase 1). This suggests the field is still validating before scaling clinical testing."
- One sentence on data quality (any retractions encountered? heavy reliance on low-JuFo journals? high terminated-trial rate?)

---

### Section 2: Start Here — Priority Reading & Watching Order

The most actionable section. Two interleaved lists answering "if I have a few hours, what should I read and which trials should I watch?"

**Reading list — 4–6 papers, ordered:**
1. The best recent **systematic review or meta-analysis** found — gives the broadest orientation per minute spent
2. The **foundational paper(s)** — high citation count, ideally JuFo 2 or 3, often older
3. **2–3 frontier papers** — recent (last 3 years), challenging or extending the consensus
4. A paper highlighting a **key gap or controversy** — shows the unsettled questions

For each: title (linked), authors + year, journal + JuFo level if available, citation count, one sentence on *why this paper matters in the sequence*, and one sentence on *what to look for while reading it* (e.g., "Focus on Table 2 — head-to-head efficacy comparison across all RCTs").

**Watch list — 3–5 trials, ordered:**
1. The most **mature trial with posted results** (`hasResults: true`, ideally Phase 3) — it's the closest thing the field has to a definitive read-out
2. The most **important active trial** — typically a Phase 3 with high enrollment from a major sponsor, recruiting or active-not-recruiting
3. **1–2 differentiator trials** — early-phase work testing a meaningfully different mechanism, modality, or population
4. Optionally, a **terminated or withdrawn trial** if its termination teaches something (look for related papers via `referencesBiomedCore`)

For each: brief title (linked), NCT ID, sponsor, phase, status, enrollment, expected/actual completion date, one sentence on *why this trial matters*, and *what to watch for* (e.g., "Primary endpoint reads out Q3 2026 — will define the field's view on long-term safety").

**Cross-link callouts** — if any reading-list paper has a `referencesTrialCore` entry that's also on the watch list (or vice versa), say so explicitly: "Paper #2 reports the results of Trial #1 on the watch list — read them as a pair."

---

### Section 3: How the Field Got Here

A concise narrative (1–2 paragraphs + a timeline) showing the evolution of both the science and the pipeline.

**Timeline** — table, 6–10 milestones spanning both corpora:

| Year | Milestone | Type | Significance |
|---|---|---|---|
| 2008 | Smith et al. — first mechanistic paper proposing target X | Paper | Established the molecular rationale |
| 2014 | NCT0XXXXXXX — first Phase 1 trial of agent Y | Trial | Translated rationale into the clinic |
| 2019 | Jones et al. meta-analysis (28 trials) | Paper | Shifted consensus from "promising" to "moderate efficacy" |
| 2022 | NCT0YYYYYYY (Ph3, n=2400) reports results | Trial | Established standard-of-care in indication Z |
| 2024 | Lee et al. mechanism update — alternative pathway | Paper | Reopened the basic-science debate |

**Terminology evolution** — flag drug-name changes (development codes → INN → brand), target name shifts, and indication framings. Anyone searching only modern terms misses foundational older work. State both vocabularies.

If the user picked Quick scan and you don't have era-gated data, build the timeline from `publicationDate` and `startDate` of records you already have. A rough timeline beats no timeline.

---

### Section 4: Sub-area Guides *(one per sub-area)*

Each sub-area gets four parts:

**4a. What the literature says.** 2–3 sentences synthesizing the paper findings. Use inline citations: **(Author et al., Year)** — bold in docx. Every citation must appear in the Bibliography.

**4b. What the trial pipeline is doing.** 2–3 sentences on what trials exist, at what phase, by whom, with what status. Inline trial citations: **(NCTxxxxxxxx)** — bold.

**4c. Key records — papers AND trials.** A combined list of 4–7 entries blending the must-read papers and must-watch trials for *this* sub-area. Each entry:
- Title (linked) + identifier (PMID/DOI for papers, NCT ID for trials)
- For papers: citation count, journal + JuFo level, year
- For trials: phase, status, sponsor, enrollment
- One sentence: why this matters in this sub-area
- Flag cross-links explicitly (★ if this record cross-links to another in the document)

**4d. Search terms & filter recipes** — 6–10 keywords plus 2–3 ready-to-paste filter recipes for *future* searches. Use only valid enum values (the canonical sets are listed in "The Two Corpora" above). Examples:

```
BiomedCore: query="GLP-1 receptor agonist obesity" minJournalQualityJufo=2 minPublicationDate=2022-01-01
TrialCore:  query="semaglutide obesity"  phase=PHASE3 hasResults=true
TrialCore:  query="tirzepatide cardiovascular" overallStatus=RECRUITING interventionType=DRUG
TrialCore:  query="bempedoic acid"  overallStatus=TERMINATED   # surface negative signals
```

These recipes are gifts to the user — they can paste them straight into the MCP next time they want to dig deeper.

---

### Section 5: Who's Driving the Field

Two sub-tables — one for paper authors, one for trial sponsors. List the 3–5 most-recurring entities in each.

**Authors / labs (from BiomedCore):** Name (institution if surfaced), which sub-areas, representative paper (linked).

**Sponsors (from TrialCore):** Name (industry/academic/government), which indications/phases/modalities they're focused on, representative trial (linked).

If the same group appears as both author and sponsor (academic centers running their own trials, or industry teams publishing their results), call it out — those are the integrated science-to-clinic players in the field.

---

### Section 6: Open Questions, Gaps & Pipeline Holes

Structured into three buckets. The Pipeline Holes bucket is where this skill earns its keep — a paper-only review can't tell you what's *not* being studied clinically.

**Methodological gaps (papers).** Where are the study designs weak? Mostly observational with no RCTs? Endpoints inconsistent across trials? Sample sizes consistently underpowered?

**Population & context gaps (papers + trials).** Which populations are missing? Pediatric? Geriatric? Specific ethnic groups? Real-world vs. controlled-setting?

**Pipeline holes (trials).** This is the unique-to-this-skill section.
- **Indication holes** — diseases the basic science suggests are tractable but no one is running trials for
- **Modality holes** — modalities (small molecule, antibody, ASO, cell therapy) that aren't represented in the pipeline despite mechanism support
- **Phase holes** — areas with lots of Phase 1 but no Phase 2/3 (translation gap), or many old completed Phase 3s but nothing newer (innovation gap)
- **Geographic holes** — trial activity concentrated in one region
- **Termination clusters** — multiple terminated trials in a sub-area can signal a known but unpublished problem

For each gap, briefly state **why it matters** — what we don't know because the gap exists.

---

### Section 7: Bibliography & Trial Registry

Two alphabetized sections. Every citation in the document must have a matching entry.

**Papers (BiomedCore)** — sorted by first-author last name:
`Author(s) (Year). Title. Journal [JuFo X]. PMID/DOI [linked]. Amass: <amassId>.`

**Trials (TrialCore)** — sorted by NCT ID:
`NCT ID [linked]. Brief title. Sponsor. Phase, Status. Started YYYY-MM. Enrollment: N. Amass: <amassId>.`

Always use the **stable URL** (`https://pubmed.ncbi.nlm.nih.gov/...`, `https://doi.org/...`, `https://clinicaltrials.gov/study/...`). Always include the `amassId` in plain text — it's the canonical identifier for a future Amass call.

---

### Section 8: Audit Log

Transparency about how the briefing was built. The reader needs this to calibrate trust.

**Calls executed** — table:

| # | Tool | Query / ID | Filters | Records returned | Status |
|---|---|---|---|---|---|
| 1 | search_amass_biomedcore_records | "semaglutide cardiovascular outcomes" | minJournalQualityJufo: 2 | 10 | Success |
| 2 | search_amass_trialcore_records | "semaglutide obesity" | phase: PHASE3, hasResults: true | 7 | Success |
| 3 | get_amass_biomedcore_record | pmid: 35658024 | includeFulltext: true | 1 | Success |
| 4 | search_amass_biomedcore_records | "tirzepatide MASH" | — | — | Failed once, retry succeeded (4) |

**Counts:**
- Calls executed: N (search: N, get: N)
- Calls successful: N
- Calls failed after retry: N — list which
- Unique BiomedCore records received: N
- Unique TrialCore records received: N
- Cross-links discovered: N (paper→trial: N, trial→paper: N)
- Records cited in this document: N papers, N trials

**Coverage notes:**
- Each search caps at 10 records — total coverage is bounded by `searches × 10` minus duplicates. With N successful searches, the theoretical ceiling is N×10; actual unique = M.
- Sub-areas with thin coverage (<5 records): list them and recommend follow-up: "Sub-area X returned 3 records — consider broadening terminology or running a targeted search on PubMed/CT.gov."
- Any content drawing on model knowledge rather than Amass results is labeled `[Background — model knowledge, not from Amass]` inline.

---

## Document properties — what the deliverable must look like

Whichever format you produce, the document must hold to these properties. The model is responsible for figuring out the right tool to satisfy them — if the host environment offers a docx authoring helper (a sibling skill, a library, a template), use it rather than writing raw document XML by hand.

**Page setup.** Standard letter or A4 with comfortable margins. The document should print sensibly without manual tweaks.

**Heading hierarchy.** The skill's section structure (Sections 1–8) is reflected as real document headings, in order. The Harness note (in non-interactive mode) sits at the very top, before Section 1.

**Hyperlinks.** Every cited record renders as a real hyperlink, not as inline plain-text URLs and not as bare display text without a target. Use the **full** stable URL — never truncate, never shorten through redirectors. Alongside every linked citation, include the `amassId` as plain text so a reader can re-query Amass.

**Lists.** Bullet lists for enumerated items (e.g., the priority reading list, the watch list, the search-recipe blocks). Don't fake bullets with bare characters — use the format's native list construct so they render correctly across readers.

**Tables.** Use proper tables with visible borders for the timeline (Section 3), the audit log (Section 8), and any other tabular content. Column widths should be readable without manual resizing in a typical reader.

**Citations within prose.** Inline citations in Sections 4a/4b appear in bold (`**(Author et al., Year)**` for papers, `**(NCTxxxxxxxx)**` for trials). The bold treatment makes the cross-reference scannable.

**Pre-delivery sanity check.** Before declaring the briefing done:
- Confirm the file opens cleanly in its target reader (no corruption, no XML errors).
- Every inline citation has a matching entry in Section 7.
- Every entry in Section 7 is cited at least once in the prose.
- Every "see Section N" pointer or "★ cross-link" callout resolves to something actually in the document.
- Every hyperlink leads somewhere real — broken links erode the trust this skill is built on.

If the host environment provides a document validator (some skills bundle one), run it. If validation fails, fix the underlying issue rather than hiding the error.
