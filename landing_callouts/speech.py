"""Background speaker thread: the built-in Joe voice (Piper), a Windows SAPI voice, or a custom <key>.wav."""

from __future__ import annotations

import array
import io
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path

from .engine import Callout

Log = Callable[[str], None]

# cfg.voice: "" = Joe when installed (else the Windows default), WINDOWS_DEFAULT = the Windows default voice,
# anything else = part of a Windows voice name ("Zira").
JOE = "Joe"
JOE_LABEL = "Joe (natural, built in)"
WINDOWS_DEFAULT = "windows"
JOE_MODEL = "en_US-joe-medium.onnx"
SENTENCE_PAUSE_S = 0.5   # after a period ("...from the left. these are some crosswinds!"); Piper's default 0.2 felt rushed


def voice_dir() -> Path:
    """The `voice` folder next to the exe (or in the source checkout) holding piper\\piper.exe and Joe's model."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "voice"
    return Path(__file__).resolve().parent.parent / "voice"


def joe_available() -> bool:
    d = voice_dir()
    return (d / "piper" / "piper.exe").is_file() and (d / JOE_MODEL).is_file() and (d / f"{JOE_MODEL}.json").is_file()


def wants_joe(voice: str) -> bool:
    return voice.strip().lower() in ("", JOE.lower())


def plain_path(path: Path | str) -> str | None:
    """The path in plain letters for piper.exe, which can't open a path with letters like the é in a user
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


def remove_stale_temp_dirs() -> None:
    """Remove empty Joe temp folders left behind when the app was killed or Windows shut down with it open.
    Called once at startup, before any voice starts (only one copy of the app runs at a time)."""
    for parent in {tempfile.gettempdir(), os.environ.get("PUBLIC"), os.environ.get("PROGRAMDATA")}:
        if not parent:
            continue
        try:
            for d in Path(parent).glob("oldbird-voice-*"):
                try:
                    d.rmdir()          # only succeeds when empty
                except OSError:
                    pass
        except OSError:
            pass


def _plain_temp_dir() -> tuple[str, str]:
    """(real path, plain path) of a new folder for Joe's .wav files: in TEMP, or somewhere with a plain name."""
    for parent in (tempfile.gettempdir(), os.environ.get("PUBLIC"), os.environ.get("PROGRAMDATA")):
        if not parent:
            continue
        try:
            real = tempfile.mkdtemp(prefix="oldbird-voice-", dir=parent)
        except OSError:
            continue
        plain = plain_path(real)
        if plain:
            return real, plain
        os.rmdir(real)
    raise RuntimeError("no folder with a plain name for the voice's temporary files")


class PiperVoice:
    """Joe: one piper.exe kept running, fed a line of text at a time; it answers with the path of a .wav."""

    def __init__(self, rate: int, volume: int):
        self.volume = volume
        d = voice_dir()
        # only the folders get the short form: piper finds Joe's settings by adding ".json" to the model's name
        piper_dir, model_dir = plain_path(d / "piper"), plain_path(d)
        exe = piper_dir and os.path.join(piper_dir, "piper.exe")
        model = model_dir and os.path.join(model_dir, JOE_MODEL)
        if not exe or not model:
            raise RuntimeError(f"the voice folder's path has characters the voice can't read ({d}). Moving the "
                               "Old Bird Copilot folder to e.g. C:\\Old Bird Copilot fixes it")
        self._out, out = _plain_temp_dir()
        # SAPI-style rate -10..10 -> Piper's length scale (smaller = faster). The default rate 2 is Joe's own pace.
        length_scale = max(0.5, min(1.6, 1.0 - 0.04 * (rate - 2)))
        self._p = subprocess.Popen(
            [exe, "--model", model, "--output_dir", out,
             "--length_scale", f"{length_scale:.2f}", "--sentence_silence", f"{SENTENCE_PAUSE_S}", "--quiet"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.render("ready")   # loads the model now, so the first real callout isn't late

    def render(self, text: str) -> bytes:
        """The spoken text as an in-memory .wav, volume applied."""
        if self._p.poll() is not None:
            raise RuntimeError("the Joe voice stopped")
        self._p.stdin.write(" ".join(text.split()) + "\n")
        self._p.stdin.flush()
        path = self._p.stdout.readline().strip()
        if not path:
            raise RuntimeError("the Joe voice stopped")
        try:
            data = Path(path).read_bytes()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        return _scaled(data, self.volume)

    def speak(self, text: str) -> None:
        import winsound
        winsound.PlaySound(self.render(text), winsound.SND_MEMORY)

    def close(self) -> None:
        try:
            self._p.stdin.close()
            self._p.wait(3)
        except Exception:  # noqa: BLE001 - shutting down anyway
            self._p.kill()
        try:
            os.rmdir(self._out)
        except OSError:
            pass


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
                 sounds_dir: Path | None = None, log: Log = print):
        self.voice_name = voice
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
            sp = win32com.client.Dispatch("SAPI.SpVoice")   # also the fallback if Joe can't start or stops
            if self.voice_name and not wants_joe(self.voice_name) and self.voice_name != WINDOWS_DEFAULT:
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
        self._ready.set()   # Joe loads below (~2 s) without holding up the window; callouts wait in the queue
        joe = None
        if wants_joe(self.voice_name) and joe_available():
            try:
                joe = PiperVoice(self.rate, self.volume)
            except Exception as e:  # noqa: BLE001 - fall back to the Windows voice
                self._log(f"Couldn't start the Joe voice ({e}). Using the Windows voice.")
        elif self.voice_name.strip().lower() == JOE.lower():
            self._log("The Joe voice isn't installed (no voice folder next to the app). Using the Windows voice.")
        self._ready.set()

        try:
            while True:
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
                        elif joe is not None:
                            try:
                                joe.speak(c.text)
                            except Exception as e:  # noqa: BLE001 - Joe died: the Windows voice takes over
                                self._log(f"The Joe voice stopped ({e}). Using the Windows voice.")
                                joe.close()
                                joe = None
                                sp.Speak(c.text, 0)
                        else:
                            sp.Speak(c.text, 0)
                    except Exception as e:  # noqa: BLE001 - one bad callout shouldn't kill the thread
                        self._log(f"Speech error: {e}")
                finally:
                    self._q.task_done()
        finally:
            if joe is not None:
                joe.close()
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
            return Speaker(cfg.voice, cfg.rate, cfg.volume, cfg.sounds_path, log=log).start()
        except RuntimeError as e:
            log(f"{e}. Printing callouts instead of speaking them.")
    return PrintSpeaker(log=log).start()
