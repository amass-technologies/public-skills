---
name: amass-landscape-monitor
description: Use when someone wants a whole life-science field mapped through the Amass MCP into a spreadsheet baseline and then kept current, such as a mechanism class in an indication (TAAR1 agonists in schizophrenia), a target's drug landscape (KRAS G12C inhibitors) or a drug and its competitors. Searches TrialCore, BiomedCore, DrugCore, RegulatoryCore, GeneCore and PatentCore like a systematic reviewer, facet by facet until nothing new appears, screens every record against the field's criteria, and delivers a landscape page with charts, a workbook and one ledger per Core. Later runs report what is new in the field and what changed on known records, per Core, on its update interval. Trigger on "map the landscape of", "baseline the field", "what is new in X since", "keep me current on", "competitive landscape for", "track the field". Needs the Amass MCP connector and a place to run Python and keep files; no API key. For a fixed list of known records use amass-watchlist-monitor instead.
license: Apache-2.0
metadata: { author: amass, version: "0.1.0" }
---

# Amass landscape monitor

For anyone who needs one field of life science mapped as completely as the Amass Cores allow
and then kept current: a competitive-intelligence analyst following a mechanism class, a
portfolio team watching a target, a medical-affairs team tracking a drug and its competitors.
The user names the field. You write the definition, build a search plan from facets, run it over
the Amass MCP until the facets stop producing, screen every record, and deliver a baseline: a
landscape page with charts, a workbook that opens on the same landscape by drug with one ledger
per Core underneath, and a one-page briefing folded into the page. Each later run reports what
is new in the field and what changed on the records already mapped, by drug and by Core.

Three things make this work. The files are the memory: every search result goes to disk the
moment it arrives, through `scripts/ledger.py`, so the conversation can be compacted or resumed
at any point and nothing is retyped from memory. Discovery is driven by data: the drug and gene
records come first and supply the names, codes and synonyms the other queries are built from,
and the literature and trials then extend that roster. And the numbers the user sees are
computed by the helper from the ledgers, never assembled by hand, so every figure in the
briefing can be traced to a row.

**Done, for a baseline:** every planned and expanded query is logged, each facet is saturated or
its Core's allowance is used, the anchors are checked, the roster of entities is complete enough
that few in-scope records are unassigned, each Core's ledger has a decision on every record and
unsure records are the genuine judgment calls, and the landscape page, the workbook, the summary
and the briefing exist.

**Done, for an update:** every due Core ran its passes (or a re-fetch), each new or changed
record is classified in `log/changes.csv`, the Cores not due are listed with the reason, each
finished Core's last-checked date advanced, and the workbook, summary and briefing were
regenerated with a "what changed since" section on top.

## When to use

- "Map everything on TAAR1 agonists in schizophrenia and keep me current."
- "Baseline the KRAS G12C inhibitor landscape: trials, papers, approvals, drugs, patents."
- "What is new in the amylin space since last month?" (an update, once a baseline exists)
- The skill invoked with nothing else: introduce it and ask what to map (see below).

Not for a fixed list of known records: that is `amass-watchlist-monitor`, which keeps a named
list under watch (over the MCP, or exactly over the REST change feed with an API key) and
imports the watchlist files this skill exports. Not for a one-off cited briefing: that is
`amass-biomedical-evidence-scout`.

## Where it runs

| Client | Runs the helper | State between runs | How to search |
| --- | --- | --- | --- |
| Claude Code | Bash | the field folder in the working directory | one subagent per Core and round, in parallel |
| Cowork | its sandbox shell | the field folder in the working folder | sequentially, in rounds |
| claude.ai chat with code execution | the code tool | none between conversations: zip the field folder for the user at the end, ask for it at the next run | sequentially, in rounds, smaller limits |

Setup: the Amass MCP connector (tools named `search_amass_*_records` and `get_amass_*_record`),
Python 3.9 or newer, write access to a folder. No API key. `<skill folder>` below is the
folder this SKILL.md is in; `<field>` is `landscapes/<name>` under the working directory unless
the user names another place.

## How a conversation starts

The aim is the fewest possible questions, and nothing about usage or cost. Amass meters the
MCP itself; when it says no, the section "When Amass says no" applies.

**Invoked with nothing.** Two sentences and one question: what the skill does (maps a field
through Amass into a workbook and a briefing, then reports what changed on later runs) and
"which field?", with three examples that show where it shines: a mechanism class in an
indication (GLP-1 agonists in NASH), a target's drug landscape (KRAS G12C inhibitors), a drug
and its competitors (ulotaront and the other TAAR1 agonists). The field is free text; the
examples are inspiration.

