"""Group tweets into narratives (recurring claims) with an LLM via OpenRouter.

State is kept across runs so narrative ids stay stable and already-assigned tweets are not re-sent.
state = {"next_id": int, "narratives": {id: {title, description, last_active}}, "assignments": {tweet_id: id}}
"""
import json
import re
import time
from datetime import date, timedelta

import httpx

SYSTEM = """You group Community Notes on X into narratives. Each note explains why a tweet is misleading.
A narrative is one specific false or misleading claim (e.g. "Video shows a 2024 flood, not the current one"),
not a broad topic (not "Politics" or "Health"). Tweets pushing the same claim belong together even if
the wording differs; tweets about the same topic but different claims do not.
Notes are in many languages: write titles and descriptions in English, and group across languages.
Reply with JSON only."""

PROMPT = """Existing narratives:
{existing}

New notes (idx: note text):
{items}

Assign every note to an existing narrative id, or to a new narrative. Reply with JSON:
{{"new_narratives": [{{"key": "a", "title": "<claim, max 12 words>", "description": "<one sentence on what is false or missing>"}}],
 "assignments": [{{"idx": 0, "narrative": "<existing id, or new:<key>>"}}]}}
Prefer reusing an existing narrative when the claim matches. Every idx must appear exactly once."""


def empty_state() -> dict:
    return {"next_id": 1, "narratives": {}, "assignments": {}}


def _call(client: httpx.Client, cfg: dict, api_key: str, prompt: str) -> dict:
    payload = {
        "model": cfg["model"],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = client.post(cfg["endpoint"], json=payload, headers=headers)
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
            return json.loads(text)
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"OpenRouter call failed: {last_err}")


def _active_narratives(state: dict, cfg: dict, today: date) -> list[tuple[str, dict]]:
    cutoff = (today - timedelta(days=cfg["narrative_active_days"])).isoformat()
    active = [(i, n) for i, n in state["narratives"].items() if n["last_active"] >= cutoff]
    active.sort(key=lambda kv: kv[1]["last_active"], reverse=True)
    return active[: cfg["max_existing_narratives"]]


def _apply(state: dict, batch: list[dict], result: dict, valid_existing: set[str], today: date) -> int:
    new_keys: dict[str, str] = {}
    for item in result.get("new_narratives", []):
        key, title = item.get("key"), (item.get("title") or "").strip()
        if not key or not title:
            continue
        nid = f"n{state['next_id']}"
        state["next_id"] += 1
        state["narratives"][nid] = {
            "title": title,
            "description": (item.get("description") or "").strip(),
            "last_active": today.isoformat(),
        }
        new_keys[str(key)] = nid

    assigned = 0
    for a in result.get("assignments", []):
        idx, ref = a.get("idx"), str(a.get("narrative", ""))
        if not isinstance(idx, int) or not 0 <= idx < len(batch):
            continue
        nid = new_keys.get(ref[4:]) if ref.startswith("new:") else (ref if ref in valid_existing else None)
        if nid:
            state["assignments"][batch[idx]["tweet_id"]] = nid
            state["narratives"][nid]["last_active"] = today.isoformat()
            assigned += 1
    return assigned


def cluster(tweets: list[dict], state: dict, cfg: dict, api_key: str, today: date) -> dict:
    """Assign the top-ranked unassigned tweets to narratives; mutates and returns state."""
    pending = [t for t in tweets[: cfg["top_n_tweets"]] if t["tweet_id"] not in state["assignments"]]
    # Tweets that are still in the report keep their narrative alive.
    for t in tweets:
        nid = state["assignments"].get(t["tweet_id"])
        if nid in state["narratives"]:
            state["narratives"][nid]["last_active"] = today.isoformat()

    with httpx.Client(timeout=120) as client:
        for start in range(0, len(pending), cfg["batch_size"]):
            batch = pending[start : start + cfg["batch_size"]]
            existing = _active_narratives(state, cfg, today)
            existing_txt = "\n".join(f"{i}: {n['title']} -- {n['description']}" for i, n in existing) or "(none yet)"
            items_txt = "\n".join(f"{k}: {t['summary'][:500]}" for k, t in enumerate(batch))
            result = _call(client, cfg, api_key, PROMPT.format(existing=existing_txt, items=items_txt))
            got = _apply(state, batch, result, {i for i, _ in existing}, today)
            print(f"  clustered batch {start // cfg['batch_size'] + 1}: {got}/{len(batch)} assigned")
    return state
