#!/usr/bin/env python3
"""
Refero Styles → Velox match ranker.

Fetches every style from styles.refero.design's public API and scores each
against the Velox design DNA encoded below (extracted from
.interface-design/03-tokens-applied.md).

Outputs:
  - refero_ranked.md       — full ranked report (markdown)
  - refero_ranked.json     — raw scores for inspection
  - refero_design_md/      — DESIGN.md files for the top-N matches

Usage:
  pip install requests
  python refero_velox_match.py
  # optional flags:
  python refero_velox_match.py --top 8 --max-pages 5

This is a comparator. It does NOT recommend you change colors. The scoring
explicitly penalises any reference that would push Velox away from its
crimson + warm-pink identity. What it rewards is references that share
Velox's STRUCTURAL DNA — restraint, single saturated accent, warm light
backgrounds, system fonts, border-over-shadow, editorial typography rhythm.
"""

from __future__ import annotations

import argparse
import colorsys
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("Missing dependency: pip install requests", file=sys.stderr)
    sys.exit(1)


# ----------------------------------------------------------------------------
# Velox design DNA — derived from .interface-design/03-tokens-applied.md
# ----------------------------------------------------------------------------

VELOX_DNA = {
    # Hard rules — references that violate these get heavily penalised
    "theme": "light",                          # Velox is light, never dark
    "primary_accent_hue_range": (340, 20),     # crimson family (wraps through 0)
    "background_warmth": "warm",               # #FFF0F4 — pink-warm
    "surface_panel_warmth": "warm",            # #F9F8F6 — warm off-white
    "max_saturated_accents": 1,                # one accent only; >1 is over-rich
    "prefer_system_fonts": True,               # no webfont dependencies
    "elevation_strategy": "border-first",      # hairlines over ambient shadows
    "density": "comfortable",                  # not "compact", not "spacious"
    "max_radius_px": 12,                       # nothing bigger than rounded-lg

    # Soft preferences — bonuses for references that share these
    "preferred_mood_keywords": [
        "editorial", "precision", "blueprint", "architectural", "restrained",
        "minimal", "warm", "canvas", "command center", "command-center",
        "considered", "quiet", "clarity", "crisp", "clean", "subtle",
    ],
    "penalised_mood_keywords": [
        "playful", "bouncy", "vibrant", "neon", "psychedelic", "maximalist",
        "brutalist", "y2k", "retro", "grunge", "noisy", "energetic", "loud",
    ],

    # What we want to LEARN from references
    "things_we_want_better": [
        "typography weight strategy (lighter weights for display)",
        "letter-spacing curve across the type scale",
        "modular type ratio (so sizes are intentional, not Tailwind defaults)",
        "OpenType features (tnum for tabular data)",
        "spacing scale granularity",
        "shadow vocabulary (soft ambient, not heavy drop)",
        "data-display rhythm (stat cards, tables, numerical hierarchy)",
        "empty-state and section-heading patterns",
        "single-accent discipline (where to use accent, where not to)",
    ],
}

# Velox's actual current values, for the report
VELOX_TOKENS = {
    "background": "#FFF0F4",
    "surface_panel": "#F9F8F6",
    "card": "#FFFFFF",
    "primary": "#D31528",
    "ink_body": "#3B3B3B",
    "ink_muted": "#727272",
    "border": "#E2E8F0",
    "border_zone": "#EDEAE5",
    "primary_font": "Helvetica Neue (+ system fallback)",
    "mono_font": "system monospace",
    "h1_weight": 700,
    "h1_tracking": "-0.01em",
    "card_radius": "8px (rounded-lg)",
    "button_radius": "6px (rounded-md)",
}


# ----------------------------------------------------------------------------
# Refero API client
# ----------------------------------------------------------------------------

API_BASE = "https://styles.refero.design/api"
SITE_BASE = "https://styles.refero.design"
TIMEOUT = 30
THROTTLE_SEC = 0.4  # be polite — small library, public endpoint


def fetch_json(url: str) -> dict:
    """GET with a simple retry."""
    for attempt in (1, 2, 3):
        try:
            r = requests.get(
                url,
                timeout=TIMEOUT,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "velox-refero-match/1.0 (research)",
                },
            )
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == 3:
                raise
            print(f"  retry {attempt} after {e}", file=sys.stderr)
            time.sleep(1.5 * attempt)
    return {}