**Given a description.** Decide whether the inclusion criteria can be written from it. They can
when it names a mechanism, target, drug class or drug, and either an indication or "across
indications": then ask nothing. Write the field definition and the first plan, take the scope
decisions yourself with sensible defaults, tell the user in two or three lines what you are
mapping (the reading of the field, the Cores, the decisions taken), and start in the same turn.
When the description is vague, ask one concise question, the one whose answer changes the
ledger most, with a recommended default so that a one-word reply works. If the answer is still
vague, ask once more the same way, then proceed with your defaults and say so. Never a third
question, and none when the request is clear. Worth asking: "map KRAS" could mean G12C
inhibitors, pan-RAS agents or KRAS diagnostics. Not worth asking: whether type 2 diabetes trials
with a weight endpoint belong to an obesity field; decide it (they do) and state it.

**Use the client's question widget when it has one.** Claude Code offers a question tool with
selectable options (AskUserQuestion). Where it exists, ask through it: the three examples as
options with free text for the real answer. Where it does not exist (Cowork, claude.ai chat),
ask in prose. Never list options as numbered menus in prose.

**During the run.** No questions. Each Core has a search allowance (Baseline, step 4) that the
helper enforces; when it is used up with facets still producing, close the Core with what it
has, deliver, and say in one line that the field is wider than one run and "extend" continues it.

**At the end.** Deliver the files, then a short report in the chat: the headline of the summary,
the by-entity table, two or three sentences on coverage (anchors, cross-links, which facets were
still producing), the number of unsure records with the rule you applied and an offer to settle
the rest, and where the files are, naming the landscape page first. Then one optional offer: the
watchlist export. Nothing about credits, budgets or cost.

## The files

Everything lives in `<field>/`. Read these first after compaction or in a new session.

| File | Holds |
| --- | --- |
| `field.yaml` | The definition (question, include, exclude, cores, anchors), the entities (the roster the landscape is grouped by: name, aliases, kind), the plan (one entry per query: id, Core, facet, query text, filters, limit, update subset), the intervals, the stopping rule and the per-Core search allowance. The helper rewrites this file without comments; durable remarks go in `notes`. |
| `ledger/<core>.csv` | One row per record, keyed by `amassId`: the tracked columns, the decision (in, out, unsure) with its reason, how it was first reached (search or fetch), first and last seen date and query, seen count, the columns observed so far. Excluded records stay so updates do not propose them again. |
| `log/queries.csv` | One row per MCP call: timestamp, run, query id, Core, tool, pass, parameters, results returned, cap hit, new ids, decisions, changes. |
| `log/runs.csv` | One row per run and Core with start and finish times. A Core's last-checked date is its latest finish; it advances only when `finish-core` is run for it. |
| `log/changes.csv` | One row per detected change (run, Core, id, field, old, new, class, group) and one per in-scope or unsure record new to the ledger in an update. |
| `log/candidates.csv` | Ids reached through cross-links or anchors that no search returned: the known coverage gaps, and whether each was resolved, dismissed or is still open. |
| `log/summary-<run>.md` | The deterministic run summary: headline, scope, by-entity table, coverage, changes. The briefing is written from it. |
| `<field>-landscape.html` | Regenerated at the end of each run: the landscape page, one self-contained file with the pipeline and activity charts by drug, papers and patent families per year, the by-entity table, the briefing, the key records with registry links, the coverage box, the scope and the instructions. The file to open first and the file to send. |
| `<field>.xlsx` | Regenerated at the end of each run. Sheets in order: Landscape, Key records, one per Core, Excluded, Queries, Candidates, Changes, New. |
| `<field>-briefing.md` | The one-page briefing you write for the user from the summary and the ledgers; the page folds it in. Exactly one briefing file per field. |

The helper keeps nothing in memory between commands, so `status` after compaction shows
exactly where a run stands and which query ids are still to run.

## Baseline

### 1. Define the field, on paper first

From the request, write the inclusion criteria (what a record must be about to belong), the
exclusion criteria (the near neighbours that do not), and the Cores. List the facet seeds you
already know: mechanism and target terms, drug names with their development codes, indications,
sponsors. These are only seeds; the roster round replaces guesswork with Amass data.

### 2. Write `field.yaml` and a small first plan

