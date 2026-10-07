import json
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import Notice

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class SourceResult:
    notices: list[Notice]
    errors: list[str]
    complete: bool
    successful_pages: int


class PKUKnowSource:
    def __init__(self, *, opener=None, sleep=None):
        self.opener = opener or urlopen
        self.sleep = sleep or time.sleep

    def fetch(self):
        notices, errors = [], []
        successful_pages = 0
        for page in range(1, 4):
            if page > 1:
                self.sleep(3)
            params = dict(q="", category="全部通知", source="all", group="wechat,official",
                          intent="all", view="list", page=page)
            request = Request("https://pkuknow.cn/api/notices?" + urlencode(params),
                              headers={"User-Agent": "PKURadar/0.2"})
            try:
                with self.opener(request, timeout=30) as response:
                    if not 200 <= response.status < 300:
                        raise ValueError(f"HTTP {response.status}")
                    payload = json.load(response)
                if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                    raise ValueError("response must be an object with an items list")
            except Exception as exc:
                errors.append(f"Source page {page}: {type(exc).__name__}: {exc}")
                continue
            successful_pages += 1
            for index, raw in enumerate(payload["items"]):
                try:
                    # Validate mapped types before SQLite ingestion so one bad item stays isolated.
                    if not isinstance(raw, dict) or type(raw.get("id")) not in (str, int) or not str(raw["id"]).strip():
                        raise ValueError("item requires a nonempty string or integer id")
                    for field in ("source_id", "source_name", "title", "published_at", "url",
                                  "category", "intent_group", "ai_summary", "ai_event_time", "ai_event_location"):
                        if raw.get(field) is not None and not isinstance(raw[field], str):
                            raise ValueError(f"{field} must be a string or null")
                    if raw.get("ai_is_event") is not None and type(raw["ai_is_event"]) is not bool:
                        raise ValueError("ai_is_event must be a bool or null")
                    notices.append(Notice.from_raw(raw))
                except Exception as exc:
                    errors.append(f"Source page {page} item {index + 1}: {type(exc).__name__}: {exc}")
        return SourceResult(notices, errors, not errors, successful_pages)


class FakeSource:
    def __init__(self, day=1, fixture=ROOT / "tests/fixtures/pkuknow_notices.json"):
        if day not in (1, 2):
            raise ValueError("fake day must be 1 or 2")
        self.day, self.fixture = day, Path(fixture)

    def fetch(self):
        items = json.loads(self.fixture.read_text(encoding="utf-8"))["items"]
        if self.day == 2:
            def synthetic(identity, title, date):
                return {
                    "id": identity,
                    "source_id": None,
                    "source_name": None,
                    "title": title,
                    "published_at": date,
                    "url": None,
                    "category": None,
                    "intent_group": None,
                    "ai_summary": title,
                    "ai_is_event": None,
                    "ai_event_time": None,
                    "ai_event_location": None,
                }

            items = [
                synthetic("offline:day2:ai", "AI Agent 技术讲座", "2026-10-08T10:00:00+08:00"),
                synthetic("offline:day2:club", "动漫社团活动", "2026-10-08T09:00:00+08:00"),
                *items[:24],
                synthetic("offline:day2:late", "开源工程实践（迟到通知）", "2025-01-01T09:00:00+08:00"),
            ]
        return [Notice.from_raw(item) for item in items]