def list_all_styles(max_pages: int) -> list[dict]:
    """Walk the paginated /styles endpoint."""
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        url = f"{API_BASE}/styles?page={page}"
        print(f"[list] page {page}: {url}")
        data = fetch_json(url)
        styles = data.get("styles", [])
        if not styles:
            print(f"[list] page {page} empty, stopping")
            break
        out.extend(styles)
        if data.get("nextPage") is None:
            print(f"[list] no next page, stopping")
            break
        time.sleep(THROTTLE_SEC)
    print(f"[list] total: {len(out)} styles")
    return out


def fetch_style_detail(style_id: str) -> dict:
    """GET /styles/{id}."""
    return fetch_json(f"{API_BASE}/styles/{style_id}")


# ----------------------------------------------------------------------------
# Color analysis helpers
# ----------------------------------------------------------------------------

def hex_to_hsv(hex_str: str) -> tuple[float, float, float] | None:
    """Convert #RRGGBB to HSV with H in degrees, S/V in 0-1."""
    h = hex_str.strip().lstrip("#")
    if len(h) != 6:
        return None
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None
    hue, sat, val = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return (hue * 360, sat, val)


def is_warm_light(hex_str: str) -> bool:
    """Light surface (V > 0.92) with a warm bias (hue 0-60 or 340-360)."""
    hsv = hex_to_hsv(hex_str)
    if not hsv:
        return False
    h, s, v = hsv
    if v < 0.92:
        return False
    if s < 0.005:  # pure white-ish — neutral, not warm
        return False
    return h < 60 or h > 340


def is_saturated_accent(hex_str: str) -> bool:
    """Saturated color suitable for primary accent — not a neutral."""
    hsv = hex_to_hsv(hex_str)
    if not hsv:
        return False
    h, s, v = hsv
    # V upper bound is loose: a fully-saturated colour can have V near 1
    # (e.g. Stripe's #533afd has V ≈ 0.99 because its blue channel is 253).
    # The lower bound excludes near-black; the upper excludes pure white.
    return s > 0.35 and 0.18 < v < 0.995


def is_dark_surface(hex_str: str) -> bool:
    hsv = hex_to_hsv(hex_str)
    if not hsv:
        return False
    _, _, v = hsv
    return v < 0.25


# ----------------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------------

@dataclass
class ScoreBreakdown:
    name: str
    delta: int
    detail: str


@dataclass
class StyleScore:
    style_id: str
    site_name: str
    north_star: str
    theme: str
    score: int = 0
    breakdown: list[ScoreBreakdown] = field(default_factory=list)
    learnings: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    url: str = ""

    def add(self, name: str, delta: int, detail: str) -> None:
        self.score += delta
        self.breakdown.append(ScoreBreakdown(name, delta, detail))


