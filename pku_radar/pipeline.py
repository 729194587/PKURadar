import json
from time import perf_counter

from .digest import DigestBuilder, TerminalWriter
from .models import Notice, Recommendation, utc_now
from .source import SourceResult


def run_pipeline(store, source, ranker, preferences, *, clock=utc_now, writer=None,
                 builder=None, max_rank_attempts=3, rerank=False, observer=None, email_writer=None):
    if max_rank_attempts < 1:
        raise ValueError("max_rank_attempts must be positive")
    writer = writer if writer is not None else TerminalWriter()
    builder = builder if builder is not None else DigestBuilder()
    now = clock()
    run_id = store.start_run(now)
    if observer is not None:
        observer.begin(run_id, now)
    source_duration_ms = None
    counts = dict(fetched=0, new=0, ranked=0, recommended=0)
    errors = []
    failures = exhausted = 0
    try:
        source_started = perf_counter()
        try:
            fetched = source.fetch()
        finally:
            source_duration_ms = (perf_counter() - source_started) * 1000
        source_incomplete = isinstance(fetched, SourceResult) and not fetched.complete
        source_failed = isinstance(fetched, SourceResult) and fetched.successful_pages == 0
        if isinstance(fetched, SourceResult):
            errors.extend(fetched.errors)
            notices = fetched.notices
        else:
            notices = list(fetched)
        counts["fetched"] = len(notices)
        counts["new"] = store.ingest(notices, now)
        candidates = store.candidates(max_rank_attempts, rerank)
        # Keep IDs unambiguous across providers while capping requests at 15 items.
        batches = []
        for row in candidates:
            if not batches or len(batches[-1]) == 15 or batches[-1][0]["provider"] != row["provider"]:
                batches.append([])
            batches[-1].append(row)
        for batch_index, batch in enumerate(batches, 1):
            batch_notices = [Notice.from_raw(json.loads(row["raw_json"]), row["provider"]) for row in batch]
            if rerank:
                for row in batch:
                    store.reset_attempts(row)
            rank_started = perf_counter()
            try:
                results = ranker.rank_batch(batch_notices, preferences, now)
            except Exception as exc:
                results = {notice.external_id: exc for notice in batch_notices}
            duration_ms = (perf_counter() - rank_started) * 1000
            metadata = getattr(ranker, "last_observation", {})
            item_failures = 0
            for row, notice in zip(batch, batch_notices):
                attempts = 0 if rerank else row["rank_attempts"]
                try:
                    output = results[notice.external_id]
                    if isinstance(output, Exception):
                        raise output
                    result = Recommendation.validate(output)
                except Exception as exc:
                    if observer is not None:
                        observer.ranking(notice, error=exc)
                    failures += 1
                    item_failures += 1
                    error = f"{type(exc).__name__}: {exc}"
                    store.record_rank(row, now, error=error)
                    errors.append(f"{row['provider']}/{row['external_id']}: {error}")
                else:
                    if observer is not None:
                        observer.ranking(notice, result=result)
                    store.record_rank(row, now, result=result)
                    counts["ranked"] += 1
                    counts["recommended"] += result.recommend
                if attempts + 1 == max_rank_attempts:
                    exhausted += 1
                    errors.append(f"{row['provider']}/{row['external_id']}: reached max_rank_attempts")
            if observer is not None:
                observer.batch(duration_ms, batch_index, len(batches), len(batch), metadata, item_failures)
        rows = store.recommendations()
        digest = builder.build(rows, counts, now, failures, exhausted)
        if source_incomplete:
            digest += "\n\nSource fetch was incomplete; some notices may be missing."
        writer.write(digest)
        if email_writer is not None and rows:
            delivery_started = perf_counter()
            try:
                email_writer.write(digest, now=now, recommendation_count=len(rows))
            except Exception as exc:
                if observer is not None:
                    observer.emit("delivery_failed", channel="email", recommendation_count=len(rows),
                                  duration_ms=(perf_counter() - delivery_started) * 1000,
                                  error_type=type(exc).__name__, error_message=str(exc))
                raise
            else:
                if observer is not None:
                    observer.emit("delivery_finished", channel="email", recommendation_count=len(rows),
                                  duration_ms=(perf_counter() - delivery_started) * 1000)
        status = "partial" if failures and counts["ranked"] else "failed" if failures else "success"
        if source_failed:
            status = "failed"
        elif source_incomplete and status == "success":
            status = "partial"
        store.finish_run(run_id, clock(), status, counts, errors, rows)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
        store.finish_run(run_id, clock(), "failed", counts, errors)
    result = store.run(run_id)
    if observer is not None:
        observer.finish(result["status"], source_duration_ms)
    return result
