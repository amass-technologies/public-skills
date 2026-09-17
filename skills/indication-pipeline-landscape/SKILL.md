---
name: indication-pipeline-landscape
description: Use when a competitive-intelligence analyst names one indication (e.g. "MASH", "nonalcoholic steatohepatitis", "obesity") and wants the current Phase-3 drug pipeline mapped across every sponsor — running two or more TrialCore searches and unioning them to broaden coverage past any one query's relevance ranking, tagging each program novel-agent vs repurposed-generic, and overlaying the cross-core referencesBiomedCore publication-evidence count as a maturity proxy — emitted as one sorted .xlsx matrix.
license: Apache-2.0
metadata: { author: amass, version: "0.1.0" }
---

# Indication pipeline landscape

A competitive-intelligence analyst names **one indication** and gets back the current **Phase-3
drug pipeline** for it: every program enumerated across every sponsor, each row tagged
**novel-agent vs repurposed-generic/supplement**, and each overlaid with the count of publications
Amass links to that trial — a **program-maturity proxy** — assembled into one downloadable `.xlsx`
sorted by evidence count. The single input is the indication name. The hallucination delta: asked to
"map the Phase-3 pipeline," a plain LLM recites half-remembered programs and fabricates NCT numbers,
sponsors, and publication counts; this skill returns the real registry records and the real
`referencesBiomedCore` edge lengths.

## When to invoke

- A CI / strategy analyst (or scientific founder, BD lead, equity analyst) asks to **map an
  indication's current late-stage competitive pipeline** — "who is in Phase 3 for X," "show me the
  X landscape," "which programs are mature vs pre-readout."
- The analyst wants the answer **graded by evidence depth** (how much published literature each
  program has generated), not just a flat list of trials.
- Generalizes to any indication: MASH, NASH, obesity, IPF, ulcerative colitis, HFpEF, etc. MASH is
  the anchor example, not a hard-coded assumption.

**Do NOT invoke when** the input is a single asset or a single trial ID (this skill is
indication-scoped, not asset- or trial-scoped), or when the user wants a literature review rather
than a pipeline map.

## Inputs

| Input | Required | Notes |
|---|---|---|
| `indication` | yes | The disease/indication name. Anchor example: `MASH (metabolic dysfunction-associated steatohepatitis)`. |
| `query_angles` | optional | Two or more spellings/terminologies to union (e.g. modern "metabolic dysfunction associated steatohepatitis" + classic "nonalcoholic steatohepatitis liver fibrosis"). Default: derive sensible variants — the search engine is bag-of-tokens with no stemming, so spellings are distinct tokens. |
| `phase` | optional | TrialCore `phase` enum. Default `PHASE3`. |

Strip punctuation (quotes, hyphens, parentheses) from the indication before building each query
string — the search engine treats them as garbage tokens.

## The Amass MCP calls (exact sequence)

1. **Search, angle A.** `search_amass_trialcore_records(query="<modern spelling>", phase="PHASE3",
   interventionType="DRUG")` → up to `limit` trials. There is no `sponsorType` filter, so the
   multi-sponsor view comes free from the indication query itself.
2. **Search, angle B.** `search_amass_trialcore_records(query="<classic spelling>", phase="PHASE3",
   interventionType="DRUG")` → up to `limit` trials. Run one search per terminology variant.
   **No total:** `limit` is 1–50 and defaults to 10, and a search never returns a `total` — so this is
   a **sample, not a census**, at any limit. Raising `limit` and unioning terminology variants both
   broaden coverage; neither makes it exhaustive. Say so in the output.