Create `<field>/field.yaml` (see the worked example and `templates/field.example.yaml`). The
first plan is small and grows after measurement: the roster queries (DrugCore for the mechanism
and for each drug name you know, GeneCore for the target genes) and two or three queries per
facet per Core, about twenty searches for a narrow field. Expansions are added later through
`add-query`, after `saturation` and `terms` have shown where the field still produces, within
each Core's allowance (step 4). Give each
query an id by Core letter (T for TrialCore, B BiomedCore, D DrugCore, R RegulatoryCore, G
GeneCore, P PatentCore), a facet name (drug, code, mechanism, target, indication, sponsor,
class), the Core's filters where they sharpen it, and `update: true` on the two to four most
specific queries per Core (names and codes), which an update reruns with a date filter. Set
`limit` to 50 for TrialCore and 30 for papers and patents in Claude Code, 30 and 20 in Cowork
and chat, 10 to 20 for narrow probes. Then:

```bash
python3 <skill folder>/scripts/ledger.py validate --field <field>
python3 <skill folder>/scripts/ledger.py plan --field <field>
```

`plan` prints the plan and its context load; use it to choose the fan-out, not to ask. Tell the
user in two or three lines what you are mapping and start.

### 3. Run the plan

```bash
python3 <skill folder>/scripts/ledger.py start-run --field <field> --mode baseline
```

Round 0, the roster. Run the DrugCore and GeneCore queries and ingest them. Put every in-scope
drug into the plan as an entity with its synonyms and codes, and every target gene as a
target entity:

```bash
python3 <skill folder>/scripts/ledger.py add-entity --field <field> --name ulotaront --alias SEP-363856 --alias SEP-856 --kind drug
python3 <skill folder>/scripts/ledger.py add-entity --field <field> --name TAAR1 --kind target
python3 <skill folder>/scripts/ledger.py add-entity --field <field> --name "TAAR1 agonists" --alias "TAAR1 agonist" --kind class
```

A drug entity is matched on every Core. A target entity is matched on gene records, papers and
patents, not on trials, because a trial is about its drug. A class entity (one per field is
usual) is the fallback bucket for records that name no drug or target: class reviews, class
patents. Fetch the main drug records too (1 call each): a DrugCore fetch carries its linked
trials, papers and authorizations, which you ingest as `links` so the helper can tell you
which linked ids the searches have not reached. Do not probe DrugCore for development codes the
literature surfaces later; DrugCore is anchored on ChEMBL and assigns records to newer
compounds with a lag, so their trials and papers are reached by name and code on the other
Cores, and the entity carries them.

Round 1 onwards, per Core: run the queries for that Core, one at a time. For every search:

1. Call the search tool with the query text, filters and limit from the plan.
2. Screen each result against the criteria and decide in, out or unsure, with a short reason
   for out and unsure. Screen on the evidence in the result (title, interventions, conditions,
   indication text, abstract), not on the query that found it.
3. Ingest every result, excluded ones included, straight from the tool result. When the client
   saved the tool result to a file (Claude Code does this for large results and tells you the
   path), pass the file and only your decisions; the helper extracts every tracked column itself:

```bash
python3 <skill folder>/scripts/ledger.py ingest --field <field> --query-id T01 --raw <path to the saved result> --default out "not a TAAR1 agonist" <<'JSON'
{"AMTC_...": ["in", "ulotaront, schizophrenia, phase 3"], "AMTC_...": {"decision": "unsure", "reason": "mechanism not stated"}}
JSON
```

   Otherwise pass flat rows: `amassId`, `decision`, `reason`, then the tracked columns copied
   exactly as the tool returned them (table under Per Core). For in-scope and unsure records
   include every tracked column the result carries; for excluded records a minimal row
   (amassId, decision, reason, title) is enough. The helper validates enums, dates, numbers and
   ids and rejects the whole batch on any slip, so a typo costs a retry, never a corrupt ledger.
   Nested objects on GeneCore can be pasted whole.
4. Read the one-line answer: how many returned, how many new and in scope, the facet's verdict.
   Do not repeat results in the conversation; the ledger has them.

In Claude Code, after round 0, fan out: one general-purpose subagent per Core and per round of
at most four queries, given the field path, the run id, the Core, the query ids, and this brief:
"Read `<field>/field.yaml`. Run `status`, then run the named queries for `<core>` that are not
yet logged, one at a time: search, screen against the criteria, ingest before the next search.
Return only the ingest summary lines and one sentence naming any drug names or codes you saw
that no entity or query carries." Four 50-result searches on BiomedCore or PatentCore fill a
subagent's context, which is why every result is ingested before the next search and rounds
stay short; a subagent that runs out of context after searching but before ingesting has made
calls whose results nobody will see. Ledgers are per Core and every write takes the field lock,
so rounds of the same Core may run in parallel. Sequential clients do the same in rounds of
three or four queries, writing after each.

