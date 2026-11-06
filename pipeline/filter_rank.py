"""Select tweets with a misleading note rated helpful, and rank them by a proxy score."""
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import duckdb

MISLEADING = "MISINFORMED_OR_POTENTIALLY_MISLEADING"
TWITTER_EPOCH_MS = 1288834974657

QUERY = """
with helpful as (
    select n.noteId, n.tweetId, n.noteAuthorParticipantId as author, n.summary,
           coalesce(try_cast(s.timestampMillisOfMostRecentStatusChange as bigint),
                    try_cast(s.timestampMillisOfCurrentStatus as bigint)) as rated_ms
    from notes n join status s using (noteId)
    where n.classification = $misleading and s.currentStatus = 'CURRENTLY_RATED_HELPFUL'
),
per_tweet as (
    select tweetId,
           count(*) as helpful_notes,
           count(distinct author) as authors,
           max(rated_ms) as rated_ms,
           arg_max(summary, rated_ms) as summary
    from helpful
    group by tweetId
    having max(rated_ms) >= $cutoff_ms
),
totals as (
    select tweetId, count(*) as total_notes
    from notes
    where classification = $misleading and tweetId in (select tweetId from per_tweet)
    group by tweetId
)
select p.tweetId, p.helpful_notes, p.authors, t.total_notes, p.rated_ms, p.summary
from per_tweet p join totals t using (tweetId)
"""


def _sql_list(paths: list[Path]) -> str:
    return "[" + ", ".join("'" + str(p).replace("'", "''") + "'" for p in paths) + "]"


def _view(con: duckdb.DuckDBPyConnection, name: str, paths: list[Path]) -> None:
    con.execute(
        f"create view {name} as select * from read_csv({_sql_list(paths)}, delim='\t', header=true, "
        "quote='', escape='', all_varchar=true, ignore_errors=true, union_by_name=true)"
    )


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def select_tweets(notes: list[Path], status: list[Path], window_days: int, score_cfg: dict,
                  now: datetime | None = None) -> tuple[list[dict], datetime]:
    """Return (tweets ranked by score desc, data_as_of). data_as_of is the newest status change in the dump."""
    now = now or datetime.now(timezone.utc)
    con = duckdb.connect()
    _view(con, "notes", notes)
    _view(con, "status", status)

    as_of_ms = con.execute(
        "select max(try_cast(timestampMillisOfMostRecentStatusChange as bigint)) from status"
    ).fetchone()[0]
    data_as_of = datetime.fromtimestamp(as_of_ms / 1000, timezone.utc)

    # The window is measured back from the data, not from wall-clock, because the dump lags ~2 days.
    cutoff_ms = as_of_ms - window_days * 86_400_000
    rows = con.execute(QUERY, {"misleading": MISLEADING, "cutoff_ms": cutoff_ms}).fetchall()

    tweets = []
    for tweet_id, helpful, authors, total, rated_ms, summary in rows:
        age_days = max(0.0, (as_of_ms - rated_ms) / 86_400_000)
        raw = (score_cfg["w_helpful_notes"] * helpful
               + score_cfg["w_authors"] * authors
               + score_cfg["w_total_notes"] * math.log1p(total))
        score = raw * 0.5 ** (age_days / score_cfg["recency_half_life_days"])
        created_ms = (int(tweet_id) >> 22) + TWITTER_EPOCH_MS if tweet_id.isdigit() else None
        tweets.append({
            "tweet_id": tweet_id,
            "url": f"https://x.com/i/web/status/{tweet_id}",
            "score": round(score, 3),
            "helpful_notes": helpful,
            "authors": authors,
            "total_notes": total,
            "rated_helpful_at": datetime.fromtimestamp(rated_ms / 1000, timezone.utc).isoformat(),
            "tweet_created_at": (datetime.fromtimestamp(created_ms / 1000, timezone.utc).isoformat()
                                 if created_ms else None),
            "summary": _clean(summary),
        })
    tweets.sort(key=lambda t: (-t["score"], t["tweet_id"]))
    return tweets, data_as_of
