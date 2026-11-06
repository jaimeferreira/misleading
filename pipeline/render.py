"""Render the static dashboard from a snapshot."""
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

ROOT = Path(__file__).resolve().parent.parent


def sparkline(values: list[float], w: int = 90, h: int = 22) -> Markup:
    if len(values) < 2:
        return Markup("")
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pts = " ".join(
        f"{i * w / (len(values) - 1):.1f},{h - 2 - (v - lo) / span * (h - 4):.1f}" for i, v in enumerate(values)
    )
    return Markup(
        f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}" aria-hidden="true">'
        f'<polyline points="{pts}" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>'
    )


def render(snapshot: dict, out_dir: Path) -> Path:
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape())
    env.globals["sparkline"] = sparkline
    html = env.get_template("dashboard.html.j2").render(s=snapshot)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    (out_dir / "latest.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    return out_dir / "index.html"
