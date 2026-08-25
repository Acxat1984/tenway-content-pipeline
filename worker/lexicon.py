"""Latin terms rewritten in Russian letters, with stress, before synthesis.

A Russian TTS model has no Latin letters in its alphabet: left alone, "ChatGPT"
is swallowed or spelled out wrong. Every term the scripts use is therefore
respelled here the way it should be read aloud, stress marks included.

Anything Latin that survives the pass is reported by `transcribe` so it can be
added here instead of quietly reaching the microphone.
"""
import re

# Spelling of English letters, for abbreviations not worth a dictionary entry.
LETTERS = {
    "a": "эй", "b": "би", "c": "си", "d": "ди", "e": "и", "f": "эф", "g": "джи",
    "h": "эйч", "i": "ай", "j": "джей", "k": "кей", "l": "эл", "m": "эм", "n": "эн",
    "o": "о+у", "p": "пи", "q": "кью", "r": "ар", "s": "эс", "t": "ти", "u": "ю",
    "v": "ви", "w": "д+абл-ю", "x": "икс", "y": "у+ай", "z": "зед",
}

LEXICON = {
    # AI-сервисы
    "chatgpt": "чат джи-пи-т+и",
    "gpt": "джи-пи-т+и",
    "claude": "кл+од",
    "openai": "оуп+ен эй-а+й",
    "anthropic": "антр+опик",
    "gemini": "джем+ини",
    "midjourney": "мидж+орни",
    "copilot": "к+опайлот",
    "deepseek": "дипс+ик",
    "perplexity": "перпл+ексити",
    "grok": "грок",
    "sora": "с+ора",
    "runway": "р+анвей",
    "suno": "с+уно",
    "llama": "лл+ама",
    "qwen": "квен",
    "yandexgpt": "яндекс джи-пи-т+и",
    "gigachat": "гигач+ат",
    # площадки и софт
    "telegram": "телегр+ам",
    "threads": "тредс",
    "instagram": "инстагр+ам",
    "youtube": "уть+юб",
    "tiktok": "тикт+ок",
    "google": "гугл",
    "notion": "н+оушен",
    "excel": "экс+ель",
    "word": "ворд",
    "figma": "ф+игма",
    "canva": "к+анва",
    "photoshop": "фотош+оп",
    "zoom": "зум",
    "slack": "слэк",
    "github": "гитх+аб",
    # термины
    "ai": "эй-а+й",
    "api": "эй-пи-а+й",
    "prompt": "промпт",
    "promt": "промпт",
    "it": "ай-т+и",
    "crm": "си-эр-+эм",
    "smm": "эс-эм-+эм",
    "seo": "сь+ео",
    "hr": "эйч-+ар",
    "vpn": "ви-пи-+эн",
    "pdf": "пи-ди-+эф",
    "url": "ю-эр-+эл",
    "b2b": "би-ту-б+и",
    "b2c": "би-ту-с+и",
    "saas": "саас",
    "ok": "о+кей",
}

LATIN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-+][A-Za-z0-9]+)*")


def spell(word: str) -> str:
    """Read an unknown token letter by letter, the way an abbreviation is said."""
    return "-".join(LETTERS.get(c.lower(), c) for c in word if c.isalpha())


def transcribe(text: str):
    """Return (respelled text, list of Latin words that had no entry).

    Digits attached to a term are kept — "GPT-4" reads as the term plus "4",
    which Russian TTS handles on its own.
    """
    unknown = []

    def repl(m):
        word = m.group(0)
        head = re.match(r"[A-Za-z]+", word).group(0)
        # "GPT-4": the hyphen would be read as part of the spelling, so space it out.
        tail = word[len(head):].replace("-", " ")
        key = head.lower()
        if key in LEXICON:
            return LEXICON[key] + tail
        # All-caps short token: almost certainly an abbreviation.
        if head.isupper() and len(head) <= 5:
            return spell(head) + tail
        unknown.append(word)
        return spell(head) + tail

    return LATIN.sub(repl, text), unknown
