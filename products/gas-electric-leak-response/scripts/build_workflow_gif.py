"""Record the README workflow GIF from docs/workflow.html (needs `pip install playwright pillow`; uses an installed Chromium).

  python scripts/build_workflow_gif.py [--scenario 0] [--out docs/assets/workflow.gif]

The page exposes `window.__wf.frame(scenario, step, t)` so every frame is deterministic: the step is shown, and the data
packets are advanced by `t` seconds. No browser animation timing is involved.
"""
from __future__ import annotations

import argparse
import io
import os
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


class WorkflowRecorder:
    def __init__(self, html: Path, width: int = 1500, out_width: int = 1200, frames_per_step: int = 6, dt: float = 0.13, colors: int = 160):
        self.html, self.width, self.out_width, self.fps, self.dt, self.colors = html, width, out_width, frames_per_step, dt, colors

    @staticmethod
    def chromium() -> str | None:
        env = os.environ.get("CHROMIUM_PATH")
        if env:
            return env
        for p in sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")):
            return str(p)
        return None

    def capture(self, scenario: int) -> list[Image.Image]:
        from playwright.sync_api import sync_playwright

        frames: list[Image.Image] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=self.chromium(), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": self.width, "height": 900}, device_scale_factor=1)
            page.goto(self.html.resolve().as_uri() + "#capture")
            page.wait_for_selector("#diagram .node")
            steps = page.evaluate("window.__wf.counts()")[scenario]
            t = 0.0
            for step in range(steps):
                for _ in range(self.fps):
                    page.evaluate(f"window.__wf.frame({scenario},{step},{t})")
                    png = page.locator("#diagram").screenshot()
                    frames.append(Image.open(io.BytesIO(png)).convert("RGB"))
                    t += self.dt
            browser.close()
        return frames

    def encode(self, frames: list[Image.Image], out: Path) -> int:
        h = round(frames[0].height * self.out_width / frames[0].width)
        small = [f.resize((self.out_width, h), Image.LANCZOS) for f in frames]
        # One shared palette built from a sample of ALL frames, so the amber highlight and the packets keep their colour.
        sample = small[:: max(1, len(small) // 12)]
        sheet = Image.new("RGB", (self.out_width, h * len(sample)))
        for i, f in enumerate(sample):
            sheet.paste(f, (0, i * h))
        pal = sheet.quantize(colors=self.colors, method=Image.Quantize.MEDIANCUT)
        paletted = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in small]
        out.parent.mkdir(parents=True, exist_ok=True)
        paletted[0].save(out, save_all=True, append_images=paletted[1:], duration=int(self.dt * 1000 * 1.0), loop=0, optimize=True, disposal=1)
        return out.stat().st_size

    def run(self, scenario: int, out: Path) -> int:
        return self.encode(self.capture(scenario), out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", type=int, default=0)
    ap.add_argument("--html", default=str(ROOT / "docs" / "workflow.html"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "assets" / "workflow.gif"))
    a = ap.parse_args()
    size = WorkflowRecorder(Path(a.html)).run(a.scenario, Path(a.out))
    print(f"wrote {a.out} ({size / 1024:.0f} KB)")
