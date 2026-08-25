"""Concat voiceover, build ASS subtitles, mux with screen recording -> final mp4."""
import subprocess
from pathlib import Path

LEAD = 0.45  # seconds of silence before first segment (page-load compensation)


def _run(cmd, status):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        status.append(f"ffmpeg fail: {' '.join(map(str, cmd))[:200]}\n{r.stderr[-600:]}")
        raise RuntimeError("ffmpeg failed")


def _ts(sec: float) -> str:
    h = int(sec // 3600); m = int(sec % 3600 // 60); s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _chunks(text: str, limit: int = 34):
    words, cur, out = text.split(), "", []
    for w in words:
        if len(cur) + len(w) + 1 > limit and cur:
            out.append(cur); cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


def build_subs(segs: list[dict], path: Path):
    header = """[Script Info]
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,64,&H00E8F1F5,&H00E8F1F5,&H00202025,&H96202025,-1,0,0,0,100,100,0,0,1,3,0,2,60,60,120,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    t = LEAD
    for seg in segs:
        chunks = _chunks(seg["text"])
        total_chars = sum(len(c) for c in chunks) or 1
        ct = t
        for c in chunks:
            d = seg["dur"] * len(c) / total_chars
            lines.append(f"Dialogue: 0,{_ts(ct)},{_ts(ct + d)},Cap,,0,0,0,,{c}")
            ct += d
        t += seg["dur"]
    path.write_text(header + "\n".join(lines), encoding="utf-8")


def assemble(job_id: str, segs: list[dict], outdir: Path, status) -> Path:
    # 1. voiceover: lead silence + concat segments
    concat = outdir / "concat.txt"
    concat.write_text("\n".join(f"file '{outdir.resolve()}/{s['file']}'" for s in segs))
    voice = outdir / "voice.m4a"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat,
          "-af", f"adelay={int(LEAD*1000)}|{int(LEAD*1000)},loudnorm=I=-16:TP=-1.5",
          "-c:a", "aac", "-b:a", "160k", voice], status)

    # 2. subtitles
    subs = outdir / "subs.ass"
    build_subs(segs, subs)

    # 3. mux
    total = LEAD + sum(s["dur"] for s in segs)
    final = outdir / f"{job_id}.mp4"
    _run(["ffmpeg", "-y", "-i", outdir / "screen.webm", "-i", voice,
          "-filter_complex",
          f"[0:v]fps=30,scale=1080:1920,setsar=1,subtitles={subs}[v]",
          "-map", "[v]", "-map", "1:a", "-t", f"{total:.2f}",
          "-c:v", "libx264", "-crf", "22", "-preset", "medium", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-movflags", "+faststart", final], status)
    status.append(f"final: {final.name} ({final.stat().st_size/1e6:.1f} MB, {total:.1f}s)")
    return final
