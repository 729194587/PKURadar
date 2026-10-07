import argparse
import sys

from .audit import audit
from .pipeline import run_pipeline
from .digest import DigestBuilder
from .ranking import FakeRanker, LLMRanker, load_preferences
from .source import FakeSource, PKUKnowSource, ROOT
from .storage import Store


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description="PKU Radar (offline by default)")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--fake-day", type=int, choices=(1, 2), default=1)
    mode.add_argument("--live", action="store_true", help="Fetch public notices and call the configured LLM")
    run.add_argument("--db")
    run.add_argument("--preferences", default=ROOT / "config/preferences.yaml")
    run.add_argument("--max-rank-attempts", type=positive_int, default=3)
    run.add_argument("--rerank", action="store_true", help="Rerank every unsurfaced item in the database")
    review = commands.add_parser("audit", help="Review stored ranking decisions without reranking")
    review.add_argument("--db", default="data/pku_radar_live.db")
    review.add_argument("--output", help="Write the full audit as UTF-8 Markdown")
    args = parser.parse_args(argv)
    if args.command == "audit":
        try:
            audit(args.db, args.output)
            return 0
        except Exception as exc:
            print(f"Audit failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    args.db = args.db or ("data/pku_radar_live.db" if args.live else "data/pku_radar_offline.db")
    store = None
    try:
        store = Store(args.db)
        # Configuration failures also belong to the persisted run lifecycle.
        class ConfiguredSource:
            def fetch(self):
                nonlocal ranker
                preferences.update(load_preferences(args.preferences))
                if args.live:
                    ranker = LLMRanker.from_env()
                    return PKUKnowSource().fetch()
                return FakeSource(args.fake_day).fetch()

        class ConfiguredRanker:
            def rank(self, *values):
                return ranker.rank(*values)

        preferences = {}
        ranker = FakeRanker()
        result = run_pipeline(store, ConfiguredSource(), ConfiguredRanker(), preferences,
                              builder=DigestBuilder(live=args.live),
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
