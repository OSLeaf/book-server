"""Render an SVG cover to a PNG with headless Chromium (Playwright)."""

import re
from pathlib import Path

from playwright.sync_api import sync_playwright

# Every cover clips its artwork to one rectangle; that rectangle is the cover.
CLIP_RECT = re.compile(
    r'<clipPath[^>]*>\s*<rect\s+x="([-\d.]+)"\s+y="([-\d.]+)"'
    r'\s+width="([\d.]+)"\s+height="([\d.]+)"'
)


def _prepare(svg_text, width):
    m = CLIP_RECT.search(svg_text)
    if m:
        x, y, vw, vh = (float(v) for v in m.groups())
        box = f"{x:g} {y:g} {vw:g} {vh:g}"
        if re.search(r'viewBox="[^"]*"', svg_text):
            svg_text = re.sub(r'viewBox="[^"]*"', f'viewBox="{box}"', svg_text, count=1)
        else:
            svg_text = svg_text.replace("<svg", f'<svg viewBox="{box}"', 1)
    vb = re.search(r'viewBox="([^"]*)"', svg_text)
    if not vb:
        raise ValueError("The SVG has no viewBox and no cover rectangle")
    _, _, vw, vh = (float(v) for v in vb.group(1).split())
    height = round(width * vh / vw)
    root = re.search(r"<svg[^>]*>", svg_text).group(0)
    new_root = re.sub(r'\s(?:width|height)="[^"]*"', "", root)
    new_root = new_root.replace("<svg", f'<svg width="{width}" height="{height}"', 1)
    return svg_text.replace(root, new_root, 1), width, height


def svg_to_png(svg_path, out_path, width=1800):
    """Render the SVG at the given pixel width; height follows its aspect ratio."""
    svg, w, h = _prepare(Path(svg_path).read_text(encoding="utf-8"), width)
    html = f'<!doctype html><html><body style="margin:0">{svg}</body></html>'
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": w, "height": h})
            page.set_content(html)
            page.screenshot(
                path=str(out_path), clip={"x": 0, "y": 0, "width": w, "height": h}
            )
        finally:
            browser.close()
    return Path(out_path)
