import array
import io
import unittest
import wave

from landing_callouts import speech


def tiny_wav(samples):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(array.array("h", samples).tobytes())
    return buf.getvalue()


def samples_of(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes)) as w:
        return list(array.array("h", w.readframes(w.getnframes())))


class TestVoiceChoice(unittest.TestCase):
    def test_default_is_joe(self):
        self.assertTrue(speech.wants_joe(""))
        self.assertTrue(speech.wants_joe("Joe"))

    def test_windows_voices_are_not_joe(self):
        self.assertFalse(speech.wants_joe(speech.WINDOWS_DEFAULT))
        self.assertFalse(speech.wants_joe("Zira"))


class TestPlainPath(unittest.TestCase):
    """piper.exe can't open paths like C:\\Users\\José\\... (2026-10-17): it gets Windows' short names."""

    def test_plain_paths_pass_through(self):
        self.assertEqual(speech.plain_path(r"C:\Old Bird Copilot\voice"), r"C:\Old Bird Copilot\voice")

    def test_special_characters_get_the_short_name(self):
        import os
        import tempfile
        d = tempfile.mkdtemp(suffix=" Jösé Ñ")
        try:
            plain = speech.plain_path(d)
            if plain is not None:              # None only where short names are switched off
                self.assertTrue(plain.isascii())
                self.assertTrue(os.path.isdir(plain))
        finally:
            os.rmdir(d)


class TestVolume(unittest.TestCase):
    def test_full_volume_unchanged(self):
        wav = tiny_wav([1000, -2000, 3000])
        self.assertIs(speech._scaled(wav, 100), wav)

    def test_half_volume(self):
        self.assertEqual(samples_of(speech._scaled(tiny_wav([1000, -2000, 3000]), 50)), [500, -1000, 1500])

    def test_muted(self):
        self.assertEqual(samples_of(speech._scaled(tiny_wav([1000, -2000]), 0)), [0, 0])



class TestStaleTempDirs(unittest.TestCase):
    def test_removes_only_empty_leftovers(self):
        import os
        import tempfile
        from pathlib import Path
        base = Path(tempfile.mkdtemp())
        empty = base / "oldbird-voice-old1"
        busy = base / "oldbird-voice-busy"
        other = base / "something-else"
        for d in (empty, busy, other):
            d.mkdir()
        (busy / "x.wav").write_bytes(b"")
        old = tempfile.tempdir
        tempfile.tempdir = str(base)
        try:
            speech.remove_stale_temp_dirs()
        finally:
            tempfile.tempdir = old
        self.assertFalse(empty.exists())
        self.assertTrue(busy.exists())
        self.assertTrue(other.exists())
        os.remove(busy / "x.wav")


if __name__ == "__main__":
    unittest.main()
