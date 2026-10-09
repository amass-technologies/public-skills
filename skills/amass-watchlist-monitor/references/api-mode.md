# API mode: the exact monitor over the Amass change feed

API mode runs `scripts/monitor.py`, a deterministic monitor over the Amass REST change feed. It sees
what MCP mode cannot: trial results revised after their first posting, why a trial stopped, which
label or SmPC sections changed, removals from the source, and the date of each change. It also
suits hundreds of records and scheduled or CI runs. It needs:

- `AMASS_API_KEY` in the environment (get one at <https://platform.amass.tech/api-keys>). The script
  reads it there and never writes it anywhere. If it is missing, ask the user to provide it the
  way they normally provide secrets, and to start the client again that way: a secret manager
  that injects it at launch (for example 1Password's `op run -- claude`), or, failing that, an
  `export AMASS_API_KEY=…` line in their shell profile. A variable exported in another terminal
  does not reach this session. Never ask them to paste the key into the chat.
- A shell that can reach `api.amass.tech`: Claude Code, a server or CI. Claude chat's code tool and
  Cowork's sandbox usually cannot.
- Python 3.9 or newer. Standard library only, nothing to install.

As in MCP mode, say nothing about usage or cost. If the API answers that the plan's usage is
exhausted, stop and relay its message, which says whether to wait or to upgrade.

## Getting a watchlist

- From a board: `python3 <skill folder>/scripts/board.py export <board> watchlist --out watchlists/`
  writes one watchlist file per Core, with the Amass IDs and a comment per record.
- From ids the user gives: write the file by hand (format below).
- From a description: scope the list over MCP as in SKILL.md, then export it.

The monitor keeps its state in `.amass-monitor/` next to the watchlist file; in a git repository,
offer to add that folder to `.gitignore`, or to commit it on purpose for CI.

## Preview recent activity

Before the first real run, preview the last 90 days. The preview reads feed pages only (about one per
100 records, more for RegulatoryCore, where every changed document section counts) and fetches no
record. The safety limit in the command keeps it small; if it stops there, say the window was too
busy to preview in full and offer a shorter one (`--since` 30 days back):

```bash
python3 <skill folder>/scripts/monitor.py run watchlists/<name>.yaml --dry-run --since <today minus 90 days> --max-credits 20
```

Relay from its output: how many records changed in the window and when; any marked new to Amass;
the note when many records share one date (an Amass-wide refresh, not news about each record);
unresolved ids; how many records the first real run will fetch. Say plainly what the preview
cannot show: what changed. There is no history to compare against, so field-by-field changes are
reported from the second real run on. For RegulatoryCore, DrugCore and GeneCore, quiet weeks are
normal.

## The first run

Say that this run captures a baseline and reports no changes. If the preview's "Next real run" is
over 500 calls, pass a higher `--max-credits` so the safety limit does not stop it halfway. Then run:

```bash
python3 <skill folder>/scripts/monitor.py run watchlists/<name>.yaml
```

Afterwards tell the user: baseline captured for N records, and when changes can first
appear (the "Next expected refresh" line of the digest). Explain the scheduling options in Running
it; set one up only if the user asks, and make sure the scheduled job has the API key in its
environment and keeps the state folder. Say that the list is fixed: new trials from the same
sponsor do not join it by themselves; offer to re-scope every month or quarter.

## The watchlist file

One file per watchlist, one Core per file. YAML or JSON.

```yaml
name: nsclc-adc-competitors     # names the state folder; letters, digits, . _ -
core: trialcore                 # trialcore | biomedcore | regulatorycore | drugcore | genecore
ids:
  - AMTC_KGCxl0fhol8AzsKSl4lwafcHyxW     # an Amass ID, used as is
  - nctId: NCT04907084                   # or an external id, resolved once on the first run
  - registryId: EUCTR2013-000487-28      # a EudraCT number without country suffix adds every country
include: [referencesDrugCore]   # optional extra fields; each Core's defaults are always fetched
cadence: daily                  # informational; scheduling is up to you
```

External ids each Core accepts (one key per entry):

| Core | Amass ID prefix | External ids |
| --- | --- | --- |
| TrialCore | `AMTC_` | `nctId`, `registryId` |
| BiomedCore | `AMBC_` | `pmid`, `doi` |
| RegulatoryCore | `AMRC_` | `fdaApplicationNumber`, `emaProductNumber`, `ndc`, `splSetId` |
| DrugCore | `AMDC_` | `chemblId` |
| GeneCore | `AMGC_` | `ensemblGeneId`, `hgncId`, `entrezGeneId`, `uniprotId`, `symbol`, `omimId`, `orphanet`, `iuphar` |

An Amass ID that is malformed or belongs to another Core stops the run before any request is sent.
An external id the lookup cannot resolve is listed under "Unresolved identifiers" in the digest and
in the printed summary, and is retried on the next run. A starting file is in
`templates/watchlist.example.yaml`.

## Running it

```bash
python3 <skill folder>/scripts/monitor.py run path/to/watchlist.yaml
```

| Flag | Effect |
| --- | --- |
| `--dry-run` | Sweep the feed and list which records changed and what a real run would fetch. Reads feed pages only, plus one lookup per 100 external ids not yet resolved; fetches no record and stores nothing. With `--since` on a new watchlist it is the activity preview. |
| `--since YYYY-MM-DD` | Replay the feed from this date instead of from the stored position. Every record with an event in the window is fetched and diffed against its stored copy. |
| `--state-dir PATH` | Where state lives. Default: `.amass-monitor/` next to the watchlist file, one subfolder per watchlist name. |
| `--max-credits N` | A safety limit: stop before the run's metered calls exceed N (default 500; each feed page, fetch and lookup is about one). Checked before every call, feed pages included, and again before fetching records; a stopped run reports what it would need and stores nothing. |

Exit codes: `0` done; `1` failed, or finished with record errors listed in the digest; `2` stopped by
the safety limit.

The first run of a record captures a baseline and reports no diff. History is not backfilled unless
you pass `--since`. The state folder must persist between runs (in CI, cache or commit it); it holds
the resolved ids, each record's last fetched copy, the stored position, `runs.jsonl` (one line per
run: events and records fetched) and `digests/<date>.md`. Delete a watchlist's state folder only
if you want to start over from a fresh baseline.

To schedule it, use a Claude Code scheduled routine, cron or launchd
(`claude -p "run the amass watchlist monitor on watchlists/nsclc.yaml"`), or a CI cron job that keeps
the state folder. The API is poll-only; nothing runs unless something invokes the script.

## What to do with the output

1. Run the command. The last lines on stdout are a one-line summary (counts per class, digest path)
   followed by feed and fetch totals. Relay the one-line summary.
2. Read the digest file it names. Summarize it for the user if they want prose, drawing **only** on
   what the digest shows. Never infer a change the digest does not list, and never describe a
   Metadata-only entry as a substantive change.
3. If the run exits `2`, it stopped at its safety limit before fetching or storing anything more:
   say the run is larger than usual and re-run it with the `--max-credits` the message names.

## The digest

Markdown, grouped by class in this order. Empty classes are left out.

1. **Removed from source**: the feed reported `deleted` (the registry, PubMed or agency register dropped the record).
2. **Status and stage changes**: trial status, why stopped, phase, enrollment, start and completion dates; authorization status, designations, orphan flag, withdrawal date; drug stage or modality.
3. **Results posted or revised**: `hasResults`, `resultsFirstPostDate`, and outcomes added, revised or removed (with the rows and values that moved).
4. **Label and SmPC section changes**: sections the feed named, by document type (FDA label and review, EMA SmPC and EPAR), labels and SmPCs first; indication text, label date, SmPC date and revision number.
5. **Retractions and corrections**: `isRetracted` flips and retraction, erratum or concern publication types.
6. **New cross-links**: linked papers, trials, drugs or authorizations added or removed.
7. **Other field changes**: everything else watched (sponsor, conditions, interventions, titles, mechanisms, tractability, safety liabilities, synonyms).
8. **Metadata-only**: the feed reported the record but no watched field changed (for example a citation-count refresh). The line says which unwatched fields moved.
9. **New to Amass**: the record entered Amass inside the window.
10. **Baseline captured**: first look at the record, nothing to compare yet.

Each entry carries the title or name, the Amass ID, the source identifier and URL, what the feed
reported, and each field that moved with its old and new value. The digest ends with counts per
class, feed pages and events, records fetched, and the next expected refresh for the
Core. When nothing changed it says so. `templates/digest.example.md` is a real digest from a test run.

## Per-Core expectations

| Core | Refresh | Visible on the feed | Recommended run | Worth knowing |
| --- | --- | --- | --- | --- |
| TrialCore | daily | next day | daily | Results-only revisions are reported even when the trial's `lastUpdateDate` does not move. Withdrawn or terminated trials are status changes, never removals. |
| BiomedCore | daily | next day | daily | Expect many Metadata-only entries: citation-count refreshes touch far more papers than real changes do. A retraction is a change to `isRetracted`; a removal means NCBI withdrew the citation. |
| RegulatoryCore | weekly | about a day after the weekly refresh | weekly | A revision confined to section text arrives with the record row unchanged; the digest still names the sections. A document parsed for the first time arrives as every one of its sections created. |
| DrugCore | weekly | about a day after the weekly refresh | weekly | An Open Targets release marks most drugs updated in one week. A new linked trial or authorization alone does not report the drug. A changed ChEMBL ID shows as a removal; look the drug up again by `chemblId`. |
| GeneCore | weekly | about a day after the weekly refresh | weekly | Most weeks hold nothing for a given gene. A changed Ensembl id shows as a removal. |

Running a weekly Core daily is harmless; most runs then read one feed page and report nothing.

## Calls and rate limit

A run makes one or two feed-page calls per 100 records, one fetch per record that moved (a typical
day fetches 0 to 5 of 100 trials), and on the first run one lookup per 100 external ids. The API
allows 60 requests per 60 seconds per user and organization; the script waits out `429` responses as
`Retry-After` asks, so 100 fetches take about two minutes. Observed on 2026-10-05: a 120-trial 9-day
replay took about 3 minutes with two rate-limit waits. RegulatoryCore section events count against
the page size, so a replay over records with many sections reads more pages than the record count
suggests. Use `--dry-run` to see the page and fetch counts before a large replay.

## Correctness rules the monitor follows

- **`nextCursor: null` is the only caught-up signal.** A short page still has a cursor, and the monitor follows it.
- **Delivery is at-least-once.** Events are merged per record, never counted. A change already captured in the stored copy is not fetched or reported again.
- **Each run resumes two days before the last completed run**, never from "now": the most recent day is withheld until its ingest completes. The stored position is a date, never a cursor.
- **Chunks of 100 are stable.** Over 100 records, the watchlist is split into chunks of 100 that keep their members across runs; editing the watchlist changes only the chunks it touches. Edit the watchlist between runs, not during one. If the feed ever rejects a cursor because the id set changed mid-sweep, the monitor says so and re-sweeps that chunk from its stored position.
- **`created` describes the delivery, not the record.** "New to Amass" comes from the record's `createDate` against the window.
- **Halted is not removed.** A withdrawn or terminated trial, a retracted paper, or a withdrawn authorization is reported as a change. "Removed from source" means the source dropped the record.
- **The feed reports current state, not history.** A record removed and re-registered ends up present; the digest says it came back.
- **Ids are checked before any request.** One malformed, foreign-Core or empty id makes the feed reject the whole chunk, so the monitor refuses to start instead.
- **Not every correction reaches the feed.** BiomedCore corrected `publicationDate` in place without moving `lastUpdateDate` or reporting on the feed. A periodic full re-fetch of the watchlist is the only way to catch silent corrections of that kind; the monitor does not do it automatically.
- **RegulatoryCore section lists on the feed are partial per page.** The monitor merges them across pages and names sections from the record's current table of contents; a section id that is no longer in the table of contents is counted, not named.

## Limits

One Core per watchlist; run several watchlists for several Cores. No topic or query monitors, no
sponsor- or target-anchored resolution, no PatentCore, no email or Slack delivery (the digest is a
file), no field-level diffs of full text or section text.
