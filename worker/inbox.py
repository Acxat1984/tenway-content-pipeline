"""Collect voice messages from the Telegram bot into voice/<job-id>/.

There is no server here — the worker only exists while a workflow runs — so a
run starts by draining whatever accumulated, saving the voice messages it finds
as segment files and recording the offset for next time.

Waiting a quarter hour between segments would make recording unusable, so once
a run sees the owner is at the phone it stays on the line and answers in real
time, dropping off after a couple of minutes of silence.

Recording session, from the phone:

    /rec                 начать запись текущей джобы с seg00
    /rec <job-id>        то же, но для конкретной джобы
    /seg 3               следующее голосовое пойдёт в seg03
    <голосовое>          принимается как очередной сегмент
    /status              что уже записано, чего не хватает
    /done                закончить приём сразу, не дожидаясь тишины

Only the owner's chat is accepted: anyone can message a bot, and an unfiltered
inbox would let a stranger's audio into the video.
"""
import json, os, re, subprocess, sys, time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path(__file__).parent.parent
JOBS, VOICE = ROOT / "jobs", ROOT / "voice"
STATE = VOICE / ".inbox.json"
API = "https://api.telegram.org/bot{token}/{method}"
FILEAPI = "https://api.telegram.org/file/bot{token}/{path}"


def log(msg, status):
    print(msg, flush=True)
    status.append(str(msg))


def call(token, method, **kw):
    # Long polling holds the request open, so the read timeout must outlast it.
    wait = int(kw.get("timeout", 0) or 0)
    r = requests.post(API.format(token=token, method=method), data=kw, timeout=wait + 30)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {}


def say(token, cid, text):
    call(token, "sendMessage", chat_id=cid, text=text[:4000])


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"offset": 0, "job": None, "next": 0, "chat_id": None}


