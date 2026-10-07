from pathlib import Path

import yaml


def load_preferences(path):
    preferences = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(preferences, dict):
        raise ValueError("preferences must be a mapping")
    for key in ("primary_interests", "secondary_interests", "low_interest"):
        values = preferences.get(key, [])
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError(f"{key} must be a list of nonempty strings")
        preferences[key] = values
    return preferences


class FakeRanker:
    """Substring matching; optional per-identity scripted failures for offline tests."""

    def __init__(self, failures=None, invalid_outputs=None):
        self.failures = dict(failures or {})
        self.invalid_outputs = dict(invalid_outputs or {})
        self.calls = []

    def rank(self, notice, preferences, current_datetime):
        key = (notice.provider, notice.external_id)
        self.calls.append((key, current_datetime))
        remaining = self.failures.get(key, 0)
        if remaining:
            if remaining > 0:
                self.failures[key] -= 1
            raise RuntimeError("scripted ranking failure")
        if key in self.invalid_outputs:
            return self.invalid_outputs[key]
        text = " ".join(value or "" for value in
                        (notice.title, notice.summary, notice.category, notice.source_name)).casefold()
        for group, priority in (("primary_interests", "high"), ("secondary_interests", "medium"),
                                ("low_interest", None)):
            matches = [term for term in preferences.get(group, []) if term.casefold() in text]
            if matches:
                return {"recommend": priority is not None, "priority": priority,
                        "reason": f"Matched {group}: {', '.join(matches)}"}
        return {"recommend": False, "priority": None, "reason": "No matching interests."}
