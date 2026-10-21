"""Background speaker thread: the built-in Michael voice (Kokoro), a Windows SAPI voice, or a custom <key>.wav."""

from __future__ import annotations

import array
import io
import logging
import os
import queue
import sys
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path

from .engine import Callout
from .numbers import number_words

Log = Callable[[str], None]

# cfg.voice: "" = Michael when installed (else the Windows default), WINDOWS_DEFAULT = the Windows default voice,
# anything else = part of a Windows voice name ("Zira"). "Michael" (and the old "Joe") also mean the built-in voice.
MICHAEL = "Michael"
MICHAEL_LABEL = "Michael (natural, built in)"
BUILT_IN_NAMES = ("", "michael", "joe")
WINDOWS_DEFAULT = "windows"
KOKORO_VOICE = "am_michael"
# The model: the higher-quality fp16 build if present, else the compact int8 one.
KOKORO_MODELS = ("kokoro-v1.0.fp16.onnx", "kokoro-v1.0.int8.onnx")
KOKORO_VOICE_FILE = "michael.bin"   # just am_michael's style vectors (the full pack has 54 voices)
KOKORO_THREADS = 2       # CPU threads for the voice: leaves the rest to MSFS
SENTENCE_PAUSE_S = 0.5   # after a period ("...from the left. these are some crosswinds!")


def voice_dir() -> Path:
    """The `voice` folder next to the exe (or in the source checkout): kokoro\\ (model, Michael) and espeak\\."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "voice"
    return Path(__file__).resolve().parent.parent / "voice"


def _model_path() -> Path | None:
    d = voice_dir() / "kokoro"
    return next((d / m for m in KOKORO_MODELS if (d / m).is_file()), None)


def michael_available() -> bool:
    d = voice_dir()
    return (_model_path() is not None and (d / "kokoro" / KOKORO_VOICE_FILE).is_file()
            and (d / "espeak" / "espeak-ng.dll").is_file() and (d / "espeak" / "espeak-ng-data").is_dir())


def wants_michael(voice: str) -> bool:
    return voice.strip().lower() in BUILT_IN_NAMES


def plain_path(path: Path | str) -> str | None:
    """The path in plain letters for eSpeak, which can't open a path with letters like the é in a user
    folder named José (it reads paths as ANSI): the path itself if it's plain, else Windows' 8.3 short name
    (JOS~1). None if there is no plain form (short names switched off on that drive)."""
    path = str(path)
    if path.isascii():
        return path
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf)) and buf.value.isascii():
            return buf.value
    except Exception:  # noqa: BLE001 - not Windows
        pass
    return None


def _keep_espeak_path_as_given() -> None:
    """phonemizer resolve()s the eSpeak data folder, which turns the short JOS~1 form back into the long
    C:\\Users\\José\\... one that eSpeak can't open. Use the folder exactly as given (tested with phonemizer 3.4)."""
    import pathlib

    from phonemizer.backend.espeak.wrapper import EspeakWrapper

    def data_path(self):
        if self._ESPEAK_DATA_PATH:
            self._data_path = pathlib.Path(self._ESPEAK_DATA_PATH)
        elif self._data_path is None and hasattr(self, "_espeak"):
            self._fetch_version_and_path()
        return self._data_path

    EspeakWrapper.data_path = property(data_path)


def fixed_phrases(cfg) -> list[str]:
    """Callouts whose words never change, rendered ahead of time so they play instantly (most urgent first:
    the countdown can't wait for the voice). Calls with live numbers (speeds, wind, landing rate) aren't here."""
    t = cfg.text
    heights = sorted(cfg.heights)
    out = [t(str(h), number_words(h)) for h in heights if h <= 100]
    out += [t("retard"), t("minimums"), t("approaching_minimums"), t("sink_rate"), t("too_low_gear"),
            t("landing_check", "flaps full, three green"), t("check_flaps", "check flaps"),
            t("check_gear", "check gear"), t("speed_ref", "ref"), t("flap_speed"), t("gear_speed"),
            f"{t('500', number_words(500))}, {t('stable', 'stable')}"]
    out += [t(f"speed_plus_{k}", f"plus {number_words(k)}") for k in (10, 15, 20, 30, 40, 50)]
    out += [t(f"speed_minus_{k}", f"minus {number_words(k)}") for k in (5, 10)]
    out += [t(str(h), number_words(h)) for h in heights if h > 100]
    out += [t("v1", "vee one"), t("rotate", "rotate"), t("positive_rate", "positive rate"),
            t("v2", "vee two"), t("eighty_knots", "eighty knots"), t("takeoff_flaps", "check flaps")]
    out += [t("flaps_0", "flaps up")] + [t(f"flaps_{d}", f"flaps {number_words(d)}") for d in range(5, 55, 5)]
    out += [t(f"rollout_{k}", f"{number_words(k)} knots") for k in sorted({40, *cfg.rollout_callouts_kt})]
    out += [t("top_of_descent", "top of descent"), t("request_descent", "request descent"),
            t("high_on_profile", "high on profile"), t("level_off", "five hundred to level off"),
            t("wind_calm", "wind calm")]
    seen: set[str] = set()
    return [p for p in out if p and not (p in seen or seen.add(p))]


