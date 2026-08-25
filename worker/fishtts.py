"""Fish Audio TTS: discover popular RU stock voices + synthesize segments."""
import json, os, re, subprocess, sys
from pathlib import Path

import lexicon

import requests

API = "https://api.fish.audio"
KEY = os.environ.get("FISH_API_KEY", "")
HDR = {"Authorization": f"Bearer {KEY}"}

MALE_HINTS = ["male", "муж", "man ", "мужс", "dmit", "дмитр", "алекс", "иван", "серг", "андре", "narrator male"]
FEMALE_HINTS = ["female", "жен", "woman", "girl", "svet", "свет", "анна", "мария", "елен", "ольг", "narrator female"]


ACUTE = "\u0301"  # combining acute accent


def spoken(text: str) -> str:
    """`+` before a vowel becomes a combining acute — the only notation Fish honours.

    Probed in out/_stress: capitals are folded away before synthesis and a bare
    `+` is voiced as a pause, but the acute measurably changes the reading.
    """
    return re.sub(r"\+(.)", lambda m: m.group(1) + ACUTE, text)


def written(text: str) -> str:
    """Same line with stress notation removed, for subtitles and logs."""
    return text.replace("+", "").replace(ACUTE, "")


def log(msg, status_lines=None):
    print(msg, flush=True)
    if status_lines is not None:
        status_lines.append(str(msg))


def discover(outdir: Path, status):
    """List popular RU voice models; return dict with male/female candidates."""
    attempts = [
        {"language": "ru", "sort_by": "like_count", "page_size": 20},
        {"language": "ru", "sort_by": "task_count", "page_size": 20},
        {"language": "ru", "page_size": 20},
        {"title_language": "ru", "page_size": 20},
    ]
    items = []
    for params in attempts:
        try:
            r = requests.get(f"{API}/model", headers=HDR, params=params, timeout=30)
            log(f"discover {params} -> {r.status_code}", status)
            if r.status_code == 200:
                data = r.json()
                items = data.get("items") or data.get("data") or []
                if items:
                    break
        except Exception as e:
            log(f"discover error: {e}", status)
    (outdir / "voices.json").write_text(
        json.dumps([{k: it.get(k) for k in ("_id", "id", "title", "description", "languages", "like_count", "task_count", "tags")} for it in items],
                   ensure_ascii=False, indent=1))
    if not items:
        return None

    def pick(hints):
        for it in items:
            blob = (str(it.get("title", "")) + " " + str(it.get("description", "")) + " " + " ".join(map(str, it.get("tags") or []))).lower()
            if any(h in blob for h in hints):
                return it
        return None

    male, female = pick(MALE_HINTS), pick(FEMALE_HINTS)
    if male is None:
        male = items[0]
    if female is None:
        female = next((it for it in items if it is not male), items[0])
    res = {"male": male.get("_id") or male.get("id"), "male_title": male.get("title"),
           "female": female.get("_id") or female.get("id"), "female_title": female.get("title")}
    log(f"voices picked: {json.dumps(res, ensure_ascii=False)}", status)
    return res


def synth(text: str, ref_id: str, path: Path, status, normalize: bool = True) -> bool:
    # normalize=False keeps stress notation intact; Fish's text normaliser strips it.
    body = {"text": text, "reference_id": ref_id, "format": "mp3", "mp3_bitrate": 128,
            "normalize": normalize, "latency": "normal"}
    for extra_hdr in ({"model": "s1"}, {"model": "speech-1.6"}, {}):
        try:
            r = requests.post(f"{API}/v1/tts", headers={**HDR, **extra_hdr, "Content-Type": "application/json"},
                              json=body, timeout=120)
            if r.status_code == 200 and r.content[:3] != b'{"':
                path.write_bytes(r.content)
                return True
            log(f"tts {extra_hdr} -> {r.status_code}: {r.text[:200]}", status)
        except Exception as e:
            log(f"tts error {extra_hdr}: {e}", status)
    return False


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    return float(out.stdout.strip())


def tts_job(job: dict, outdir: Path, status) -> dict | None:
    voices = discover(outdir, status)
    if not voices:
        log("FAIL: could not list Fish Audio voices", status)
        return None
    for i, seg in enumerate(job["segments"]):
        bad = lexicon.bad_marks(seg["text"])
        if bad:
            log(f"WARN seg{i:02d}: ударение стоит не на гласной — {bad}", status)
    want = job.get("voice", "male")
    ref = voices.get(want) if want in ("male", "female") else want
    segs = []
    for i, seg in enumerate(job["segments"]):
        p = outdir / f"seg{i:02d}.mp3"
        if not synth(spoken(seg["text"]), ref, p, status):
            log(f"FAIL: tts segment {i}", status)
            return None
        # Subtitles get the clean line — stress marks belong in the audio, not on screen.
        segs.append({"i": i, "file": p.name, "dur": round(duration(p), 3), "text": written(seg["text"])})
        log(f"seg{i:02d}: {segs[-1]['dur']}s", status)
    meta = {"voice_used": ref, "voice_title": voices.get(f"{want}_title", ""), "voices": voices, "segments": segs}
    (outdir / "audio_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta
