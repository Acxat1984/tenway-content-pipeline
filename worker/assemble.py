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


CAP_SIZE = 58          # кегль субтитров
# Ширина кадра минус поля стиля, и ещё 3% на округление таблицы глифов:
# без запаса самая длинная строка садилась впритык, и любая правка текста
# снова выбрасывала её за край.
CAP_WIDTH = int((1080 - 80 - 180) * 0.97)

# Ширина глифов DejaVu Sans Bold в долях кегля — измерена в браузере, а не
# угадана. Считать строку по числу символов нельзя: «Щ» вчетверо шире «i»,
# и строка из двадцати знаков вылезала за кадр, переносилась и уезжала вниз.
_W = {
    0.31: "'",
    0.34: "ijl",
    0.35: " ",
    0.37: "/IJ",
    0.38: ",.",
    0.4: ":;",
    0.41: "-f",
    0.46: "!()",
    0.48: "t",
    0.49: "r",
    0.5: "–",
    0.52: "\"г",
    0.58: "?zзт",
    0.59: "cсэ",
    0.6: "s",
    0.63: "вь",
    0.64: "LГя",
    0.65: "vxy«»ух",
    0.67: "akа",
    0.68: "EFSeЁЕТекё",
    0.69: "oнопч",
    0.7: "0123456789Tбий₽",
    0.71: "hnuЗ",
    0.72: "Ybdgpqр",
    0.73: "CPZРСЭл",
    0.74: "ц",
    0.75: "ъ",
    0.76: "BБВЬ",
    0.77: "AKRVXАУХЯ",
    0.81: "UЧд",
    0.82: "GКм",
    0.83: "DЛ",
    0.84: "HNИЙНП",
    0.85: "OQО",
    0.89: "Д",
    0.9: "ы",
    0.92: "w",
    0.93: "Ц",
    0.94: "Ъ",
    0.97: "ю",
    0.99: "Фф",
    1.0: "%MМж—",
    1.04: "mЫ",
    1.06: "ш",
    1.1: "W",
    1.11: "щ",
    1.17: "Ю",
    1.2: "№",
    1.22: "Ж",
    1.24: "Ш",
    1.33: "Щ",
}
# Неизвестный символ считаем самым широким — лучше лишний перенос, чем обрез.
_WIDTH = {c: x for x, chars in _W.items() for c in chars}
_FALLBACK = max(_WIDTH.values())


def _text_width(s: str, size: int = CAP_SIZE) -> float:
    return sum(_WIDTH.get(c, _FALLBACK) for c in s) * size


def _chunks(text: str, limit: float = CAP_WIDTH):
    """Разбить на строки, которые гарантированно влезают по ширине."""
    words, cur, out = text.split(), "", []
    for w in words:
        probe = f"{cur} {w}".strip()
        if cur and _text_width(probe) > limit:
            out.append(cur)
            cur = w
        else:
            cur = probe
    if cur:
        out.append(cur)
    return out


def build_subs(segs: list[dict], path: Path):
    # MarginV 440 держит субтитры над подписью и кнопками площадки; правое
    # поле 180 уводит строку из-под колонки лайков. Кегль 68 и плотная
    # подложка — читаемость на дешёвом телефоне.
    header = """[Script Info]
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,58,&H00E8F1F5,&H00E8F1F5,&H00202025,&HC8202025,-1,0,0,0,100,100,0,0,1,4,0,2,80,180,440,1

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