class KokoroVoice:
    """Michael: the Kokoro neural voice (am_michael), run in-process on the CPU with a phrase cache."""

    def __init__(self, rate: int, volume: int):
        import numpy as np
        import onnxruntime as ort
        from kokoro_onnx import Kokoro
        from kokoro_onnx.config import EspeakConfig

        logging.getLogger("phonemizer").setLevel(logging.ERROR)
        self._np = np
        self.volume = volume
        # SAPI-style rate -10..10 -> Kokoro speed (1.0 = natural). The default rate 2 is Michael's own pace.
        self.speed = max(0.7, min(1.4, 1.0 + 0.05 * (rate - 2)))
        d = voice_dir()
        model = _model_path()
        # Only the folder gets the short form; the names inside stay as eSpeak expects them.
        espeak_dir = plain_path(d / "espeak")
        lib = espeak_dir and os.path.join(espeak_dir, "espeak-ng.dll")
        data = espeak_dir and os.path.join(espeak_dir, "espeak-ng-data")
        _keep_espeak_path_as_given()
        if model is None or not lib or not data:
            raise RuntimeError(f"the voice folder's path has characters the voice can't read ({d}). Moving the "
                               "Old Bird Copilot folder to e.g. C:\\Old Bird Copilot fixes it")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = KOKORO_THREADS
        opts.inter_op_num_threads = 1
        session = ort.InferenceSession(str(model), opts, providers=["CPUExecutionProvider"])
        self._kokoro = Kokoro.from_session(session, str(d / "kokoro" / KOKORO_VOICE_FILE),
                                           espeak_config=EspeakConfig(lib_path=lib, data_path=data))
        self._cache: dict[str, bytes] = {}
        self.render("ready", keep=False)   # loads everything now, so the first real callout isn't late

    def render(self, text: str, keep: bool = True) -> bytes:
        """The spoken text as an in-memory 16-bit .wav, volume applied. Kept in the cache when `keep`."""
        text = " ".join(text.split())
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        np = self._np
        samples, sr = self._kokoro.create(text, voice=KOKORO_VOICE, speed=self.speed, lang="en-us",
                                          sentence_pause=SENTENCE_PAUSE_S)
        pcm = (np.clip(samples * (max(0, self.volume) / 100), -1.0, 1.0) * 32767).astype("<i2")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm.tobytes())
        wav = buf.getvalue()
        if keep:
            self._cache[text] = wav
        return wav

    def is_cached(self, text: str) -> bool:
        return " ".join(text.split()) in self._cache

    def speak(self, text: str) -> None:
        import winsound
        winsound.PlaySound(self.render(text), winsound.SND_MEMORY)

    def close(self) -> None:
        self._cache.clear()


def _scaled(wav_bytes: bytes, volume: int) -> bytes:
    """16-bit .wav with its samples scaled to volume (0..100)."""
    if volume >= 100:
        return wav_bytes
    with wave.open(io.BytesIO(wav_bytes)) as w:
        params, frames = w.getparams(), w.readframes(w.getnframes())
    samples = array.array("h", frames)
    k = max(0, volume) / 100
    for i, v in enumerate(samples):
        samples[i] = int(v * k)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setparams(params)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


class PrintSpeaker:
    """Fallback that just prints what would be said. Used off Windows and in tests."""

    def __init__(self, log: Log = print):
        self._t0 = time.monotonic()
        self._log = log

    def start(self) -> "PrintSpeaker":
        return self

    def say(self, c: Callout) -> None:
        self._log(f"[SAY {time.monotonic() - self._t0:7.2f}s] {c.text}")

    def wait_idle(self, timeout: float = 10) -> None:
        pass

    def stop(self) -> None:
        pass


