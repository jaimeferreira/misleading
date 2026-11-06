"""Download and unpack the public Community Notes dumps."""
import zipfile
from datetime import date, timedelta
from pathlib import Path

import httpx

BASE = "https://ton.twimg.com/birdwatch-public-data"
KINDS = ("notes", "noteStatusHistory")
MAX_PARTS = 20


def _url(day: date, kind: str, part: int) -> str:
    return f"{BASE}/{day:%Y/%m/%d}/{kind}/{kind}-{part:05d}.zip"


def _download(client: httpx.Client, url: str, dest: Path) -> bool:
    """Stream url to dest. Returns False if the file is not published."""
    with client.stream("GET", url) as resp:
        if resp.status_code != 200 or "zip" not in resp.headers.get("content-type", ""):
            return False
        tmp = dest.with_suffix(".part")
        with tmp.open("wb") as f:
            for chunk in resp.iter_bytes(1 << 20):
                f.write(chunk)
        tmp.rename(dest)
    return True


def _fetch_kind(client: httpx.Client, day: date, kind: str, work: Path) -> list[Path]:
    out = work / f"{day:%Y-%m-%d}"
    out.mkdir(parents=True, exist_ok=True)
    tsvs: list[Path] = []
    for part in range(MAX_PARTS):
        zpath = out / f"{kind}-{part:05d}.zip"
        if not zpath.exists() and not _download(client, _url(day, kind, part), zpath):
            break
        with zipfile.ZipFile(zpath) as z:
            for name in z.namelist():
                target = out / Path(name).name
                if not target.exists():
                    with z.open(name) as src, target.open("wb") as dst:
                        while chunk := src.read(1 << 20):
                            dst.write(chunk)
                tsvs.append(target)
    return tsvs


def fetch_dump(today: date, work: Path, max_lookback: int) -> tuple[date, list[Path], list[Path]]:
    """Find the newest published dump on or before `today`; return (dump_date, notes, status)."""
    work.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=httpx.Timeout(60, read=300), follow_redirects=True) as client:
        for back in range(max_lookback + 1):
            day = today - timedelta(days=back)
            notes = _fetch_kind(client, day, KINDS[0], work)
            if not notes:
                continue
            status = _fetch_kind(client, day, KINDS[1], work)
            if status:
                return day, notes, status
    raise RuntimeError(f"No Community Notes dump found in the {max_lookback + 1} days up to {today}")