### 4. Grow the plan until the facets stop producing

```bash
python3 <skill folder>/scripts/ledger.py saturation --field <field>
python3 <skill folder>/scripts/ledger.py terms --field <field>
python3 <skill folder>/scripts/ledger.py entities --field <field>
python3 <skill folder>/scripts/ledger.py add-query --field <field> --core trialcore --facet drug --query "ralmitaront RO6889450" --limit 50 --update
```

The stopping rule is in `field.yaml` (`stopping.consecutive`, `stopping.minNew`; default 2 and
2): a facet is saturated when its last two queries each added fewer than two new in-scope
records. A saturated facet gets no more queries. A facet still producing gets the next phrasing:
another synonym, a code on its own, the indication combined with the mechanism, a status or
phase filter that lets a crowded query show its tail. `terms` lists values seen on in-scope
records that no query mentions; `entities` lists in-scope records that no entity matches, so
you add aliases, and drugs with no trial in scope, which get their sponsor's name as a query.
Every drug name or code a subagent reports becomes an entity and, if it is in development, a
query. The allowance bounds the effort: after the roster round, set it from the field's breadth,
`allowance --breadth narrow` for one drug class in one indication (10 TrialCore searches, 8
BiomedCore, 6 PatentCore), `medium` for a target with several drugs (16, 12, 8), `broad` for a
class across indications (24, 16, 10). `add-query` refuses a query past the Core's allowance,
and `ingest` says how much is used; a plan that is bigger than its allowance is a plan that was
written before measuring. Stop a Core when every facet is saturated or the allowance is used,
whichever comes first, and leave the rest to "extend". Each run also has a cap on its calls
(`budget` in `field.yaml`): when `ingest` says `RUN CAP REACHED` (exit code 2), make no more calls,
close the Cores, write the briefing and report what is mapped, offering "extend". Do not probe
DrugCore for the codes the literature surfaces, and give the sponsor facet only to sponsors that
appear on in-scope records or to roster drugs that have no trial yet.

**Unsure is for judgment calls, not for a category.** When the same kind of record keeps landing
unsure (broad class reviews that mention the field in passing, basic physiology without a drug,
formulation patents claimed for another indication), decide the category once, write the rule
into the field's `notes`, apply it to the rows already in the ledger (ingest them again with the
decision), and name the rule in the report. Unsure should be a short list the user can settle in
minutes, well under a tenth of what is in scope: a review is in when the class has its own
section and out when it is a mention; physiology is in only with a drug or a weight angle;
a formulation patent is in when its claims reach the field's indication.

Snowballing is the second discovery path. Each fetch of an in-scope drug or trial returns
cross-linked ids; the `links` you ingest become candidates when the ledger lacks them:

```bash
echo '["AMTC_...", "AMTC_..."]' | python3 <skill folder>/scripts/ledger.py crosscheck --field <field> --core trialcore --source AMDC_...
```

The reached fraction is an independent recall estimate: when a drug's linked trials are nearly
all in the ledger, the plan is reaching the field. Fetch the open candidates (1 call each),
starting with the Core the user cares about most, and ingest them as fetch rows; they stay
marked as reached by fetch, which is a true statement about the plan's gap. Do this before
closing a Core: a closed Core takes no more rows in that run.

### 5. Check recall with anchors

Anchors are five to ten records that must be in the field: the user's, or the obvious ones from
the roster (the pivotal trials, the approval, the lead drug). Put them in `field.yaml` as
external ids (NCT, PMID, DOI, ChEMBL, application number, publication number) and run:

```bash
python3 <skill folder>/scripts/ledger.py anchors --field <field>
```

An anchor found by search is reached by the plan. One found only by fetch, or missing, means the
plan has a gap: find the record's own terms (fetch it if needed), add a query that carries them,
run it, and check again.

### 6. Close, deliver, report

```bash
python3 <skill folder>/scripts/ledger.py finish-core --field <field> --core trialcore   # once per Core, after its snowball fetches
python3 <skill folder>/scripts/ledger.py finish-run --field <field>
```

`finish-run` writes `log/summary-<run>.md`, `<field>.xlsx` and `<field>-landscape.html`. Then
write the briefing (next section), run `export` once more so the page folds it in, and report in
the chat as described under "At the end".

## The briefing

