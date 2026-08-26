"""Turn a radar item into a ready-to-record job.

The radar selects; this writes. One news video a day only works if the script
is waiting in the bot by morning instead of being written by hand each time.

Structure is fixed and comes from the feed research: the opening line names a
thing rather than a topic, then what it changes for the owner, then what to do.
Nothing is invented — the model is given the item's own title and text and told
to say plainly when the source does not support a claim.
"""
import json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
JOBS = ROOT / "jobs"
RESEARCH = ROOT / "research"

MODEL = "claude-opus-5"

SYSTEM = """\
Ты пишешь сценарии вертикальных роликов на русском для канала о нейросетях
для предпринимателей и владельцев малого бизнеса.

Аудитория: занятой человек с своим делом. Не разработчик. Ему интересно, что
это меняет в его работе и деньгах, а не как это устроено внутри.

Структура ролика жёсткая:
1. Факт. Первая фраза называет конкретную вещь — цифру, событие, решение.
   Не «сегодня поговорим о», не «вы не поверите». Утверждение с конфликтом.
2. Что это меняет для владельца бизнеса.
3. Что делать — конкретное действие, а не «следите за трендами».
4. Финал — вопрос, на который зрителю есть что ответить.

Правила:
- 6–8 сегментов, каждый 5–8 секунд речи (12–20 слов). Всего 40–55 секунд.
- Живой разговорный русский. Короткие предложения. Без канцелярита и без
  восторгов вроде «революция» и «прорыв».
- Числа словами там, где их произносят: «девятнадцать процентов».
- Английские названия писать кириллицей так, как читаются: «чат джи-пи-ти»,
  «клод», «эйч-эн». Латиницу в текст сегментов не ставить.
- Опираться только на то, что есть в источнике. Если чего-то в нём нет —
  не додумывать. Лучше сегментом меньше, чем выдуманный факт.
- Если источник слабый или тема не переводится на язык денег и действий —
  вернуть usable: false и объяснить почему. Это нормальный исход.

Сцены к сегментам:
- "title" — только первый сегмент: kicker, h1 (коротко, до 20 знаков), h2.
- "bullets" — основные: head, необязательный note, 2–4 строки label/value.
  value короткий: цифра, «да», «нет», слово. У одной строки можно accent: true.
- "outro" — только последний: kicker, h1 (вопрос), h2.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "usable": {"type": "boolean"},
        "reason": {"type": "string"},
        "slug": {"type": "string"},
        "caption": {"type": "string"},
        "post_text": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "scene": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["title", "bullets", "outro"]},
                            "kicker": {"type": "string"},
                            "h1": {"type": "string"},
                            "h2": {"type": "string"},
                            "head": {"type": "string"},
                            "note": {"type": "string"},
                            "rows": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "value": {"type": "string"},
                                        "accent": {"type": "boolean"},
                                    },
                                    "required": ["label", "value"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["type"],
                        "additionalProperties": False,
                    },
                },
                "required": ["text", "scene"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["usable", "reason", "slug", "caption", "post_text", "segments"],
    "additionalProperties": False,
}


def log(msg, status):
    print(msg, flush=True)
    status.append(str(msg))


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9а-яё]+", "-", s.lower()).strip("-")
    return (s[:48] or "news").rstrip("-")


def write_job(data: dict, item: dict, stamp: str, status) -> Path | None:
    if not data.get("usable"):
        log(f"тема отклонена: {data.get('reason', '')[:200]}", status)
        return None
    segs = data.get("segments") or []
    if not (4 <= len(segs) <= 10):
        log(f"FAIL: {len(segs)} сегментов — вне разумного диапазона", status)
        return None
    for i, seg in enumerate(segs):
        if re.search(r"[A-Za-z]", seg["text"]):
            log(f"WARN seg{i:02d}: латиница в тексте для озвучки — {seg['text'][:70]}", status)

    jid = f"{stamp}-{slugify(data['slug'])}"
    job = {
        "id": jid,
        # Draft on purpose: a machine-written script is a proposal, and the
        # person reading it aloud is the one who approves it.
        "status": "draft",
        "lang": "ru",
        "voice": "self",
        "source": f"{item.get('src', '')} · {item.get('discuss') or item.get('url', '')}",
        "caption": data["caption"],
        "post_text": data["post_text"],
        "segments": segs,
    }
    path = JOBS / f"{jid}.json"
    path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"джоба: {path.name}, сегментов {len(segs)}", status)
    return path


def main():
    status = ["# scriptgen"]
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        log("SKIP: нет ANTHROPIC_API_KEY — сценарий пишется вручную из дайджеста", status)
        return finish(status, False)

    try:
        items = json.loads((RESEARCH / "radar-latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log(f"FAIL: нет свежего радара ({e})", status)
        return finish(status, False)
    if not items:
        log("FAIL: радар пуст", status)
        return finish(status, False)

    import anthropic
    client = anthropic.Anthropic()
    stamp = os.environ.get("RADAR_STAMP", "").strip() or "today"
    tries = int(os.environ.get("SCRIPTGEN_TRIES", "3"))

    # Ranked order already; walk down until one topic actually converts.
    for item in items[:tries]:
        log(f"пробую: [{item['src']}] {item['title'][:80]}", status)
        brief = (f"Источник: {item['src']}, {item['score']} голосов, "
                 f"{item['comments']} комментариев, {item['age_h']} часов назад.\n"
                 f"Заголовок: {item['title']}\n"
                 f"Ссылка: {item.get('url', '')}\n"
                 f"Обсуждение: {item.get('discuss', '')}\n\n"
                 f"Текст поста, если есть:\n{item.get('text') or '(нет)'}")
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=SYSTEM,
                thinking={"type": "adaptive"},
                output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content":
                           "Сделай сценарий ролика по этой новости.\n\n" + brief}],
            )
        except Exception as e:
            log(f"api error: {e}", status)
            continue

        text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            data = json.loads(text)
        except ValueError as e:
            log(f"не разобрал ответ модели: {e}", status)
            continue

        path = write_job(data, item, stamp, status)
        if path:
            notify(path, item, status)
            return finish(status, True)

    log("ни одна из тем не подошла — сегодня сценарий вручную", status)
    return finish(status, False)


def notify(path: Path, item: dict, status):
    import requests
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    cid = os.environ.get("TG_CHAT_ID", "").strip()
    if not cid:
        try:
            st = json.loads((ROOT / "voice" / ".inbox.json").read_text(encoding="utf-8"))
            cid = str(st.get("chat_id") or "")
        except (OSError, ValueError):
            cid = ""
    if not (token and cid):
        return
    job = json.loads(path.read_text(encoding="utf-8"))
    first = job["segments"][0]["text"]
    msg = (f"Сценарий дня готов: {job['id']}\n\n"
           f"Источник: {item['src']} · {item['title'][:90]}\n\n"
           f"Первая фраза:\n{first}\n\n"
           f"Сегментов: {len(job['segments'])}\n"
           f"Черновик — посмотри и скажи «ок», тогда включу в запись.")
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                  data={"chat_id": cid, "text": msg[:4000],
                        "disable_web_page_preview": "true"}, timeout=60)
    log("сценарий отправлен в Telegram", status)


def finish(status, ok):
    RESEARCH.mkdir(parents=True, exist_ok=True)
    (RESEARCH / "scriptgen-status.md").write_text("\n".join(status), encoding="utf-8")
    print("\n".join(status), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
