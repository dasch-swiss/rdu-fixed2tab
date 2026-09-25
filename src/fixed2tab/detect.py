"""Reading the input, and the first two of the parser's three passes.

The parser makes three streaming passes and never holds the file in memory:

1. length histograms, in characters and in bytes, and the record width
2. classification, preamble collection, and the blank map
3. carving — in ``carve``

Pass 1's state is proportional to the number of *distinct* line lengths, not
to the number of lines.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from fixed2tab.header import Preamble, build_preamble
from fixed2tab.model import (
    BlankRun,
    Counts,
    Diagnostic,
    Field,
    InputError,
    RejectedLine,
    RejectReason,
    UsageError,
    count_by_reason,
)

__all__ = [
    "PAD_NEEDS_HEADER_LINE",
    "ClassifiedInput",
    "LengthProfile",
    "LineSource",
    "ShortLinePolicy",
    "blank_runs",
    "classify_input",
    "classify_line",
    "derive_fields",
    "detect_record_width",
    "has_control_char",
    "preamble_continues",
    "preamble_reason",
    "profile_lengths",
]

BOM = "﻿"

# Classes of control character that mark a line as non-record (REQ-2.8). TAB is
# excluded here because it gets its own fatal treatment: a TAB inside a record
# would silently corrupt the TSV, which is the failure this tool exists to stop.
PAD_NEEDS_HEADER_LINE = (
    "--short-lines pad needs --header-line. Under padding the records are "
    "shorter than the record width, so the preamble cannot be found by line "
    "length: leading records would be absorbed into it and one of them used as "
    "the column headings. Pass --header-line N, where N is the line holding the "
    "column headings, or --header-line 0 if the file has none."
)

CONTROL_RANGES = ((0x00, 0x08), (0x0B, 0x1F), (0x7F, 0x9F))


class ShortLinePolicy:
    """What to do with lines shorter than the record width.

    Many real fixed-width files have trailing blanks stripped, so records come
    out short, and rejecting them wholesale hands the user an empty table with
    their entire dataset in the rejected file — a tool that looks broken. That
    argued for padding by default, until it was measured.

    **Padding cannot be the default.** On a paginated file, short noise lines
    are padded out to the record width and then satisfy every remaining
    criterion: a ``Page 1`` marker and an ``end of table`` footer both became
    data rows. Silently promoting page furniture to records is precisely the
    class of corruption this tool exists to prevent, and it is worse than a
    visible over-rejection, because an empty table is obvious while two bogus
    rows in fifty thousand are not.

    So ``reject`` is the default and the length test stays strict. When a file
    genuinely is right-trimmed, pass 1 recognises the signature and the report
    says so, naming ``--short-lines pad`` — an explicit opt-in, on a file the
    user has been told about.

    **Padding also requires ``--header-line``.** The length-based preamble
    boundary cannot work under padding: records are shorter than the width by
    definition, so every record before the first full-width one was absorbed
    into the preamble, and the last of them became the header. Following the
    report's own advice on an eight-record file produced four rows, exit 0 and
    a reconciliation that balanced. Requiring the boundary to be stated makes
    that loss impossible rather than merely warned about.
    """

    PAD = "pad"
    REJECT = "reject"
    CHOICES = (REJECT, PAD)
    DEFAULT = REJECT


@dataclass(frozen=True, slots=True)
class LengthProfile:
    """What pass 1 learned about line lengths."""

    histogram: tuple[tuple[int, int], ...]  # (length, count), descending by count
    total_lines: int
    max_length: int
    record_width: int
    tied_lengths: tuple[int, ...] = ()
    byte_uniform: bool = False  # every line has the same length in bytes

    def lines_at(self, width: int) -> int:
        """How many lines have exactly ``width`` characters."""
        return dict(self.histogram).get(width, 0)

    def share_at(self, width: int) -> float:
        """Fraction of lines having exactly ``width`` characters."""
        return self.lines_at(width) / self.total_lines if self.total_lines else 0.0

    @property
    def width_share(self) -> float:
        """Fraction of lines having the detected record width."""
        return self.share_at(self.record_width)

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
        for number, text, _n_bytes in self._lines():
            yield number, text

    def _lines(self) -> Iterator[tuple[int, str, int]]:
        """Each line with its number, its text, and its length in bytes.

        The byte length is for the encoding diagnostic (REQ-1.10). Measuring it
        here, in pass 1, is what spares that diagnostic a pass of its own.
        """
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
                yield number, text, len(raw)


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
    byte_lengths: set[int] = set()
    total = 0
    for _number, text, n_bytes in source._lines():
        histogram[len(text)] += 1
        byte_lengths.add(n_bytes)
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
        byte_uniform=len(byte_lengths) == 1,
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
                f"stripped. Records shorter than the detected width will be "
                "REJECTED, not padded. To parse this file, pass "
                f"--record-width {profile.max_length} --short-lines pad "
                "--header-line N, where N is the line holding the column "
                "headings (0 if there are none).",
            )
        )
    return profile, diagnostics


def has_control_char(text: str) -> bool:
    """Whether the line carries a control character other than TAB (REQ-2.8).

    Form feeds are the common case: paginated output separates pages with one.
    TAB is excluded deliberately — inside a record it is fatal rather than a
    reason to skip the line, because emitting it would corrupt the TSV.
    """
    return any(any(lo <= ord(ch) <= hi for lo, hi in CONTROL_RANGES) for ch in text)


def classify_line(
    text: str,
    record_width: int,
    preamble_rstripped: frozenset[str],
    short_policy: str = ShortLinePolicy.DEFAULT,
    past_preamble: bool = True,
) -> tuple[str | None, RejectReason | None]:
    """Classify one line, returning either its record text or a rejection reason.

    The four criteria are applied in a fixed order (REQ-2.6) — length, control
    characters, preamble identity, rule line — and the line is attributed to the
    first one that matches. A short line of dashes satisfies two of them at
    once, so without a stated order the grouped counts in the report would not
    be reproducible.

    Crucially, none of these criteria looks at the gutters. Using gutter
    conformance to detect noise would be circular, and it is exactly how a
    thresholded earlier design silently discarded every intercalary-day record.
    """
    if len(text) != record_width:
        if short_policy == ShortLinePolicy.PAD and past_preamble and len(text) < record_width:
            text = text.ljust(record_width)
        else:
            return None, RejectReason.LENGTH

    if has_control_char(text):
        return None, RejectReason.CONTROL_CHAR

    if text.rstrip(" ") in preamble_rstripped:
        return None, RejectReason.PREAMBLE_IDENTITY

    # A rule line must BOTH lack digits AND be built from at most two distinct
    # characters. Requiring only the second would reject a legitimate all-zero
    # record such as "0   0   0.0   0" — two distinct non-blank characters —
    # which is the same class of silent, biased data loss the tool exists to
    # prevent.
    stripped = text.strip(" ")
    if not any(ch.isdigit() for ch in text) and len(set(stripped)) <= 2:
        return None, RejectReason.RULE_LINE

    return text, None


@dataclass(frozen=True, slots=True)
class ClassifiedInput:
    """The result of pass 2."""

    preamble: Preamble
    rejected: tuple[RejectedLine, ...]
    counts: Counts
    blank: tuple[bool, ...] = ()

    @property
    def by_reason(self) -> dict[str, int]:
        return count_by_reason(self.rejected)


def preamble_continues(number: int, text: str, record_width: int, header_line: int | None) -> bool:
    """Whether line ``number`` still belongs to the preamble (REQ-0.7).

    The single definition of the preamble boundary. Pass 2 and pass 3 both call
    it, so the two cannot disagree about where the data starts.

    Without ``--header-line`` the preamble is the leading lines whose length
    differs from the record width. Length equality is exact even when
    ``--short-lines pad`` is active: if padding were applied first, the
    reference file's 37-character title and 121-character heading would both
    pad out to 128, become records, and be carved as data — losing the header
    entirely. Padding is a repair for lines *after* the boundary, never a way
    into it.

    An explicit ``--header-line`` ends the preamble *at that line*, regardless
    of its length. Without this the flag is not the escape hatch REQ-0.7b
    claims: a heading that happens to match the record width never enters the
    preamble, so it is carved as a data row and there is no way to say
    otherwise.
    """
    if header_line is not None:
        return number <= header_line
    return len(text) != record_width


def preamble_reason(text: str, record_width: int) -> RejectReason:
    """The reason a preamble line is reported under in the rejected file.

    A forced preamble line may legitimately be record-width — that is the case
    ``--header-line`` exists for — so attribute by what is true of the line
    rather than assuming a length mismatch.
    """
    return RejectReason.LENGTH if len(text) != record_width else RejectReason.PREAMBLE_IDENTITY


def classify_input(
    source: LineSource,
    record_width: int,
    short_policy: str = ShortLinePolicy.DEFAULT,
    header_line: int | None = None,
) -> tuple[ClassifiedInput, list[Diagnostic]]:
    """Pass 2. Split the input into records and rejects, and find the header.

    Streams once. The preamble is bounded by construction — it ends at the first
    record-width line, or at ``header_line`` — so memory does not grow with the
    file.
    """
    if short_policy == ShortLinePolicy.PAD and header_line is None:
        raise UsageError(PAD_NEEDS_HEADER_LINE)
    preamble_acc: list[tuple[int, str]] = []
    in_preamble = True
    preamble = Preamble()

    rejected: list[RejectedLine] = []
    # One boolean per character position — the entire per-record state. Records
    # are deliberately NOT retained: holding them would make memory grow with
    # the file, which REQ-0.16 forbids. Pass 3 re-streams and re-classifies,
    # which is cheap, rather than buffering 6 MB of strings here.
    blank = [True] * record_width
    n_records = 0
    total = 0
    padded = 0

    for number, text in source:
        total += 1

        if in_preamble:
            if preamble_continues(number, text, record_width, header_line):
                preamble_acc.append((number, text))
                rejected.append(RejectedLine(number, text, preamble_reason(text, record_width)))
                continue
            in_preamble = False
            preamble = build_preamble(preamble_acc, header_line)

        before = len(text)
        record, reason = classify_line(
            text, record_width, preamble.rstripped, short_policy, past_preamble=True
        )
        if reason is not None:
            rejected.append(RejectedLine(number, text, reason))
            continue
        assert record is not None
        if "\t" in record:
            # Checked here in pass 2, before any output file is opened, so a
            # fatal TAB leaves no truncated table behind for a later step to
            # consume as if it were complete.
            raise InputError(
                f"line {number} contains a TAB. Fixed-width input must not, "
                "because emitting it would shift every later column in the "
                "output and corrupt the table silently. If this file came "
                "through a Galaxy upload, check that 'convert spaces to "
                "tabs' was not applied."
            )
        if before < record_width:
            padded += 1
        n_records += 1
        for i, ch in enumerate(record):
            if blank[i] and ch != " ":
                blank[i] = False

    if in_preamble:
        # No line ever matched the record width: everything is preamble.
        preamble = build_preamble(preamble_acc, header_line)

    counts = Counts(input_lines=total, table_rows=n_records, rejected_lines=len(rejected))

    diagnostics: list[Diagnostic] = []
    if padded:
        diagnostics.append(
            Diagnostic(
                "short-lines-padded",
                f"{padded} line(s) shorter than the record width were padded with "
                "spaces. Use --short-lines reject to treat them as errors instead.",
            )
        )
        if preamble.lines:
            diagnostics.append(
                Diagnostic(
                    "padding-and-preamble",
                    f"padding is active, so the preamble is lines 1-{len(preamble.lines)} "
                    f"as set by --header-line {header_line}, and the column headings "
                    f"are taken from {preamble.header!r}. If that is not the heading "
                    "line, change --header-line.",
                )
            )
    if not n_records:
        diagnostics.append(Diagnostic("no-records", "no line was classified as a record."))
    return (
        ClassifiedInput(preamble, tuple(rejected), counts, tuple(blank)),
        diagnostics,
    )


def blank_runs(blank: tuple[bool, ...]) -> tuple[BlankRun, ...]:
    """Maximal runs of positions blank in every record, as 1-based ranges.

    Strictly *every* record — never a percentage. A threshold looks harmless and
    is not: a column blank in 98% of rows is indistinguishable from whitespace
    by frequency, so thresholding classifies it as a gutter and then discards
    every row that carries a value there. Measured on the reference file, a 90%
    threshold silently dropped all 16 intercalary-day records and every
    quality-flagged row — the exceptional rows, which are the interesting ones.
    """
    runs: list[BlankRun] = []
    start: int | None = None
    for i, is_blank in enumerate(blank):
        if is_blank and start is None:
            start = i
        elif not is_blank and start is not None:
            runs.append(BlankRun(start + 1, i))
            start = None
    if start is not None:
        runs.append(BlankRun(start + 1, len(blank)))
    return tuple(runs)


def derive_fields(
    blank: tuple[bool, ...], min_gutter: int = 2
) -> tuple[tuple[Field, ...], tuple[BlankRun, ...]]:
    """Split the record into fields at blank runs of at least ``min_gutter``.

    ``min_gutter`` is load-bearing and genuinely file-dependent. On the reference
    file it yields 16 fields at 1, 11 at 2, 10 at 3 and 8 at 4: at 1 it splits
    inside ``4h  6m 22s``, at 3 it merges two sparse flag columns whose
    separating run is exactly two wide. The report lists every run with its
    width so a user can see what another value would produce.
    """
    if min_gutter < 1:
        raise UsageError(f"--min-gutter must be at least 1, got {min_gutter}")

    runs = blank_runs(blank)
    width = len(blank)
    fields: list[Field] = []
    cursor = 1  # 1-based position of the next unclaimed character
    for run in runs:
        if run.width < min_gutter:
            continue  # a narrow run sits inside a field, not between two
        if run.start > cursor:
            fields.append(Field(cursor, run.start - 1))
        cursor = run.end + 1
    if cursor <= width:
        fields.append(Field(cursor, width))
    return tuple(fields), runs
