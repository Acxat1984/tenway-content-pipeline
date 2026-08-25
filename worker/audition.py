"""Voice audition: read one RU phrase with several Fish voices, send them all to Telegram.

Run it when the picked voice sounds wrong. Every candidate reads the same
phrase, so the voices are compared on equal terms; each arrives as a separate
Telegram audio captioned with its reference_id, ready to paste into a job.
"""
import json, os, re, sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import fishtts, notify  # noqa: E402

API = "https://api.fish.audio"
ROOT = Path(__file__).parent.parent
OUT = ROOT / "out" / "_audition"

PHRASE = ("Ты платишь за нейросеть и используешь её процентов на десять. "
          "Три фишки, которые это исправят.")

# Clones of identifiable real people — recognisable, and their likeness is not ours to sell.
BLOCK_TITLE = ["путин", "putin", "зеленск", "литвинов", "меллстрой", "гнус", "быков", "дудь", "лебедев"]
# Character/meme voices: wrong register for a business explainer.
BLOCK_TAGS = {"character-voice", "anime", "angry", "character"}

GOOD_TAGS = {"narration": 3, "educational": 3, "professional": 3, "narrative": 2,
             "clear": 2, "confident": 2, "measured": 2, "calm": 1, "smooth": 1,
             "warm": 1, "friendly": 1, "conversational": 1, "social-media": 1}


def log(msg, status):
    print(msg, flush=True)
    status.append(str(msg))


def catalog(status, pages=3):
    """Page through the RU voice list; return de-duplicated model dicts."""
    items, seen = [], set()
    for page in range(1, pages + 1):
        params = {"language": "ru", "sort_by": "task_count", "page_size": 30, "page_number": page}
        try:
            r = requests.get(f"{API}/model", headers=fishtts.HDR, params=params, timeout=30)
            log(f"catalog page {page} -> {r.status_code}", status)
            if r.status_code != 200:
                break
            batch = r.json().get("items") or r.json().get("data") or []
        except Exception as e:
            log(f"catalog page {page} error: {e}", status)
            break
        if not batch:
            break
        for it in batch:
            vid = it.get("_id") or it.get("id")
            if vid and vid not in seen:
                seen.add(vid)
                items.append(it)
    log(f"catalog: {len(items)} models", status)
    return items


def norm(s: str) -> str:
    """Fold a title for matching: letters only, doubles collapsed.

    Uploaders respell clone names freely — Мелстрой / Меллстрой / Мел Строй
    all name the same blogger — so compare on the folded form.
    """
    s = re.sub(r"[^a-zа-яё]", "", str(s).lower().replace("ё", "е"))
    return re.sub(r"(.)\1+", r"\1", s)


def score(it):
    """Rank a model for narrating a short vertical explainer. None = unusable."""
    langs = [str(x).lower() for x in (it.get("languages") or [])]
    if "ru" not in langs:
        return None
    title = norm(it.get("title"))
    if any(norm(b) in title for b in BLOCK_TITLE):
        return None
    tags = {str(t).lower() for t in (it.get("tags") or [])}
    if tags & BLOCK_TAGS:
        return None
    # Multilingual grab-bags read Russian with an accent; insist on a RU-first model.
    if len(langs) > 2:
        return None
    return sum(w for tag, w in GOOD_TAGS.items() if tag in tags)


def rejected(status):
    """Voices already heard: whatever the jobs currently use, plus AUDITION_EXCLUDE."""
    out = {v for v in re.split(r"[,\s]+", os.environ.get("AUDITION_EXCLUDE", "")) if v}
    for jp in sorted((ROOT / "jobs").glob("*.json")):
        try:
            v = json.loads(jp.read_text(encoding="utf-8")).get("voice", "")
        except Exception:
            continue
        if v and v not in ("male", "female"):
            out.add(v)
    if out:
        log(f"excluding {len(out)} already-heard voices: {', '.join(sorted(out))}", status)
    return out


def shortlist(items, limit, status):
    skip = rejected(status)
    ranked = []
    for it in items:
        if (it.get("_id") or it.get("id")) in skip:
            continue
        s = score(it)
        if s is not None:
            ranked.append((s, it.get("task_count") or 0, it))
    ranked.sort(key=lambda r: (-r[0], -r[1]))
    picked = [it for _, _, it in ranked[:limit]]
    log(f"shortlist: {len(picked)} of {len(items)} candidates passed the filter", status)
    return picked


def send_audio(token, cid, path: Path, caption: str, title: str, status):
    with path.open("rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendAudio",
                          data={"chat_id": cid, "caption": caption[:1000],
                                "title": title[:64], "performer": "Fish Audio"},
                          files={"audio": (path.name, f, "audio/mpeg")}, timeout=180)
    log(f"sendAudio {path.name} -> {r.status_code}", status)
    return r.status_code == 200


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    status = ["# voice audition"]
    phrase = os.environ.get("AUDITION_TEXT", "").strip() or PHRASE
    limit = int(os.environ.get("AUDITION_LIMIT", "8"))
    log(f"phrase: {phrase}", status)

    if not os.environ.get("FISH_API_KEY"):
        log("FAIL: no FISH_API_KEY", status)
        return finish(status, False)

    forced = [v for v in re.split(r"[,\s]+", os.environ.get("AUDITION_IDS", "")) if v]
    if forced:
        picked = [{"_id": v, "title": v[:8]} for v in forced]
        log(f"using {len(picked)} explicit ids", status)
    else:
        items = catalog(status)
        if not items:
            log("FAIL: empty catalog", status)
            return finish(status, False)
        (OUT / "catalog.json").write_text(json.dumps(
            [{k: it.get(k) for k in ("_id", "title", "languages", "like_count", "task_count", "tags")} for it in items],
            ensure_ascii=False, indent=1), encoding="utf-8")
        picked = shortlist(items, limit, status)
        if not picked:
            log("FAIL: no candidate survived the filter", status)
            return finish(status, False)

    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    if not token:
        log("FAIL: no TG_BOT_TOKEN", status)
        return finish(status, False)
    cid = notify.find_chat_id(token, status)
    if not cid:
        return finish(status, False)

    sent = 0
    for i, it in enumerate(picked):
        vid = it.get("_id") or it.get("id")
        title = str(it.get("title") or vid)
        mp3 = OUT / f"cand{i:02d}.mp3"
        if not fishtts.synth(phrase, vid, mp3, status):
            log(f"cand{i:02d} {title}: tts FAILED", status)
            continue
        dur = fishtts.duration(mp3)
        log(f'cand{i:02d} {title} ({vid}) {dur}s', status)
        caption = f'Вариант {i + 1}: «{title}»\nvoice: {vid}\n{dur:.1f} с'
        if send_audio(token, cid, mp3, caption, title, status):
            sent += 1

    log(f"sent {sent}/{len(picked)} candidates", status)
    if sent:
        listing = "\n".join(
            f'{i + 1}. «{it.get("title") or ""}» — {it.get("_id") or it.get("id")}'
            for i, it in enumerate(picked))
        code, _ = notify._call("sendMessage", token, chat_id=cid,
                               text=f"Прослушка голосов. Ответь номером понравившегося.\n\n{listing}"[:4000])
        log(f"sendMessage(list) -> {code}", status)
    return finish(status, sent > 0)


def finish(status, ok):
    (OUT / "status.md").write_text("\n".join(status), encoding="utf-8")
    print("\n".join(status), flush=True)
    print(f"audition_ok={ok}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
