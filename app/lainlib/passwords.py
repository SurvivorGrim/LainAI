"""Passphrase strength estimate and diceware generator."""
import math
import os
import re
import secrets

MIN_BITS = 60
_SEQS = ("abcdefghijklmnopqrstuvwxyz", "0123456789", "qwertyuiop", "asdfghjkl", "zxcvbnm")
_LEET = str.maketrans("0134578@$!", "oleastbasi")


def strength(pw, common_words_path):
    """Return (bits, reason). Strips what attackers try first - common words (incl. l33t),
    years, keyboard/alphabet runs, repeated characters - and counts only what's left."""
    if len(pw) < 12:
        return 0, "use at least 12 characters"
    pool = (26 if re.search(r"[a-z]", pw) else 0) + (26 if re.search(r"[A-Z]", pw) else 0) \
        + (10 if re.search(r"[0-9]", pw) else 0) + (33 if re.search(r"[^a-zA-Z0-9]", pw) else 0)
    r = pw.lower()
    r = re.sub(r"(19|20)[0-9]{2}", "", r)
    r = re.sub(r"(.)\1{2,}", r"\1", r)
    for seq in _SEQS:
        for s in (seq, seq[::-1]):
            for ln in range(len(s), 2, -1):
                for i in range(len(s) - ln + 1):
                    r = r.replace(s[i:i + ln], "")
    r = r.translate(_LEET)
    if os.path.exists(common_words_path):
        words = sorted((w.strip() for w in open(common_words_path, encoding="utf-8") if w.strip()), key=len, reverse=True)
        for w in words:
            r = r.replace(w, "")
    bits = int(len(r) * math.log2(pool)) if pool else 0
    reason = ""
    if bits < MIN_BITS:
        reason = ("avoid common words, names, years and keyboard patterns" if len(pw) - len(r) >= 4
                  else "make it longer, or mix in more kinds of characters")
    return bits, reason


def generate(wordlist_path, n=6):
    """n random words from the EFF diceware list (6 words ~ 77 bits)."""
    words = [w.strip() for w in open(wordlist_path, encoding="utf-8") if w.strip()]
    return "-".join(secrets.choice(words) for _ in range(n))
