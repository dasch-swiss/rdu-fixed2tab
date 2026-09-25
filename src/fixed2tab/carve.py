"""Pass 3: carve records into cells and write the three outputs.

Re-streams the input rather than reusing anything pass 2 held, which is what
keeps memory proportional to the record width instead of the file size.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from fixed2tab.detect import (
    LineSource,
    ShortLinePolicy,
    classify_line,
    preamble_continues,
    preamble_reason,
)
from fixed2tab.header import Preamble
from fixed2tab.model import (
    Counts,
    Field,
    Geometry,
    RejectReason,
)

__all__ = ["FieldStats", "HeaderMode", "carve_cells", "write_outputs"]


@dataclass(frozen=True, slots=True)
class FieldStats:
    """Per-field figures gathered while carving, for the report.

    Collected here because only pass 3 knows both the fields and the record
    contents, and doing it inline avoids a fourth pass over the file.
    """

    blank_counts: tuple[int, ...] = ()
    samples: tuple[tuple[str, ...], ...] = ()  # first three rows, as carved
    rows: int = 0

    def blank_rate(self, index: int) -> float:
        return self.blank_counts[index] / self.rows if self.rows else 0.0

    def sample_for(self, index: int) -> tuple[str, ...]:
        return tuple(row[index] for row in self.samples if index < len(row))


class HeaderMode(str, Enum):
    """Whether to write column names as the table's first row.

    ``none`` is the default. Names live in the geometry report, where they
    document the columns without occupying a data line — which matters because
    downstream steps address rows positionally, and a header row would shift
    every offset by one without anything visibly breaking.
    """

    NONE = "none"  # the default
    PLAIN = "plain"

    def __str__(self) -> str:
        return self.value


def carve_cells(record: str, fields: tuple[Field, ...], collapse: bool = False) -> list[str]:
    """Extract one row of cells from a record.

    Cells are verbatim substrings with leading and trailing spaces removed and
    internal spacing preserved, so ``4h  6m 22s`` survives intact. Nothing is
    coerced: ``-.075`` stays ``-.075`` and a year of ``-2000`` stays ``-2000``.
    Conversion is a separate concern, and doing it here is how a lossless
    extraction quietly becomes a lossy one.
    """
    cells = [f.slice_of(record).strip(" ") for f in fields]
    if collapse:
        # Opt-in, because it is a transformation rather than an extraction. It
        # earns its place when a range merges several layout columns into one
        # value — "761     Tevet (4)         1" carries gaps that were only ever
        # alignment — but it would also destroy the meaningful spacing in
        # "4h  6m 22s", which is why it is off by default.
        cells = [" ".join(part for part in cell.split(" ") if part) for cell in cells]
    return cells


def iter_classified(
    source: LineSource,
    record_width: int,
    preamble: Preamble,
    short_policy: ShortLinePolicy,
    header_line: int | None,
) -> Iterator[tuple[int, str, str | None, RejectReason | None]]:
    """Replay pass 2's classification, yielding each line's verdict.

    Deliberately a replay rather than a cache: re-reading and re-classifying is
    cheap, whereas retaining every record is what the streaming guarantee rules
    out.
    """
    in_preamble = True
    for number, text in source:
        if in_preamble:
            if preamble_continues(number, text, record_width, header_line):
                yield number, text, None, preamble_reason(text, record_width)
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
    short_policy: ShortLinePolicy = ShortLinePolicy.REJECT,
    header_line: int | None = None,
    header_mode: HeaderMode = HeaderMode.NONE,
    collapse: bool = False,
) -> tuple[Counts, FieldStats, dict[str, int]]:
    """Write the table and the rejected lines; return the reconciliation counts.

    Both files are always written, the rejected one possibly empty, so that a
    reader can always check that rows plus rejects equals input lines. Newlines
    are explicit LF: the platform default would emit CRLF on Windows and break
    both byte-for-byte reproducibility and the golden comparison.
    """
    rows = 0
    rejects = 0
    lines_seen = 0
    blank_counts = [0] * len(geometry.fields)
    samples: list[tuple[str, ...]] = []
    # Counted here rather than in a separate pass: the carver already sees every
    # verdict, so re-deriving them elsewhere would mean reading the file again.
    by_reason = {name: 0 for name in RejectReason.names()}

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
                by_reason[reason.value] += 1
                rejects += 1
                continue
            assert record is not None
            cells = carve_cells(record, geometry.fields, collapse)
            for i, cell in enumerate(cells):
                if not cell:
                    blank_counts[i] += 1
            if len(samples) < 3:
                # The first three classified records, in input order (REQ-4.11).
                # A fixed rule matters: without one the report would vary between
                # runs and could not be compared byte for byte.
                samples.append(tuple(cells))
            table.write("\t".join(cells) + "\n")
            rows += 1

    return (
        Counts(input_lines=lines_seen, table_rows=rows, rejected_lines=rejects),
        FieldStats(tuple(blank_counts), tuple(samples), rows),
        by_reason,
    )
