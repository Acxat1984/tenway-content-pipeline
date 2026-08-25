"""Tune Silero: how it reads Latin terms, and how to slow it down.

Two questions in one pass. Latin words are compared raw against the lexicon
respelling, and the accepted reading is then slowed two ways — Silero's own
SSML prosody, and ffmpeg's atempo, which works for any engine and leaves pitch
alone.
"""
import os, subprocess, sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import lexicon, notify, providers  # noqa: E402

ROOT = Path(__file__).parent.parent
OUT = ROOT / "out" / "_tune"

PHRASE = ("Ты пл+атишь за ChatGPT и исп+ользуешь его проц+ентов на д+есять. "
          "Claude, Gemini и Midjourney — там то же с+амое. "
          "Заведи API, поставь Notion — и раб+ота пойдёт.")

VOICE = "eugene"


def log(msg, status=None):
    print(msg, flush=True)
    if status is not None:
        status.append(str(msg))


def atempo(src: Path, dst: Path, rate: float) -> bool:
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                        "-filter:a", f"atempo={rate}", "-codec:a", "libmp3lame",
                        "-b:a", "128k", str(dst)], capture_output=True, text=True)
    return r.returncode == 0 and dst.exists()


def dur(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    try:
        return round(float(out.stdout.strip()), 2)
    except ValueError:
        return 0.0


def send_audio(token, cid, path: Path, caption, title, status):
    with path.open("rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendAudio",
                          data={"chat_id": cid, "caption": caption[:1000], "title": title[:64],
                                "performer": "Silero eugene"},
                          files={"audio": (path.name, f, "audio/mpeg")}, timeout=180)
    log(f"sendAudio {path.name} -> {r.status_code}", status)
    return r.status_code == 200


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    status = ["# silero tuning: латиница и темп"]
    phrase = os.environ.get("TUNE_TEXT", "").strip() or PHRASE
    voice = os.environ.get("TUNE_VOICE", "").strip() or VOICE

    said, unknown = lexicon.transcribe(phrase)
    log(f"voice: {voice}", status)
    log(f"raw : {phrase}", status)
    log(f"lex : {said}", status)
    if unknown:
        log(f"НЕТ В СЛОВАРЕ (прочитаны по буквам): {unknown}", status)

    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    if not token:
        log("FAIL: no TG_BOT_TOKEN", status)
        return finish(status, False)
    cid = notify.find_chat_id(token, status)
    if not cid:
        return finish(status, False)

    silero = providers.Silero()
    variants = []

    # 1-2: does respelling Latin terms actually help?
    for label, text in (("raw-latin", phrase), ("lexicon", said)):
        mp3 = OUT / f"{label}.mp3"
        if silero.synth(text, voice, mp3, lambda m: log(m, status)):
            variants.append((label, mp3, f"латиница: {'как есть' if label == 'raw-latin' else 'словарём'}"))
        else:
            log(f"{label}: FAILED", status)

    # 3: Silero's own SSML prosody, if this build supports it.
    ssml = OUT / "ssml-slow.mp3"
    if silero.synth_ssml(f'<speak><prosody rate="slow">{said}</prosody></speak>',
                         voice, ssml, lambda m: log(m, status)):
        variants.append(("ssml-slow", ssml, "замедление средствами Silero (SSML)"))
    else:
        log("ssml-slow: недоступно в этой сборке silero", status)

    # 4-5: post-hoc slow-down, engine-agnostic and pitch-preserving.
    base = OUT / "lexicon.mp3"
    if base.exists():
        for rate in (0.92, 0.85):
            slow = OUT / f"atempo-{rate}.mp3"
            if atempo(base, slow, rate):
                variants.append((f"atempo-{rate}", slow, f"замедление ffmpeg ×{rate}"))
            else:
                log(f"atempo {rate}: FAILED", status)

    sent = 0
    for label, path, note in variants:
        d = dur(path)
        log(f"{label}: {d}s", status)
        if send_audio(token, cid, path, f"{label} · {note}\n{d} с", label, status):
            sent += 1

    log(f"sent {sent} samples", status)
    if sent:
        notify._call("sendMessage", token, chat_id=cid, text=(
            "Silero eugene: латиница и темп.\n\n"
            "• raw-latin — English как есть, проверь, слышно ли ChatGPT/Claude вообще\n"
            "• lexicon — те же слова русскими буквами со словарём\n"
            "• ssml-slow / atempo — то же самое, но медленнее\n\n"
            "Скажи, какой темп норм и правильно ли звучат названия."))
    return finish(status, sent > 0)


def finish(status, ok):
    (OUT / "status.md").write_text("\n".join(status), encoding="utf-8")
    print("\n".join(status), flush=True)
    print(f"tune_ok={ok}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