def score_style(summary: dict, detail: dict) -> StyleScore:
    """
    Score a single style against Velox's DNA. Returns a StyleScore with a
    numeric score (higher is better fit) plus learnings and cautions.

    Scoring philosophy:
      +points for sharing Velox's structural DNA (light, single accent,
              warm canvas, restraint, system-font-friendly)
      -points for direct conflicts (dark theme, multi-accent palette,
              cream/parchment surfaces — forbidden in 03-tokens-applied.md)
      learnings: specific patterns this reference does well that Velox could
              borrow (typography weight, letter-spacing, spacing rhythm,
              shadow vocabulary)
      cautions: things to NOT take from this reference even if score is high
    """
    s = StyleScore(
        style_id=summary["id"],
        site_name=summary.get("siteName", "?"),
        north_star=summary.get("northStar", ""),
        theme=summary.get("colorScheme", "?"),
        url=f"{SITE_BASE}/style/{summary['id']}",
    )

    ds = (detail.get("style", {}) or detail).get("fullResult", {}).get("designSystem", {}) or {}
    if not ds:
        ds = detail.get("fullResult", {}).get("designSystem", {}) or {}

    # ─── Theme (HARD) ─────────────────────────────────────────────────────
    theme = (summary.get("colorScheme") or ds.get("theme") or "").lower()
    if theme == "dark":
        s.add("dark theme", -60,
              "Velox is light-only; the patterns won't transfer without "
              "fundamental colour inversion. Reading dark-theme styles for "
              "structural ideas (spacing, type) is still useful, just not "
              "for surface/elevation logic.")
    elif theme == "light":
        s.add("light theme", +20, "Direct fit with Velox's light surface stack.")

    # ─── Background warmth ────────────────────────────────────────────────
    surfaces = ds.get("surfaces") or []
    colors = summary.get("colors") or ds.get("colors") or []

    bg_hex = None
    # Try surfaces first, then fall back to first colour with role mentioning bg
    for sf in surfaces:
        col = sf.get("color")
        if col and not is_dark_surface(col):
            bg_hex = col
            break
    if not bg_hex:
        for c in colors:
            hx = c.get("hex") if isinstance(c, dict) else None
            if hx and not is_dark_surface(hx) and hex_to_hsv(hx) and hex_to_hsv(hx)[2] > 0.92:
                bg_hex = hx
                break

    if bg_hex:
        if is_warm_light(bg_hex):
            s.add("warm light background", +25,
                  f"Background {bg_hex} shares Velox's warm-canvas DNA "
                  f"(Velox uses {VELOX_TOKENS['background']}).")
        else:
            hsv = hex_to_hsv(bg_hex)
            if hsv and hsv[1] < 0.01:
                s.add("neutral light background", +5,
                      f"Background {bg_hex} is neutral white. Transferable "
                      f"but loses the warmth that Velox uses to soften "
                      f"surfaces.")
            else:
                s.add("cool/non-warm background", -5,
                      f"Background {bg_hex} is light but cool-toned. Velox's "
                      f"warm pink palette will clash with cool-blue surfaces.")

    # ─── Accent discipline (single saturated accent) ──────────────────────
    saturated = []
    for c in colors:
        hx = c.get("hex") if isinstance(c, dict) else None
        if hx and is_saturated_accent(hx):
            saturated.append((hx, c.get("name", "")))
    if len(saturated) == 1:
        s.add("single saturated accent", +25,
              f"Reference uses ONE accent ({saturated[0][1]} {saturated[0][0]}). "
              f"Direct structural match for Velox's crimson-only rule.")
    elif len(saturated) == 2:
        s.add("two saturated accents", +5,
              f"Two accents present ({', '.join(h for h, _ in saturated)}). "
              f"Borderline — manageable if the second is rarely used.")
    elif len(saturated) >= 3:
        s.add("multi-accent palette", -15,
              f"{len(saturated)} saturated accents. Velox is explicitly "
              f"single-accent; multi-accent references invite drift.")

    # ─── Type system: font familiarity ────────────────────────────────────
    fonts = summary.get("fonts") or []
    families = [f.lower() for f in fonts]
    system_friendly = any(
        kw in fam for fam in families
        for kw in ("system", "helvetica", "arial", "inter", "sans-serif")
    )
    custom_only = fonts and not system_friendly
    if system_friendly and fonts:
        s.add("system-font-compatible", +10,
              f"Fonts include {fonts[0]} — substitutable with Velox's "
              f"Helvetica Neue stack without losing identity.")
    elif custom_only:
        s.add("custom-webfont dependency", -8,
              f"Fonts: {', '.join(fonts)}. Velox forbids webfonts; you'd "
              f"have to substitute Helvetica Neue and accept some "
              f"character drift.")

    # ─── Type scale — modular ratio + weight strategy ─────────────────────
    type_scale = ds.get("typeScale") or []
    if len(type_scale) >= 6:
        s.add("rich type scale", +8,
              f"{len(type_scale)} type-scale tokens defined. Velox currently "
              f"uses Tailwind defaults; borrowing the modular ratio is a "
              f"high-leverage win.")
        # Detect light display weights — a Stripe-style signal
        weights = []
        for ts in type_scale:
            w = ts.get("weight")
            if w is not None:
                try:
                    weights.append(int(str(w).split()[0]))
                except (ValueError, AttributeError):
                    pass
        if weights and min(weights) <= 350:
            s.learnings.append(
                f"Uses font-weight ≤350 for display text "
                f"(min observed: {min(weights)}). Velox uses 700 for H1; "
                f"trying weight 300-500 with this reference's letter-spacing "
                f"is a high-leverage typographic upgrade."
            )

    # ─── Spacing system ───────────────────────────────────────────────────
    spacing = ds.get("spacing") or {}
    if spacing.get("elementGap") and spacing.get("cardPadding"):
        s.add("explicit spacing tokens", +5,
              f"Defines elementGap={spacing.get('elementGap')}, "
              f"cardPadding={spacing.get('cardPadding')}. Useful as a "
              f"reference for tightening Velox's currently-implicit spacing.")

    # ─── Border radius ────────────────────────────────────────────────────
    # Velox caps at 8px (lg). References that go to 16px+ everywhere indicate
    # a softer/playful identity that conflicts.
    radius_violations = 0
    if isinstance(spacing.get("radius"), str):
        m = re.search(r"(\d+)", spacing["radius"])
        if m and int(m.group(1)) > VELOX_DNA["max_radius_px"]:
            radius_violations += 1
    if radius_violations:
        s.add("oversized radius default", -5,
              f"Reference uses border radius beyond Velox's "
              f"{VELOX_DNA['max_radius_px']}px cap.")

    # ─── Shadow strategy ──────────────────────────────────────────────────
    elevation = ds.get("elevation") or []
    if elevation:
        # Heuristic: look for "0px 0px Xpx" style ambient shadows vs offset drops
        shadow_text = json.dumps(elevation).lower()
        if "0px 0px" in shadow_text or "0 0 " in shadow_text:
            s.add("soft ambient elevation", +8,
                  "Uses ambient (centred) shadows rather than offset drop "
                  "shadows. Aligns with Velox's restraint preference; could "
                  "inform Velox's currently-thin shadow vocabulary.")
        if "16px 16px" in shadow_text or "20px 20px" in shadow_text:
            s.add("heavy offset shadow", -5,
                  "Heavy offset shadows present — clashes with Velox's "
                  "hairline-border-first elevation model.")

    # ─── Mood keywords from north star + description ──────────────────────
    descriptive_text = " ".join([
        summary.get("northStar", ""),
        ds.get("description", "") or "",
    ]).lower()

    for kw in VELOX_DNA["preferred_mood_keywords"]:
        if kw in descriptive_text:
            s.add(f"mood: {kw}", +3,
                  f"Description mentions '{kw}', aligning with Velox's "
                  f"restrained character.")

    for kw in VELOX_DNA["penalised_mood_keywords"]:
        if kw in descriptive_text:
            s.add(f"mood: {kw}", -10,
                  f"Description mentions '{kw}', conflicts with Velox's "
                  f"restrained character.")

    # ─── Forbidden surfaces — cream/parchment (per 03-tokens-applied) ─────
    surfaces_text = json.dumps(surfaces).lower()
    if any(forbidden in surfaces_text for forbidden in ("cream", "parchment", "sepia", "linen")):
        s.cautions.append(
            "Reference uses cream/parchment/linen surfaces. Velox explicitly "
            "forbids these (03-tokens-applied.md §3). Read it for ideas, but "
            "do not import the surface colours."
        )

    # ─── Forbidden font families ──────────────────────────────────────────
    forbidden_fonts = ["spectral", "familjen grotesk", "jetbrains mono"]
    for ff in forbidden_fonts:
        if any(ff in f.lower() for f in fonts):
            s.cautions.append(
                f"Reference uses {ff}. Velox forbids this family "
                f"(03-tokens-applied.md). Substitute with Helvetica Neue."
            )

    # ─── Build the "what to learn" list ───────────────────────────────────
    if type_scale:
        s.learnings.append(
            "Borrow the letter-spacing curve across the type scale — "
            "Velox currently sets it only at H1."
        )
    if elevation:
        s.learnings.append(
            "Study the shadow definitions for Velox's modal/popover elevation "
            "(currently uses bare shadow-md)."
        )
    if spacing:
        s.learnings.append(
            "Compare this spacing scale against Velox's implicit Tailwind "
            "spacing — pin a modular ratio."
        )
    if ds.get("components"):
        comp_names = [c.get("name", "?") for c in ds["components"]]
        if any("button" in n.lower() for n in comp_names):
            s.learnings.append(
                "Look at how this reference handles primary vs secondary vs "
                "outline buttons — Velox's button hierarchy could be tightened."
            )

    return s


