# PKU Radar — Phase 1A

Entirely offline: reads the preserved PKU Know fixture, persists lifecycle state
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

No real source client, LLM, delivery service, scheduler, or Phase 1B features.
