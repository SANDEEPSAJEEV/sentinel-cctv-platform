"""Matching a queried registration against noisy OCR reads.

The registration handed over on the day will be exact. The reads it has to be
matched against will not be: on this grid a plate is 30-130 px wide, and the
characters that survive are the ones OCR confuses anyway — O/0, I/1, B/8, S/5,
Z/2, G/6.

So an exact-match query answers "no sightings" for a vehicle that was seen four
times. Instead, score every candidate read against the query with a distance
that knows which substitutions are cheap, and report the score alongside the
sighting. An operator can then see that a leg rests on a fuzzy match rather
than being told a story with the seams hidden.
"""

from __future__ import annotations

from dataclasses import dataclass

from anpr.plates import clean, is_valid, repair

# Substitutions OCR actually makes on plates, and what they cost. 0.0 would say
# the two characters are interchangeable; these are cheap but not free, so an
# exact read always outranks a repaired one.
CONFUSION_COST = 0.25
CONFUSABLE = [
    {"0", "O", "Q", "D"},
    {"1", "I", "L"},
    {"2", "Z"},
    {"5", "S"},
    {"6", "G"},
    {"8", "B"},
    {"4", "A"},
    {"7", "T"},
]


def _sub_cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    for group in CONFUSABLE:
        if a in group and b in group:
            return CONFUSION_COST
    return 1.0


def distance(a: str, b: str) -> float:
    """Levenshtein distance where OCR-confusable substitutions cost less."""
    a, b = clean(a), clean(b)
    if not a or not b:
        return float(max(len(a), len(b)))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [float(i)]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1.0,            # deletion
                           cur[j - 1] + 1.0,         # insertion
                           prev[j - 1] + _sub_cost(ca, cb)))
        prev = cur
    return prev[-1]


@dataclass(frozen=True)
class Match:
    query: str
    read: str
    score: float          # 1.0 exact, lower is weaker
    exact: bool
    within_tolerance: bool

    @property
    def kind(self) -> str:
        if self.exact:
            return "exact"
        return "fuzzy" if self.within_tolerance else "rejected"


def match(query: str, read: str, *, tolerance: float = 1.0) -> Match:
    """Score one read against the queried plate.

    tolerance is in the same units as `distance`: 1.0 admits one ordinary
    character being wrong, or up to four OCR-confusable ones.
    """
    q, r = clean(query), clean(read)
    if not r:
        return Match(q, r, 0.0, False, False)
    exact = q == r or clean(repair(r)) == q
    d = 0.0 if exact else distance(q, r)
    # Normalise by length so a one-character error on a 10-character plate
    # scores better than the same error on a 4-character fragment.
    score = max(0.0, 1.0 - d / max(len(q), 1))
    return Match(q, r, round(score, 3), exact, d <= tolerance)


def candidates(query: str, *, max_variants: int = 64) -> list[str]:
    """Plausible spellings of the query, for a cheap SQL prefilter.

    Only substitutions that keep the plate a valid Indian registration are
    produced; the full scoring still runs afterwards.
    """
    q = clean(query)
    out = {q}
    for i, ch in enumerate(q):
        for group in CONFUSABLE:
            if ch not in group:
                continue
            for alt in group:
                if alt == ch:
                    continue
                variant = q[:i] + alt + q[i + 1:]
                if is_valid(variant) or not is_valid(q):
                    out.add(variant)
                if len(out) >= max_variants:
                    return sorted(out)
    return sorted(out)
