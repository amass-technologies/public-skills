---
name: amass-watchlist-monitor
description: Use when someone wants a fixed list of known Amass records kept under watch and told what changed since the last look, such as competitor trials (NCT ids), the papers behind a review (PMIDs, DOIs), FDA and EMA authorizations, drugs or targets. Works in Claude chat, Cowork and Claude Code through the Amass MCP connector, no API key needed. Builds the list with the user, keeps it in an Excel board file that is also its memory, and on each check re-reads the records, compares them field by field in a script, and reports status changes, results posted, label and SmPC revisions, retractions, new cross-links and records gone from Amass. With an Amass API key it can instead run an exact monitor over the REST change feed, for automation or long lists. Trigger on "watch these trials", "set up a watchlist", "check my watchlist", "tell me when results post", "retraction watch", "track label changes", or an attached board file. Not for mapping a whole field (amass-landscape-monitor) or PatentCore.
license: Apache-2.0
metadata: { author: amass, version: "0.2.0" }
---

# Amass watchlist monitor

For a competitive-intelligence analyst, a medical-affairs or systematic-review team, regulatory
affairs, or portfolio and BD: anyone who holds a list of specific records and needs to know what
moved on them. You build the list with the user through the Amass MCP connector and keep it in one
Excel file, the **board**. Each later check re-reads the records through the connector, and
`scripts/board.py` compares them field by field with the copies stored in the board and writes a
digest: status and stage changes, results posted, label and SmPC revisions, retractions, new
cross-links, records gone from Amass. The board is the only memory: in Claude chat the user keeps
the file and attaches it next time; in Cowork and Claude Code it stays in its folder. You never
compare values yourself and never rely on the conversation to remember anything.

There are two modes. Default to MCP mode.

| | MCP mode (this file) | API mode (`references/api-mode.md`) |
| --- | --- | --- |
| Needs | the Amass connector and code execution | `AMASS_API_KEY` and a shell that reaches `api.amass.tech` |
| Runs in | Claude chat, Cowork, Claude Code | Claude Code, scheduled jobs, CI |
| Sees | current state of each record at each check | also revised results, why a trial stopped, which label sections changed, removals, the date of each change |
| Costs | MCP credits: a few per daily check, 1 per record for a weekly full check | API credits: about 1 cent per 100 records a day |

Offer API mode only when the user needs what only it sees, watches hundreds of records, or wants
the watch scheduled or built into their own tool; then follow `references/api-mode.md`.

## When to use

- "Watch these competitor trials and tell me what changed." / "Set up a watchlist for X."
- "Check my watchlist." with a board file attached or in the folder.
- "Has anything in the reference list behind our review been retracted?"
- "Tell me when the label or SmPC of these products is revised."
- The skill invoked with nothing else: start with Set up a watchlist.

Not for mapping a whole field and finding new records (that is `amass-landscape-monitor`, whose
in-scope records can then be watched here), not for PatentCore (no Amass dates to compare), and not
for one-off questions about a record.

## Where it runs

| Client | Run the helper with | Keep the board at | Between conversations |
| --- | --- | --- | --- |
| Claude chat (code execution on) | the code tool | `/mnt/user-data/outputs/<name>.xlsx` | the user downloads the file and attaches it next time |
| Cowork | its shell | `watchlists/<name>.xlsx` in the working folder | stays there |
| Claude Code | Bash | `watchlists/<name>.xlsx` under the working directory | stays there |

`<skill folder>` below is the folder this SKILL.md is in, and `<board>` is the board's path. The
helper is `python3 <skill folder>/scripts/board.py`: Python 3.9 or newer, standard library only.
In Claude chat, files the user attaches are usually under `/mnt/user-data/uploads/`, and files
written to `/mnt/user-data/outputs/` are offered to the user for download; if those folders do not
exist, keep the board in the working directory and hand it over the way the client allows.

