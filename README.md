# PKU Radar — Phase 2A

Offline by default: reads the preserved PKU Know fixture, persists lifecycle state
in SQLite, ranks with substring matching, and writes a terminal digest.
Run from this workspace with Python 3.11+ and dependencies from `pyproject.toml`.

```powershell
python -m pku_radar run --fake-day 1
python -m pku_radar run --fake-day 2
python -m pku_radar run --fake-day 2 --rerank
python -m unittest discover -s tests -v
```

Defaults: `data/pku_radar_offline.db`, `config/preferences.yaml`, three ranking
attempts. Override with `--db`, `--preferences`, and `--max-rank-attempts`.
`data/` is ignored by Git. No network or LLM is used.

`items` uses `(provider, external_id)` as its primary key. It stores all mapped
upstream fields, raw JSON, first/last seen timestamps, ranking status/result,
attempt count/error, and surfaced timestamp. Upserts refresh upstream data only.
`runs` stores start/finish timestamps, success/partial/failed status, fetched,
new, ranked, recommended and surfaced counts, plus errors. Read `runs.error`
in SQLite for the run log referenced by the digest footer; CLI prints the run ID
and database path. All state timestamps are timezone-aware; display uses Shanghai.

Ordinary runs process pending items and failed items below the attempt limit,
including records outside today's source window, once per run. `--rerank`
reevaluates **every unsurfaced item in the database**, resets its attempts and
allows one new attempt. A failed rerank retains any previous successful decision.
Surfaced items are never reranked or displayed again. Only a successful digest
write marks its displayed records surfaced; writer/build failures leave them
available for a later run. Partial and failed runs exit with code 1; normal zero
recommendations exits with code 0.

The footer counts this run's ranking failures and items that reached the attempt
limit (including a successful final attempt). Previous runs' exhausted items do
not generate repeated warnings. Details remain in the run log.

Day 1 contains all 30 real fixture records. Day 2 adds two synthetic new records,
retains the first 24 Day 1 records, and appends an unseen record dated 2025 to
exercise late arrival. Synthetic records have no URL. The original fixture is
unchanged. FakeRanker matches title, summary, category and source name without
case sensitivity: primary interests → high, secondary → medium, low interests
or no match → not recommended. Upstream AI event labels remain auxiliary data.

## First live digest

Set these environment variables in the process running PKU Radar:

- `LLM_BASE_URL`: your provider's OpenAI-compatible API base URL, including its
  API prefix (for example `/v1`), without `/chat/completions`.
- `LLM_API_KEY`: your provider credential, supplied through the environment.
- `LLM_MODEL`: your provider's model identifier.

No provider or model is hardcoded. All three variables are required for live mode;
missing configuration produces a failed run with an explanatory error before
network access. Offline mode requires none of them. `.env` is ignored by Git but
is **not automatically loaded**; supply variables through your shell or secret
manager. Do not put credentials in source, preferences, or committed files.

```powershell
python -m pku_radar run --live
python -m pku_radar run --live --rerank
```

Live mode displays `[PKU RADAR LIVE]` and defaults to `data/pku_radar_live.db`.
Offline mode keeps `[OFFLINE FIXTURE MODE]` and `data/pku_radar_offline.db`.
`--db` overrides either default; use separate paths to keep fixture and live
state apart. `--live` and `--fake-day` cannot be combined.

The source reads exactly list pages 1–3 from `https://pkuknow.cn/api/notices`,
serially with a three-second pause between requests, a 30-second timeout, and
`PKURadar/0.2` User-Agent. Parameters are `q=`, `category=全部通知`, `source=all`,
`group=wechat,official`, `intent=all`, `view=list`, and `page=1..3`. It does not
stop on seen IDs or old publication dates, or fetch article bodies.

Each candidate Notice makes one real LLM request to the configured base URL's
`/chat/completions`, with a 60-second timeout and no in-call retries. The first run
may make a few dozen to about 90 model calls, with corresponding latency and
provider charges. Subsequent runs also retry eligible failed items outside the
current source window. `--rerank` reevaluates all unsurfaced items and may make
additional calls. The prompt includes the current timezone-aware datetime,
all three preference groups and the Notice's content/event fields. It evaluates
interest, remaining action value and whether an active reminder is warranted;
ended events and expired deadlines are excluded, while uncertain dates and a
false upstream event label do not automatically exclude long-term opportunities.
Reasons are short Chinese explanations grounded in the provided content.
Plain JSON output is validated with the existing Recommendation validator;
invalid JSON/schema and provider errors enter the existing per-item retry lifecycle.

`SourceResult` carries notices, errors, completeness and the number of successfully
decoded pages. A failed page or malformed item does not discard other valid items.
Their raw upstream JSON is retained unchanged in meaning. An incomplete fetch
makes a run at least partial; all three page failures make it failed, even if
previously queued items can still be processed. Successfully decoded empty pages
are valid, so zero recommendations can still be a success. A page containing bad
items counts as decoded but makes the fetch incomplete. Detailed source errors
are saved in `runs.error`, and the digest ends with:

> Source fetch was incomplete; some notices may be missing.

All automated tests use mocked transports and require no API credentials:

```powershell
python -m unittest discover -s tests -v
```

No delivery service, scheduler, coverage health, batching, or other later-phase features.
