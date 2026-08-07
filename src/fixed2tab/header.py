"""Preamble and header-line identification.

Deliberately independent of the column geometry. An earlier design identified
the header by overlapping its tokens with the detected fields, which was
circular: classification needs the header, gutter detection needs the
classification, and the fields need the gutters. Defining the preamble purely by
line length breaks that cycle — and it identifies the header correctly on both
the reference file and a paginated variant of it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

__all__ = ["Preamble", "build_preamble", "collect_preamble"]


@dataclass(frozen=True, slots=True)
class Preamble:
    """The lines before the first record-width line, and the header among them.

    ``header`` is the last preamble line: in generated output the title comes
    first and the column headings sit immediately above the data. An empty
    preamble means no header was identified and columns are named positionally.
    """

    lines: tuple[tuple[int, str], ...] = ()
    header: str | None = None

    @property
    def rstripped(self) -> frozenset[str]:
        """Preamble text with trailing spaces removed, for identity comparison.

        Comparing right-stripped is what catches a paginated repeat of the
        header. Such a repeat is normally blank-padded out to the record width,
        which made it length-conforming and *not* byte-identical to the
        original — so it passed every classification criterion and was carved as
        a data row, filling the gutters and corrupting the geometry.

        Right-stripping is safe as well as necessary: across all 49,486 records
        of the reference file, none collides with another record or with a
        preamble line after stripping.
        """
        return frozenset(text.rstrip(" ") for _n, text in self.lines)

    @property
    def header_line_number(self) -> int | None:
        return self.lines[-1][0] if self.lines else None


def build_preamble(
    collected: list[tuple[int, str]],
    header_line: int | None = None,
) -> Preamble:
    """Assemble a `Preamble` from the leading lines already gathered.

    The caller streams and decides where the preamble ends; this only picks the
    header out of it. ``header_line`` (from ``--header-line``) overrides the
    default choice, for files where the last preamble line is not the headings.
    """
    if not collected:
        return Preamble()
    if header_line is not None:
        chosen = next((t for n, t in collected if n == header_line), None)
        if chosen is not None:
            return Preamble(tuple(collected), chosen)
    return Preamble(tuple(collected), collected[-1][1])


def collect_preamble(
    numbered_lines: Iterable[tuple[int, str]],
    record_width: int,
    header_line: int | None = None,
) -> Preamble:
    """Leading lines whose length differs from the record width (REQ-0.7).

    Length equality is exact here even when ``--short-lines pad`` is active. If
    padding were applied first, the reference file's 37-character title and
    121-character heading would both pad out to 128, become records, and be
    carved as data — losing the header entirely. Padding is therefore a repair
    for lines *after* the preamble boundary, never a way into it.

    Consumes only the leading lines, so it is safe on a large file.
    """
    collected: list[tuple[int, str]] = []
    for number, text in numbered_lines:
        if len(text) == record_width:
            break
        collected.append((number, text))
    return build_preamble(collected, header_line)