**Credits.** Every MCP search costs 2 credits whatever its `limit` and every fetch 1; the helper
counts these nominal credits, and the plan balance shows the exact charge. Setting
up a list usually costs 4 to 10. A daily check costs 2 per stored search plus 1 per record the
searches miss; a weekly full check costs 1 per record. Say the expected cost in a short clause
before spending ("this check is about 4 MCP credits"); ask first only when `start` says a check is
over the board's limit (30 by default).

## How a conversation starts

- **A board is attached or named, or the user asks to check:** go to Check for changes.
- **No board:** go to Set up a watchlist.
- **No Amass tools** (`search_amass_*_records`, `get_amass_*_record`): say the Amass connector is
  needed (in Claude: Settings, Connectors) and stop.
- **No way to run Python:** say code execution is needed (in Claude: Settings, Capabilities) and stop.

## Set up a watchlist

1. **Open** in two sentences: what the watch does, and that you will build the list together with a
   few searches (2 MCP credits each) and hand over one Excel file to keep.
2. **Interview, only what is missing.** If the request already says what to watch, restate it in
   one line and go on. Otherwise ask, in one message, at most three things with an example answer
   each: what to watch (trials, papers, FDA or EMA authorizations, drugs, genes); which records (ids
   they have, or a description such as a drug, sponsor code, indication and phase); what counts as
   news (status, results, retractions, label changes). A topic ("all schizophrenia drugs") is not a
   list: offer to build today's list from it and to re-scope monthly. Several kinds of records can
   share one board.
3. **Create the board.** `<name>` is short, lowercase and hyphenated (`ulotaront-phase3`):

   ```bash
   python3 <skill folder>/scripts/board.py new <board> --name <name> --title "Ulotaront phase 3 trials"
   ```

4. **Scope with the MCP tools**, and after every call put the records you propose on the board
   before the next call (format under Copying records). Send every proposed record that call
   returned, including ones already on the board from an earlier search: the helper merges them
   and learns which searches cover which records, so later checks run as few searches as possible.

   ```bash
   python3 <skill folder>/scripts/board.py add <board> --core trialcore --search "ulotaront" --filter phase=PHASE3 --limit 50 <<'EOF'
   [{"amassId": "AMTC_…", "nctId": "NCT0…", "registryId": "NCT0…", "sourceRegistry": "clinicaltrials_gov", "url": "https://…", "briefTitle": "…", "acronym": null, "sponsorName": "…", "phase": "PHASE3", "overallStatus": "COMPLETED", "studyType": "INTERVENTIONAL", "startDate": "2019-11-01", "completionDate": "2023-02-01", "enrollment": 435, "conditions": ["Schizophrenia"], "interventionNames": ["SEP-363856", "Placebo"], "interventionTypes": ["DRUG"], "facilityCountries": ["US"], "hasResults": true}]
   EOF
   ```

   - `--search` takes the exact query, with one `--filter key=value` per filter and the `--limit`
     you ran it with: the search is stored and quick checks re-run it exactly. Run searches you may
     keep at `limit` 50: a search costs 2 credits whatever its limit, and room below the board's
     records keeps a newly listed trial from pushing one of them out (when that happens, the check
     fetches the missing record for 1 credit).
   - Records you fetched go in with `--fetch`. Ids the user gave that you did not fetch go in with
     `--id nctId:NCT04072354` (or an Amass ID); they are fetched in the first check, 1 credit each.
     More than 10 ids given: add them with `--id` rather than fetching them one by one now.
   - Search returns the best matches by relevance, not a complete list. Use the tool's filters
     (phase, status, `hasResults`, agency, stage) and `limit` up to 50. A trial id or a sponsor drug
     code in the query ranks those trials first; put one code per query. When completeness matters,
     run a second phrasing and ask the user to name anything missing.
   - One trial is often registered twice (an NCT record and a JPRN, CTIS or EUCTR record with the
     same title and sponsor). They are two records: watching both catches either registry's updates.
     Non-US copies often carry no phase in Amass, so a phase filter hides them; run one search
     without it if copies matter. Live copies (JPRN, CTIS) can disagree with ClinicalTrials.gov and
     are worth watching; old EUCTR copies of trials that ended long ago often keep a stale status,
     so suggest skipping them. EUCTR holds one record per country (`EUCTR2019-000696-16-HR`) and
     search returns just one of them: watch one country record per trial, or the CTIS record when
     the trial has one. A record that no stored search returns is fetched at every check (1 credit).

   | Core | Search tool | Fetch tool: `type` values |
   | --- | --- | --- |
   | TrialCore | `search_amass_trialcore_records` | `get_amass_trialcore_record`: `amassId`, `nctId`, `registryId` |
   | BiomedCore | `search_amass_biomedcore_records` | `get_amass_biomedcore_record`: `amassId`, `pmid`, `doi` |
   | RegulatoryCore | `search_amass_regulatorycore_records` | `get_amass_regulatorycore_record`: `amassId`, `fdaApplicationNumber`, `emaProductNumber`, `ndc`, `splSetId` |
   | DrugCore | `search_amass_drugcore_records` | `get_amass_drugcore_record`: `amassId`, `chemblId` |
   | GeneCore | `search_amass_genecore_records` | `get_amass_genecore_record`: `amassId`, `ensemblGeneId` |