def save_state(st):
    VOICE.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def approved_jobs():
    out = []
    for jp in sorted(JOBS.glob("*.json")):
        try:
            job = json.loads(jp.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if job.get("status") == "approved" and job.get("voice") == "self":
            out.append(job)
    return out


def pick_job(jid=None):
    jobs = approved_jobs()
    if jid:
        return next((j for j in jobs if j["id"] == jid), None)
    return jobs[0] if jobs else None


def missing(job):
    d = VOICE / job["id"]
    out = []
    for i in range(len(job["segments"])):
        if not any((d / f"seg{i:02d}{e}").exists()
                   for e in (".oga", ".ogg", ".opus", ".mp3", ".m4a", ".wav")):
            out.append(i)
    return out


def report(job):
    miss = missing(job)
    total = len(job["segments"])
    if not miss:
        return f"{job['id']}: все {total} сегментов на месте — сборка пойдёт следующим пушем."
    have = total - len(miss)
    return (f"{job['id']}: записано {have} из {total}.\n"
            f"Не хватает: {', '.join(f'seg{i:02d}' for i in miss)}")


def fetch_voice(token, file_id, dest: Path, status) -> bool:
    code, data = call(token, "getFile", file_id=file_id)
    if code != 200 or not data.get("ok"):
        log(f"getFile -> {code}: {str(data)[:150]}", status)
        return False
    path = data["result"]["file_path"]
    r = requests.get(FILEAPI.format(token=token, path=path), timeout=180)
    if r.status_code != 200:
        log(f"download -> {r.status_code}", status)
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return True


def push(status):
    """Commit what has arrived so far.

    A session lives inside a workflow run, and the runner takes its disk with
    it — a timeout or a cancel would otherwise lose every segment recorded so
    far. Pushing per segment also lets the build start on the last one.
    """
    def run(*args):
        return subprocess.run(args, capture_output=True, text=True)

    run("git", "config", "user.name", "pipeline-bot")
    run("git", "config", "user.email", "bot@users.noreply.github.com")
    run("git", "add", "-A", "voice/")
    if run("git", "diff", "--cached", "--quiet").returncode == 0:
        return
    run("git", "commit", "-m", "voice inbox")
    run("git", "pull", "--rebase", "origin", "main")
    r = run("git", "push", "origin", "HEAD:main")
    log("push: ok" if r.returncode == 0 else f"push failed: {r.stderr[-200:]}", status)


def handle_text(text, st, token, cid, status):
    """Commands that steer the session. Returns True if the text was a command."""
    t = text.strip().lower()
    if t.startswith("/rec"):
        arg = text.strip()[4:].strip()
        job = pick_job(arg or None)
        if not job:
            say(token, cid, f"Не нашёл джобу {arg or '(нет approved с voice: self)'}")
            return True
        st["job"], st["next"] = job["id"], 0
        seg0 = job["segments"][0]["text"].replace("+", "")
        say(token, cid, f"Пишем {job['id']}.\n\nseg00 — читай:\n\n{seg0}")
        log(f"/rec -> {job['id']}", status)
        return True

    if t.startswith("/seg"):
        m = re.search(r"\d+", t)
        job = pick_job(st.get("job"))
        if not m or not job:
            say(token, cid, "Формат: /seg 3")
            return True
        i = int(m.group(0))
        if i >= len(job["segments"]):
            say(token, cid, f"В {job['id']} только {len(job['segments'])} сегментов (0–{len(job['segments']) - 1}).")
            return True
        st["next"] = i
        say(token, cid, f"seg{i:02d} — читай:\n\n{job['segments'][i]['text'].replace('+', '')}")
        return True

    if t.startswith("/done") or t.startswith("/stop"):
        job = pick_job(st.get("job"))
        st["stop"] = True
        say(token, cid, ("Закончил приём.\n\n" + report(job)) if job else "Закончил приём.")
        return True

    if t.startswith("/status"):
        job = pick_job(st.get("job"))
        say(token, cid, report(job) if job else "Нет approved-джобы с voice: self")
        return True

    return False


def drain(token, st, owner, status, wait=0):
    """Process one batch. Returns (saved, active, owner) — active means the
    owner is at the phone right now, which is what keeps the session open."""
    code, data = call(token, "getUpdates", offset=st["offset"], timeout=wait,
                      allowed_updates='["message"]')
    if code != 200 or not data.get("ok"):
        log(f"getUpdates -> {code}: {str(data)[:200]}", status)
        return 0, False, owner

    saved, active = 0, False
    for upd in data.get("result", []):
        st["offset"] = upd["update_id"] + 1
        msg = upd.get("message")
        if not msg:
            continue
        cid = str(msg["chat"]["id"])
        # First contact defines the owner; everyone else is ignored from then on.
        if owner is None:
            owner = cid
            st["chat_id"] = cid
            log(f"owner chat_id: {cid}", status)
        if cid != str(owner):
            log(f"игнорирую сообщение из чужого чата {cid}", status)
            continue
        st["chat_id"] = cid
        active = True

        if "text" in msg and handle_text(msg["text"], st, token, cid, status):
            continue

        media = msg.get("voice") or msg.get("audio") or msg.get("video_note")
        if not media:
            continue

        job = pick_job(st.get("job"))
        if not job:
            say(token, cid, "Сначала /rec — не знаю, для какой джобы это.")
            continue

        # A caption like "3" or "seg3" overrides the running counter.
        m = re.search(r"\d+", msg.get("caption", ""))
        i = int(m.group(0)) if m else st.get("next", 0)
        if i >= len(job["segments"]):
            say(token, cid, f"seg{i:02d} — в джобе только {len(job['segments'])} сегментов.")
            continue

        dest = VOICE / job["id"] / f"seg{i:02d}.oga"
        if not fetch_voice(token, media["file_id"], dest, status):
            say(token, cid, f"Не смог скачать seg{i:02d}, пришли ещё раз.")
            continue

        saved += 1
        st["next"] = i + 1
        dur = media.get("duration", "?")
        log(f"seg{i:02d} <- voice {dur}s ({dest.stat().st_size // 1024} KB)", status)

        if st["next"] < len(job["segments"]):
            nxt = f"\n\nseg{st['next']:02d} — читай:\n\n{job['segments'][st['next']]['text'].replace('+', '')}"
        else:
            nxt = "\n\nЭто был последний. " + report(job)
        say(token, cid, f"Принял seg{i:02d} ({dur} с).{nxt}")
        save_state(st)
        push(status)

    return saved, active, owner


def main():
    status = ["# voice inbox"]
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    if not token:
        log("FAIL: no TG_BOT_TOKEN", status)
        return finish(status, False)

    st = load_state()
    owner = os.environ.get("TG_CHAT_ID", "").strip() or st.get("chat_id")
    # A hand-started session names its own length and holds the line for it;
    # a scheduled one only lingers if somebody is actually there.
    minutes = os.environ.get("INBOX_MINUTES", "").strip()
    if minutes.isdigit() and int(minutes) > 0:
        hard, idle, always = int(minutes) * 60, 900, True
    else:
        hard, idle, always = 600, 150, False

    saved, active, owner = drain(token, st, owner, status)
    log(f"первый проход: сегментов {saved}, активность {active}", status)
    active = active or always

    # Somebody is recording right now: stay on the line instead of making them
    # wait for the next scheduled run between every segment.
    if active and hard > 0:
        say(token, owner, f"На связи ещё {hard // 60} минут — присылай сегменты подряд.")
        started, last = time.monotonic(), time.monotonic()
        while (time.monotonic() - started < hard
               and time.monotonic() - last < idle
               and not st.get("stop")):
            got, act, owner = drain(token, st, owner, status, wait=25)
            saved += got
            if act:
                last = time.monotonic()
        log(f"сессия закрыта через {int(time.monotonic() - started)}s"
            + (" по /done" if st.get("stop") else ""), status)
        if not st.get("stop"):
            say(token, owner, "Пауза — ушёл. Пиши /rec или просто присылай дальше, "
                              "подхвачу в течение пяти минут.")
    st.pop("stop", None)

    save_state(st)
    log(f"сохранено сегментов: {saved}", status)
    return finish(status, True)


def finish(status, ok):
    VOICE.mkdir(parents=True, exist_ok=True)
    (VOICE / "inbox-status.md").write_text("\n".join(status), encoding="utf-8")
    print("\n".join(status), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
