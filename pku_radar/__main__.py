import argparse
import sys

from .pipeline import run_pipeline
from .ranking import FakeRanker, load_preferences
from .source import FakeSource, ROOT
from .storage import Store


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description="PKU Radar offline fixture mode")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--fake-day", type=int, choices=(1, 2), default=1)
    run.add_argument("--db", default="data/pku_radar_offline.db")
    run.add_argument("--preferences", default=ROOT / "config/preferences.yaml")
    run.add_argument("--max-rank-attempts", type=positive_int, default=3)
    run.add_argument("--rerank", action="store_true", help="Rerank every unsurfaced item in the database")
    args = parser.parse_args(argv)
    store = None
    try:
        store = Store(args.db)
        # Configuration failures also belong to the persisted run lifecycle.
        class ConfiguredSource:
            def fetch(self):
                preferences.update(load_preferences(args.preferences))
                return FakeSource(args.fake_day).fetch()

        preferences = {}
        result = run_pipeline(store, ConfiguredSource(), FakeRanker(), preferences,
                              max_rank_attempts=args.max_rank_attempts, rerank=args.rerank)
        print(f"Run {result['id']}: {result['status']}. Run log: {args.db} (runs table).", file=sys.stderr)
        if result["status"] != "success":
            print(result["error"], file=sys.stderr)
        return 0 if result["status"] == "success" else 1
    except Exception as exc:
        print(f"Pipeline failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