`<field>-briefing.md`, one page, written by you from `log/summary-<run>.md` and the ledgers,
never from memory. Every count comes from the summary's tables; names, acronyms, sponsors and
dates come from ledger rows. If a figure you want is in neither, leave it out rather than count
by hand. One briefing file per field, overwritten on each run. Structure:

1. **The field in three sentences:** what it is, how many drugs are in play and at what stage,
   where the activity is (trials by phase and status, papers in the last two years).
2. **By drug:** one short paragraph per drug entity that has trials, in order of the table: its
   trial count by phase, active versus completed, results posted, papers, patents,
   authorizations. State the facts in the ledger; do not add knowledge from memory.
3. **Authorizations:** what exists. When none exists, say the drug class has no approval yet; that
   is a fact about the field.
4. **Coverage:** anchors, cross-link recall, which facets saturated and which were still producing,
   the unsure count and what kind of records they are.
5. **How to use the files:** the landscape page is the document to read and to send; the
   workbook opens on Landscape; Key records lists the Phase 2 and later trials and the
   authorizations; the Core sheets hold every in-scope record with its reason; Excluded holds
   what was screened out; the ledgers are the memory for updates, so the folder must be kept;
   say "run the update" to get what changed.

The page is the document. It folds the briefing in, prints to PDF from any browser, and needs
no software. Make a Word copy only if the user asks for one. On an update, the briefing opens
with "What changed since <date>", built from the summary's changes-by-entity list, and the rest
is refreshed.

**How to speak about what is not there.** Every absence has one of three causes, and the
briefing says which: the thing does not exist (no authorization because the drug is not
approved anywhere yet), the source does not carry it by design (a newer compound without a
ChEMBL record, whose trials and papers the plan reached by name), or the plan did not reach it
(a code the registry text does not carry). Only the third is a gap; name it with the one thing
that would close it, such as the sponsor's trial ids. Lead with what the map shows.

## Searching like a systematic reviewer

- **Facets, not one big query.** Search is relevance-ranked and capped at 50 per call, so one
  query sees one slice. Cover the field from several directions: drug names, development codes,
  mechanism phrases, target symbols, indication plus mechanism, sponsors, and a class term.
  A trial id or a sponsor drug code in the query ranks those trials first, so codes are the
  sharpest trial queries.
- **One code per query.** Several codes in one query match each other's fragments: "BEBT-607
  FMC-376 AB-001" returned TTP-607, ximelagatran (H-376) and RST-001. Give each code its own
  query, or one name plus its own code, at a small limit.
- **Filters sharpen, they do not replace text.** Every search needs text. Use `phase`,
  `overallStatus`, `interventionType`, `agency`, `drugType`, `maxClinicalStage`,
  `minPublicationDate` and the rest to let a crowded query show more of its tail. A filter does
  not rescue a generic word: `assignee=Sunovion` with "TAAR1 agonist" returned that assignee's
  bronchodilators, ranked on "agonist". Keep the specific term in the text.
- **DrugCore names drugs; it does not enumerate a class.** "TAAR1 agonist" returned ulotaront and
  49 unrelated agonists; ralmitaront surfaced only by name. The roster grows from BiomedCore and
  TrialCore results as much as from DrugCore, and DrugCore search results carry no mechanisms of
  action (the fetch does).
- **GeneCore is fetch-first.** A field has a handful of target genes; search once by symbol at a
  small limit, or fetch by Ensembl id, and ingest that. A gene search result is enormous.
- **RegulatoryCore sweeps label and EPAR text**, so an indication phrase surfaces products whose
  label merely mentions it. Screen on `activeSubstance` and `therapeuticIndication`.
- **Screen on evidence.** Decide in or out from the record, and write the reason in the row.
  Keep `unsure` for records whose relevance the user has to call. A withdrawn or terminated
  trial, a retracted paper or a withdrawn authorization is still in the field; status is
  tracked, it is not an exclusion.
- **Duplicates are records.** One trial can be two records (an NCT record and a CTIS, EUCTR or
  JPRN registration with the same title); keep both, they are counted as registry records.
  PatentCore returns one member per family; the Landscape counts families.
- **Context hygiene.** Write every result to the ledger immediately, keep only the counts, never
  echo results back, never reconstruct a record from memory.

## Update

```bash
python3 <skill folder>/scripts/ledger.py start-run --field <field> --mode update        # add --force to run every Core
```

The interval table (days; the default is copied into `field.yaml` on creation, one line each):

