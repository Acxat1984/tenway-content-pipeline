"""Stress-mark probe: does Fish Audio respect any stress notation at all?

Fish takes plain text and guesses Russian stress itself, which it gets wrong
on homographs and less common words. There is no documented SSML, so the only
way to find out what it honours is to send the same sentence several ways and
listen.

Phrases are authored in a master format with `+` before the stressed vowel
(the Silero/RHVoice convention); every scheme below is derived from it, so all
variants carry identical stress marks and differ only in notation.
"""
import os, re, sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import fishtts, notify  # noqa: E402

ROOT = Path(__file__).parent.parent
OUT = ROOT / "out" / "_stress"
ACUTE = "́"  # combining acute accent

# Stress marked with `+` before the stressed vowel.
PHRASES = [
    "Ты пл+атишь за нейрос+еть и исп+ользуешь её проц+ентов на д+есять.",
    "Пол+учишь не простын+ю т+екста, а докум+ент, по кот+орому м+ожно приним+ать реш+ение.",
]

DEFAULT_VOICE = "7312c38557eb4fb384e3874e8e9cea67"  # Мужской Профессиональный213


def log(msg, status):
    print(msg, flush=True)
    status.append(str(msg))


def plain(t):
    """No marks at all — the current pipeline's behaviour, as a baseline."""
    return t.replace("+", "")


def acute(t):
    """Vowel followed by U+0301, the way stress is printed in dictionaries."""
    return re.sub(r"\+(.)", lambda m: m.group(1) + ACUTE, t)


def caps(t):
    """Stressed vowel upper-cased — a convention some engines pick up."""
    return re.sub(r"\+(.)", lambda m: m.group(1).upper(), t)


def keep_plus(t):
    """`+` left in place — honoured by Silero and RHVoice."""
    return t


# (label, text transform, normalize). Fish's normaliser may strip stress marks,
# so acute is probed both ways to tell "ignored" apart from "stripped".
SCHEMES = [("plain", plain, True),
           ("acute", acute, True),
           ("acute-raw", acute, False),
           ("caps", caps, True),
           ("plus", keep_plus, False)]


def send_audio(token, cid, path: Path, caption, title, status):
    with path.open("rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendAudio",
                          data={"chat_id": cid, "caption": caption[:1000],
                                "title": title[:64], "performer": "Fish Audio"},
                          files={"audio": (path.name, f, "audio/mpeg")}, timeout=180)
    log(f"sendAudio {path.name} -> {r.status_code}", status)
    return r.status_code == 200


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    status = ["# stress-mark probe"]
    voice = os.environ.get("STRESS_VOICE", "").strip() or DEFAULT_VOICE
    custom = os.environ.get("STRESS_TEXT", "").strip()
    phrases = [custom] if custom else PHRASES
    log(f"voice: {voice}", status)

    if not os.environ.get("FISH_API_KEY"):
        log("FAIL: no FISH_API_KEY", status)
        return finish(status, False)
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    if not token:
        log("FAIL: no TG_BOT_TOKEN", status)
        return finish(status, False)
    cid = notify.find_chat_id(token, status)
    if not cid:
        return finish(status, False)

    sent = 0
    for pi, master in enumerate(phrases):
        log(f"\nphrase {pi + 1}: {master}", status)
        for name, fn, norm in SCHEMES:
            text = fn(master)
            mp3 = OUT / f"p{pi}_{name}.mp3"
            log(f"  {name} (normalize={norm}): {text}", status)
            if not fishtts.synth(text, voice, mp3, status, normalize=norm):
                log(f"  {name}: tts FAILED", status)
                continue
            dur = fishtts.duration(mp3)
            log(f"  {name}: {dur}s", status)
            caption = (f"Фраза {pi + 1} · схема «{name}»\n{text}\n"
                       f"normalize={norm} · {dur:.1f} с")
            if send_audio(token, cid, mp3, caption, f"{name} #{pi + 1}", status):
                sent += 1

    log(f"\nsent {sent} samples", status)
    if sent:
        notify._call("sendMessage", token, chat_id=cid, text=(
            "Проверка ударений. В каждой фразе 5 вариантов:\n"
            "• plain — как сейчас, без разметки\n"
            "• acute — ударение знаком ´ над гласной\n"
            "• acute-raw — то же, но без нормализации текста\n"
            "• caps — ударная гласная заглавной\n"
            "• plus — плюс перед ударной гласной\n\n"
            "Скажи, в какой схеме ударения встали правильно. "
            "Если plain звучит так же, как остальные — Fish разметку игнорирует, "
            "будем решать иначе."))
    return finish(status, sent > 0)


def finish(status, ok):
    (OUT / "status.md").write_text("\n".join(status), encoding="utf-8")
    print("\n".join(status), flush=True)
    print(f"stress_ok={ok}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
