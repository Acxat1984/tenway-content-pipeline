"""Record the scripted chat demo as 1080x1920 video via Playwright."""
import json, shutil, sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent


def record(job: dict, durations: list[float], outdir: Path, status) -> Path | None:
    segments = []
    for seg, dur in zip(job["segments"], durations):
        segments.append({"dur": dur, "scene": seg.get("scene", {"type": "hold"})})
    timeline = {"segments": segments}
    total = sum(durations)

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--force-device-scale-factor=1", "--hide-scrollbars"])
        ctx = browser.new_context(viewport={"width": 1080, "height": 1920},
                                  record_video_dir=str(outdir / "video_raw"),
                                  record_video_size={"width": 1080, "height": 1920})
        page = ctx.new_page()
        page.add_init_script(f"window.TIMELINE = {json.dumps(timeline, ensure_ascii=False)};")
        page.goto((HERE / "template" / "chat.html").as_uri())
        try:
            page.wait_for_function("document.title === 'DONE'", timeout=(total + 40) * 1000)
        except Exception as e:
            print(f"record timeout: {e}", flush=True)
            status.append(f"record timeout: {e}")
        video = page.video
        ctx.close()
        browser.close()
        raw = Path(video.path())
    dst = outdir / "screen.webm"
    shutil.move(str(raw), dst)
    status.append(f"recorded {dst.name}, planned {total:.1f}s")
    return dst
