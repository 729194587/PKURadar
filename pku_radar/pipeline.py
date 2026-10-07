import json

from .digest import DigestBuilder, TerminalWriter
from .models import Notice, Recommendation, utc_now


def run_pipeline(store, source, ranker, preferences, *, clock=utc_now, writer=None,
                 builder=None, max_rank_attempts=3, rerank=False):
    if max_rank_attempts < 1:
        raise ValueError("max_rank_attempts must be positive")
    writer = writer if writer is not None else TerminalWriter()
    builder = builder if builder is not None else DigestBuilder()
    now = clock()
    run_id = store.start_run(now)
    counts = dict(fetched=0, new=0, ranked=0, recommended=0)
    errors = []
    failures = exhausted = 0
    try:
        notices = list(source.fetch())
        counts["fetched"] = len(notices)
        counts["new"] = store.ingest(notices, now)
        for row in store.candidates(max_rank_attempts, rerank):
            attempts = 0 if rerank else row["rank_attempts"]
            if rerank:
                store.reset_attempts(row)
            notice = Notice.from_raw(json.loads(row["raw_json"]), row["provider"])
            try:
                result = Recommendation.validate(ranker.rank(notice, preferences, now))
            except Exception as exc:
                failures += 1
                error = f"{type(exc).__name__}: {exc}"
                store.record_rank(row, now, error=error)
                errors.append(f"{row['provider']}/{row['external_id']}: {error}")
            else:
                store.record_rank(row, now, result=result)
                counts["ranked"] += 1
                counts["recommended"] += result.recommend
            if attempts + 1 == max_rank_attempts:
                exhausted += 1
                errors.append(f"{row['provider']}/{row['external_id']}: reached max_rank_attempts")
        rows = store.recommendations()
        writer.write(builder.build(rows, counts, now, failures, exhausted))
        status = "partial" if failures and counts["ranked"] else "failed" if failures else "success"
        store.finish_run(run_id, clock(), status, counts, errors, rows)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
        store.finish_run(run_id, clock(), "failed", counts, errors)
    return store.run(run_id)