3. **Union + dedupe** the result sets on `nctId`. (Anchor: A=10, B=10, overlap=2 → **18 unique**.)
4. **Per-trial cross-core walk.** For each unique NCT,
   `get_amass_trialcore_record(type="nctId", value=<NCT>)`. Read `referencesBiomedCore` — the array
   of `AMBC_` IDs of publications describing the trial. Its **length** is the program's
   `evidence_paper_count`. Also read the identity fields verbatim: `acronym`, `interventionNames`,
   `sponsorName`, `phase`, `overallStatus`, `hasResults`.
   - **Rate-limit batching (fan-out):** keep each batch to **≤40–55 calls, then pause 10 s** to stay
     under 60 requests / 60 s per user+org. On a `429`, read `Retry-After` and back off exponentially
     before resuming.
   - **Metadata-only overflow recovery:** reading a trial's `referencesBiomedCore` *length* never
     overflows — you count IDs, you do not fetch the papers. If you optionally drill into a linked
     landmark paper and that `get-by-ID` overflows on a huge `citedBy`, recover by re-reading just
     the needed metadata fields rather than dropping the row.
5. **Client-side intervention-class tag (a wrapper-gap workaround).** The MCP exposes no
   intervention-class field, so tag by inspecting `interventionNames`: `novel-agent` (new molecular
   entity / incretin), `repurposed-generic` (an approved generic, e.g. losartan, estradiol, an SGLT2
   inhibitor), or `supplement` (e.g. butyrate). When the class is not obvious, flag for review rather
   than guessing.

## Output template

A markdown table sorted by `evidence_paper_count` **descending**, then the `.xlsx`. Columns:

`NCT | acronym | drug (interventionNames) | sponsor | phase | overallStatus | intervention_class | evidence_paper_count | hasResults`

Then:

- A **Read** line: how many novel-agent vs repurposed/supplement; the evidence-count split (which
  programs are mature/published vs pre-readout at 0). Frame `evidence_paper_count` explicitly as a
  **maturity proxy** — published/approved/terminated programs accumulate linked papers; ongoing
  pre-readout programs sit near 0 — **not** an exhaustive literature search.
- A **Verdict** line: `Indication <X>: N unique Phase-3 drug trials (k-query union, overlap=j)
  across ~M sponsors; P novel-agent vs Q repurposed/supplement; evidence links concentrated in the
  mature tier (top program: <acronym> <drug>, <count> papers).`
- A **Scope note** blockquote (no-total sample-not-census; class tag read client-side; evidence count
  is a maturity proxy, not a census).
- Emit the matrix as `<indication-slug>-pipeline-landscape.xlsx`.

## Failure modes & recovery

- **Empty `referencesBiomedCore` array on a trial.** Not a failure — it is the genuine "no linked
  publication yet" case (ongoing / pre-readout / unpublished). Record `evidence_paper_count = 0`; do
  not infer the program is weak — infer only that it has no linked publication in Amass.
- **A search returns < 10 (or 0) for an angle.** Fine — that angle is just narrower; the union still
  holds. If both angles return 0, the indication term is mis-spelled or too rare for the index; try
  another terminology variant before concluding the pipeline is empty.
- **429 / rate limit.** Read `Retry-After`, back off exponentially, resume the fan-out where you left
  off — never skip a trial.
- **Get-by-ID overflow.** Recover with a metadata-only re-read; do not drop the row, do not fabricate
  a count.
- **Thin or ambiguous results.** If `interventionNames` does not make the class obvious, flag for
  review — do not invent a class. Any figure not present in a returned field is written
  **"not in abstract."** Do not guess.

## Guardrail

This skill names commercial programs and their sponsors, so it is **medium-stakes** — a wrong
"terminated/withdrawn" label is reputationally loaded. Bind these into every run:

- **Field-grounded rows only.** `overallStatus`, `sponsorName`, `interventionNames`, `hasResults` are
  rendered **verbatim from the returned TrialCore record** — never characterized. Write `TERMINATED`
  because the field says `TERMINATED`; do **not** write "failed," "abandoned," or impute *why* a
  trial stopped (`whyStopped` is not exposed by the MCP — never paraphrase it).
- **No subjective characterization** of a sponsor, drug, or program. "0 linked papers" is a checkable
  edge-count claim, not a quality judgement — state it as such.
- **The verdict is a checkable field claim** — counts and statuses that trace to the returned
  records, not an opinion about who is winning.
