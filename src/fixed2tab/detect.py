"""Reading the input and pass 1: the line model and the record width.

The parser makes three streaming passes and never holds the file in memory:

1. length histogram, and locating the first record-width line
2. classification, preamble collection, and the blank map
3. carving

Only pass 1 lives here so far. Its state is proportional to the number of
*distinct* line lengths, not to the number of lines.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from fixed2tab.model import Diagnostic, InputError

__all__ = [
    "LengthProfile",
    "LineSource",
    "ShortLinePolicy",
    "detect_record_width",
    "profile_lengths",
]

BOM = "﻿"

# Classes of control character that mark a line as non-record (REQ-2.8). TAB is
# excluded here because it gets its own fatal treatment: a TAB inside a record
# would silently corrupt the TSV, which is the failure this tool exists to stop.
CONTROL_RANGES = ((0x00, 0x08), (0x0B, 0x1F), (0x7F, 0x9F))


class ShortLinePolicy:
    """What to do with lines shorter than the record width.

    Many real fixed-width files have trailing blanks stripped, so records come
    out short. Rejecting them wholesale hands the user an empty table and a
    rejected file containing their entire dataset — a tool that looks broken.
    Padding is therefore the default; ``reject`` remains available for files
    where a short line really does mean corruption.
    """

    PAD = "pad"
    REJECT = "reject"
    CHOICES = (PAD, REJECT)


@dataclass(frozen=True, slots=True)
class LengthProfile:
    """What pass 1 learned about line lengths."""

    histogram: tuple[tuple[int, int], ...]  # (length, count), descending by count
    total_lines: int
    max_length: int
    record_width: int
    tied_lengths: tuple[int, ...] = ()

    @property
    def width_share(self) -> float:
        """Fraction of lines having the detected record width."""
        if not self.total_lines:
            return 0.0
        counts = dict(self.histogram)
        return counts.get(self.record_width, 0) / self.total_lines

    @property
    def looks_right_trimmed(self) -> bool:
        """Whether the length spread suggests trailing blanks were stripped.

        The signature is many distinct lengths, none exceeding the longest, with
        the detected width well short of that longest line. In such a file the
        true record width is the *maximum*, not the mode, so detection needs the
        user's help via ``--record-width``.
        """
        return (
            len(self.histogram) > 3
            and self.record_width < self.max_length
            and self.width_share < 0.75
        )


class LineSource:
    """Re-iterable, line-numbered, decoded view of the input.

    Decoding happens per line so a failure can name the offending line number
    (REQ-1.8) rather than a byte offset. Iteration re-opens the file, which is
    what lets three passes run without buffering.

    The line model (REQ-0.1, REQ-0.2): split on LF only, strip one optional
    trailing CR, treat no other character as a terminator, count a final
    unterminated line as one line, and do not count a trailing LF as an extra
    line. Anything else and the CLI would disagree with Galaxy, whose uploader
    normalises line endings — the same file would then yield different geometry
    through the two delivery routes.
    """

    def __init__(self, path: Path, encoding: str = "utf-8") -> None:
        self.path = path
        self.encoding = encoding

    def __iter__(self) -> Iterator[tuple[int, str]]:
        try:
            handle = self.path.open("rb")
        except OSError as exc:
            raise InputError(f"cannot read {self.path}: {exc}") from exc
        with handle:
            for number, raw in enumerate(handle, start=1):
                # Binary iteration splits on b"\n" and applies no newline
                # translation, which is exactly the model we want.
                if raw.endswith(b"\n"):
                    raw = raw[:-1]
                if raw.endswith(b"\r"):
                    raw = raw[:-1]
                try:
                    text = raw.decode(self.encoding)
                except UnicodeDecodeError as exc:
                    raise InputError(
                        f"cannot decode line {number} of {self.path} as {self.encoding}: "
                        f"{exc.reason}. If this is byte-padded legacy output, "
                        f"try --encoding latin-1."
                    ) from exc
                if number == 1 and text.startswith(BOM):
                    # Strip before any length is measured: a BOM would otherwise
                    # make line 1 one character longer, shifting the histogram
                    # and with it the preamble boundary and the header.
                    text = text[len(BOM) :]
                yield number, text

    def byte_lengths(self) -> Counter[int]:
        """Line lengths in *bytes*, for the encoding diagnostic (REQ-1.10)."""
        lengths: Counter[int] = Counter()
        with self.path.open("rb") as handle:
            for raw in handle:
                if raw.endswith(b"\n"):
                    raw = raw[:-1]
                if raw.endswith(b"\r"):
                    raw = raw[:-1]
                lengths[len(raw)] += 1
        return lengths


def detect_record_width(histogram: Counter[int]) -> tuple[int, tuple[int, ...]]:
    """The most frequent line length, breaking ties toward the greatest (REQ-0.3, REQ-0.4).

    A deterministic tie-break matters more than which way it goes: without one,
    the same input could yield different geometry on different runs, and both
    REQ-0.11 and every golden test would become unreliable.

    Returns the width and, when there was a tie, the lengths that tied.
    """
    if not histogram:
        raise InputError("input contains no lines")
    best = max(histogram.values())
    tied = tuple(sorted(length for length, count in histogram.items() if count == best))
    return max(tied), (tied if len(tied) > 1 else ())


def profile_lengths(source: LineSource) -> tuple[LengthProfile, list[Diagnostic]]:
    """Pass 1. Build the length histogram and derive the record width."""
    histogram: Counter[int] = Counter()
    total = 0
    for _number, text in source:
        histogram[len(text)] += 1
        total += 1

    if total == 0:
        raise InputError(
            f"{source.path} contains no lines. An empty input is treated as an "
            "error rather than an empty table, so a failed upstream step cannot "
            "masquerade as a successful conversion."
        )

    width, tied = detect_record_width(histogram)
    profile = LengthProfile(
        histogram=tuple(sorted(histogram.items(), key=lambda kv: (-kv[1], -kv[0]))),
        total_lines=total,
        max_length=max(histogram),
        record_width=width,
        tied_lengths=tied,
    )

    diagnostics: list[Diagnostic] = []
    if tied:
        diagnostics.append(
            Diagnostic(
                "width-tie",
                f"line lengths {', '.join(str(t) for t in tied)} are equally common; "
                f"chose the greatest ({width}). Override with --record-width.",
            )
        )
    if profile.width_share < 0.75:
        diagnostics.append(
            Diagnostic(
                "width-share",
                f"only {profile.width_share:.1%} of lines have the detected record "
                f"width {width}; the rest will be rejected or padded. "
                "Override with --record-width.",
            )
        )
    if profile.looks_right_trimmed:
        diagnostics.append(
            Diagnostic(
                "right-trimmed",
                f"line lengths vary widely and none exceeds {profile.max_length}, "
                "which is the signature of a file whose trailing blanks were "
                f"stripped. Consider --record-width {profile.max_length} "
                "--short-lines pad.",
            )
        )
    return profile, diagnostics
