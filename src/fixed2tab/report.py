"""The geometry report.

This is the substitute for OpenRefine's live preview. A batch tool cannot show
you a table as you adjust the boundaries, so instead it tells you what it found,
how confident it is, and hands back the exact string that reproduces the result —
which you edit and pass to the next run.

Nothing here may vary between runs on identical input: no timestamps, no host
names, and no input paths. In Galaxy the dataset path is randomised per job, so
including it would make byte-identical reruns impossible on the one platform
that most needs reproducibility.
"""

from __future__ import annotations

from fixed2tab import __version__
from fixed2tab.carve import FieldStats
from fixed2tab.detect import LengthProfile
from fixed2tab.header import Preamble
from fixed2tab.model import Counts, Diagnostic, Geometry, RejectReason

__all__ = ["render_report"]

RULE = "=" * 78


def _section(title: str) -> list[str]:
    return ["", title, "-" * len(title)]


def render_report(
    geometry: Geometry,
    profile: LengthProfile,
    preamble: Preamble,
    counts: Counts,
    stats: FieldStats,
    by_reason: dict[str, int],
    diagnostics: list[Diagnostic],
    parameters: dict[str, object],
) -> str:
    """Render the report as plain text."""
    out: list[str] = [RULE, f"fixed2tab {__version__} — geometry report", RULE]

    out += _section("Parameters")
    # Every effective value, including defaults the user did not supply, so the
    # report alone explains how its table was produced.
    for key in sorted(parameters):
        out.append(f"  {key:<16} {parameters[key]}")

    out += _section("Input")
    out.append(f"  record width     {profile.record_width}")
    out.append(
        f"  lines at width   {counts.input_lines and stats.rows} records, "
        f"{profile.width_share:.1%} of {profile.total_lines} lines"
    )
    if profile.tied_lengths:
        out.append(
            f"  length tie       {', '.join(str(t) for t in profile.tied_lengths)} "
            "(chose the greatest)"
        )
    out.append("  line lengths     " + ", ".join(
        f"{length}×{count}" for length, count in profile.histogram[:8]
    ))
    if preamble.lines:
        out.append(f"  preamble         {len(preamble.lines)} line(s)")
        out.append(f"  header           {preamble.header!r}")
    else:
        out.append("  preamble         none; columns named positionally")

    out += _section("Reconciliation")
    out.append(f"  input lines      {counts.input_lines}")
    out.append(f"  table rows       {counts.table_rows}")
    out.append(f"  rejected lines   {counts.rejected_lines}")
    verdict = "OK" if counts.reconciles else "MISMATCH — please report this"
    out.append(f"  rows + rejected  {counts.table_rows + counts.rejected_lines}  [{verdict}]")

    out += _section("Rejected by reason")
    # Every reason is listed, including those that never fired, so "this
    # criterion found nothing" is distinguishable from "this criterion is not a
    # thing".
    for name in RejectReason.names():
        out.append(f"  {name:<20} {by_reason.get(name, 0)}")

    out += _section("Columns")
    out.append(f"  {'#':<3} {'range':<11} {'w':<4} {'blank':<7} {'name':<16} samples")
    for i, field in enumerate(geometry.fields):
        samples = stats.sample_for(i)
        shown = "  ".join(repr(s) for s in samples) if samples else "(no records)"
        out.append(
            f"  {i + 1:<3} {f'{field.start}-{field.end}':<11} {field.width:<4} "
            f"{stats.blank_rate(i):<7.1%} {(field.name or ''):<16} {shown}"
        )

    out += _section("Blank runs")
    out.append(
        f"  runs of at least {geometry.min_gutter} blank positions separate columns; "
        "narrower runs sit inside one"
    )
    out.append(
        "  " + " ".join(f"{r.start}-{r.end}({r.width})" for r in geometry.blank_runs)
    )

    out += _section("Reproducing or correcting this geometry")
    out.append("  Pass the following back to get exactly this table again, or edit")
    out.append("  a boundary first to correct it:")
    out.append("")
    out.append(f"    --columns '{geometry.columns_spec()}'")

    if diagnostics:
        out += _section("Warnings")
        for d in diagnostics:
            out.append(f"  [{d.code}] {d.message}")

    out.append("")
    return "\n".join(out)
