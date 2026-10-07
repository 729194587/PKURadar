from datetime import datetime
from zoneinfo import ZoneInfo


class TerminalWriter:
    def write(self, text):
        print(text, flush=True)


def sort_key(row):
    try:
        date = datetime.fromisoformat(row["published_at"])
        value = date.timestamp() if date.tzinfo is not None else float('-inf')
    except (TypeError, ValueError, OverflowError):
        value = float('-inf')
    return ({"high": 0, "medium": 1, "low": 2}[row["priority"]], -value,
            row["provider"], row["external_id"])


class DigestBuilder:
    def __init__(self, *, live=False):
        self.marker = "[PKU RADAR LIVE]" if live else "[OFFLINE FIXTURE MODE]"

    def build(self, rows, counts, now, failed_count=0, exhausted_count=0):
        lines = [self.marker, "", f"PKU Radar · {now.astimezone(ZoneInfo('Asia/Shanghai')):%Y-%m-%d}", ""]
        lines.extend(f"{name.title()}: {counts[name]}" for name in ("fetched", "new", "ranked", "recommended"))
        lines.extend([f"Shown: {len(rows)}", ""])
        for row in sorted(rows, key=sort_key):
            lines.extend([f"[{row['priority'].upper()}] {row['title'] or '(untitled)'}",
                          row["recommendation_reason"], row["url"] or "", ""])
        if not rows:
            lines.append("No relevant new notices today.")
        if failed_count:
            lines.extend(["", f"{failed_count} items failed ranking. See run log for details."])
        if exhausted_count:
            lines.append(f"{exhausted_count} items reached max_rank_attempts this run. See run log for details.")
        return "\n".join(lines)