# ----------------------------------------------------------------------------
# Report generation
# ----------------------------------------------------------------------------

def render_md(top: list[StyleScore], all_scores: list[StyleScore]) -> str:
    lines = []
    lines.append("# Refero Styles — Match Against Velox Design DNA\n")
    lines.append(
        "Generated by `refero_velox_match.py`. Scoring is heuristic, based on "
        "the rules in `03-tokens-applied.md`. Re-read the file before "
        "actioning anything; the script is a filter, not a designer.\n"
    )
    lines.append(f"Scanned **{len(all_scores)}** styles. ")
    lines.append(f"Showing top **{len(top)}**.\n")

    lines.append("\n## Velox DNA used for scoring\n")
    lines.append("```")
    lines.append(json.dumps(VELOX_TOKENS, indent=2))
    lines.append("```\n")

    lines.append("\n## Top matches\n")
    for i, s in enumerate(top, 1):
        lines.append(f"### {i}. {s.site_name}  ·  score {s.score}\n")
        lines.append(f"> {s.north_star}\n")
        lines.append(f"- URL: {s.url}")
        lines.append(f"- Theme: {s.theme}")
        lines.append("\n**Score breakdown**\n")
        for b in s.breakdown:
            sign = "+" if b.delta > 0 else ""
            lines.append(f"- `{sign}{b.delta}` {b.name} — {b.detail}")
        if s.learnings:
            lines.append("\n**What to learn from this reference**\n")
            for l in s.learnings:
                lines.append(f"- {l}")
        if s.cautions:
            lines.append("\n**[!] Do NOT take from this reference**\n")
            for c in s.cautions:
                lines.append(f"- {c}")
        lines.append("")

    lines.append("\n## Full ranking (all styles)\n")
    lines.append("| Rank | Score | Site | Theme | North Star |")
    lines.append("|------|-------|------|-------|------------|")
    for i, s in enumerate(all_scores, 1):
        ns = s.north_star.replace("|", "\\|")[:80]
        lines.append(f"| {i} | {s.score} | {s.site_name} | {s.theme} | {ns} |")

    lines.append("")
    lines.append("\n## How to use this report\n")
    lines.append(
        "1. **Read the top 3 entries.** For each, click through to the Refero "
        "page and skim the live preview.\n"
        "2. **Download the DESIGN.md** for the top 1–2 (saved to "
        "`refero_design_md/`).\n"
        "3. **Run the design-systems session prompt** with those DESIGN.md "
        "files as context, plus Velox's `system.md` and "
        "`03-tokens-applied.md`. Ask for a *token-level diff*, not a redesign.\n"
        "4. **Anything in the 'Do NOT take' list is non-negotiable.** Velox's "
        "tokens-applied document is the source of truth — if a reference is "
        "high-scoring but uses cream surfaces, you still don't use cream."
    )
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=5,
                    help="How many top matches to download DESIGN.md for")
    ap.add_argument("--max-pages", type=int, default=5,
                    help="How many pages of /styles to walk (20 per page)")
    ap.add_argument("--out", type=str, default=".",
                    help="Output directory")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_dir = out_dir / "refero_design_md"
    md_dir.mkdir(exist_ok=True)

    # 1. List
    summaries = list_all_styles(args.max_pages)
    if not summaries:
        print("No styles returned. API shape may have changed.", file=sys.stderr)
        sys.exit(1)

    # 2. Detail + score
    scores: list[StyleScore] = []
    for i, summary in enumerate(summaries, 1):
        sid = summary["id"]
        print(f"[detail {i}/{len(summaries)}] {summary.get('siteName')} ({sid})")
        try:
            detail = fetch_style_detail(sid)
        except Exception as e:
            print(f"  detail fetch failed: {e}", file=sys.stderr)
            detail = {}
        scores.append(score_style(summary, detail))
        time.sleep(THROTTLE_SEC)

    scores.sort(key=lambda s: s.score, reverse=True)
    top = scores[: args.top]

    # 3. Download DESIGN.md for top matches by scraping the public page.
    #    The /api endpoint returns JSON; the DESIGN.md is rendered server-side
    #    on the /style/{id} page. We pull the JSON detail and synthesise.
    for s in top:
        print(f"[download] {s.site_name}")
        try:
            detail = fetch_style_detail(s.style_id)
        except Exception as e:
            print(f"  failed: {e}", file=sys.stderr)
            continue
        target = md_dir / f"{s.site_name.lower().replace(' ', '-')}-detail.json"
        target.write_text(json.dumps(detail, indent=2), encoding="utf-8")
        print(f"  saved JSON to {target}")
        # Direct DESIGN.md is on the public page. We don't HTML-scrape here —
        # the JSON detail above contains the same tokens. If you want the
        # rendered .md, visit the URL printed in the ranked report.

    # 4. Write reports — JSON FIRST so a crash on the MD write doesn't lose
    #    the scoring data. Force UTF-8 explicitly; Windows defaults to cp1252
    #    which can't encode some Unicode characters used in the report.
    (out_dir / "refero_ranked.json").write_text(
        json.dumps([asdict(s) for s in scores], indent=2),
        encoding="utf-8",
    )
    md_text = render_md(top, scores)
    (out_dir / "refero_ranked.md").write_text(md_text, encoding="utf-8")

    print(f"\n[done] {len(scores)} styles scored.")
    print(f"  → {out_dir / 'refero_ranked.md'}")
    print(f"  → {out_dir / 'refero_ranked.json'}")
    print(f"  → {md_dir}/ (JSON details for top {len(top)})")
    print(f"\nTop {len(top)}:")
    for i, s in enumerate(top, 1):
        print(f"  {i}. {s.site_name:20s} score={s.score:4d}  {s.north_star[:60]}")


if __name__ == "__main__":
    main()