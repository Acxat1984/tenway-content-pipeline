"""Watch English-language AI discussion and report what actually took off.

The Russian-language niche recycles the same material; the same story reaches
Hacker News and Reddit days earlier and in more depth. This collects both,
keeps what outran its usual level rather than what merely has a big number,
and hands over a digest to write scripts from.

It does not translate or write anything: the selection is mechanical, the
judgement is not. Translating a thread badly is worse than not covering it.
"""
import json, os, re, sys, time, urllib.parse
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
OUT = ROOT / "research"
UA = {"User-Agent": "tenway-content-radar/1.0 (contact via github Acxat1984)"}

SUBS = [
    "ClaudeAI", "LocalLLaMA", "OpenAI", "ChatGPTCoding",
    "artificial", "singularity", "MachineLearning", "ExperiencedDevs",
]

# Что вообще относится к нише: ИИ в работе, агенты, стоимость, инструменты.
TOPIC = re.compile(
    r"\b(ai|llm|gpt|claude|anthropic|openai|gemini|agent|agents|agentic|prompt|prompting|"
    r"copilot|cursor|automation|automate|rag|context|token|tokens|coding|codex|"
    r"model|models|fine-?tun|workflow|mcp)\b", re.I)

# Что для предпринимателя мимо: релизы весов, бенчмарки, драма вокруг компаний.
NOISE = re.compile(
    r"\b(waifu|nsfw|girlfriend|benchmark leaderboard|gguf|quantiz|vram|"
    r"lawsuit|stock|ipo|valuation|layoff)\b", re.I)


def log(msg, status):
    print(msg, flush=True)
    status.append(str(msg))