5. **Make the board readable.** Before showing it, record what you worked out about the records:

   ```bash
   python3 <skill folder>/scripts/board.py annotate <board> <<'EOF'
   [{"id": "JPRN-jRCT2031250398", "copyOf": "NCT06894212"},
    {"id": "NCT06894212", "label": "Acute, US + Japan"},
    {"id": "NCT04072354", "label": "Acute, 50/75 mg vs placebo"}]
   EOF
   ```

   - `copyOf` links a registry copy (jRCT, CTIS, EUCTR, ChiCTR and the like) to the trial it copies.
     The board then counts trials rather than registrations, lists the copy under "Also in", and
     flags it when the copy is ahead of its trial. Link only copies you established from the records
     (same title, sponsor or design, a cross-reference). Link to the trial itself, never to another
     copy; two ClinicalTrials.gov records are two trials and cannot be linked. To undo a link, send
     `{"id": "<copy>", "copyOf": null}`.
   - `label` gives each trial a short name of about 30 characters (40 at most), built from its title,
     design or conditions ("Long-term, Japan", "Switch study extension"). Make short names unique
     across the board; when it spans several indications, include the indication where names would
     clash ("GAD long-term safety"). Use a nickname such as a programme name only when the record's
     `acronym` carries it or the user gives it; never from memory.
   - `indication` (optional) merges groups whose conditions are spelled differently
     ("Generalised anxiety disorder" and "Generalized Anxiety Disorder"), or names a group better.
6. **Show the list and agree it.** Run `board.py show <board>` and paste its tables; the "Also in"
   column names each linked copy by its registry id, so the user can check the links. Ask the user
   to confirm, drop (`board.py remove <board> --id <Amass ID, NCT id or registry id>`), rename or
   add. Repeat until agreed. If `show` ends with a hint about registry records not linked to a
   trial, link the ones that are copies before going on.
7. **Close the setup** with `board.py start <board>`. If it lists first-look fetches (records added
   by id, and authorizations, drugs or genes found by search, whose search results lack fields a
   check compares), make them and finish as in Check for changes. If the digest lists ids that Amass
   does not know, show them to the user and offer to remove them (they are retried at each full
   check, in case Amass adds them later). If `start` says nothing is due, the board is ready; do not
   run a check now, since Amass has nothing newer than what setup just read.
