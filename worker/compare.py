"""Read one phrase through every available TTS engine and send them to Telegram.

Fish reads the text robotically and ignores stress notation, so the engine
itself is what's up for replacement. This puts the candidates side by side on
the same sentence — including the word Fish gets wrong, "де́сять".
"""
import os, sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import notify, providers  # noqa: E402

ROOT = Path(__file__).parent.parent
OUT = ROOT / "out" / "_compare"

PHRASE = ("Ты пл+атишь за нейрос+еть и исп+ользуешь её проц+ентов на д+есять. "
          "Пол+учишь не простын+ю т+екста, а докум+ент, по кот+орому м+ожно приним+ать реш+ение.")


def log(msg, status=None):
    print(msg, flush=True)
    if status is not None:
        status.append(str(msg))


def send_audio(token, cid, path: Path, caption, title, status):
    with path.open("rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendAudio",
                          data={"chat_id": cid, "caption": caption[:1000], "title": title[:64],
                                "performer": "TTS compare"},
                          files={"audio": (path.name, f, "audio/mpeg")}, timeout=180)
    log(f"sendAudio {path.name} -> {r.status_code}", status)
    return r.status_code == 200


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    status = ["# tts engine comparison"]
    phrase = os.environ.get("COMPARE_TEXT", "").strip() or PHRASE
    per = int(os.environ.get("COMPARE_PER_PROVIDER", "3"))
    log(f"phrase: {phrase}", status)

    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    if not token:
        log("FAIL: no TG_BOT_TOKEN", status)
        return finish(status, False)
    cid = notify.find_chat_id(token, status)
    if not cid:
        return finish(status, False)

    active = [p for p in providers.ALL if p.available()]
    skipped = [p.name for p in providers.ALL if not p.available()]
    log(f"active: {[p.name for p in active]}", status)
    log(f"skipped (нет ключа): {skipped}", status)
    if not active:
        log("FAIL: no provider available", status)
        return finish(status, False)

    sent = 0
    for prov in active:
        voices = prov.voices[:per]
        if prov.name == "elevenlabs":
            voices = [v for v, _ in prov.list_voices(lambda m: log(m, status), per)]
        if not voices:
            log(f"{prov.name}: нет голосов", status)
            continue
        for voice in voices:
            mp3 = OUT / f"{prov.name}_{voice}.mp3".replace("/", "_")
            log(f"{prov.name}/{voice} ...", status)
            if not prov.synth(phrase, voice, mp3, lambda m: log(m, status)):
                log(f"{prov.name}/{voice}: FAILED", status)
                continue
            mark = "читает разметку ударения" if prov.stress else "ударение на усмотрение движка"
            caption = f"{prov.name} · {voice}\n{mark}"
            if send_audio(token, cid, mp3, caption, f"{prov.name} {voice}", status):
                sent += 1

    log(f"sent {sent} samples", status)
    if sent:
        lines = "\n".join(f"• {p.name} — {p.note}" for p in active)
        miss = f"\n\nНе проверены (нужен ключ): {', '.join(skipped)}" if skipped else ""
        notify._call("sendMessage", token, chat_id=cid, text=(
            f"Сравнение движков озвучки на одной фразе.\n\n{lines}{miss}\n\n"
            "Слушай два места: звучит ли живо и правильно ли сказано «на де́сять»."))
    return finish(status, sent > 0)


def finish(status, ok):
    (OUT / "status.md").write_text("\n".join(status), encoding="utf-8")
    print("\n".join(status), flush=True)
    print(f"compare_ok={ok}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