def get(url, params=None, timeout=30):
    r = requests.get(url, headers=UA, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def hn(hours: int, min_points: int, status):
    """Hacker News via the public Algolia index — no key, no auth."""
    since = int(time.time()) - hours * 3600
    try:
        data = get("https://hn.algolia.com/api/v1/search",
                   {"tags": "story", "hitsPerPage": 100,
                    "numericFilters": f"created_at_i>{since},points>{min_points}"})
    except Exception as e:
        log(f"hn error: {e}", status)
        return []
    out = []
    for h in data.get("hits", []):
        title = h.get("title") or ""
        if not TOPIC.search(title) or NOISE.search(title):
            continue
        out.append({
            "src": "HN", "title": title,
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
            "discuss": f"https://news.ycombinator.com/item?id={h['objectID']}",
            "score": h.get("points", 0), "comments": h.get("num_comments", 0),
            "age_h": round((time.time() - h.get("created_at_i", 0)) / 3600, 1),
            "text": (h.get("story_text") or "")[:600],
        })
    log(f"HN: {len(out)} по теме из {len(data.get('hits', []))}", status)
    return out


def reddit_token(status):
    """Reddit blocks the public .json from datacentre IPs, and a CI runner is
    one — so the app credentials are the only way in from here. Free to create
    at reddit.com/prefs/apps as a "script" app."""
    cid = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    sec = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    if not (cid and sec):
        return None
    try:
        r = requests.post("https://www.reddit.com/api/v1/access_token",
                          auth=(cid, sec), data={"grant_type": "client_credentials"},
                          headers=UA, timeout=30)
        if r.status_code != 200:
            log(f"reddit auth -> {r.status_code}: {r.text[:120]}", status)
            return None
        return r.json().get("access_token")
    except Exception as e:
        log(f"reddit auth error: {e}", status)
        return None


def lobsters(status):
    """Lobste.rs stays reachable from CI and skews to practitioners."""
    try:
        data = get("https://lobste.rs/t/ai.json")
    except Exception as e:
        log(f"lobsters error: {e}", status)
        return []
    out = []
    for p in data:
        title = p.get("title") or ""
        if not TOPIC.search(title) or NOISE.search(title):
            continue
        created = p.get("created_at", "")
        out.append({
            "src": "lobsters", "title": title,
            "url": p.get("url") or p.get("short_id_url", ""),
            "discuss": p.get("comments_url") or p.get("short_id_url", ""),
            "score": p.get("score", 0), "comments": p.get("comment_count", 0),
            "age_h": 12.0, "created": created,
            "text": (p.get("description_plain") or "")[:400],
        })
    log(f"lobsters: {len(out)} по теме", status)
    return out


def reddit(sub: str, status, token=None):
    """Top of the day plus the subreddit's own recent baseline.

    A big number means nothing on a big subreddit — what matters is how far a
    post outran what that subreddit normally does, which is why the median of
    the current hot page is fetched alongside.
    """
    if token:
        base_url, hdr = "https://oauth.reddit.com", {**UA, "Authorization": f"bearer {token}"}
    else:
        base_url, hdr = "https://www.reddit.com", UA
    try:
        top = requests.get(f"{base_url}/r/{sub}/top",
                           headers=hdr, params={"t": "day", "limit": 25}, timeout=30)
        top.raise_for_status()
        top = top.json()
        hot = requests.get(f"{base_url}/r/{sub}/hot",
                           headers=hdr, params={"limit": 50}, timeout=30)
        hot.raise_for_status()
        hot = hot.json()
    except Exception as e:
        log(f"r/{sub} error: {e}", status)
        return []

    def rows(d):
        return [c["data"] for c in d.get("data", {}).get("children", [])]

    base = sorted(p.get("score", 0) for p in rows(hot) if not p.get("stickied"))
    median = base[len(base) // 2] if base else 0
    out = []
    for p in rows(top):
        if p.get("stickied"):
            continue
        title = p.get("title") or ""
        if not TOPIC.search(title) or NOISE.search(title):
            continue
        score = p.get("score", 0)
        out.append({
            "src": f"r/{sub}", "title": title,
            "url": p.get("url_overridden_by_dest") or f"https://reddit.com{p['permalink']}",
            "discuss": f"https://reddit.com{p['permalink']}",
            "score": score, "comments": p.get("num_comments", 0),
            "age_h": round((time.time() - p.get("created_utc", 0)) / 3600, 1),
            "ratio": round(score / median, 1) if median else None,
            "median": median,
            "text": (p.get("selftext") or "")[:600],
        })
    log(f"r/{sub}: {len(out)} по теме, медиана саба {median}", status)
    return out


def rank(items):
    """Freshness times how far it outran the baseline; comments break ties."""
    def key(it):
        ratio = it.get("ratio") or (it["score"] / 150)
        fresh = 1.4 if it["age_h"] <= 12 else 1.0 if it["age_h"] <= 24 else 0.7
        return ratio * fresh + min(it["comments"], 400) / 400
    return sorted(items, key=key, reverse=True)


def digest(items, hours):
    lines = [f"# Англоязычный радар — что залетело за {hours} ч", "",
             "Собрано автоматически. Отбор механический: свежесть, превышение обычного",
             "уровня площадки, живость обсуждения. Что из этого годится в ролик —",
             "решается вручную, здесь только сырьё.", ""]
    for i, it in enumerate(items, 1):
        lines.append(f"## {i}. {it['title']}")
        m = f"{it['src']} · {it['score']} · {it['comments']} комм. · {it['age_h']} ч назад"
        if it.get("ratio"):
            m += f" · ×{it['ratio']} к медиане саба ({it['median']})"
        lines += ["", m, "", f"- ссылка: {it['url']}", f"- обсуждение: {it['discuss']}"]
        if it.get("text"):
            lines += ["", "> " + it["text"].replace("\n", " ")[:400]]
        lines.append("")
    return "\n".join(lines)


def main():
    status = ["# radar"]
    hours = int(os.environ.get("RADAR_HOURS", "36"))
    top_n = int(os.environ.get("RADAR_TOP", "12"))
    min_points = int(os.environ.get("RADAR_HN_POINTS", "80"))

    items = hn(hours, min_points, status)
    items += lobsters(status)

    token = reddit_token(status)
    if token:
        log("reddit: авторизован по app-ключу", status)
    else:
        log("reddit: нет REDDIT_CLIENT_ID/SECRET — публичный json с CI блокируется, "
            "сабреддиты пропускаю", status)
    if token:
        for sub in SUBS:
            items += reddit(sub, status, token)
            time.sleep(1.5)  # Reddit не любит частых запросов подряд

    fresh = [it for it in items if it["age_h"] <= hours]
    picked = rank(fresh)[:top_n]
    log(f"всего {len(items)}, свежих {len(fresh)}, в дайджест {len(picked)}", status)

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = os.environ.get("RADAR_STAMP", "").strip() or "latest"
    md = digest(picked, hours)
    (OUT / f"radar-{stamp}.md").write_text(md, encoding="utf-8")
    (OUT / "radar-latest.json").write_text(json.dumps(picked, ensure_ascii=False, indent=1),
                                           encoding="utf-8")

    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    cid = os.environ.get("TG_CHAT_ID", "").strip()
    if not cid:
        try:
            st = json.loads((ROOT / "voice" / ".inbox.json").read_text(encoding="utf-8"))
            cid = str(st.get("chat_id") or "")
        except (OSError, ValueError):
            cid = ""
    if token and cid and picked:
        head = [f"Радар за {hours} ч — {len(picked)} тем:", ""]
        for i, it in enumerate(picked[:8], 1):
            extra = f" ×{it['ratio']}" if it.get("ratio") else ""
            head.append(f"{i}. [{it['src']}{extra}] {it['title'][:90]}\n{it['discuss']}")
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      data={"chat_id": cid, "text": "\n\n".join(head)[:4000],
                            "disable_web_page_preview": "true"}, timeout=60)
        log("дайджест отправлен в Telegram", status)

    (OUT / "radar-status.md").write_text("\n".join(status), encoding="utf-8")
    print("\n".join(status), flush=True)
    return 0 if picked else 1


if __name__ == "__main__":
    sys.exit(main())
