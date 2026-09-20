"""Indian registration marks: parsing, OCR-confusion repair, and voting.

Two jobs.

1. Know what a valid Indian plate looks like, so a read can be judged rather
   than merely returned. Formats (Central Motor Vehicles Rules, and the BH
   series introduced in 2021):

       GJ01AB1234     state + RTO + series + number   (current standard)
       GJ1A1234       older short series
       22BH1234AA     Bharat series: year + BH + number + series
       DL1CAA1234     three-letter series (large RTOs)

2. Vote across a track. Never OCR frame by frame and keep the last answer:
   read the plate on every frame the vehicle is visible and combine. Ten poor
   reads of the same plate usually agree on most characters, and the ones they
   disagree on are exactly the ones a single frame would get wrong.

The vote is per character position, weighted by OCR confidence, and it is
format-aware: position 3 of GJ01AB1234 must be a digit, so an 'O' read there
is counted as a '0' rather than thrown away. That repair is only applied where
the format says so — never blindly across the string.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

# Two-letter state/UT codes. A read that does not start with one of these is
# either a misread or not an Indian plate; either way it is not a route fix.
STATE_CODES = {
    "AN", "AP", "AR", "AS", "BR", "CG", "CH", "DD", "DL", "DN", "GA", "GJ",
    "HP", "HR", "JH", "JK", "KA", "KL", "LA", "LD", "MH", "ML", "MN", "MP",
    "MZ", "NL", "OD", "OR", "PB", "PY", "RJ", "SK", "TN", "TR", "TS", "UK",
    "UP", "WB",
}

STANDARD_RE = re.compile(r"^([A-Z]{2})(\d{1,2})([A-Z]{1,3})(\d{1,4})$")
BH_RE = re.compile(r"^(\d{2})(BH)(\d{4})([A-Z]{1,2})$")

# Shapes that OCR confuses, in both directions. Applied per position, and only
# when the format tells us which class that position must be.
TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2",
            "S": "5", "B": "8", "G": "6", "T": "7", "A": "4"}
TO_LETTER = {v: k for k, v in reversed(list(TO_DIGIT.items()))}


def clean(raw: str) -> str:
    """Strip everything that cannot be part of a registration mark."""
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


@dataclass(frozen=True)
class PlateFormat:
    name: str
    mask: str          # one character per position: 'A' letter, '9' digit
    state_ok: bool


def classify(plate: str) -> PlateFormat | None:
    """Return the format of a cleaned plate string, or None if it is not one."""
    m = STANDARD_RE.match(plate)
    if m:
        state, rto, series, number = m.groups()
        return PlateFormat(
            name="standard",
            mask="A" * 2 + "9" * len(rto) + "A" * len(series) + "9" * len(number),
            state_ok=state in STATE_CODES,
        )
    m = BH_RE.match(plate)
    if m:
        year, _, number, series = m.groups()
        return PlateFormat(name="bh", mask="99AA" + "9" * len(number) + "A" * len(series),
                           state_ok=True)
    return None


def is_valid(plate: str) -> bool:
    fmt = classify(plate)
    return bool(fmt and fmt.state_ok)


def repair(plate: str) -> str:
    """Try the character-class swaps that make an almost-valid read valid.

    Only swaps characters whose class is wrong for a candidate mask, and only
    accepts the result if it becomes a valid plate. Anything else is returned
    unchanged — a repair that cannot be justified by the format is a guess.
    """
    plate = clean(plate)
    if is_valid(plate):
        return plate
    for mask in _candidate_masks(len(plate)):
        out = []
        for ch, kind in zip(plate, mask):
            if kind == "9" and not ch.isdigit():
                out.append(TO_DIGIT.get(ch, ch))
            elif kind == "A" and not ch.isalpha():
                out.append(TO_LETTER.get(ch, ch))
            else:
                out.append(ch)
        candidate = "".join(out)
        if is_valid(candidate):
            return candidate
    return plate


def _candidate_masks(length: int) -> list[str]:
    """Masks worth trying for a string of this length, commonest first."""
    masks = []
    for rto in (2, 1):
        for series in (2, 1, 3):
            for number in (4, 3, 2, 1):
                if 2 + rto + series + number == length:
                    masks.append("A" * 2 + "9" * rto + "A" * series + "9" * number)
    for number in (4,):
        for series in (2, 1):
            if 4 + number + series == length:
                masks.append("99AA" + "9" * number + "A" * series)
    return masks


@dataclass
class PlateRead:
    """One OCR result for one frame of one track."""
    text: str
    confidence: float
    pts_ms: float
    plate_width_px: float = 0.0
    plate_height_px: float = 0.0
    char_confidences: tuple[float, ...] = ()


@dataclass
class VoteResult:
    plate: str
    confidence: float
    reads: int
    agreeing_reads: int
    valid_format: bool
    repaired: bool
    best_pts_ms: float
    best_width_px: float
    char_agreement: tuple[float, ...] = field(default_factory=tuple)

    # Mean per-character agreement below this means the frames disagreed about
    # most of the string, and the winner won by a whisker.
    MIN_AGREEMENT = 0.6

    @property
    def is_usable(self) -> bool:
        """A read worth putting in front of an operator.

        Three conditions: the format is a real Indian registration, more than
        one frame contributed, and the characters actually agreed. Note it does
        NOT require a single frame to have produced the winning string — the
        whole point of voting is that the correct plate is often one no
        individual frame got right.
        """
        return (self.valid_format and self.reads >= 2
                and self.confidence >= self.MIN_AGREEMENT)


def vote(reads: list[PlateRead], *, min_reads: int = 1) -> VoteResult | None:
    """Combine every read of one track into a single answer.

    Character positions are voted independently, weighted by OCR confidence,
    among reads whose length matches the commonest length. Then the format
    repair runs once on the winner.
    """
    reads = [r for r in reads if clean(r.text)]
    if len(reads) < min_reads or not reads:
        return None

    cleaned = [(clean(r.text), r) for r in reads]
    length = Counter(len(t) for t, _ in cleaned).most_common(1)[0][0]
    same_length = [(t, r) for t, r in cleaned if len(t) == length]

    per_position: list[defaultdict[str, float]] = [defaultdict(float) for _ in range(length)]
    for text, read in same_length:
        for i, ch in enumerate(text):
            char_conf = (read.char_confidences[i]
                         if i < len(read.char_confidences) else read.confidence)
            per_position[i][ch] += max(char_conf, 1e-3)

    winner, agreement = [], []
    for slot in per_position:
        total = sum(slot.values())
        ch, weight = max(slot.items(), key=lambda kv: kv[1])
        winner.append(ch)
        agreement.append(weight / total if total else 0.0)

    voted = "".join(winner)
    fixed = repair(voted)
    best = max(same_length, key=lambda tr: tr[1].confidence)[1]
    agreeing = sum(1 for t, _ in same_length if t == voted or repair(t) == fixed)

    return VoteResult(
        plate=fixed,
        confidence=sum(a for a in agreement) / len(agreement) if agreement else 0.0,
        reads=len(reads),
        agreeing_reads=agreeing,
        valid_format=is_valid(fixed),
        repaired=fixed != voted,
        best_pts_ms=best.pts_ms,
        best_width_px=best.plate_width_px,
        char_agreement=tuple(round(a, 3) for a in agreement),
    )
