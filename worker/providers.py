"""TTS back-ends behind one interface, so engines can be compared by ear.

Each provider exposes `available()` and `synth(text, voice, path)`. Text arrives
in the master format with `+` before the stressed vowel; a provider that reads
that notation natively (`stress = True`) gets it verbatim, the rest get the
marks stripped.

Only Silero runs without a key — everything else activates once its secret is
present, so adding a key is the whole cost of auditioning another engine.
"""
import os, re, subprocess
from pathlib import Path

import requests

ACUTE = "́"


def strip_marks(text: str) -> str:
    return text.replace("+", "").replace(ACUTE, "")


def to_mp3(src: Path, dst: Path) -> bool:
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                        "-codec:a", "libmp3lame", "-b:a", "128k", str(dst)],
                       capture_output=True, text=True)
    return r.returncode == 0 and dst.exists()


class Provider:
    name = "?"
    stress = False       # reads `+` stress notation natively
    voices: list = []
    note = ""

    def available(self) -> bool:
        return False

    def prep(self, text: str) -> str:
        return text if self.stress else strip_marks(text)

    def synth(self, text: str, voice: str, path: Path, log) -> bool:
        raise NotImplementedError


class Silero(Provider):
    name = "silero"
    stress = True
    voices = ["eugene", "aidar", "baya", "kseniya", "xenia"]
    note = "локально, без ключа; ударение по '+' гарантировано"
    _model = None

    def available(self):
        return os.environ.get("ENABLE_SILERO", "1") != "0"

    def _load(self, log):
        if Silero._model is None:
            import torch
            torch.set_num_threads(max(1, os.cpu_count() or 1))
            log("silero: loading v4_ru")
            model, _ = torch.hub.load("snakers4/silero-models", "silero_tts",
                                      language="ru", speaker="v4_ru", trust_repo=True)
            model.to("cpu")
            Silero._model = model
        return Silero._model

    def synth(self, text, voice, path, log):
        try:
            model = self._load(log)
            wav = path.with_suffix(".wav")
            model.save_wav(text=self.prep(text), speaker=voice,
                           sample_rate=48000, audio_path=str(wav), put_accent=True, put_yo=True)
            return to_mp3(wav, path)
        except Exception as e:
            log(f"silero error: {e}")
            return False

    def synth_ssml(self, ssml, voice, path, log):
        """SSML pass — not every silero build exposes it, so failure is expected."""
        try:
            model = self._load(log)
            wav = path.with_suffix(".wav")
            model.save_wav(ssml_text=ssml, speaker=voice, sample_rate=48000,
                           audio_path=str(wav), put_accent=True, put_yo=True)
            return to_mp3(wav, path)
        except Exception as e:
            log(f"silero ssml error: {e}")
            return False


class ElevenLabs(Provider):
    name = "elevenlabs"
    note = "самый живой тембр; ударение не размечается, движок решает сам"

    def available(self):
        return bool(os.environ.get("ELEVEN_API_KEY", "").strip())

    def list_voices(self, log, limit=3):
        key = os.environ["ELEVEN_API_KEY"].strip()
        try:
            r = requests.get("https://api.elevenlabs.io/v1/voices",
                             headers={"xi-api-key": key}, timeout=30)
            if r.status_code != 200:
                log(f"elevenlabs /voices -> {r.status_code}: {r.text[:150]}")
                return []
            return [(v["voice_id"], v.get("name", v["voice_id"]))
                    for v in r.json().get("voices", [])[:limit]]
        except Exception as e:
            log(f"elevenlabs voices error: {e}")
            return []

    def synth(self, text, voice, path, log):
        key = os.environ["ELEVEN_API_KEY"].strip()
        try:
            r = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": key, "Content-Type": "application/json"},
                json={"text": self.prep(text), "model_id": "eleven_multilingual_v2"},
                timeout=120)
            if r.status_code == 200:
                path.write_bytes(r.content)
                return True
            log(f"elevenlabs -> {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log(f"elevenlabs error: {e}")
        return False


class Yandex(Provider):
    name = "yandex"
    stress = True
    voices = ["alena", "filipp", "jane", "ermil"]
    note = "лучший русский из платных; читает '+' нативно"

    def available(self):
        return bool(os.environ.get("YANDEX_API_KEY", "").strip())

    def synth(self, text, voice, path, log):
        key = os.environ["YANDEX_API_KEY"].strip()
        data = {"text": self.prep(text), "lang": "ru-RU", "voice": voice,
                "format": "mp3", "sampleRateHertz": "48000"}
        folder = os.environ.get("YANDEX_FOLDER_ID", "").strip()
        if folder:
            data["folderId"] = folder
        try:
            r = requests.post("https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize",
                              headers={"Authorization": f"Api-Key {key}"}, data=data, timeout=120)
            if r.status_code == 200:
                path.write_bytes(r.content)
                return True
            log(f"yandex -> {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log(f"yandex error: {e}")
        return False


class Azure(Provider):
    name = "azure"
    voices = ["ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"]
    note = "полноценный SSML, ударение задаётся через <phoneme>"

    def available(self):
        return bool(os.environ.get("AZURE_SPEECH_KEY", "").strip())

    def synth(self, text, voice, path, log):
        key = os.environ["AZURE_SPEECH_KEY"].strip()
        region = os.environ.get("AZURE_SPEECH_REGION", "westeurope").strip()
        body = (f"<speak version='1.0' xml:lang='ru-RU'><voice name='{voice}'>"
                f"{self.prep(text)}</voice></speak>")
        try:
            r = requests.post(
                f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
                headers={"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/ssml+xml",
                         "X-Microsoft-OutputFormat": "audio-48khz-96kbitrate-mono-mp3"},
                data=body.encode("utf-8"), timeout=120)
            if r.status_code == 200:
                path.write_bytes(r.content)
                return True
            log(f"azure -> {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log(f"azure error: {e}")
        return False


class OpenAI(Provider):
    name = "openai"
    voices = ["nova", "onyx"]
    note = "ровный тембр, но русское ударение не контролируется"

    def available(self):
        return bool(os.environ.get("OPENAI_API_KEY", "").strip())

    def synth(self, text, voice, path, log):
        key = os.environ["OPENAI_API_KEY"].strip()
        try:
            r = requests.post("https://api.openai.com/v1/audio/speech",
                              headers={"Authorization": f"Bearer {key}",
                                       "Content-Type": "application/json"},
                              json={"model": "gpt-4o-mini-tts", "voice": voice,
                                    "input": self.prep(text), "response_format": "mp3"},
                              timeout=120)
            if r.status_code == 200:
                path.write_bytes(r.content)
                return True
            log(f"openai -> {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log(f"openai error: {e}")
        return False


ALL = [Silero(), ElevenLabs(), Yandex(), Azure(), OpenAI()]
