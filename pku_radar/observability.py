"""Best-effort live diagnostics; never a pipeline correctness dependency."""
import hashlib
import json
import sys
from pathlib import Path
from time import perf_counter, time_ns
from urllib.parse import urlsplit, urlunsplit

from .ranking import RANKING_PROMPT


TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens",
                "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "reasoning_tokens")


class LiveObserver:
    def __init__(self, directory="data/traces", *, secrets=()):
        self.directory = Path(directory)
        self.secrets = tuple(value for value in secrets if value)
        self.stream = None
        self.calls = self.succeeded = self.failed = 0
        self.ranking_ms = 0
        self.tokens = dict.fromkeys(TOKEN_FIELDS)

    def clean(self, text):
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        return text

    def progress(self, text):
        try:
            print(self.clean(text), file=sys.stderr, flush=True)
        except Exception:
            pass

    def begin(self, run_id, now):
        self.run_id, self.timestamp = run_id, now.isoformat()
        self.started = perf_counter()
        self.path = self.directory / f"run-{time_ns()}-{run_id}.jsonl"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.stream = self.path.open("x", encoding="utf-8")
        except Exception:
            self.progress("Warning: trace file unavailable")

    def emit(self, event, **fields):
        if self.stream is None:
            return
        try:
            # Redact before serialization, including secrets containing JSON escapes.
            def redact(value):
                if isinstance(value, str):
                    return self.clean(value)
                if isinstance(value, dict):
                    return {self.clean(str(k)): redact(v) for k, v in value.items()}
                if isinstance(value, (list, tuple)):
                    return [redact(v) for v in value]
                return value
            self.stream.write(json.dumps(redact(dict(event=event, **fields)), ensure_ascii=False) + "\n")
            self.stream.flush()
        except Exception:
            self.progress("Warning: trace event could not be written")

    def start(self, preferences, model=None, base_url=None):
        try:
            url = urlsplit(base_url or "")
            base_url = urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1], url.path, "", ""))
        except ValueError:
            base_url = None
        self.emit("run_start", run_id=self.run_id, timestamp=self.timestamp,
                  model=model, llm_base_url=base_url, preferences=preferences,
                  ranking_prompt_sha256=hashlib.sha256(RANKING_PROMPT.encode()).hexdigest())

    def page(self, **fields):
        self.emit("source_page_finished", **fields)
        state = "" if fields["success"] else " ERROR"
        self.progress(f"[source] page {fields['page']}/3:{state} {fields['item_count']} items, "
                      f"{fields['bad_item_count']} bad, {fields['duration_ms'] / 1000:.2f}s")

    def batch(self, duration_ms, index, total, size, metadata, failures):
        self.calls += metadata.get("provider_calls", 1)
        self.ranking_ms += duration_ms
        usage = metadata.get("usage")
        if isinstance(usage, dict):
            for key in TOKEN_FIELDS:
                value = usage.get(key)
                if key == "reasoning_tokens":
                    details = usage.get("completion_tokens_details")
                    value = details.get(key) if isinstance(details, dict) else None
                if type(value) is int and value >= 0:
                    self.tokens[key] = (self.tokens[key] or 0) + value
        self.emit("ranking_batch_finished", batch_index=index, batch_size=size,
                  duration_ms=duration_ms, usage=usage, success=not failures and not metadata.get("protocol_errors"),
                  succeeded=size - failures, failed=failures,
                  protocol_errors=metadata.get("protocol_errors", []),
                  **({"raw_output_preview": metadata["raw_output_preview"]}
                     if "raw_output_preview" in metadata else {}))
        self.progress(f"[rank batch {index}/{total}] {size} items, {duration_ms / 1000:.2f}s"
                      + (f", {failures} failed" if failures else ""))

    def ranking(self, notice, *, result=None, error=None):
        fields = dict(external_id=notice.external_id, title=notice.title)
        if error is None:
            self.succeeded += 1
            self.emit("ranking_finished", **fields, recommend=result.recommend,
                      priority=result.priority, reason=result.reason)
        else:
            self.failed += 1
            self.emit("ranking_failed", **fields, error_type=type(error).__name__, error_message=str(error))

    def finish(self, status, source_duration_ms):
        total_ms = (perf_counter() - self.started) * 1000
        self.emit("run_finished", run_id=self.run_id, status=status, total_duration_ms=total_ms,
                  source_duration_ms=source_duration_ms, ranking_duration_ms=self.ranking_ms,
                  llm_calls=self.calls, ranking_succeeded=self.succeeded, ranking_failed=self.failed,
                  **self.tokens)
        self.progress(f"LLM calls: {self.calls}\nSucceeded: {self.succeeded}\nFailed: {self.failed}")
        for key, value in self.tokens.items():
            self.progress(f"{key.replace('_', ' ').capitalize()}: {value if value is not None else 'unavailable'}")
        self.progress(f"Ranking time: {self.ranking_ms / 1000:.2f}s\nTotal time: {total_ms / 1000:.2f}s\nTrace: {self.path}")
        if self.stream is not None:
            try:
                self.stream.close()
            except Exception:
                self.progress("Warning: trace file could not be closed")
