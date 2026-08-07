"""Pass 3: carve records into cells and write the three outputs.

Re-streams the input rather than reusing anything pass 2 held, which is what
keeps memory proportional to the record width instead of the file size.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fixed2tab.detect import LineSource, ShortLinePolicy, classify_line
from fixed2tab.header import Preamble
from fixed2tab.model import (
    Counts,
    Field,
    Geometry,
    InputError,
    RejectReason,
    RejectedLine,
)

__all__ = ["HeaderMode", "carve_cells", "write_outputs"]


class HeaderMode:
    """Whether to write column names as the table's first row.

    ``none`` is the default. Names live in the geometry report, where they
    document the columns without occupying a data line — which matters because
    downstream steps address rows positionally, and a header row would shift
    every offset by one without anything visibly breaking.
    """

    NONE = "none"
    PLAIN = "plain"
    CHOICES = (NONE, PLAIN)
    DEFAULT = NONE


def carve_cells(record: str, fields: tuple[Field, ...]) -> list[str]:
    """Extract one row of cells from a record.

    Cells are verbatim substrings with leading and trailing spaces removed and
    internal spacing preserved, so ``4h  6m 22s`` survives intact. Nothing is
    coerced: ``-.075`` stays ``-.075`` and a year of ``-2000`` stays ``-2000``.
    Conversion is a separate concern, and doing it here is how a lossless
    extraction quietly becomes a lossy one.
    """
    return [f.slice_of(record).strip(" ") for f in fields]


def iter_classified(
    source: LineSource,
    record_width: int,
    preamble: Preamble,
    short_policy: str,
    header_line: int | None,
) -> Iterator[tuple[int, str, str | None, RejectReason | None]]:
    """Replay pass 2's classification, yielding each line's verdict.

    Deliberately a replay rather than a cache: re-reading and re-classifying is
    cheap, whereas retaining every record is what the streaming guarantee rules
    out.
    """
    in_preamble = True
    preamble_numbers = {n for n, _ in preamble.lines}
    for number, text in source:
        if in_preamble:
            still = (
                number <= header_line
                if header_line is not None
                else number in preamble_numbers
            )
            if still:
                reason = (
                    RejectReason.LENGTH
                    if len(text) != record_width
                    else RejectReason.PREAMBLE_IDENTITY
                )
                yield number, text, None, reason
                continue
            in_preamble = False
        record, reason = classify_line(
            text, record_width, preamble.rstripped, short_policy, past_preamble=True
        )
        yield number, text, record, reason


def write_outputs(
    source: LineSource,
    geometry: Geometry,
    preamble: Preamble,
    table_path: Path,
    rejected_path: Path,
    short_policy: str = ShortLinePolicy.DEFAULT,
    header_line: int | None = None,
    header_mode: str = HeaderMode.DEFAULT,
) -> Counts:
    """Write the table and the rejected lines; return the reconciliation counts.

    Both files are always written, the rejected one possibly empty, so that a
    reader can always check that rows plus rejects equals input lines. Newlines
    are explicit LF: the platform default would emit CRLF on Windows and break
    both byte-for-byte reproducibility and the golden comparison.
    """
    rows = 0
    rejects = 0
    lines_seen = 0

    with (
        table_path.open("w", encoding="utf-8", newline="") as table,
        rejected_path.open("w", encoding="utf-8", newline="") as rejected,
    ):
        if header_mode == HeaderMode.PLAIN:
            names = [f.name or f"col{i}" for i, f in enumerate(geometry.fields, 1)]
            table.write("\t".join(names) + "\n")

        for number, text, record, reason in iter_classified(
            source, geometry.record_width, preamble, short_policy, header_line
        ):
            lines_seen += 1
            if reason is not None:
                rejected.write(f"{number}\t{reason.value}\t{text}\n")
                rejects += 1
                continue
            assert record is not None
            if "\t" in record:
                raise InputError(
                    f"line {number} contains a TAB. Fixed-width input must not, "
                    "because emitting it would shift every later column in the "
                    "output and corrupt the table silently. If this file came "
                    "through a Galaxy upload, check that 'convert spaces to "
                    "tabs' was not applied."
                )
            table.write("\t".join(carve_cells(record, geometry.fields)) + "\n")
            rows += 1

    return Counts(input_lines=lines_seen, table_rows=rows, rejected_lines=rejects)


def rejected_line_records(
    source: LineSource,
    record_width: int,
    preamble: Preamble,
    short_policy: str = ShortLinePolicy.DEFAULT,
    header_line: int | None = None,
) -> list[RejectedLine]:
    """Every rejected line, for callers that need them in memory (tests)."""
    return [
        RejectedLine(number, text, reason)
        for number, text, _record, reason in iter_classified(
            source, record_width, preamble, short_policy, header_line
        )
        if reason is not None
    ]