class Speaker:
    """Speaks callouts one at a time on its own thread, dropping any that got too old."""

    def __init__(self, voice: str = "", rate: int = 2, volume: int = 100,
                 sounds_dir: Path | None = None, log: Log = print, phrases: list[str] | None = None):
        self.voice_name = voice
        self.phrases = list(phrases or [])   # rendered ahead of time while idle (built-in voice only)
        self.rate = rate
        self.volume = volume
        self.sounds_dir = Path(sounds_dir) if sounds_dir else None
        self._log = log
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="speaker", daemon=True)
        self._ready = threading.Event()
        self.error: Exception | None = None

    def start(self) -> "Speaker":
        self._thread.start()
        self._ready.wait(10)
        if self.error:
            raise RuntimeError(f"Could not start the Windows voice: {self.error}")
        return self

    def say(self, c: Callout) -> None:
        self._q.put((time.monotonic(), c))

    def wait_idle(self, timeout: float = 10) -> None:
        """Block until everything queued has been spoken (or dropped)."""
        deadline = time.monotonic() + timeout
        while self._q.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.05)

    def stop(self) -> None:
        self._q.put(None)
        self._thread.join(timeout=5)

    def _run(self) -> None:
        try:
            import pythoncom
            import winsound
            import win32com.client

            pythoncom.CoInitialize()
            sp = win32com.client.Dispatch("SAPI.SpVoice")   # also the fallback if Michael can't start or stops
            if self.voice_name and not wants_michael(self.voice_name) and self.voice_name != WINDOWS_DEFAULT:
                token = find_voice(sp, self.voice_name)
                if token is None:
                    self._log(f"Voice '{self.voice_name}' not found, using the default voice.")
                else:
                    sp.Voice = token
                del token
            sp.Rate = self.rate
            sp.Volume = self.volume
        except Exception as e:  # noqa: BLE001 - report any import/COM failure to the caller
            self.error = e
            self._ready.set()
            return
        self._ready.set()   # Michael loads below (~2 s) without holding up the window; callouts wait in the queue
        voice = None
        if wants_michael(self.voice_name) and michael_available():
            try:
                voice = KokoroVoice(self.rate, self.volume)
            except Exception as e:  # noqa: BLE001 - fall back to the Windows voice
                self._log(f"Couldn't start the Michael voice ({e}). Using the Windows voice.")
        elif wants_michael(self.voice_name) and self.voice_name.strip():
            self._log("The Michael voice isn't installed (no voice folder next to the app). Using the Windows voice.")
        todo = list(self.phrases) if voice is not None else []

        try:
            while True:
                if todo:
                    # Idle: render the next fixed callout ahead of time, so it plays instantly when it comes.
                    try:
                        item = self._q.get_nowait()
                    except queue.Empty:
                        phrase = todo.pop(0)
                        try:
                            if not voice.is_cached(phrase):
                                voice.render(phrase)
                        except Exception:  # noqa: BLE001 - it'll be rendered on demand instead
                            pass
                        continue
                else:
                    item = self._q.get()
                try:
                    if item is None:
                        break
                    created, c = item
                    if time.monotonic() - created > c.max_age_s:
                        continue  # stale: better silent than late
                    wav = self.sounds_dir / f"{c.key}.wav" if self.sounds_dir else None
                    try:
                        if wav is not None and wav.is_file():
                            winsound.PlaySound(str(wav), winsound.SND_FILENAME)
                        elif voice is not None:
                            try:
                                voice.speak(c.text)
                            except Exception as e:  # noqa: BLE001 - Michael failed: the Windows voice takes over
                                self._log(f"The Michael voice stopped ({e}). Using the Windows voice.")
                                voice.close()
                                voice, todo = None, []
                                sp.Speak(c.text, 0)
                        else:
                            sp.Speak(c.text, 0)
                    except Exception as e:  # noqa: BLE001 - one bad callout shouldn't kill the thread
                        self._log(f"Speech error: {e}")
                finally:
                    self._q.task_done()
        finally:
            if voice is not None:
                voice.close()
            del sp  # release COM objects before uninitializing
            pythoncom.CoUninitialize()


def find_voice(sp, name: str):
    voices = sp.GetVoices()
    for i in range(voices.Count):
        token = voices.Item(i)
        if name.lower() in token.GetDescription().lower():
            return token
    return None


def list_voices() -> list[str]:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    sp = win32com.client.Dispatch("SAPI.SpVoice")
    voices = sp.GetVoices()
    names = [voices.Item(i).GetDescription() for i in range(voices.Count)]
    del sp, voices  # release COM objects before uninitializing
    pythoncom.CoUninitialize()
    return names


def make_speaker(cfg, force_print: bool = False, log: Log = print):
    """A SAPI Speaker on Windows, else a PrintSpeaker."""
    if not force_print:
        try:
            return Speaker(cfg.voice, cfg.rate, cfg.volume, cfg.sounds_path, log=log,
                           phrases=fixed_phrases(cfg)).start()
        except RuntimeError as e:
            log(f"{e}. Printing callouts instead of speaking them.")
    return PrintSpeaker(log=log).start()
