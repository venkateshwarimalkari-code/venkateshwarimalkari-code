#!/usr/bin/env python3
"""Render the animated SVG cards for the GitHub profile README into cards/.

    python render_cards.py              # animated cards
    STATIC=1 python render_cards.py     # final frame only (for screenshots)
    DATA=path.json python render_cards.py

The cards are shown through <img> tags, so each SVG is fully self-contained:
one inline <style> block (CSS keyframes), SMIL <animate> elements, and fonts
subset with pyftsubset and embedded as base64 WOFF data URIs. No scripts,
no external requests. Everything animates top to bottom on staggered delays
and switches off under prefers-reduced-motion.

Data comes from data/github.json (written daily by scripts/fetch_github_data.py
in .github/workflows/profile-cards.yml). Requires: fonttools; Pillow only if
you add a portrait.
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "cards"
STATIC = os.environ.get("STATIC") == "1"

# --------------------------------------------------------------------------
# Theme: change colours and fonts here and nowhere else.
# --------------------------------------------------------------------------
THEME = {
    "bg": "#15141b",        # page behind the cards' rounded corners
    "surface": "#1b1a23",   # card fill
    "raised": "#232130",    # stat boxes, chips
    "border": "#2e2b40",
    "text": "#edecee",
    "muted": "#a19fb0",
    "faint": "#6a687a",
    "brand": "#a277ff",     # main accent: underline, bars, contribution graph
    "accents": ["#a277ff", "#61ffca", "#ffca85"],  # tiles, in order
    "radius": 16,
}
FONTS = {  # key -> (file, weight). Files are latin WOFFs from Fontsource (OFL).
    "display": ("fonts/space-grotesk-latin-700-normal.woff", 700),
    "body": ("fonts/space-grotesk-latin-400-normal.woff", 400),
    "mono": ("fonts/jetbrains-mono-latin-500-normal.woff", 500),
    "monob": ("fonts/jetbrains-mono-latin-700-normal.woff", 700),
}
FALLBACK = {"display": "sans-serif", "body": "sans-serif", "mono": "monospace", "monob": "monospace"}

# --------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------
PROFILE = {
    "login": "venkateshwarimalkari-code",
    "name": "Venkateshwari Malkari",
    "kicker": "HEY, I'M",
    "tagline": "CSE AI & ML student turning “what if?” into “let’s try it!”",
    "chips": ["AI & ML", "Python", "Computer Vision", "Cloud"],
    "portrait": "assets/portrait.jpg",  # optional; a monogram is drawn if missing
    "tiles": [
        {"kicker": "LINKEDIN", "title": "Let’s connect", "sub": "in/venkateshwari",
         "url": "https://linkedin.com/in/venkateshwari-85543b338"},
        {"kicker": "INSTAGRAM", "title": "Off the clock", "sub": "@venkateshwariii",
         "url": "https://instagram.com/venkateshwariii"},
        {"kicker": "EMAIL", "title": "Say hello", "sub": "venkateshwarimalkari@gmail.com",
         "url": "mailto:venkateshwarimalkari@gmail.com"},
    ],
    # Repo names to feature, in order. Empty = your pinned repos, else your
    # most-starred / most recently pushed. The profile repo itself is skipped.
    "featured": [],
    "featured_max": 3,
}
W = 830  # card width in px; matches GitHub's README column, so 1 unit = 1 px

# Page timeline (seconds). Each card's clock starts when its image loads,
# which is near-simultaneous, so these offsets make the page play top-down.
T_HERO, T_TILES, T_CHART, T_REPOS, T_GRAPH = 0.0, 0.55, 1.0, 1.7, 2.3


# --------------------------------------------------------------------------
# Fonts: measuring and embedding
# --------------------------------------------------------------------------
class Face:
    def __init__(self, key: str):
        self.key = key
        self.path = ROOT / FONTS[key][0]
        self.weight = FONTS[key][1]
        f = TTFont(self.path)
        self.cmap = f.getBestCmap()
        self.hmtx = f["hmtx"].metrics
        self.upm = f["head"].unitsPerEm
        self.notdef = self.hmtx.get(".notdef", (self.upm // 2, 0))[0]

    def width(self, s: str, size: float, tracking: float = 0) -> float:
        units = sum(self.hmtx.get(self.cmap.get(ord(ch), ".notdef"), (self.notdef, 0))[0] for ch in s)
        return units * size / self.upm + tracking * size * len(s)


FACE = {k: Face(k) for k in FONTS}
_subset_cache: dict[tuple[str, str], str] = {}


def woff_data_uri(key: str, chars: set[str]) -> str:
    text = "".join(sorted(chars | {" "}))
    if (key, text) in _subset_cache:
        return _subset_cache[(key, text)]
    with tempfile.TemporaryDirectory() as tmp:
        txt, out = Path(tmp, "chars.txt"), Path(tmp, "out.woff")
        txt.write_text(text, encoding="utf-8")
        subprocess.run([sys.executable, "-m", "fontTools.subset", str(FACE[key].path),
                        f"--text-file={txt}", f"--output-file={out}", "--flavor=woff",
                        "--layout-features=kern,liga", "--no-hinting", "--desubroutinize",
                        "--name-IDs=", "--drop-tables+=FFTM,GDEF"],
                       check=True)  # same as the pyftsubset CLI
        uri = "data:font/woff;base64," + base64.b64encode(out.read_bytes()).decode()
    _subset_cache[(key, text)] = uri
    return uri


def wrap(s: str, key: str, size: float, maxw: float, max_lines: int) -> list[str]:
    words, lines, cur = s.split(), [], ""
    for w_ in words:
        trial = f"{cur} {w_}".strip()
        if FACE[key].width(trial, size) <= maxw or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and FACE[key].width(last + "…", size) > maxw:
            last = last[:-1].rstrip()
        lines[-1] = last.rstrip(" ,.;:") + "…"
    return lines


def fit(s: str, key: str, size: float, maxw: float, min_size: float = 9, tracking=0) -> float:
    while size > min_size and FACE[key].width(s, size, tracking) > maxw:
        size -= 0.5
    return size


# --------------------------------------------------------------------------
# SVG building
# --------------------------------------------------------------------------
BASE_CSS = """
@keyframes up{from{opacity:0;transform:translateY(12px)}}
@keyframes fade{from{opacity:0}}
@keyframes pop{from{opacity:0;transform:scale(.2)}}
@keyframes growY{from{transform:scaleY(0)}}
@keyframes growX{from{transform:scaleX(0)}}
@keyframes slide{from{opacity:0;transform:translateX(-14px)}}
@keyframes nudge{from{opacity:0;transform:translate(-8px,8px)}}
.a{animation-duration:.8s;animation-timing-function:cubic-bezier(.2,.8,.2,1);animation-fill-mode:both}
.up{animation-name:up}.fade{animation-name:fade}.pop{animation-name:pop}
.growY{animation-name:growY}.growX{animation-name:growX}.slide{animation-name:slide}.nudge{animation-name:nudge}
.ob{transform-box:fill-box;transform-origin:50% 100%}
.ol{transform-box:fill-box;transform-origin:0 50%}
.oc{transform-box:fill-box;transform-origin:50% 50%}
.nosmil{display:none}
@media (prefers-reduced-motion:reduce){
*{animation:none!important}
.smil{display:none}.nosmil{display:inline}
}
"""


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def anim(kind: str, delay: float, extra_cls: str = "", dur: float | None = None) -> str:
    """class/style attributes for a CSS-keyframe entrance. Empty in STATIC mode."""
    if STATIC:
        return f'class="{extra_cls}"' if extra_cls else ""
    style = f"animation-delay:{delay:.2f}s" + (f";animation-duration:{dur}s" if dur else "")
    cls = f"a {kind} {extra_cls}".strip()
    return f'class="{cls}" style="{style}"'


def smil(motion: str, still: str) -> str:
    """SMIL can't be switched off by CSS, so pair it with a still twin that the
    reduced-motion media query swaps in. STATIC mode emits only the twin."""
    if STATIC:
        return still
    return f'<g class="smil">{motion}</g><g class="nosmil">{still}</g>'


class Card:
    def __init__(self, name: str, w: float, h: float, title: str, desc: str):
        self.name, self.w, self.h, self.title, self.desc = name, w, h, title, desc
        self.parts: list[str] = []
        self.defs: list[str] = []
        self.css: list[str] = []
        self.chars: dict[str, set[str]] = defaultdict(set)

    def add(self, *s: str):
        self.parts.extend(s)

    def text(self, x, y, s, font, size, fill, *, anchor="start", a="", tracking=0, weight_attr=""):
        self.chars[font].update(s)
        ls = f' letter-spacing="{tracking * size:.2f}"' if tracking else ""
        cls = f"f-{font}"
        m = re.search(r'class="([^"]*)"', a)
        if m:  # merge the animation classes into the font class
            cls += " " + m.group(1)
            a = a.replace(m.group(0), "")
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" font-size="{size:g}" fill="{fill}"'
                 f' text-anchor="{anchor}"{ls} {a}>{esc(s)}</text>')

    def frame(self, glow: tuple[float, float] | None = None, glow_color: str | None = None):
        r = THEME["radius"]
        if glow:
            self.defs.append(
                f'<radialGradient id="glow" cx="{glow[0]}" cy="{glow[1]}" r="0.75">'
                f'<stop offset="0" stop-color="{glow_color or THEME["brand"]}" stop-opacity=".20"/>'
                f'<stop offset="1" stop-color="{glow_color or THEME["brand"]}" stop-opacity="0"/>'
                f'</radialGradient>')
        self.add(f'<rect x=".5" y=".5" width="{self.w - 1}" height="{self.h - 1}" rx="{r}" '
                 f'fill="{THEME["surface"]}" stroke="{THEME["border"]}"/>')
        if glow:
            self.add(f'<rect x=".5" y=".5" width="{self.w - 1}" height="{self.h - 1}" rx="{r}" fill="url(#glow)"/>')

    def svg(self) -> str:
        faces = []
        for key, chars in sorted(self.chars.items()):
            faces.append(f"@font-face{{font-family:'c-{key}';src:url({woff_data_uri(key, chars)}) format('woff');"
                         f"font-weight:{FACE[key].weight};font-display:block}}"
                         f".f-{key}{{font-family:'c-{key}',{FALLBACK[key]};font-weight:{FACE[key].weight}}}")
        css = "\n".join(faces) + ("" if STATIC else BASE_CSS) + "\n".join(self.css)
        if STATIC:
            css += "\n.nosmil{display:inline}"
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w:g}" height="{self.h:g}" '
                f'viewBox="0 0 {self.w:g} {self.h:g}" role="img" aria-labelledby="t d">'
                f'<title id="t">{esc(self.title)}</title><desc id="d">{esc(self.desc)}</desc>'
                f'<style>{css}</style><defs>{"".join(self.defs)}</defs>'
                + "\n".join(self.parts) + "</svg>\n")

    def save(self):
        (OUT / f"{self.name}.svg").write_text(self.svg(), encoding="utf-8")
        print(f"  cards/{self.name}.svg  {len(self.svg()) / 1024:.1f} KB")


# Small icons (24-unit paths, drawn by hand so no icon font is needed)
ICON_STAR = "M12 2.5l2.9 6 6.6.9-4.8 4.6 1.2 6.5L12 17.4l-5.9 3.1 1.2-6.5L2.5 9.4l6.6-.9z"
ICON_FORK = "M7 7v1.5a3 3 0 0 0 3 3h4a3 3 0 0 0 3-3V7M12 11.5V17M7 3a2 2 0 1 1 0 4 2 2 0 0 1 0-4zM17 3a2 2 0 1 1 0 4 2 2 0 0 1 0-4zM12 17a2 2 0 1 1 0 4 2 2 0 0 1 0-4z"
ICON_BOOK = ("M5 3.5h11.5A2.5 2.5 0 0 1 19 6v14.5H7A2.5 2.5 0 0 1 4.5 18V4a.5.5 0 0 1 .5-.5zm1.5 14.5c0 .6.4 1 1 1"
             "H17v-2H7.5c-.6 0-1 .4-1 1z")


def icon(path, x, y, size, fill, stroke=False):
    paint = (f'fill="none" stroke="{fill}" stroke-width="2" stroke-linecap="round"' if stroke
             else f'fill="{fill}"')
    return f'<g transform="translate({x:.1f} {y:.1f}) scale({size / 24:.4f})"><path d="{path}" {paint}/></g>'


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
def load_data() -> dict:
    path = Path(os.environ["DATA"]) if os.environ.get("DATA") else ROOT / "data" / "github.json"
    if not path.exists():
        if os.environ.get("CI"):
            sys.exit(f"{path} is missing; run scripts/fetch_github_data.py first")
        path = ROOT / "data" / "sample.json"
        print(f"! data/github.json not found, rendering with {path.name} (placeholder data)")
    return json.loads(path.read_text())


def stats(data: dict) -> dict:
    days = sorted((dt.date.fromisoformat(d["date"]), d["count"]) for d in data["calendar"]["days"])
    counts = [c for _, c in days]
    # Current streak: today's count may still be 0 because the day isn't over.
    i = len(days) - 1
    if i >= 0 and counts[i] == 0:
        i -= 1
    cur = 0
    while i >= 0 and counts[i] > 0:
        cur, i = cur + 1, i - 1
    best = run = 0
    best_end = None
    for d, c in days:
        run = run + 1 if c else 0
        if run > best:
            best, best_end = run, d
    busiest = max(days, key=lambda dc: (dc[1], dc[0])) if days else (None, 0)
    last = days[-1][0]
    months = []
    y, m = last.year, last.month
    for _ in range(12):
        months.append((y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    months.reverse()
    per_month = defaultdict(int)
    for d, c in days:
        per_month[(d.year, d.month)] += c
    return {
        "days": days, "total": data["calendar"]["total"], "current": cur, "longest": best,
        "longest_end": best_end, "active": sum(1 for c in counts if c), "span": len(days),
        "busiest": busiest, "months": [(ym, per_month[ym]) for ym in months], "last": last,
    }


def featured_repos(data: dict) -> list[dict]:
    repos = {r["name"]: r for r in data.get("repos", [])
             if r["name"].lower() != PROFILE["login"].lower()}
    names = PROFILE["featured"] or [n for n in data.get("pinned", []) if n in repos]
    if not names:
        ranked = sorted((r for r in repos.values() if not r["is_fork"]),
                        key=lambda r: (r["stars"], r["pushed_at"] or ""), reverse=True)
        names = [r["name"] for r in ranked]
    return [repos[n] for n in names if n in repos][: PROFILE["featured_max"]]


def plural(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"


# --------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------
def hero() -> Card:
    t = THEME
    portrait = ROOT / PROFILE["portrait"] if PROFILE.get("portrait") else None
    text_w = W - 44 - 270
    name_size = fit(PROFILE["name"], "display", 50, text_w)
    # one line if it fits at 17px or more, otherwise wrap at 19px
    tag_size = fit(PROFILE["tagline"], "body", 19, text_w, min_size=17)
    tag_lines = ([PROFILE["tagline"]] if FACE["body"].width(PROFILE["tagline"], tag_size) <= text_w
                 else wrap(PROFILE["tagline"], "body", tag_size := 19, text_w, 2))
    h = 242 + 26 * (len(tag_lines) - 1)
    c = Card("hero", W, h, PROFILE["name"], PROFILE["tagline"])
    c.frame(glow=(0.92, 0.1))
    x, d0 = 44, T_HERO + 0.1
    c.text(x, 62, PROFILE["kicker"], "mono", 13, t["brand"], tracking=0.2, a=anim("slide", d0))
    c.text(x, 116, PROFILE["name"], "display", name_size, t["text"], a=anim("up", d0 + 0.12))
    # SMIL underline that draws itself, with a still twin
    uw = min(FACE["display"].width(PROFILE["name"], name_size), text_w) * 0.42
    c.defs.append(f'<linearGradient id="ul" x1="0" x2="1"><stop offset="0" stop-color="{t["brand"]}"/>'
                  f'<stop offset="1" stop-color="{t["accents"][1]}"/></linearGradient>')
    bar = f'<rect x="{x}" y="132" height="4" rx="2" fill="url(#ul)"'
    c.add(smil(f'{bar} width="0"><animate attributeName="width" from="0" to="{uw:.1f}" begin="{d0 + .45:.2f}s" '
               f'dur=".9s" fill="freeze" calcMode="spline" keyTimes="0;1" keySplines=".2 .8 .2 1"/></rect>',
               f'{bar} width="{uw:.1f}"/>'))
    y = 172
    for i, line in enumerate(tag_lines):
        c.text(x, y, line, "body", tag_size, t["muted"], a=anim("up", d0 + 0.3 + i * 0.08))
        y += 26
    # chips
    cx, cy = x, y + 18
    for i, chip in enumerate(PROFILE["chips"]):
        cw = FACE["mono"].width(chip, 12) + 22
        if cx + cw > x + text_w + 40:
            break
        c.add(f'<g {anim("pop", d0 + 0.55 + i * 0.07, "oc")}>'
              f'<rect x="{cx:.1f}" y="{cy - 17}" width="{cw:.1f}" height="26" rx="13" fill="{t["raised"]}" '
              f'stroke="{t["border"]}"/>')
        c.text(cx + cw / 2, cy, chip, "mono", 12, t["text"], anchor="middle")
        c.add("</g>")
        cx += cw + 8
    # portrait or monogram, with a ring that draws itself (SMIL)
    px, py, pr = W - 140, h / 2, 78
    circ = 2 * 3.14159 * (pr + 12)
    ring = (f'<circle cx="{px}" cy="{py}" r="{pr + 12}" fill="none" stroke="url(#ul)" stroke-width="2.5" '
            f'stroke-linecap="round" stroke-dasharray="{circ:.1f}" transform="rotate(-90 {px} {py})"')
    c.add(smil(f'{ring} stroke-dashoffset="{circ:.1f}"><animate attributeName="stroke-dashoffset" '
               f'from="{circ:.1f}" to="0" begin="{d0 + .3:.2f}s" dur="1.4s" fill="freeze" calcMode="spline" '
               f'keyTimes="0;1" keySplines=".3 .7 .2 1"/></circle>', f"{ring}/>"))
    c.add(f'<g {anim("pop", d0 + 0.2, "oc", dur=1.0)}>')
    if portrait and portrait.exists():
        from PIL import Image  # only needed with a portrait
        im = Image.open(portrait).convert("RGB")
        s = min(im.size)
        im = im.crop(((im.width - s) // 2, (im.height - s) // 2, (im.width + s) // 2, (im.height + s) // 2))
        im = im.resize((pr * 4, pr * 4), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82, optimize=True)
        c.defs.append(f'<clipPath id="pc"><circle cx="{px}" cy="{py}" r="{pr}"/></clipPath>')
        c.add(f'<image href="data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}" '
              f'x="{px - pr}" y="{py - pr}" width="{2 * pr}" height="{2 * pr}" clip-path="url(#pc)"/>')
    else:
        c.defs.append(f'<linearGradient id="mg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{t["raised"]}"/>'
                      f'<stop offset="1" stop-color="{t["surface"]}"/></linearGradient>')
        c.add(f'<circle cx="{px}" cy="{py}" r="{pr}" fill="url(#mg)" stroke="{t["border"]}"/>')
        initials = "".join(p[0] for p in PROFILE["name"].split()[:2]).upper()
        c.text(px, py + 22, initials, "display", 60, t["brand"], anchor="middle")
    c.add("</g>")
    return c


def tile(i: int, spec: dict) -> Card:
    t = THEME
    accent = t["accents"][i % len(t["accents"])]
    w, h = 270, 132
    c = Card(f"tile-{i + 1}", w, h, f'{spec["kicker"].title()}: {spec["sub"]}', spec["title"])
    c.frame(glow=(1.0, 0.0), glow_color=accent)
    d = T_TILES + i * 0.15
    c.text(22, 36, spec["kicker"], "mono", 11, accent, tracking=0.2, a=anim("fade", d))
    c.add(f'<g {anim("nudge", d + 0.2)}><path d="M{w - 38} 38 L{w - 24} 24 M{w - 34} 24 H{w - 24} V34" '
          f'fill="none" stroke="{accent}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></g>')
    c.text(22, 78, spec["title"], "display", fit(spec["title"], "display", 24, w - 44), t["text"],
           a=anim("up", d + 0.08))
    c.text(22, 104, spec["sub"], "mono", fit(spec["sub"], "mono", 12.5, w - 44), t["muted"],
           a=anim("up", d + 0.16))
    c.add(f'<rect x="22" y="{h - 14}" width="{w - 44}" height="2" rx="1" fill="{accent}" opacity=".55" '
          f'{anim("growX", d + 0.3, "ol", dur=1.0)}/>')
    return c


def chart(s: dict) -> Card:
    t = THEME
    h = 370
    c = Card("activity", W, h, "Contributions per month",
             f'{s["total"]} contributions in the last year; current streak {s["current"]} days')
    c.frame(glow=(0.0, 0.0))
    d, pad = T_CHART, 32
    c.text(pad, 44, "CONTRIBUTIONS · LAST 12 MONTHS", "mono", 12, t["muted"], tracking=0.16, a=anim("fade", d))
    c.text(W - pad, 46, f'{s["total"]:,}', "display", 30, t["brand"], anchor="end", a=anim("fade", d + 0.1))
    # stat boxes
    bd, bc = s["busiest"]
    longest_sub = f'best {plural(s["longest"], "day")}'
    pct = round(100 * s["active"] / s["span"]) if s["span"] else 0
    boxes = [
        ("CURRENT STREAK", plural(s["current"], "day"), longest_sub),
        ("LONGEST STREAK", plural(s["longest"], "day"),
         f'ended {s["longest_end"]:%b %-d}' if s["longest_end"] else "—"),
        ("ACTIVE DAYS", f'{s["active"]:,}', f'{pct}% of the year'),
        ("BUSIEST DAY", f"{bc:,}" if bc else "—", f"contributions · {bd:%b %-d}" if bc else "—"),
    ]
    gap, by, bh = 12, 66, 84
    bw = (W - 2 * pad - 3 * gap) / 4
    for i, (label, value, sub) in enumerate(boxes):
        bx = pad + i * (bw + gap)
        c.add(f'<g {anim("up", d + 0.15 + i * 0.08)}>'
              f'<rect x="{bx:.1f}" y="{by}" width="{bw:.1f}" height="{bh}" rx="10" fill="{t["raised"]}"/>')
        c.text(bx + 16, by + 24, label, "mono", 10.5, t["muted"], tracking=0.12)
        c.text(bx + 16, by + 54, value, "display", fit(value, "display", 24, bw - 32), t["text"])
        c.text(bx + 16, by + 72, sub, "mono", 11, t["faint"])
        c.add("</g>")
    # monthly bars
    top, base = 190, h - 50
    months = s["months"]
    mx = max((v for _, v in months), default=0) or 1
    slot = (W - 2 * pad) / len(months)
    bar_w = slot * 0.56
    peak = max(range(len(months)), key=lambda k: months[k][1])
    for k in range(4):  # faint guides
        gy = base - (base - top) * k / 3
        dash = "" if k == 0 else 'stroke-dasharray="2 4"'
        c.add(f'<line x1="{pad}" x2="{W - pad}" y1="{gy:.1f}" y2="{gy:.1f}" stroke="{t["border"]}" '
              f'stroke-width="1" {dash}/>')
    for k, ((yy, mm), v) in enumerate(months):
        x = pad + k * slot + (slot - bar_w) / 2
        bh_ = max((base - top - 22) * v / mx, 2 if v else 0)
        dk = d + 0.5 + k * 0.06
        fill = t["brand"]
        op = "1" if k == peak else ".5"
        if bh_:
            c.add(f'<rect x="{x:.1f}" y="{base - bh_:.1f}" width="{bar_w:.1f}" height="{bh_:.1f}" rx="4" '
                  f'fill="{fill}" opacity="{op}" {anim("growY", dk, "ob", dur=0.9)}/>')
        c.text(x + bar_w / 2, base - bh_ - 8, str(v), "monob" if k == peak else "mono", 11,
               t["text"] if k == peak else t["muted"], anchor="middle", a=anim("fade", dk + 0.5))
        label = dt.date(yy, mm, 1).strftime("%b")
        c.text(x + bar_w / 2, base + 20, label, "mono", 11, t["faint"], anchor="middle", a=anim("fade", dk))
    # SMIL: three soft pulses over the peak month
    if months[peak][1]:
        px = pad + peak * slot + slot / 2
        py = base - (base - top - 22) - 30
        c.add(smil(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3" fill="none" stroke="{t["brand"]}" stroke-width="1.5" '
                   f'opacity="0"><animate attributeName="r" values="3;12" begin="{d + 1.6:.2f}s" dur="1.2s" '
                   f'repeatCount="3"/><animate attributeName="opacity" values=".9;0" begin="{d + 1.6:.2f}s" '
                   f'dur="1.2s" repeatCount="3"/></circle>', ""))
    return c


def repo_card(i: int, r: dict) -> Card:
    t = THEME
    pad = 28
    desc = r.get("description") or "No description yet."
    lines = wrap(desc, "body", 15, W - 2 * pad, 2)
    h = 116 + 21 * (len(lines) - 1)
    c = Card(f"repo-{i + 1}", W, h, r["name"], desc)
    c.frame(glow=(1.0, 0.0))
    d = T_REPOS + i * 0.2
    c.add(f'<g {anim("fade", d)}>{icon(ICON_BOOK, pad, 22, 20, t["brand"])}</g>')
    c.text(pad + 30, 39, r["name"], "display", fit(r["name"], "display", 20, W - 2 * pad - 200), t["text"],
           a=anim("slide", d + 0.05))
    # stars / forks, right-aligned
    stars, forks = f'{r["stars"]:,}', f'{r["forks"]:,}'
    fx = W - pad - FACE["mono"].width(forks, 13)
    sx = fx - 26 - FACE["mono"].width(stars, 13) - 22
    c.add(f'<g {anim("fade", d + 0.15)}>{icon(ICON_STAR, sx, 25, 15, t["accents"][2])}'
          f'{icon(ICON_FORK, fx - 20, 25, 15, t["muted"], stroke=True)}')
    c.text(sx + 20, 37, stars, "mono", 13, t["text"])
    c.text(fx, 37, forks, "mono", 13, t["text"])
    c.add("</g>")
    y = 68
    for j, line in enumerate(lines):
        c.text(pad, y, line, "body", 15, t["muted"], a=anim("up", d + 0.12 + j * 0.06))
        y += 21
    fy = h - 22
    lang = r.get("language")
    if lang:
        c.add(f'<circle cx="{pad + 5}" cy="{fy - 4}" r="5" fill="{r.get("language_color") or t["brand"]}" '
              f'{anim("pop", d + 0.3, "oc")}/>')
        c.text(pad + 16, fy, lang, "mono", 12, t["text"], a=anim("fade", d + 0.3))
    if r.get("pushed_at"):
        pushed = dt.datetime.fromisoformat(r["pushed_at"].replace("Z", "+00:00"))
        c.text(W - pad, fy, f"updated {pushed:%b %Y}", "mono", 12, t["faint"], anchor="end",
               a=anim("fade", d + 0.35))
    return c


def graph(s: dict) -> Card:
    t = THEME
    days = s["days"]
    pad, label_w = 28, 30
    first = days[0][0]
    start = first - dt.timedelta(days=(first.weekday() + 1) % 7)  # Sunday on/before
    weeks = (days[-1][0] - start).days // 7 + 1
    step = (W - 2 * pad - label_w) / weeks
    cell = step * 0.78
    gx, gy = pad + label_w, 82
    h = int(gy + 7 * step + 58)
    c = Card("graph", W, h, "Contribution graph", f'{s["total"]} contributions in the last year')
    c.frame()
    d = T_GRAPH
    c.text(pad, 44, "CONTRIBUTION GRAPH", "mono", 12, t["muted"], tracking=0.16, a=anim("fade", d))
    c.text(W - pad, 44, f'{plural(s["total"], "contribution")} in the last year', "mono", 12, t["text"],
           anchor="end", a=anim("fade", d + 0.05))
    # levels: GitHub-style quartiles of non-zero days
    nz = sorted(cnt for _, cnt in days if cnt)
    q = [nz[int(len(nz) * f)] for f in (0.25, 0.5, 0.75)] if nz else [1, 2, 3]
    shades = [t["raised"]] + [mix(t["surface"], t["brand"], a) for a in (0.32, 0.55, 0.78, 1.0)]

    def level(n):
        return 0 if n == 0 else 1 + sum(n > x for x in q)

    if not STATIC:
        # shorthand first, per-week delays after it (the shorthand would reset them)
        c.css.append(".cell{animation:pop .5s cubic-bezier(.2,.8,.2,1) both;transform-box:fill-box;transform-origin:center}")
        c.css.append("".join(f".w{k}{{animation-delay:{d + 0.2 + k * 0.022:.3f}s}}" for k in range(weeks)))
        c.css.append("@media (prefers-reduced-motion:reduce){.cell{animation:none!important}}")
    seen_month = None
    for k in range(weeks):
        wk_start = start + dt.timedelta(weeks=k)
        if wk_start.month != seen_month and wk_start.day <= 7 and k < weeks - 1:
            seen_month = wk_start.month
            c.text(gx + k * step, gy - 10, wk_start.strftime("%b"), "mono", 10.5, t["faint"],
                   a=anim("fade", d + 0.2 + k * 0.022))
    for row, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.text(pad, gy + row * step + cell * 0.8, name, "mono", 10, t["faint"], a=anim("fade", d + 0.15))
    cells = []
    by_date = dict(days)
    for k in range(weeks):
        for row in range(7):
            day = start + dt.timedelta(days=k * 7 + row)
            if day not in by_date:
                continue
            n = by_date[day]
            cls = "" if STATIC else f' class="cell w{k}"'
            cells.append(f'<rect x="{gx + k * step:.1f}" y="{gy + row * step:.1f}" width="{cell:.1f}" '
                         f'height="{cell:.1f}" rx="2.5" fill="{shades[level(n)]}"{cls}>'
                         f'<title>{n} on {day:%b %-d}</title></rect>')
    c.add(*cells)
    # SMIL ring on the busiest day
    bd, bc = s["busiest"]
    if bc:
        k, row = (bd - start).days // 7, (bd - start).days % 7
        cx_, cy_ = gx + k * step + cell / 2, gy + row * step + cell / 2
        begin = d + 0.2 + weeks * 0.022 + 0.3
        c.add(smil(f'<circle cx="{cx_:.1f}" cy="{cy_:.1f}" r="{cell / 2:.1f}" fill="none" stroke="{t["text"]}" '
                   f'stroke-width="1.5" opacity="0"><animate attributeName="r" values="{cell / 2:.1f};{cell * 1.6:.1f}" '
                   f'begin="{begin:.2f}s" dur="1.3s" repeatCount="2"/><animate attributeName="opacity" values="1;0" '
                   f'begin="{begin:.2f}s" dur="1.3s" repeatCount="2"/></circle>', ""))
    # legend
    ly = h - 26
    lx = W - pad - 5 * (cell + 4) - FACE["mono"].width("More", 10.5) - 8
    c.add(f'<g {anim("fade", d + 1.2)}>')
    c.text(lx - 8, ly + cell - 2, "Less", "mono", 10.5, t["faint"], anchor="end")
    for j, sh in enumerate(shades):
        c.add(f'<rect x="{lx + j * (cell + 4):.1f}" y="{ly}" width="{cell:.1f}" height="{cell:.1f}" rx="2.5" fill="{sh}"/>')
    c.text(lx + 5 * (cell + 4) + 4, ly + cell - 2, "More", "mono", 10.5, t["faint"])
    c.add("</g>")
    c.text(pad, ly + cell - 2, f'Updated {s["last"]:%b %-d, %Y}', "mono", 10.5, t["faint"],
           a=anim("fade", d + 1.2))
    return c


def mix(a: str, b: str, f: float) -> str:
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(pa, pb))


# --------------------------------------------------------------------------
def update_readme(repos: list[dict]):
    """Keep the README's repo block in sync with however many repo cards exist."""
    readme = ROOT / "README.md"
    if not readme.exists():
        return
    body = "\n\n".join(
        f'<a href="{esc(r["url"])}"><img src="cards/repo-{i + 1}.svg" width="100%" alt="{esc(r["name"])}: '
        f'{esc(r.get("description") or "repository")}"></a>' for i, r in enumerate(repos))
    src = readme.read_text()
    new = re.sub(r"(<!-- cards:repos:start -->\n).*?(<!-- cards:repos:end -->)",
                 lambda m: m.group(1) + (body + "\n" if body else "") + m.group(2), src, flags=re.S)
    if new != src:
        readme.write_text(new)
        print("  README.md repo block updated")


def main():
    OUT.mkdir(exist_ok=True)
    data = load_data()
    s = stats(data)
    repos = featured_repos(data)
    print(f"Rendering {'STATIC' if STATIC else 'animated'} cards")
    for old in OUT.glob("repo-*.svg"):
        old.unlink()
    cards = [hero(), *[tile(i, sp) for i, sp in enumerate(PROFILE["tiles"])], chart(s),
             *[repo_card(i, r) for i, r in enumerate(repos)], graph(s)]
    for card in cards:
        card.save()
    update_readme(repos)


if __name__ == "__main__":
    main()
