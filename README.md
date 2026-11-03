# Job Hunter: an autonomous AI job-search pipeline

![Tests](https://github.com/SherwanAli0/job-hunter/actions/workflows/test.yml/badge.svg)
![Deploy](https://github.com/SherwanAli0/job-hunter/actions/workflows/deploy.yml/badge.svg)

Every day, this pipeline scans **300+ company job boards and public job APIs**, filters
thousands of postings down to the handful worth applying to, scores each one against
track-specific CV profiles with Claude Haiku, and delivers a ranked email digest with
pre-drafted screening answers, keyword-gap tailoring hints and freshness badges.

It runs on **AWS Fargate**, scheduled by EventBridge, with state in S3 and secrets in
SSM Parameter Store. Deploys are automatic from `main` over GitHub OIDC, with no
long-lived AWS keys anywhere.

Built and maintained by [Sherwan Ali](https://github.com/SherwanAli0) as a real
daily-driver, and as a showcase of production-minded LLM engineering on a shoestring
budget: the Batches API, prompt caching and free keyword and pre-screen gates keep most
postings away from the model. Volume and cost figures, with the date and source of each,
are under [Measured figures](#measured-figures).

---

## Architecture

```mermaid
flowchart TD
    A["Sources<br/>ATS APIs · official job APIs · RSS/JSON feeds"] --> B["Cross-source dedup<br/>(company+title normalization, source priority)"]
    B --> C["Recall-friendly filter chain<br/>location · language · experience · seniority · degree"]
    C --> D["Stage 1: deterministic pre-screen<br/>(regex disqualifiers, zero API cost)"]
    D --> E["Stage 2: Haiku scoring<br/>per-track CV profiles, Batches API, structured outputs"]
    E --> G["Ranking layer<br/>closed-posting check · diversity quotas · remote → near Bonn → NRW ordering"]
    G --> H["Email digest<br/>screening-answer kits · tailoring hints · health warnings"]
    G --> I["Durable state (S3)<br/>seen_jobs.json · digested_keys.json · run_stats.jsonl"]
```

**Runtime.** The whole pipeline is one container image. EventBridge Scheduler starts an
ECS Fargate task once a day at 10:00 Europe/Berlin (so daylight saving is handled for me;
a second, 16:00 run existed until 2026-08-18). The schedule lives in AWS, not in this
repo, so the repo cannot show its current state. The task reads its secrets from SSM
Parameter Store as SecureStrings, reads and writes state in a versioned private S3
bucket, and logs structured JSON to CloudWatch. Fargate was chosen over Lambda only after
measuring the real scrape: about 40 minutes (range 36 to 72) when it moved to Fargate in
July 2026, well past Lambda's 15-minute ceiling, and slower on some later days (one
healthy run took 2 h 03 min, which is why the overlap claim below has a 4-hour TTL). A
push to `main` runs the tests, builds the image, pushes it to ECR and registers a new
task definition revision, authenticating with GitHub OIDC against a least-privilege
role. Pushes that only change Markdown files, `cv/` or the state files do not deploy.

The stages (scrape, filter, score, rank, notify, persist) are wired together as a small
[LangGraph](https://github.com/langchain-ai/langgraph) state machine in
[graph.py](graph.py); the nodes call the plain functions in [main.py](main.py), and the
graph's only branch skips scoring when nothing new was found.

Two concurrent runs would each see the same jobs as unseen and both send a digest, so
runs take a claim in S3 (conditional write, a TTL longer than the slowest healthy run,
and an owner token so a run that overran cannot release its successor's claim) and a
second run exits cleanly instead of failing. A state file that exists but cannot be
read stops the run: carrying on with empty history would re-send old jobs.

**Sources** (public endpoints; some are unofficial scrapes of public pages): Greenhouse,
Lever, Ashby, Personio, Recruitee, SmartRecruiters and Workday endpoints across 300+
company boards (358 distinct board slugs plus 21 Workday tenants in
[config.py](config.py), counted 2026-11-03); the official **Bundesagentur für Arbeit**
job-search API; the **Adzuna** API; the **Brave Search** API; the JobSpy library
(`scrape_jobspy`), further job-board scrapers (see `scrape_all` in
[scrapers.py](scrapers.py)) and DuckDuckGo web search; and public feeds
(Arbeitnow, Remotive, WorkingNomads, WeWorkRemotely RSS) plus other public job boards. A
monthly health-check workflow probes the configured
Greenhouse, Lever, Ashby, Personio, Recruitee and SmartRecruiters boards and fails CI
when one has been removed, so the source list can't silently rot.

## What makes it interesting

**LLM scoring with per-track CV routing.** Every surviving job is classified
(AI / ML / Data Science / Data Analyst) and judged by Claude Haiku against a CV profile
*framed for that track*: a Data Scientist job is scored against the DS-framed CV,
not a generic one. A Sonnet re-score of the finalists used to follow; it was measured
at a third of every run's bill for a second opinion on an order Haiku had already set,
and removed. Structured outputs constrain each response to valid JSON; they say nothing
about whether a score is right, which is what the golden set and `calibrate.py` below
are for. A retry plus a score-0 default means a bad API moment cannot crash the run.
Known limitation: a job whose scoring fails both attempts keeps score 0 and is marked
seen like any other, so it is not retried on the next run.

**A measured scoring pipeline, not vibes.** A hand-labeled golden set
([golden/](golden/golden_set.jsonl)) gates the deterministic pre-screen in CI on
every push, and [calibrate.py](calibrate.py) measures LLM band accuracy on demand
before any prompt edit ships. The calibration set caught a real filter bug on its
first run ("ideally 2-3 years" being read as a 3-year wall).

**Recall-then-precision filtering.** The pre-scorer filter chain is deliberately
recall-friendly (unknown location is kept), while the scorer's disqualifier is the
precision stage. Both share one source of truth ([filters.py](filters.py)) so the
two stages can't drift apart. The filter chain is covered by regression tests seeded
with real incidents that once killed good jobs.

**Cost engineering.** Message Batches API (50% off all tokens), prompt caching on
the per-track system prompts, a free keyword gate built from the CV, and a
deterministic pre-screen that keeps most jobs away from the API entirely. Every run
records its token usage, priced per request mode and cache TTL, as `llm_cost_usd` in its
stats line. Only postings that survive the filters and pre-screen reach the model (252
on the 2026-09-04 measurement below), not everything that was scraped.

**Observability.** Every run appends a stats line to `run_stats.jsonl` in the S3
state bucket (per-source counts, filter drops, score distribution, digest mix, token
usage). A source that historically delivers jobs but returns zero for 3 straight runs,
or one that has been silent past a grace period, triggers a red warning banner *inside
the digest email*. Sources switched off on purpose are listed as retired so their
alarm does not become noise. A digest that cannot be sent fails the run.

**Losses are measured, not assumed.** An October 2026 audit traced where suitable jobs
were lost before any rule could judge them, and each fix carries a regression test:
half the search queries never ran after the schedule went to once a day, job ads
whose page failed to load were marked seen and never retried, a location written as
just "Düsseldorf" was dropped by two board integrations, and keyword and page caps
cut student roles before they were read.

**Privacy architecture.** The repo is public, so personal data lives outside it by
design: contact/salary facts in SSM Parameter Store (with a gitignored local mirror),
drafted answers in the private S3 state bucket, and the application tracker kept
outside the repository. None of those gitignored paths (`personal/`, `answers.json`,
`applied_jobs.json`) appears anywhere in this repository's git history (checked
2026-11-03). The three CV profiles in [cv/](cv/) are tracked on purpose, because the
scorer reads them, and they contain the owner's contact e-mail address.

## Design decisions

- **No auto-apply, by choice.** Major ATS platforms require authenticated,
  server-side integrations for submissions, and job boards prohibit automated
  applications. This pipeline optimizes the *human* application instead:
  pre-drafted screening answers, direct apply links, keyword gaps, and follow-up
  reminders. Applying stays a deliberate act, as it should.
- **Filters fail open, the scorer fails closed.** Cheap regex filters keep
  anything ambiguous; the LLM stage (which can read the description) makes the
  precision call. False negatives are unrecoverable; false positives cost a cent.
- **State is auditable.** Seen-job ids carry last-seen dates and prune after 60
  days; every run's behaviour is one JSON line of run stats.
- **What it hunts is configuration, not architecture.** The target changed
  completely once already: it looked for junior full-time roles until its owner
  was admitted to an M.Sc. in Bonn, then only for Werkstudent (working-student), internship and
  part-time (Teilzeit) IT roles, and since 2026-10-14 for entry-level full-time roles as well.
  English-language ads only: remote or hybrid anywhere
  in Germany, on-site only in North Rhine-Westphalia or the Bonn belt.
  That pivot touched the CV profiles and query lists in
  [config.py](config.py), one required-employment-form filter, one location-ranking rule,
  and the labels on the calibration set. The regression suite is what made it
  safe: it caught the spots where the old assumption had leaked into unrelated
  code, including a language filter that treated the word "Werkstudent" itself
  as evidence that a posting was written in German.
- **Measurement outranks intuition.** The first live run of that new target
  returned an empty digest, and the per-filter drop counters said why: the
  ad-language filter had removed 62 of the 76 reachable student roles. The
  German student market advertises in German, so the language of the
  advertisement had stopped being evidence about the job, so for three weeks
  the filter applied only the explicit language *requirement* to student
  roles. Then the owner, who reads German at B1, decided that digests full of
  German ads were not worth his time and reversed it: since 2026-09-07 only
  ads whose body reads as English are sent, stub bodies are fetched in full
  before being judged, and the search runs Germany-wide for remote and
  hybrid roles (on-site only in NRW) with the digest ordered remote → near
  Bonn → NRW → elsewhere, so the smaller English pool is as large as it can be. A funnel that reports what each stage killed turns a
  silent empty inbox into a one-line diagnosis.

## Repo tour

| File | Role |
|---|---|
| [main.py](main.py) | Orchestrator: scrape → dedup → filter → score → rank → notify |
| [scrapers.py](scrapers.py) | All source integrations |
| [scorer.py](scorer.py) | Pre-screen + Claude Haiku scoring, Batches API layer |
| [graph.py](graph.py) | LangGraph state machine that sequences the stages in `main.py` |
| [filters.py](filters.py) | Shared Germany-eligibility term lists (single source of truth) |
| [notifier.py](notifier.py) | HTML digest email (+ optional Notion) |
| [application_kit.py](application_kit.py) | Fetches real screening questions, drafts answers |
| [track.py](track.py) | Application tracker: funnel stats + follow-up nudges |
| [calibrate.py](calibrate.py) / [golden/](golden/) | Scoring calibration harness + labeled set |
| [health_check.py](health_check.py) | Monthly board-rot detector |
| [liveness.py](liveness.py) | Drops postings an employer's own API reports as closed |
| [handler.py](handler.py) / [storage.py](storage.py) | AWS entrypoint, S3 state and the overlap claim guard |
| [secrets_loader.py](secrets_loader.py) / [metrics.py](metrics.py) | SSM secrets into the environment; optional CloudWatch metrics |
| [tests/](tests/) | 1,278 offline tests (`pytest --collect-only -q`, 2026-11-03), run on every push to `main` and every pull request |

## Run your own

Production runs on AWS: the [deploy workflow](.github/workflows/deploy.yml) builds the
image and registers the task definition, and [aws/](aws/) holds the task, schedule and
IAM definitions. The simplest way to run your own copy is the GitHub Actions workflow
[daily.yml](.github/workflows/daily.yml), kept as a rollback path for the AWS setup. The
workflow file still carries its cron lines; whether it is enabled is a GitHub repository
setting, not something visible in the file.

1. **Fork the repo** and edit [config.py](config.py): your CV profiles, search
   queries, and target boards.
2. **Add repository secrets** (Settings → Secrets and variables → Actions):

   | Secret | Purpose |
   |---|---|
   | `ANTHROPIC_API_KEY` | Scoring ([console.anthropic.com](https://console.anthropic.com)) |
   | `GMAIL_USER` / `GMAIL_APP_PASSWORD` / `GMAIL_TO` | Digest delivery (use an [app password](https://myaccount.google.com/security)) |
   | `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | Optional: [developer.adzuna.com](https://developer.adzuna.com), free tier |
   | `BRAVE_API_KEY` | Optional: Brave Search API |
   | `APPKIT_FACTS` | Optional: personal fact sheet for screening-answer drafting |
   | `NOTION_TOKEN` / `NOTION_DATABASE_ID` | Optional: Notion mirror |

3. **Enable the "Daily Job Hunt" workflow** (`gh workflow enable "Daily Job Hunt"`).
   Its cron runs at 05:00 and 13:00 UTC (twice a day, unlike the single daily AWS run);
   trigger it manually from the Actions tab to test. The Tests workflow runs on pushes
   to `main` and on pull requests.

Local run (Python 3.11, the version CI and the Docker image use; the pins in
`constraints.txt` were resolved for it):

```bash
pip install -r requirements.txt -c constraints.txt pytest
pytest tests/ -q           # offline test suite, no API keys or network needed
export ANTHROPIC_API_KEY=sk-ant-...
python main.py --dry-run   # NOT offline and NOT free, see below
```

**What `--dry-run` does.** It runs the real pipeline, not a simulation. It still scrapes
the live sources, sends the surviving postings to Claude (a paid Message Batches API
call), checks employer APIs for closed postings, and, if a fact sheet is configured, may
fetch Greenhouse application questions and draft answers with Haiku. It skips only the
email, the Notion mirror, the `seen_jobs.json`, digest-memory, source-registry and
run-stats writes, and the overlap claim. One state write is not skipped: newly drafted
screening answers are still saved to the answer bank (`answers.json` locally, or the
state bucket on AWS). Use `pytest tests/ -q` for an offline check. The same applies to
the `DRY_RUN` input of the "Run job hunt now" workflow and `run-now.ps1 -DryRun`, whose
descriptions say "no state change".

A local run without `--dry-run` sends a real digest through Gmail and rewrites
`seen_jobs.json`, `run_stats.jsonl` and the other state files in the working directory.
The `seen_jobs.json` and `run_stats.jsonl` committed in this repo are a snapshot from the
July 2026 move to AWS; production state now lives in S3.

## Measured figures

Figures that can be traced to something in this repository. Anything not listed here has
not been re-measured.

| Figure | Value | Measured | Source |
|---|---|---|---|
| Offline tests | 1,278 collected and passing | 2026-11-03 | `pytest tests/ -q`, Python 3.11, `constraints.txt` |
| Configured company boards | 358 distinct slugs + 21 Workday tenants | 2026-11-03 | [config.py](config.py) |
| Postings scraped per run | 8,589 to 9,019 before dedup, 7,415 to 7,776 after | 2026-07-19 and 07-20 (4 runs) | [run_stats.jsonl](run_stats.jsonl) |
| Posting IDs in seen-state | 26,501 | 2026-07-20 snapshot | [seen_jobs.json](seen_jobs.json) |
| Postings sent to Claude | 252 (20 reached the digest) | 2026-09-04 | commit `005f8a1` message |
| LLM bill for that run | about 20 cents, about 7 of them the removed Sonnet re-score | 2026-09-04 | commit `005f8a1` message |
| Expected bill after the cuts | about 7 cents (an estimate, not re-measured here) | 2026-09-04 | commit `005f8a1` message |
| Full-run duration | 40.3 min (range 36 to 72); one slow healthy run 2 h 03 min | July and October 2026 | [aws/README.md](aws/README.md), commit `c377268` message |

The current per-run LLM cost and run time are in each run's `llm_cost_usd` and `phases`
fields in `run_stats.jsonl` in the S3 state bucket, which this repo does not contain.

## License

MIT, see [LICENSE.md](LICENSE.md).
