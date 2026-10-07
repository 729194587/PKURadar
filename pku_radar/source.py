import json
from pathlib import Path

from .models import Notice

ROOT = Path(__file__).resolve().parent.parent


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
