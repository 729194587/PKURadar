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

Candidates are ranked sequentially in batches of at most 15 (separated by provider
so external IDs stay unambiguous). Each batch makes one real LLM request to
`/chat/completions`, with a 60-second timeout, thinking explicitly disabled, and
no in-call retries. For example, 90 candidates from one provider take six calls.
Subsequent runs retry eligible failed items outside the current source window.
`--rerank` reevaluates all unsurfaced items. The prompt includes the current timezone-aware datetime,
all three preference groups and each Notice's external ID and content/event fields. It evaluates
interest, remaining action value and whether an active reminder is warranted;
ended events and expired deadlines are excluded, while uncertain dates and a
false upstream event label do not automatically exclude long-term opportunities.
Reasons are short Chinese explanations grounded in the provided content.
The response must be a JSON array mapped by external ID, with each decision
validated by the existing Recommendation validator. Invalid or missing decisions
fail only the affected items; duplicate IDs fail that item without overwriting it.
Unknown IDs are traced and ignored. Valid attributable decisions are preserved.
Provider failures or unparseable/non-array responses fail every item in the batch.
Attempts, cross-run retries, persistence, reranking and surfacing remain per item.

`SourceResult` carries notices, errors, completeness and the number of successfully
decoded pages. A failed page or malformed item does not discard other valid items.
Their raw upstream JSON is retained unchanged in meaning. An incomplete fetch
makes a run at least partial; all three page failures make it failed, even if
previously queued items can still be processed. Successfully decoded empty pages
are valid, so zero recommendations can still be a success. A page containing bad
items counts as decoded but makes the fetch incomplete. Detailed source errors
are saved in `runs.error`, and the digest ends with:

> Source fetch was incomplete; some notices may be missing.

Live runs write and flush events to `data/traces/run-<timestamp>-<run_id>.jsonl` and print
page/ranking progress plus final timing and token totals to stderr. Digest output
on stdout is unchanged. Offline runs do not create these traces or progress lines.
Trace I/O failures warn on stderr without failing the pipeline.

Events are `run_start` (configuration, preferences, prompt SHA-256),
`source_page_finished` (page timing, item/bad-item counts, success or error),
`ranking_batch_finished` (batch index/size, timing, provider usage, success, item
outcome counts, protocol errors and at most 2000 characters of invalid output),
`ranking_finished` (identity, title, decision), `ranking_failed` (identity, title, error),
and `run_finished` (status, timings, call/outcome counts and token sums).
Page item counts include bad items; page success means the page decoded correctly,
consistent with `SourceResult.successful_pages`. Source duration includes inter-page
waits and configuration; individual page duration excludes the wait before a page.
Token sums include usage returned on invalid model outputs, count only supplied
fields, and remain null when unavailable; they are never estimated. The configured
API key is redacted, and URL credentials/query/fragment, request headers and full
provider response bodies are not recorded. Preferences and decision text remain
local trace data. Filenames include a wall-clock nanosecond timestamp and are
created exclusively, preventing append mixing across databases with the same run ID.
LLM calls and usage count actual batch requests, never individual items; ranking
duration sums batch durations. No per-item token usage is estimated.

All automated tests use mocked transports and require no API credentials:

```powershell
python -m unittest discover -s tests -v
```

No delivery service, scheduler, coverage health, batching, or other later-phase features.
