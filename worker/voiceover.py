"""Voice-over from your own recordings instead of TTS.

Russian TTS could not be made to sound right — stress lands wrong, Latin terms
drop out, and the timbre stays robotic — so a Russian job is read by a human
and the pipeline just takes the files. Fish still handles English jobs, where
none of those problems arise.

Drop one file per segment into voice/<job-id>/ as seg00, seg01, … in any format
ffmpeg reads (m4a from a phone, ogg from Telegram, mp3, wav). Each is trimmed
of leading and trailing silence and levelled to broadcast loudness, and the
measured durations drive the screencast timing exactly as the synthesised ones
did.
"""
import json, subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
VOICE = ROOT / "voice"
EXTS = (".mp3", ".m4a", ".wav", ".ogg", ".oga", ".opus", ".aac", ".flac", ".mp4")

# Trim silence at both ends, then level to the loudness streaming platforms expect.
FILTER = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,"
          "areverse,"
          "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,"
          "areverse,"
          "loudnorm=I=-16:TP=-1.5:LRA=11")


def log(msg, status):
    print(msg, flush=True)
    status.append(str(msg))


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def find(indir: Path, i: int):
    """Locate the recording for segment i, whatever container it arrived in."""
    for ext in EXTS:
        p = indir / f"seg{i:02d}{ext}"
        if p.exists():
            return p
    matches = sorted(q for q in indir.glob(f"seg{i:02d}.*") if q.suffix.lower() in EXTS)
    return matches[0] if matches else None


def clean(src: Path, dst: Path, status) -> bool:
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                        "-af", FILTER, "-ac", "1", "-ar", "48000",
                        "-codec:a", "libmp3lame", "-b:a", "128k", str(dst)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        log(f"ffmpeg failed on {src.name}: {r.stderr[-300:]}", status)
        return False
    return True


def sheet(job: dict) -> str:
    """The recording sheet: which file to record, and the line to read into it."""
    jid = job["id"]
    lines = [f"# Запись озвучки — {jid}", "",
             f"Положи файлы в `voice/{jid}/`. Формат любой: m4a с телефона, "
             "голосовое из Telegram, mp3, wav.", "",
             "Знак `+` в тексте — это ударение для синтеза, вслух его читать не надо.", ""]
    for i, seg in enumerate(job["segments"]):
        text = seg["text"].replace("+", "")
        lines += [f"## seg{i:02d}", "", text, ""]
    return "\n".join(lines)


def build(job: dict, outdir: Path, status) -> dict | None:
    """Return the same meta shape tts_job produces, built from recordings."""
    jid = job["id"]
    indir = VOICE / jid
    indir.mkdir(parents=True, exist_ok=True)
    (indir / "README.md").write_text(sheet(job), encoding="utf-8")

    segs, missing = [], []
    for i, seg in enumerate(job["segments"]):
        src = find(indir, i)
        if src is None:
            missing.append(f"seg{i:02d}")
            continue
        dst = outdir / f"seg{i:02d}.mp3"
        if not clean(src, dst, status):
            return None
        d = round(duration(dst), 3)
        if d <= 0.2:
            log(f"FAIL: {src.name} пустой или слишком короткий ({d}s)", status)
            return None
        segs.append({"i": i, "file": dst.name, "dur": d, "text": seg["text"].replace("+", "")})
        log(f"seg{i:02d}: {d}s  ← {src.name}", status)

    if missing:
        log(f"FAIL: нет записей для {', '.join(missing)} — "
            f"положи их в voice/{jid}/ (что читать: voice/{jid}/README.md)", status)
        return None

    total = round(sum(s["dur"] for s in segs), 2)
    log(f"озвучка своим голосом: {len(segs)} сегментов, {total}s", status)
    meta = {"voice_used": "self", "voice_title": "собственная запись", "segments": segs}
    (outdir / "audio_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    return meta
