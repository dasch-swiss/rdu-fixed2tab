"""Domain types, the rejection taxonomy, and the error hierarchy.

Everything here is immutable. The parser is three streaming passes that build
these values up; nothing mutates a geometry once it has been derived.

Character positions are **1-based and inclusive** throughout the public surface —
in `--columns`, in the geometry report, and in these types. The only place
0-based offsets appear is inside the slicing helpers below.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum, IntEnum

__all__ = [
    "BlankRun",
    "Counts",
    "Diagnostic",
    "ExitCode",
    "Field",
    "Fixed2TabError",
    "Geometry",
    "InputError",
    "RejectReason",
    "RejectedLine",
    "StrictViolation",
    "UsageError",
    "count_by_reason",
]


class ExitCode(IntEnum):
    """Process exit statuses.

    Galaxy only distinguishes zero from non-zero (the wrapper sets
    ``detect_errors="exit_code"``), but the test suite asserts exact values, so
    they are fixed here rather than left to chance.
    """

    SUCCESS = 0
    UNEXPECTED = 1  # reserved: a fault we did not anticipate
    USAGE = 2  # bad parameters
    INPUT = 3  # input cannot be decoded, or has no usable structure
    STRICT = 4  # --strict tripped on an unallowed rejection


class Fixed2TabError(Exception):
    """Base for every error this tool raises deliberately.

    Only ``cli`` maps these to exit statuses; nothing deeper calls ``sys.exit``,
    which keeps the parsing code importable and testable.
    """

    exit_code: ExitCode = ExitCode.UNEXPECTED


class UsageError(Fixed2TabError):
    """A parameter is malformed, out of domain, or contradicts another."""

    exit_code = ExitCode.USAGE


class InputError(Fixed2TabError):
    """The input cannot be decoded, is empty, or yields no usable records."""

    exit_code = ExitCode.INPUT


class StrictViolation(Fixed2TabError):
    """``--strict`` was set and a line was rejected for an unallowed reason."""

    exit_code = ExitCode.STRICT


class RejectReason(Enum):
    """Why a line was not treated as a record.

    **Declaration order is evaluation order** (REQ-2.6). A line can satisfy
    several criteria at once — a short line made of dashes is both ``LENGTH`` and
    ``RULE_LINE`` — and it is attributed to the first one that matches, so the
    grouped counts in the report are deterministic.

    These four string values are a public contract: the report enumerates them
    (REQ-2.7), the counts are grouped by them (REQ-3.3), and ``--allow-rejects``
    accepts them by name (REQ-3.4). Changing a value is a breaking change.

    There is no separate "no digits" reason. That test is fused into
    ``RULE_LINE`` (REQ-2.3): a line must *both* lack digits *and* be built from
    at most two distinct characters. Requiring both is what stops a legitimate
    all-zero record such as ``0   0   0.0   0`` from being discarded.
    """

    LENGTH = "length"
    CONTROL_CHAR = "control-char"
    PREAMBLE_IDENTITY = "preamble-identity"
    RULE_LINE = "rule-line"

    @classmethod
    def evaluation_order(cls) -> tuple[RejectReason, ...]:
        """The criteria in the order they are applied (REQ-2.6)."""
        return tuple(cls)

    @classmethod
    def names(cls) -> tuple[str, ...]:
        """Every reason value, including ones with zero occurrences (REQ-2.7)."""
        return tuple(r.value for r in cls)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class BlankRun:
    """A maximal run of positions blank in every record.

    Runs at least ``--min-gutter`` wide separate fields; narrower ones sit
    *inside* a field and are what stops ``4h  6m 22s`` being split into three
    columns. The report lists all of them so a user can see what a different
    ``--min-gutter`` would produce.
    """

    start: int  # 1-based inclusive
    end: int  # 1-based inclusive

    def __post_init__(self) -> None:
        if self.start < 1:
            raise ValueError(f"blank run start must be >= 1, got {self.start}")
        if self.end < self.start:
            raise ValueError(f"blank run end {self.end} precedes start {self.start}")

    @property
    def width(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True, slots=True)
class Field:
    """One output column, as a character range over the record."""

    start: int  # 1-based inclusive
    end: int  # 1-based inclusive
    name: str | None = None

    def __post_init__(self) -> None:
        if self.start < 1:
            raise ValueError(f"field start must be >= 1, got {self.start}")
        if self.end < self.start:
            raise ValueError(
                f"field end {self.end} precedes start {self.start}; "
                "ranges are 1-based and inclusive"
            )

    @property
    def width(self) -> int:
        return self.end - self.start + 1

    def slice_of(self, line: str) -> str:
        """The raw substring for this field, untrimmed.

        Short lines yield a short slice rather than an error; padding policy is
        decided upstream by ``--short-lines``.
        """
        return line[self.start - 1 : self.end]

    def spec(self) -> str:
        """This field as it appears in a ``--columns`` string."""
        return f"{self.start}-{self.end}:{self.name}" if self.name else f"{self.start}-{self.end}"


@dataclass(frozen=True, slots=True)
class Geometry:
    """The detected — or supplied — column layout of a file."""

    record_width: int
    fields: tuple[Field, ...]
    blank_runs: tuple[BlankRun, ...] = ()
    min_gutter: int = 2
    detected: bool = True  # False when the user supplied --columns

    def __post_init__(self) -> None:
        if self.record_width < 1:
            raise ValueError(f"record width must be >= 1, got {self.record_width}")
        for f in self.fields:
            if f.end > self.record_width:
                raise ValueError(
                    f"field {f.start}-{f.end} extends past record width {self.record_width}"
                )

    def columns_spec(self) -> str:
        """The ``--columns`` string that reproduces this geometry exactly.

        Emitted in the report so correcting a boundary is copy, edit, paste
        (REQ-4.2), and asserted to round-trip (REQ-4.7).
        """
        return ",".join(f.spec() for f in self.fields)


@dataclass(frozen=True, slots=True)
class RejectedLine:
    """A line that was not parsed, kept verbatim so nothing disappears."""

    number: int  # 1-based, as counted in the input
    text: str
    reason: RejectReason

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError(f"line numbers are 1-based, got {self.number}")


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A non-fatal warning.

    Diagnostics are written to the geometry report, never to stderr — stderr is
    reserved for messages that accompany a non-zero exit (REQ-0.13). Galaxy's
    legacy stdio handling treats any stderr output as job failure, so a warning
    on stderr would turn a correct conversion into a red dataset.
    """

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class Counts:
    """The reconciliation totals (REQ-3.2).

    ``table_rows`` counts rows carved from input lines. A header row written by
    ``--header plain`` corresponds to no input record and is deliberately
    excluded, which is what keeps the invariant true in that mode.

    Per-reason counts are derived from the rejected lines rather than stored: a
    mutable mapping inside a frozen dataclass would only pretend to be immutable.
    """

    input_lines: int = 0
    table_rows: int = 0
    rejected_lines: int = 0

    @property
    def reconciles(self) -> bool:
        return self.table_rows + self.rejected_lines == self.input_lines


def count_by_reason(rejected: Iterable[RejectedLine]) -> dict[str, int]:
    """Rejected-line counts grouped by reason, including zeros (REQ-2.7, REQ-3.3).

    Every reason in the taxonomy appears even when it never fired, so a reader
    can tell "this criterion found nothing" from "this criterion does not exist".
    """
    counts = {name: 0 for name in RejectReason.names()}
    for line in rejected:
        counts[line.reason.value] += 1
    return counts
