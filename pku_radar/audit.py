"""Read-only review of persisted ranking decisions; no pipeline or ranker calls."""

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def published_key(row):
    try:
        date = datetime.fromisoformat(row["published_at"])
        if date.tzinfo is None:
            date = date.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        value = date.timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        value = float("-inf")
    return -value, row["provider"], row["external_id"]


def build_audit(rows):
    ranked = [row for row in rows if row["rank_status"] == "succeeded"]
    recommended = [row for row in ranked if row["recommend"]]
    priorities = ("high", "medium", "low")
    summary = ["# PKU Radar Audit", "", f"Total ranked: {len(ranked)}",
               f"Recommended: {len(recommended)}"]
    summary.extend(f"  {priority.title()}: {sum(row['priority'] == priority for row in recommended)}"
                   for priority in priorities)
    summary.append(f"Not recommended: {len(ranked) - len(recommended)}")
    for status in ("pending", "failed"):
        count = sum(row["rank_status"] == status for row in rows)
        if count:
            summary.append(f"{status.title()} ranking: {count}")
    summary = "\n".join(summary)
    lines = [summary, "", "## Recommended", ""]
    groups = [(f"### {priority.title()}",
               [row for row in recommended if row["priority"] == priority]) for priority in priorities]
    groups.append(("## Not Recommended", [row for row in ranked if not row["recommend"]]))
    for heading, items in groups:
        lines.extend([heading, ""])
        if not items:
            lines.extend(["(none)", ""])
        for row in sorted(items, key=published_key):
            marker = row["priority"].upper() if row["recommend"] else "NO"
            lines.append(f"[{marker}] {row['title'] or '(untitled)'}")
            for label, field in (("External ID", "external_id"), ("Source", "source_name"),
                                 ("Category", "category"), ("Published", "published_at"),
                                 ("Recommend", "recommend"), ("Priority", "priority"),
                                 ("Reason", "recommendation_reason"), ("Event time", "event_time_text"),
                                 ("Event location", "event_location"), ("URL", "url")):
                value = row[field]
                if field == "recommend":
                    value = "true" if value else "false"
                lines.append(f"{label}: {value if value is not None else '—'}")
            lines.append("")
    return summary, "\n".join(lines)


def audit(db_path, output=None):
    path = Path(db_path).resolve()
    # Do not use Store: its initialization executes schema writes.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM items").fetchall()
    summary, report = build_audit(rows)
    if output is None:
        print(report)
        return
    destination = Path(output).resolve()
    protected = [path, *(Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))]
    if any(destination == candidate or (destination.exists() and candidate.exists()
                                        and destination.samefile(candidate)) for candidate in protected):
        raise ValueError("Output must not overwrite the SQLite database or its sidecar files")
    if destination.exists():
        with destination.open("rb") as existing:
            if existing.read(16) == b"SQLite format 3\x00":
                raise ValueError("Output must not overwrite a SQLite database")
    destination.write_text(report, encoding="utf-8")
    print(f"{summary}\n\nOutput: {destination}")
