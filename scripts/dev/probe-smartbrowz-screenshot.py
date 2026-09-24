#!/usr/bin/env python3
"""Does SmartBrowz's screenshot wait for JavaScript to paint? (round 21C)

`SmartBrowzPdfRenderer.preview_image` calls `smart_browz.take_screenshot(html)`
with NO options — no viewport, no delay, no wait-for-selector
(services/pdf/smartbrowz.py:243). Whether a canvas drawn by a script is in
the picture is therefore not knowable from the signature, and the whole
chart renderer depends on the answer.

So: four pages, screenshotted for real and written to disk —

  canvas    JavaScript that paints during parse (the ordinary chart library case)
  delayed   JavaScript that paints 1.5 s AFTER load (does the shot wait past load?)
  delayed5  the same, 5 s after load (where does the waiting stop?)
  svg       inline SVG, no JavaScript at all (what Ruling 7 says we will ship)

Then a HUMAN LOOKS AT THEM. A test cannot see a blank canvas (round 20L).
Latency is printed per call, because the picture rides inside a LINE webhook
and a screenshot that costs ten seconds is a different design from one that
costs three.

    OR_KEY= irrelevant; this needs the SmartBrowz credentials:
      SMARTBROWZ_CLIENT_ID= SMARTBROWZ_CLIENT_SECRET= SMARTBROWZ_REFRESH_TOKEN= \\
      CATALYST_PROJECT_ID= CATALYST_ZAID= /tmp/dv/bin/python scripts/dev/probe-smartbrowz-screenshot.py

Never prints a credential: only variable names are ever named here.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

OUT = Path("/tmp/21c-probe")

CANVAS_PAGE = """<!doctype html><html lang="th"><head><meta charset="utf-8">
<style>body{font-family:sans-serif;margin:0;padding:24px;background:#fff}
h1{font-size:18px;margin:0 0 12px}</style></head><body>
<h1>CANVAS — drawn by JavaScript</h1>
<canvas id="c" width="600" height="220"></canvas>
<script>
  const ctx = document.getElementById('c').getContext('2d');
  ctx.fillStyle = '#178a50';
  [140, 90, 200, 60].forEach((h, i) => ctx.fillRect(20 + i * 150, 220 - h, 90, h));
</script></body></html>"""

DELAYED_PAGE = """<!doctype html><html lang="th"><head><meta charset="utf-8">
<style>body{font-family:sans-serif;margin:0;padding:24px;background:#fff}
h1{font-size:18px;margin:0 0 12px}
#late{font-size:40px;color:#178a50;font-weight:700}</style></head><body>
<h1>DELAYED — the word below is written 1.5 s after load</h1>
<div id="late">NOT YET</div>
<script>
  setTimeout(function () {
    document.getElementById('late').textContent = 'LATE PAINT ARRIVED';
  }, 1500);
</script></body></html>"""

DELAYED_LONG_PAGE = DELAYED_PAGE.replace("1.5 s", "5 s").replace("}, 1500);", "}, 5000);")

SVG_PAGE = """<!doctype html><html lang="th"><head><meta charset="utf-8">
<style>body{font-family:sans-serif;margin:0;padding:24px;background:#fff}
h1{font-size:18px;margin:0 0 12px}</style></head><body>
<h1>INLINE SVG — no JavaScript</h1>
<svg width="600" height="220" viewBox="0 0 600 220" xmlns="http://www.w3.org/2000/svg">
  <rect x="20"  y="80"  width="90" height="140" fill="#178a50"/>
  <rect x="170" y="130" width="90" height="90"  fill="#178a50"/>
  <rect x="320" y="20"  width="90" height="200" fill="#178a50"/>
  <rect x="470" y="160" width="90" height="60"  fill="#178a50"/>
</svg></body></html>"""

PAGES = (
    ("canvas", CANVAS_PAGE),
    ("delayed", DELAYED_PAGE),
    ("delayed5", DELAYED_LONG_PAGE),
    ("svg", SVG_PAGE),
)


async def main() -> int:
    from chann_app.services.pdf import PdfOptions, get_renderer

    OUT.mkdir(parents=True, exist_ok=True)
    renderer = get_renderer("smartbrowz")
    verdicts = []
    for name, html in PAGES:
        started = time.monotonic()
        try:
            result = await renderer.preview_image(html, PdfOptions())
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - started
            print(f"{name}: FAILED after {elapsed:.2f}s — {type(exc).__name__}: {exc}")
            verdicts.append(False)
            continue
        elapsed = time.monotonic() - started
        content = result.content or b""
        path = OUT / f"{name}.png"
        path.write_bytes(content)
        ok = len(content) > 1024
        print(
            f"{name}: {len(content):,} bytes in {elapsed:.2f}s -> {path}"
            f"  ({'ok' if ok else 'TOO SMALL'})"
        )
        verdicts.append(ok)
    print("\nNow LOOK at all four files. If canvas.png shows four green bars, JavaScript is")
    print("awaited at least through parse; if delayed.png reads LATE PAINT ARRIVED, the shot")
    print("waits past load as well. If canvas.png shows only the heading, the chart renderer")
    print("must be inline SVG with no JavaScript — which is what the design assumes. Write the")
    print("answer, and the byte counts, into docs/SESSION_HANDOFF.md.")
    return 0 if all(verdicts) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
