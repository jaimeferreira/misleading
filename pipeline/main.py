"""Daily pipeline: ingest -> filter/rank -> cluster -> snapshot -> render.

    python -m pipeline.main                      # full run
    python -m pipeline.main --no-llm             # skip clustering
    python -m pipeline.main --notes a.tsv --status b.tsv   # use local dump files
"""
import argparse
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from . import cluster as clustering
from .filter_rank import select_tweets
from .ingest import fetch_dump
from .render import ROOT, render


def load_snapshots(snap_dir: Path, before: date, limit: int) -> list[dict]:
    files = sorted(p for p in snap_dir.glob("*.json") if p.stem < before.isoformat())
    return [json.loads(p.read_text()) for p in files[-limit:]]


def build_snapshot(tweets: list[dict], state: dict | None, cfg: dict, run_date: date,
                   dump_date: date, data_as_of: datetime, prior: list[dict]) -> dict:
    rep = cfg["report"]
    by_narr: dict[str, list[dict]] = {}
    if state:
        for t in tweets:
            nid = state["assignments"].get(t["tweet_id"])
            if nid in state["narratives"]:
                t["narrative_id"] = nid
                by_narr.setdefault(nid, []).append(t)

    narratives = []
    for nid, members in by_narr.items():
        score = round(sum(t["score"] for t in members), 2)
        past = [p_n["score"] for p in prior for p_n in p["narratives"] if p_n["id"] == nid]
        prev = past[-1] if past else None
        narratives.append({
            "id": nid,
            **{k: state["narratives"][nid][k] for k in ("title", "description")},
            "score": score,
            "prev_score": prev,
            "delta": None if prev is None else round(score - prev, 2),
            "is_new": prev is None,
            "n_tweets": len(members),
            "history": (past + [score])[-rep["history_days"]:],
            "tweets": members[: rep["tweets_per_narrative"]],
        })
    narratives.sort(key=lambda n: -n["score"])

    return {
        "run_date": run_date.isoformat(),
        "dump_date": dump_date.isoformat(),
        "data_as_of": data_as_of.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": cfg["window_days"],
        "traction_signal": "notes-based proxy (no X engagement data)",
        "total_tweets": len(tweets),
        "clustered": state is not None,
        "unclustered_tweets": sum(1 for t in tweets if "narrative_id" not in t),
        "narratives": narratives[: rep["top_narratives"]],
        "tweets": tweets[: rep["top_tweets"]],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=date.fromisoformat, default=datetime.now(timezone.utc).date())
    ap.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    ap.add_argument("--work-dir", type=Path, default=ROOT / "work")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "site")
    ap.add_argument("--notes", type=Path, nargs="+", help="local notes TSV(s); skips download")
    ap.add_argument("--status", type=Path, nargs="+", help="local noteStatusHistory TSV(s); skips download")
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()

    cfg = yaml.safe_load(args.config.read_text())

    if args.notes and args.status:
        dump_date, notes, status = args.date, args.notes, args.status
    else:
        print("Fetching dump...")
        dump_date, notes, status = fetch_dump(args.date, args.work_dir, cfg["max_dump_lookback_days"])
    print(f"Dump {dump_date}: {len(notes)} notes file(s), {len(status)} status file(s)")

    tweets, data_as_of = select_tweets(notes, status, cfg["window_days"], cfg["score"])
    print(f"{len(tweets)} tweets with a helpful misleading note in the last {cfg['window_days']} days "
          f"(data as of {data_as_of:%Y-%m-%d %H:%M} UTC)")

    state = None
    api_key = os.environ.get("OPENROUTER_API_KEY")
    state_path = args.data_dir / "state.json"
    if args.no_llm or not api_key:
        print("Skipping clustering" + ("" if args.no_llm else " (OPENROUTER_API_KEY not set)"))
    else:
        state = json.loads(state_path.read_text()) if state_path.exists() else clustering.empty_state()
        print("Clustering...")
        clustering.cluster(tweets, state, cfg["llm"], api_key, args.date)
        args.data_dir.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1))

    snap_dir = args.data_dir / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    prior = load_snapshots(snap_dir, args.date, cfg["report"]["history_days"])
    snapshot = build_snapshot(tweets, state, cfg, args.date, dump_date, data_as_of, prior)
    (snap_dir / f"{args.date.isoformat()}.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=1))

    print(f"Wrote {render(snapshot, args.out_dir)}")


if __name__ == "__main__":
    main()
