import json
from dataclasses import dataclass
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware datetime required")
    return value.isoformat()


@dataclass(frozen=True)
class Notice:
    provider: str
    external_id: str
    source_id: str | None
    source_name: str | None
    title: str | None
    published_at: str | None
    url: str | None
    category: str | None
    intent_group: str | None
    summary: str | None
    upstream_is_event: bool | None
    event_time_text: str | None
    event_location: str | None
    raw_json: str

    @classmethod
    def from_raw(cls, raw, provider="pkuknow"):
        return cls(
            provider, str(raw["id"]), raw.get("source_id"), raw.get("source_name"),
            raw.get("title"), raw.get("published_at"), raw.get("url"),
            raw.get("category"), raw.get("intent_group"), raw.get("ai_summary"),
            raw.get("ai_is_event"), raw.get("ai_event_time"), raw.get("ai_event_location"),
            json.dumps(raw, ensure_ascii=False),
        )


@dataclass(frozen=True)
class Recommendation:
    recommend: bool
    priority: str | None
    reason: str

    @classmethod
    def validate(cls, result):
        if not isinstance(result, dict) or type(result.get("recommend")) is not bool:
            raise ValueError("recommend must be a bool")
        priority = result.get("priority")
        if priority not in (None, "high", "medium", "low"):
            raise ValueError("invalid priority")
        if result["recommend"] and priority is None:
            raise ValueError("recommended items require priority")
        reason = result.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a nonempty string")
        return cls(result["recommend"], priority, reason)