| Core | Interval | Note |
| --- | --- | --- |
| BiomedCore | 1 | refreshed daily |
| TrialCore | 1 | refreshed daily |
| RegulatoryCore | 7 | refreshed weekly |
| GeneCore | 7 | refreshed weekly |
| DrugCore | 90 | drug records change slowly; changes arrive in weekly batches, so set 7 to follow them closely |
| PatentCore | 90 | irregular, no Amass dates on MCP |

`start-run` prints, for every Core, whether it is due (last checked plus interval is in the
past) or skipped with the reason, and opens the due ones with `since` set to last checked minus
one day. For each due Core:

1. **Pass created.** Rerun the Core's update subset with `--pass created`; the helper adds
   `minCreateDate`. A record new to the ledger that is in scope is "new-to-amass".
2. **Pass updated.** Rerun the subset with `--pass updated` (`minLastUpdateDate`). A record new
   to the ledger is "newly-matched"; a known record whose tracked columns differ is "changed",
   with field, old and new; a known record with no difference is counted as rewritten but
   unchanged. `start-run` prints the cheaper alternative: when the in-scope ledger is small
   (GeneCore, usually RegulatoryCore and DrugCore), re-fetch each in-scope record instead and
   ingest the fetch rows with `--pass updated`; fetches are exact and, for GeneCore and
   RegulatoryCore, return the write dates.
3. **A dated pass still returns a full page.** The date filter narrows the pool to records
   written in the window, and the ranking then returns the best matches in that pool however
   weak: a one-day "ulotaront" pass returned the one ulotaront trial written that day and 49
   unrelated trials. The in-scope records rank first, the tail is filler. So the update subset
   is the specific queries (names, codes) at `limit` 20 to 30; screen from the top and stop at
   the first run of unrelated records; ingest with `--raw ... --default drop "date-window
   filler"` (or omit the filler rows and pass `--returned <page size>`), so the ledger does not
   fill with records that will never return. A cap hit on a dated pass is normal. The sign of a
   bulk rewrite (most TrialCore records were rewritten on 2026-08-24, most BiomedCore papers
   touched by a citation refresh on 2026-09-30) is many known in-scope records coming back
   rewritten but unchanged; then re-fetch the in-scope records when their number is modest,
   otherwise split the query by facet, and say which you did. Being returned by a
   `minLastUpdateDate` search is not a change; only the diff is.
4. **PatentCore** has no Amass dates on MCP: one pass with `--pass published` sets
   `minPublicationDate`, which finds newly published patents only, and the summary says so.
5. `finish-core` each Core as it completes (that is what advances its last-checked date), then
   `finish-run`, then rewrite the briefing with "What changed since" on top. Relay the headline,
   the changes by entity, and the skipped Cores with their reasons.

## When Amass says no

The MCP meters usage itself. A tool result that says the plan's MCP usage is exhausted
means: stop calling, close the Cores with what is already logged (`finish-core`,
then `finish-run`), write the briefing from what was mapped, and tell the user plainly that the
Amass plan's MCP usage is exhausted, relaying the service's own message, which says whether to
wait or to upgrade. Nothing is lost: the same request later resumes from the files (`status`
names the remaining queries). A rate-limit response is retried after a pause; a timeout is
retried once; neither is mentioned to the user.

## Per Core

| Core | Search tool | Tracked columns from search | Fetch adds | Date filters on MCP | Blind spots |
| --- | --- | --- | --- | --- | --- |
| TrialCore | `search_amass_trialcore_records` | nctId, registryId, sourceRegistry, sourceUrl, briefTitle, acronym, sponsorName, phase, overallStatus, studyType, startDate, completionDate, enrollment, conditions, interventionNames, interventionTypes, facilityCountries, hasResults | `links` (referencesBiomedCore, referencesDrugCore) | minCreateDate, minLastUpdateDate, minStartDate | results-only revisions, deletions, no write dates |
| BiomedCore | `search_amass_biomedcore_records` | pmid, doi, url, title, journal, publicationDate, authors, citationCount (metadata), journalQualityJufo, hasFulltext, isRetracted | `links` (referencesTrialCore) | minCreateDate, minLastUpdateDate, minPublicationDate | in-place publicationDate corrections, citation refresh noise, no write dates |
| RegulatoryCore | `search_amass_regulatorycore_records` | agency, name, activeSubstance, moleculeType, authorizationStatus, procedureType, therapeuticIndication, marketingAuthorisationHolder, authorizationDate, sourceUrl, isOrphan, authorizationsByAgency | applicationNumber, productNumber, designations, labelDate, smpcDate, revisionNumber, sectionCount, lastUpdateDate, createDate, `links` | minCreateDate, minLastUpdateDate, min/maxAuthorizationDate | section-text-only revisions |
| DrugCore | `search_amass_drugcore_records` | chemblId, url, name, synonyms, tradeNames, drugType, maxClinicalStage, description | mechanisms, parent, `links` (trials, papers, authorizations, genes) | minCreateDate, minLastUpdateDate | no write dates; link changes alone do not move the drug |
| GeneCore | `search_amass_genecore_records` | ensemblGeneId, symbol, name, synonyms, geneType, hgncId, targetClass, tractability, safetyEvents, loeuf, isEssential, lastUpdateDate, createDate (fetch) | `links` (referencesDrugCore) | minCreateDate, minLastUpdateDate | weekly batches; a changed Ensembl id is a new record |
| PatentCore | `search_amass_patentcore_records` | publicationNumber, countryCode, kindCode, familyId, title, assignees, cpcCodes, publicationDate, filingDate, grantDate, priorityDate, citedByCount | `links` (drugs, papers) | minPublicationDate only | no Amass dates, no feed, family collapsing |

