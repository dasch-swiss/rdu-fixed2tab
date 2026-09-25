"""Preamble and header-line identification.

Deliberately independent of the column geometry. An earlier design identified
the header by overlapping its tokens with the detected fields, which was
circular: classification needs the header, gutter detection needs the
classification, and the fields need the gutters. Defining the preamble purely by
line length breaks that cycle — and it identifies the header correctly on both
the reference file and a paginated variant of it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from fixed2tab.model import Diagnostic, Field

__all__ = [
    "Preamble",
    "build_preamble",
    "collect_preamble",
    "name_fields",
    "sanitise",
    "tokenize",
]


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


def tokenize(header: str) -> tuple[tuple[int, int, str], ...]:
    """Maximal runs of non-blank characters, as 1-based inclusive spans."""
    tokens: list[tuple[int, int, str]] = []
    start: int | None = None
    for i, ch in enumerate(header):
        if ch != " " and start is None:
            start = i
        elif ch == " " and start is not None:
            tokens.append((start + 1, i, header[start:i]))
            start = None
    if start is not None:
        tokens.append((start + 1, len(header), header[start:]))
    return tuple(tokens)


def sanitise(name: str) -> str:
    """Reduce a derived name to identifier-safe characters.

    Every maximal run outside ``[A-Za-z0-9_]`` collapses to a single underscore,
    then leading and trailing underscores are removed. Replace-and-strip is what
    turns ``DELTAT[s]`` into ``DELTAT_s``; dropping the offending characters
    would give ``DELTATs`` and substituting one-for-one would give ``DELTAT_s_``.
    """
    return re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")


def name_fields(
    fields: Sequence[Field],
    header: str | None,
    supplied: Sequence[str | None] | None = None,
) -> tuple[tuple[Field, ...], list[Diagnostic]]:
    """Name each field from the header tokens whose spans overlap it (REQ-5.1).

    Overlap, not slicing. Header labels are not positioned to line up with the
    data beneath them, so cutting the header at the field boundaries mangles it:
    on the reference file that corrupts five names of eleven, yielding
    ``ELTAT[s``, ``gyptian Date``, ``Moon``, ``od`` and ``NEW MOON DA``. Taking
    every token that *overlaps* the field recovers all eleven, and correctly
    splits a single label such as ``NEW MOON DATE & TIME`` across the two
    columns it actually spans.
    """
    diagnostics: list[Diagnostic] = []
    # A name the user wrote always wins, and no diagnostic about *deriving* a
    # name applies to a field that was named explicitly. Passing them in here
    # rather than overwriting afterwards is what keeps the two consistent:
    # overwriting left the report warning about duplicates it had just resolved.
    given = list(supplied) if supplied is not None else [None] * len(fields)

    if header is None:
        named = tuple(
            Field(f.start, f.end, sanitise(g) if g else f"col{i}")
            for i, (f, g) in enumerate(zip(fields, given), start=1)
        )
        if any(g is None for g in given):
            diagnostics.append(
                Diagnostic(
                    "no-header",
                    "no header line was identified; columns without a supplied "
                    "name are named positionally.",
                )
            )
        return _disambiguate(named, diagnostics)

    tokens = tokenize(header)
    used: set[int] = set()
    derived: list[Field] = []

    for index, (f, g) in enumerate(zip(fields, given), start=1):
        if g is not None:
            derived.append(Field(f.start, f.end, sanitise(g)))
            # Still mark the overlapping tokens as used, so a token consumed by
            # an explicitly named field is not reported as discarded.
            used.update(
                i for i, tok in enumerate(tokens) if not (tok[1] < f.start or tok[0] > f.end)
            )
            continue
        hits = [
            (i, tok) for i, tok in enumerate(tokens) if not (tok[1] < f.start or tok[0] > f.end)
        ]
        used.update(i for i, _ in hits)
        raw = "_".join(tok[2] for _, tok in hits)
        name = sanitise(raw)
        if not hits:
            # Checked before the empty-name branch below, which would otherwise
            # always catch this case first and report the vaguer message. A
            # field with no overlapping token is guaranteed on the reference
            # file's shape: the header is 121 characters against 128-character
            # records, so anything past position 121 overlaps nothing.
            name = f"col{index}"
            diagnostics.append(
                Diagnostic(
                    "field-unnamed",
                    f"field {f.start}-{f.end} overlaps no header token; using {name!r}.",
                )
            )
        elif not name or name[0].isdigit():
            # A name starting with a digit is not a usable identifier for many
            # downstream tools, and an empty one is no name at all.
            name = f"col{index}" if not name else f"col{index}_{name}"
            diagnostics.append(
                Diagnostic(
                    "name-unusable",
                    f"field {f.start}-{f.end} derived an empty or digit-initial "
                    f"name from {raw!r}; using {name!r}.",
                )
            )
        derived.append(Field(f.start, f.end, name))

    orphans = [tok[2] for i, tok in enumerate(tokens) if i not in used]
    if orphans:
        diagnostics.append(
            Diagnostic(
                "header-token-discarded",
                f"header token(s) {', '.join(repr(o) for o in orphans)} overlap no "
                "field and were discarded.",
            )
        )

    return _disambiguate(tuple(derived), diagnostics)


def _disambiguate(
    named: tuple[Field, ...], diagnostics: list[Diagnostic]
) -> tuple[tuple[Field, ...], list[Diagnostic]]:
    """Suffix colliding names with the field index, reporting only real clashes."""
    seen: dict[str, int] = {}
    final: list[Field] = []
    duplicates: list[str] = []
    for index, f in enumerate(named, start=1):
        assert f.name is not None
        if f.name in seen:
            duplicates.append(f.name)
            final.append(Field(f.start, f.end, f"{f.name}_{index}"))
        else:
            seen[f.name] = index
            final.append(f)
    if duplicates:
        diagnostics.append(
            Diagnostic(
                "duplicate-names",
                f"duplicate column name(s) {', '.join(sorted(set(duplicates)))} "
                "were suffixed with the field index.",
            )
        )
    return tuple(final), diagnostics
