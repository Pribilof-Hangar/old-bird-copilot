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
    def test_default_is_michael(self):
        self.assertTrue(speech.wants_michael(""))
        self.assertTrue(speech.wants_michael("Michael"))
        self.assertTrue(speech.wants_michael("Joe"))          # older settings: the built-in voice was Joe

    def test_windows_voices_are_not_michael(self):
        self.assertFalse(speech.wants_michael(speech.WINDOWS_DEFAULT))
        self.assertFalse(speech.wants_michael("Zira"))


class TestPlainPath(unittest.TestCase):
    """eSpeak can't open paths like C:\\Users\\José\\... (2026-10-17): it gets Windows' short names."""

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



class TestFixedPhrases(unittest.TestCase):
    def test_countdown_first_and_no_duplicates(self):
        from landing_callouts.config import Config
        phrases = speech.fixed_phrases(Config())
        self.assertEqual(phrases[:5], ["ten", "twenty", "thirty", "forty", "fifty"])
        self.assertEqual(len(phrases), len(set(phrases)))
        for p in ("minimums", "flaps full, three green", "check flaps", "ref", "plus ten", "flaps fifty",
                  "five hundred, stable", "forty knots", "top of descent", "vee one"):
            self.assertIn(p, phrases)

    def test_follows_the_style_and_custom_texts(self):
        from landing_callouts.config import Config
        phrases = speech.fixed_phrases(Config(callout_style="airbus", texts={"retard": "idle"}))
        self.assertIn("minimum", phrases)
        self.assertIn("idle", phrases)


@unittest.skipUnless(speech.michael_available(), "the voice folder isn't here")
class TestMichael(unittest.TestCase):
    """Renders real speech with the Kokoro model in the voice folder (a few seconds)."""

    @classmethod
    def setUpClass(cls):
        cls.voice = speech.KokoroVoice(2, 100)

    def test_renders_a_wav_and_caches_it(self):
        wav = self.voice.render("fifty")
        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertGreater(len(wav), 10_000)
        self.assertTrue(self.voice.is_cached("fifty"))
        self.assertIs(self.voice.render("fifty"), wav)

    def test_one_offs_are_not_cached(self):
        self.voice.render("one thousand, speed one zero eight", keep=False)
        self.assertFalse(self.voice.is_cached("one thousand, speed one zero eight"))


if __name__ == "__main__":
    unittest.main()
