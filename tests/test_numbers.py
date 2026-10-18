import unittest

from landing_callouts.numbers import digit_words, number_words


class TestNumbers(unittest.TestCase):
    def test_heights(self):
        self.assertEqual(number_words(1000), "one thousand")
        self.assertEqual(number_words(500), "five hundred")
        self.assertEqual(number_words(50), "fifty")
        self.assertEqual(number_words(40), "forty")
        self.assertEqual(number_words(10), "ten")
        self.assertEqual(number_words(2500), "two thousand five hundred")
        self.assertEqual(number_words(125), "one hundred twenty five")

    def test_speed_digits(self):
        self.assertEqual(digit_words(112), "one one two")
        self.assertEqual(digit_words(99.6), "one zero zero")
        self.assertEqual(digit_words(0), "zero")


if __name__ == "__main__":
    unittest.main()
