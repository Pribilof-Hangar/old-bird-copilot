"""Turn numbers into words that SAPI (or a human) can say naturally."""

_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_words(n: int) -> str:
    """1000 -> "one thousand", 500 -> "five hundred", 2500 -> "two thousand five hundred"."""
    n = int(n)
    if n < 0:
        return "minus " + number_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + (" " + _ONES[ones] if ones else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return _ONES[hundreds] + " hundred" + (" " + number_words(rest) if rest else "")
    if n < 1_000_000:
        thousands, rest = divmod(n, 1000)
        return number_words(thousands) + " thousand" + (" " + number_words(rest) if rest else "")
    return digit_words(n)


def digit_words(n: int) -> str:
    """112 -> "one one two" (the way pilots read out speeds)."""
    n = int(round(n))
    prefix = "minus " if n < 0 else ""
    return prefix + " ".join(_ONES[int(c)] for c in str(abs(n)))