8. **Hand over.** Make sure the user has the file: in Claude chat, share
   `/mnt/user-data/outputs/<name>.xlsx` with the client's file-sharing tool if it has one (for
   example `present_files`) so a download link appears, and name the file in your reply. Keep the
   message short:
   - what is on the list, in one line (the Overview's count line);
   - anything under **Worth watching** in `board.py show <board>`, as it stands today;
   - that the **Overview** sheet opens first: what changed, worth watching, and a timeline; and that
     the Short name and Notes columns of the record sheets are theirs to edit and are kept;
   - when the next check is due, and what setup cost (`board.py status <board>` gives both);
   - how to come back: "attach this file to a new conversation and say *check my watchlist*" (in
     Cowork or Claude Code: "ask me to check the watchlist");
   - that changes show from the next check on, and that the list is fixed: new records do not join
     it by themselves, so offer to re-scope monthly.

## Check for changes

1. **Get the board.** In chat, take the attached `.xlsx` and copy it to `/mnt/user-data/outputs/`
   before working on it. If the user asks for a check without the file, ask for it: there is no
   other memory.
2. **Plan.** `python3 <skill folder>/scripts/board.py start <board>` prints, per Core, whether it is
   due and the exact MCP calls to make. A Core checked within its refresh interval is skipped:
   Amass has nothing new for it yet. If nothing is due, tell the user when the next check is due and
   stop, unless they want one anyway (`start --force`). Exit code 2 means the check is over the
   board's credit limit: ask, then either go on or run `start --discard`.
3. **Make the calls in the order given.** After each search, ingest every watched record in its
   result (the `start` output lists the watched ids). After every few fetches, ingest them:

   ```bash
   python3 <skill folder>/scripts/board.py ingest <board> --core trialcore --search S1 <<'EOF'
   [ …the watched records from that result… ]
   EOF
   python3 <skill folder>/scripts/board.py ingest <board> --core trialcore --fetch <<'EOF'
   [ …the fetched records… ]
   EOF
   ```

   A record the fetch tool cannot find: `ingest <board> --core <core> --fetch --not-found <id>`
   (no records needed). A record returned by two searches can be sent with each: the copies must
   agree, and the helper refuses a batch whose values differ from an earlier copy, so check the tool
   result again and resend with `--correction`.
4. **Quick checks:** after the searches, `board.py status <board>` lists any watched record they did
   not return this time. Fetch those and ingest them.
5. **Review before storing.** `board.py review <board>` prints the changes found. For each one, find
   the record in this conversation's tool result and confirm the new value is what the tool
   returned. If you copied a value wrong, ingest that record again with the exact values and
   `--correction` (no credits are counted), then review again.
6. **Finish.** `board.py finish <board>` stores the check, rewrites the board and prints the digest.
   Relay, in this order: the One line; the changes by group; the **Worth watching** items, presented
   as prompts derived from the records' dates and registries rather than news; the **Where it
   stands** table (paste it, or say it in a sentence for a board with one indication); the Cores
   not due and when they are; the credits. Draw only on the digest; never add a change it does not list, and
   never present a metadata-only entry (a citation count, an Amass update date) as news. If
   `finish` ends with a hint about registry records not linked to a trial, or trials have no short
   name (boards made before these existed), offer once to fix that with `annotate`.
7. **Hand back the board.** In chat, share the updated file in outputs again (as in setup step 8):
   it replaces the old one, so tell the user to keep the new download. Offer `board.py show
   <board>` if they want the table in the chat.

If the conversation is compacted or interrupted mid-check, run `board.py status <board>`: it names
the searches still to run and the records still to fetch. Never redo a search that was ingested.

## Copying records into the helper

- A JSON list of objects (or one object), field names exactly as the tool returns them, values
  copied exactly from the tool result in this conversation. Never type a record from memory or
  from your own summary of it.
- From a search result, copy only records on the board. Others are ignored if sent.
- Include every tracked field the result carries (table below). A field you leave out is "not
  observed" this time and is not compared.
- Leave out any text over about 300 characters (long registry intervention strings, abstracts,
  indication text): the helper drops it anyway, because a slip in copying it would look like a change.
- Paste nested objects whole: GeneCore `tractability`, `targetClass`, `gnomadConstraint`,
  `depmapEssentiality`; DrugCore `mechanismsOfAction`; RegulatoryCore `emaDetails` and `fdaDetails`.
  Leave out RegulatoryCore `documentSections` when typing.
- When the client saved a large tool result to a file and gave you its path (Claude Code does),
  pass `--raw <path>` instead of typing; the helper extracts the records and counts document
  sections itself. When only some of that result belongs on the board, add `--only` with their
  ids (`add ... --raw <path> --only NCT06894212 CTIS2022-500538-27-00`); an id that is not in the
  result is refused, not skipped.
- The helper rejects the whole batch on an unknown field name, a status or phase value outside the
  tool's vocabulary, a malformed date or an Amass ID of another Core, and says which. Fix and
  resend; nothing was stored.

## Per Core

| Core | Interval | Quick check | Tracked | Not visible over MCP |
| --- | --- | --- | --- | --- |
| TrialCore | 1 day | yes | status, phase, enrollment, start and completion dates, results posted; title, acronym, sponsor, study type, conditions, interventions, countries; linked papers and drugs (fetch) | results revised after the first posting, outcomes, why a trial stopped |
| BiomedCore | 1 day | yes | retracted; title, journal, publication date; linked trials (fetch); citations, full text, JuFo level as metadata | errata and expressions of concern (no publication types over MCP yet) |
| RegulatoryCore | 7 days | no | status, orphan flag, designations, withdrawal and refusal dates; FDA label date, SmPC date and revision, latest EMA procedure, EC decision date, parsed section count; name, substance, holder; linked drugs; Amass update date | which label or SmPC section changed, section text |
| DrugCore | 7 days | no | highest stage, modality; name, synonyms, trade names, parent, mechanisms; linked trials, papers, authorizations, genes | the date of change |
| GeneCore | 7 days | no | symbol, name, type, synonyms, target class, tractability, safety liabilities, DepMap essentiality, LOEUF; linked drugs; Amass update date | the date of change |

The interval is how often Amass refreshes the Core; `start` skips a Core checked more recently than
that, since Amass has nothing newer for it yet. To check a Core less often, change its interval
(`board.py set <board> --interval drugcore=30`).

**Quick and full checks.** For TrialCore and BiomedCore, search results carry every tracked field
except the cross-links. So within a week of a full check, a due check re-runs the stored searches
that cover the board (2 credits each) and fetches only what they miss; every 7 days it fetches every
record (1 credit each), which also catches new cross-links (`set --full-every` changes the 7). When
the searches would cost as much as fetching everything, `start` plans a full check. The other Cores
are always fetched.

## The digest and the board

**The digest** is what `finish` prints, in plain language. Changes come grouped in this order, empty
groups left out: Not found in Amass, or back again; Ids that Amass does not know (given by id, never
found: offer to remove them); Status, phase and date changes (with what a new status means, such as
"now enrolling"); Results posted; Label and SmPC revisions; Retractions and corrections; New or
removed cross-links; Other field changes (enrollment, sponsor, conditions, interventions, titles);
Metadata only (citation counts, Amass update dates: counts, not news); Baseline captured (a first
look, nothing to compare yet); Not checked this time (only with `finish --allow-missing`). Each line
names the record by its short name and source id; a registry copy is named after its trial ("Acute,
US + Japan, jRCT copy (JPRN-jRCT2031250398)"). Then **Worth watching**, **Where it stands** (per
indication: trials, ongoing, not yet recruiting, completed, stopped, the next expected end, and how
many have results), what was checked, the Cores not due, when each next check is due, and the
credits. `review` prints the same changes with the registries' raw values and the Amass IDs, for
checking against the tool results.

**Worth watching** is computed from the records alone, at no cost, one line per trial:
- ends within 60 days (expect a status change), or its completion date has passed while the
  registry still says it is ongoing;
- its start date has passed while it is still not yet recruiting;
- a registry copy is ahead of it: the copy says the trial ended, or the copy is marked as having
  results while ClinicalTrials.gov has none. For non-US registries Amass sets that mark when the
  registry gives a results flag, a results or protocol link, or a results summary, so say "marked
  as having results", not "results posted";
- completed 9 months ago or more with no results on ClinicalTrials.gov: "usually due by" a year
  after completion, then "none a year after completion".

These are prompts to look, not changes.

**The board** opens on its **Overview** sheet: the title and when it was checked; Where it stands;
what changed in the latest check, highlighted; Worth watching (marked ⚠); and a timeline of the
trials by indication, one bar per trial coloured by status (◆ ends within 60 days, ▲ results posted,
✕ stopped early, ⚠ listed under Worth watching), with registry copies under "Also in". Then one sheet
per kind of record with every tracked field (status and the latest change up front, each copy
marked ↳ under its trial, rows changed in the latest check highlighted), Changes (every change ever
found), Checks (each check, what it covered and its credits) and Settings (how the file works, what
the watch cannot see, stored searches, intervals and the credit limit). The Short name and Notes
columns of the record sheets belong to the user: what they type there is kept at every rewrite.

## What MCP mode cannot see

Say these plainly when the user relies on the watch for them. A trial's results revised after their
first posting (only the first posting shows, as "results posted"); why a trial stopped; which label or
SmPC section changed when the revision number and dates stay put; errata and expressions of concern;
the exact day a change happened (a check sees the state at the time of the check). A record the fetch
tool cannot find is reported as not found; that can be a removal at the source or an Amass hiccup, so
it is checked again next time. API mode sees all of these.

## API mode

When the user needs it, export the board's records as watchlist files and continue with
`references/api-mode.md`:

```bash
python3 <skill folder>/scripts/board.py export <board> watchlist --out watchlists/
```

It needs `AMASS_API_KEY` and a shell that reaches `api.amass.tech` (Claude Code, a server, CI);
Claude chat's code tool usually cannot reach it. Never ask the user to paste a key into the chat.

## The helper

`python3 <skill folder>/scripts/board.py <command> <board> …`; `--help` on any command lists its
options. Every command that changes the board rewrites the .xlsx; an open check lives in
`<board>.check.json` next to it until `finish` or `start --discard`.

| Command | Use |
| --- | --- |
| `new --name N --title T` | create an empty board |
| `add --core C (--search Q [--filter k=v] [--limit N] \| --fetch \| --id ID… \| --watchlist FILE) [--raw FILE [--only ID…]]` | put records on the board; their first look is the baseline; `--watchlist` imports an API-mode file |
| `remove --id ID… \| --search-id S…` | take records or stored searches off |
| `annotate` (JSON list on stdin, or `--id ID` with `--label`, `--copy-of`, `--clear-copy`, `--indication`, `--note`) | short names, registry copies, indication groups and notes |
| `show [--core C] [--brief]` | the board for the chat: summary, changes, worth watching, then the full tables (`--brief` stops after the summary) |
| `status` | the schedule, or an open check's progress |
| `start [--force] [--full] [--core C] [--discard]` | open a check: what is due and which calls to make |
| `ingest --core C (--search S \| --fetch) [--not-found ID…] [--for type:value] [--raw FILE] [--correction]` | hand over what a call returned |
| `review` | the changes found, before anything is stored |
| `finish [--allow-missing] [--digest FILE]` | store, rewrite the board, print the digest |
| `set [--interval core=days] [--full-every N] [--max-credits N] [--title T]` | settings |
| `export (csv \| watchlist) --out DIR` | CSV copies of the sheets, or watchlist files for API mode |

Exit codes: `0` done; `1` error, nothing stored (the message says why); `2` `start` found the check
over the board's credit limit.

## Rules

- The board is the only memory. Never reconstruct a record, a stored value or a past change from
  memory, and never edit the file by hand; the hidden `_state` sheet is the helper's.
- Never compare values yourself and never report a change the helper did not find.
- Ingest after every search and every few fetches, before the next call: credits spent on a result
  that never reaches the board are wasted.
- One open check per board. Finish it or discard it before adding or removing records.
- The board holds the user's list and nothing secret; still, do not send it anywhere the user did
  not ask for.

## Limits

No scheduling in MCP mode: a check runs when the user asks (in Claude Code, a scheduled routine can
ask). The list is fixed; finding new records is `amass-landscape-monitor`'s job or a re-scope. No
PatentCore. No section-text or outcome-level diffs over MCP; see API mode.