Change groups the helper assigns: status (phase, overallStatus, authorizationStatus, isOrphan,
designations, drugType, maxClinicalStage), results (hasResults), dates, enrollment, retraction
(isRetracted), text (titles, indication text, names), links, metadata (citationCount,
citedByCount, write dates, section counts), other (conditions, interventions, synonyms). Report
metadata changes as counts, not news.

## The helper

`python3 <skill folder>/scripts/ledger.py <command> --field <field>`; `--help` lists everything.

| Command | Use |
| --- | --- |
| `validate` | after writing or editing `field.yaml` |
| `plan` | the plan with its context load, for choosing the fan-out |
| `start-run --mode baseline\|update [--force] [--core X]` | open a run; update mode prints the due table |
| `ingest --query-id T01 [--pass created\|updated\|published] [--raw FILE --default out\|drop "reason"] [--returned N]` | after every search; omit `--query-id` for a fetch; `--raw` reads a saved tool result and takes decisions on stdin; `drop` counts filler without storing it |
| `add-query --core --facet --query [--filter k=v] [--limit] [--update] [--force]` | expansions; never run a search that is not in the plan; refused past the Core's allowance |
| `allowance --breadth narrow\|medium\|broad`, `allowance --core X --searches N` | the searches each Core may plan, set after the roster round |
| `add-entity --name --alias A --kind drug\|target\|class` | the roster; aliases are codes, synonyms, trade names |
| `entities` | the map by entity, the in-scope records no entity matches, drugs with no trial |
| `saturation`, `terms`, `anchors` | after each round |
| `crosscheck --core X --source <id>` | an id list on stdin against the ledger |
| `dismiss --core X --id <amassId>` | a candidate that is out of scope by design (a drug's other target gene) |
| `status` | first thing after compaction or in a new session |
| `finish-core --core X`, `finish-run` | close; `finish-run` writes the summary, the workbook and the landscape page |
| `abandon-run` | drop an open run (its logged calls stay) |
| `export`, `watchlist --core X`, `intervals` | rebuild outputs, export in-scope ids, print defaults |

Ingest rows are flat JSON objects: `amassId`, `decision`, `reason`, then tracked columns by
their names above; `links` is an object mapping a Core name to a list of Amass ids. Unknown keys,
bad enums, non-ISO dates and ids of the wrong Core reject the batch with the reason, and nothing
is written. The helper counts its calls in `log/` to hold each run to its cap; the count appears in
no deliverable and never in the chat.

## Worked example

```yaml
name: taar1-schizophrenia
question: TAAR1 agonists and ulotaront in schizophrenia and related psychiatric indications
cores: [drugcore, genecore, trialcore, biomedcore, regulatorycore, patentcore]
include:
  - Clinical trials of any TAAR1 agonist in a psychiatric indication, any phase or status
  - Papers reporting clinical or translational work on a TAAR1 agonist, or TAAR1 as a psychiatric target
  - Drugs whose mechanism includes TAAR1 agonism; the TAAR1 gene record
  - FDA or EMA authorizations of a TAAR1 agonist
  - Patents claiming TAAR1 agonist compounds or their psychiatric use
exclude:
  - Solriamfetol in sleep indications (TAAR1 activity is incidental there)
  - Trace amine biology without an agonist or a psychiatric angle
anchors:
  - {core: trialcore, id: NCT07759128, note: a Phase 3 ulotaront trial}
entities:
  - {name: ulotaront, aliases: [SEP-363856, SEP-856], kind: drug}
  - {name: ralmitaront, aliases: [RO6889450, RG7906], kind: drug}
  - {name: TAAR1, kind: target}
  - {name: TAAR1 agonists, aliases: [TAAR1 agonist], kind: class}
stopping: {consecutive: 2, minNew: 2}
queries:
  - {id: D01, core: drugcore, facet: mechanism, query: TAAR1 agonist, limit: 50}
  - {id: D02, core: drugcore, facet: drug, query: ulotaront SEP-363856, limit: 20}
  - {id: G01, core: genecore, facet: target, query: TAAR1, limit: 3}
  - {id: T01, core: trialcore, facet: drug, query: ulotaront, filters: {interventionType: DRUG}, limit: 50, update: true}
  - {id: T02, core: trialcore, facet: code, query: SEP-363856, limit: 50}
  - {id: T03, core: trialcore, facet: mechanism, query: TAAR1 agonist schizophrenia, limit: 50}
  - {id: B01, core: biomedcore, facet: drug, query: ulotaront, limit: 30, update: true}
  - {id: B02, core: biomedcore, facet: mechanism, query: TAAR1 agonist schizophrenia clinical, limit: 30}
  - {id: R01, core: regulatorycore, facet: drug, query: ulotaront, limit: 20, update: true}
  - {id: P01, core: patentcore, facet: mechanism, query: TAAR1 agonist schizophrenia, limit: 30, update: true}
```

After round 0 the DrugCore ledger lists the other TAAR1 agonists and their codes; `entities`
and `terms` name what no query carries; those become T04 onwards and B03 onwards through
`add-query`. The full plan this grew into, with its summary and briefing, is in `templates/`.

## What this cannot see

Search is relevance-ranked top-K, so the ledger is as complete as the plan and the saturation
numbers say, never more. Date-filtered searches cannot see deletions, results-only revisions on
trials, label or SmPC revisions confined to section text, or BiomedCore `publicationDate`
corrections made in place; the REST change feed used by `amass-watchlist-monitor` sees all four,
which is why the watchlist export exists. A record returned by a `minLastUpdateDate` search was
rewritten by Amass, and on bulk refresh days that means nothing changed: only a diff of tracked
columns is a change. PatentCore has no Amass dates, so its updates find newly published patents,
not newly ingested ones.

## After compaction or in a new session

Read `<field>/field.yaml`, then run `status`. It names the open run, the query ids still to run
per Core (and, in an update, the passes still to run), the anchors and the open candidates.
Continue from the first unlogged query. Never redo a logged query; never add a ledger row from
memory.

## Optional watchlist export

```bash
python3 <skill folder>/scripts/ledger.py watchlist --field <field> --core trialcore
```

Writes `<field>/watchlists/<field>-<core>.yaml`, which `amass-watchlist-monitor` imports
(`board.py add --watchlist`). Offer it once at the end of a baseline for the Cores the user cares
about; the watchlist monitor then tracks those records exactly while this skill keeps finding new
ones.

## Observed runs

For the skill's maintainer, from validation runs in October 2026. A narrow field (TAAR1 agonists
in schizophrenia, six Cores) took 35 searches and 3 fetches in Claude Code with parallel
subagents, about 25 minutes after the roster round, and ended with 52 trials, 159 papers, 3 drug
records, 1 gene, no authorization and 45 patents in scope, 3 of 3 anchors by search, and all 32
drug-linked trials reached by search. A target field (KRAS G12C inhibitors) took 38 searches and
20 fetches: 304 trials, 272 papers, 10 drugs, 4 authorizations, 85 patents in scope, 4 of 4
anchors; fetching the 18 drug-linked trials the searches had missed found 17 in scope. A Cowork
run on amylin analogues in obesity logged 126 calls in 22 minutes sequentially: 225 trials, 260
papers, 111 patents, 6 of 6 anchors, 95 of 121 drug-linked trials reached by search and the rest
fetched. Before the allowance existed that run planned 97 searches, 28 of which added nothing,
and left 106 records unsure before the category rule existed. A search returns up to 50 records in
one call whatever its limit, so a larger limit adds no call.

## Limits

One field per folder. Discovery and change detection are approximate by construction (see
above). No email or Slack delivery: the briefing is a file and the report is your message.
PatentCore updates by publication date only. `research_search` and `patent_search` (Semantic
Scholar and Lens) are not used: their records have no Amass ids and cannot be ledgered.
