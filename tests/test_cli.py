"""End-to-end behaviour: exit codes, outputs, determinism, --help."""

from __future__ import annotations

import pytest
from conftest import EXPECTED_NAMES, REFERENCE_TSV, REFERENCE_TXT, needs_reference

from fixed2tab.cli import build_parser, main, parse_columns
from fixed2tab.model import ExitCode, RejectReason, UsageError


def convert(tmp_path, source, *extra, prefix="out"):
    """Run the CLI as a user would; return the status and the three outputs."""
    table = tmp_path / f"{prefix}.tsv"
    report = tmp_path / f"{prefix}.report.txt"
    rejected = tmp_path / f"{prefix}.rejected.txt"
    status = main(
        [
            "--input",
            str(source),
            "--table",
            str(table),
            "--report",
            str(report),
            "--rejected",
            str(rejected),
            *extra,
        ]
    )
    return status, table, report, rejected


@pytest.mark.req("REQ-1.11")
def test_all_three_outputs_always_written(tmp_path, clean):
    status, table, report, rejected = convert(tmp_path, clean)
    assert status == ExitCode.SUCCESS
    for path in (table, report, rejected):
        assert path.exists(), path.name


@pytest.mark.req("REQ-6.1")
def test_clean_fixture_matches_expected_tsv(tmp_path, clean, expected_tsv):
    _s, table, _r, _j = convert(tmp_path, clean)
    assert table.read_text(encoding="utf-8") == expected_tsv.read_text(encoding="utf-8")


@pytest.mark.req("REQ-3.2")
def test_reconciliation_is_stated_in_the_report(tmp_path, messy):
    _s, table, report, rejected = convert(tmp_path, messy)
    rows = table.read_text().rstrip("\n").split("\n")
    rejects = rejected.read_text().rstrip("\n").split("\n")
    assert len(rows) + len(rejects) == 16
    assert "[OK]" in report.read_text()


@pytest.mark.req("REQ-0.6")
def test_messy_fixture_warns_about_width_share(tmp_path, messy):
    """Its line-length share at the detected width is 50%, below the threshold.

    Not 37.5% — that is the *record* share after classification, which is a
    different quantity from the one REQ-0.6 measures.
    """
    _s, _t, report, _j = convert(tmp_path, messy)
    assert "width-share" in report.read_text()


@pytest.mark.req("REQ-4.2")
@pytest.mark.req("REQ-4.7")
def test_columns_string_round_trips(tmp_path, clean):
    _s, table, report, _j = convert(tmp_path, clean, prefix="first")
    spec = next(
        line.split("--columns", 1)[1].strip().strip("'")
        for line in report.read_text().split("\n")
        if "--columns" in line and line.strip().startswith("--columns")
    )
    _s2, table2, _r2, _j2 = convert(tmp_path, clean, "--columns", spec, prefix="second")
    assert table2.read_text() == table.read_text()


@pytest.mark.req("REQ-0.11")
@pytest.mark.req("REQ-0.15")
def test_determinism_across_paths(tmp_path, clean):
    """Different directory, different filename, identical bytes.

    Running twice from the same path could not detect a leaked path or
    timestamp; this can. It matters because Galaxy randomises the dataset path
    per job, so a leak would make byte-identical reruns impossible there.
    """
    one = tmp_path / "alpha"
    two = tmp_path / "beta"
    one.mkdir()
    two.mkdir()
    (one / "first_name.txt").write_bytes(clean.read_bytes())
    (two / "second_name.txt").write_bytes(clean.read_bytes())
    _s1, t1, r1, _j1 = convert(one, one / "first_name.txt")
    _s2, t2, r2, _j2 = convert(two, two / "second_name.txt")
    assert t1.read_bytes() == t2.read_bytes()
    assert r1.read_bytes() == r2.read_bytes()


@pytest.mark.req("REQ-5.5")
def test_header_plain_writes_names(tmp_path, clean):
    _s, table, _r, _j = convert(tmp_path, clean, "--header", "plain")
    assert table.read_text().split("\n")[0].split("\t") == EXPECTED_NAMES


@pytest.mark.req("REQ-5.4")
def test_header_omitted_by_default(tmp_path, clean):
    _s, table, _r, _j = convert(tmp_path, clean)
    assert not table.read_text().startswith("DATE\t")


@pytest.mark.req("REQ-4.9")
@pytest.mark.parametrize(
    "spec",
    ["1-12,oops", "27-18", "1-12,10-20", "1-5000", "1-12,,18-27", "abc"],
)
def test_bad_columns_exit_usage(tmp_path, clean, spec):
    status, *_ = convert(tmp_path, clean, "--columns", spec)
    assert status == ExitCode.USAGE


@pytest.mark.req("REQ-4.9")
def test_parse_columns_rejects_overlap():
    with pytest.raises(UsageError, match="overlap"):
        parse_columns("1-12,10-20", 128)


@pytest.mark.req("REQ-4.5")
def test_min_gutter_zero_is_a_usage_error(tmp_path, clean):
    status, *_ = convert(tmp_path, clean, "--min-gutter", "0")
    assert status == ExitCode.USAGE


@pytest.mark.req("REQ-1.12")
def test_unknown_encoding_is_a_usage_error(tmp_path, clean):
    status, *_ = convert(tmp_path, clean, "--encoding", "not-a-codec")
    assert status == ExitCode.USAGE


@pytest.mark.req("REQ-3.5")
def test_empty_input_exits_input_error(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    status, *_ = convert(tmp_path, empty)
    assert status == ExitCode.INPUT


@pytest.mark.req("REQ-3.6")
def test_no_records_exits_input_error(tmp_path):
    f = tmp_path / "rules.txt"
    f.write_text("----\n----\n----\n", encoding="ascii")
    status, *_ = convert(tmp_path, f)
    assert status == ExitCode.INPUT


@pytest.mark.req("REQ-3.4")
def test_strict_trips_on_a_disallowed_reason(tmp_path, clean, capsys):
    """--strict alone fails on any rejection; --allow-rejects relaxes it.

    The clean fixture rejects its two preamble lines, both as ``length``.
    """
    bare, *_ = convert(tmp_path, clean, "--strict", prefix="bare")
    assert bare == ExitCode.STRICT
    assert "2 line(s) rejected as length" in capsys.readouterr().err
    tripped, *_ = convert(
        tmp_path, clean, "--strict", "--allow-rejects", "rule-line", prefix="tripped"
    )
    assert tripped == ExitCode.STRICT
    allowed, *_ = convert(
        tmp_path, clean, "--strict", "--allow-rejects", "length", prefix="allowed"
    )
    assert allowed == ExitCode.SUCCESS


@pytest.mark.req("REQ-3.7")
def test_small_sample_warns(tmp_path):
    f = tmp_path / "few.txt"
    f.write_text("heading row\n" + "\n".join(["aa 11  bb 2"] * 3) + "\n", encoding="ascii")
    _s, _t, report, _j = convert(tmp_path, f)
    assert "under-determined" in report.read_text()


@pytest.mark.req("REQ-1.13")
def test_help_documents_every_option_and_the_hazards():
    """--help is the documentation, so its completeness is a test, not a habit."""
    parser = build_parser()
    text = parser.format_help()
    options = [
        action.option_strings[0]
        for action in parser._actions
        if action.option_strings and action.option_strings[0] != "-h"
    ]
    missing = [opt for opt in options if opt not in text]
    assert not missing, f"undocumented in --help: {missing}"
    lowered = text.lower()
    assert "convert spaces to tabs" in lowered  # the Galaxy upload hazard
    assert "reduce that number" in lowered  # the positional-offset migration note
    assert "1-based" in lowered
    assert "exit status" in lowered
    for reason in RejectReason.names():
        assert reason in text  # the rejected file's reason column is documented


def test_help_examples_survive_rendering():
    """argparse collapses runs of spaces in option help before printing it.

    A literal '4h  6m 22s' was therefore shown with one space, so the example
    that demonstrates --collapse-spaces could not demonstrate it. Wrapping is
    normalised here; the spaces inside the examples are what is asserted.
    """
    flat = " ".join(build_parser().format_help().split())
    assert "'4h··6m 22s' becomes '4h·6m 22s' (· marks one space)" in flat
    assert "inside cells like '4h··6m 22s'" in flat


@needs_reference
@pytest.mark.req("REQ-6.1")
def test_golden_reference_conversion(tmp_path):
    """The headline criterion: byte-identical to a conversion made by hand.

    Skipped when the 6.4 MB file is absent; it exceeds the Galaxy tool
    repository's 1 MB per-file cap and is archived on Zenodo instead.
    """
    status, table, _r, rejected = convert(tmp_path, REFERENCE_TXT)
    assert status == ExitCode.SUCCESS

    mine = table.read_text(encoding="utf-8").rstrip("\n").split("\n")
    gold = REFERENCE_TSV.read_text(encoding="utf-8").rstrip("\n").split("\n")[3:]
    assert len(mine) == 49486
    assert mine == gold

    assert sum(1 for row in mine if "epagomene" in row) == 677
    assert sum(1 for row in mine if row.split("\t")[8].strip()) == 822
    assert len(rejected.read_text().rstrip("\n").split("\n")) == 2


@pytest.mark.req("REQ-4.3")
def test_columns_still_reports_the_blank_run_scan(tmp_path, clean):
    """Supplying --columns must not suppress the scan the report depends on."""
    _s, _t, report, _j = convert(tmp_path, clean, "--columns", "1-12,18-27")
    text = report.read_text()
    assert "Blank runs" in text
    assert "13-17(5)" in text  # a real run from the reference geometry


@pytest.mark.req("REQ-4.10")
def test_columns_warns_about_uncovered_data(tmp_path, clean):
    """Line reconciliation conserves lines; only this conserves characters."""
    _s, _t, report, _j = convert(tmp_path, clean, "--columns", "1-12:DATE,18-27:TIME")
    text = report.read_text()
    assert "uncovered-positions" in text
    # Reported as ranges, and complete — the sparse `code` column at 92-93 sits
    # far to the right and would be hidden by any truncated list of positions.
    assert "92-93" in text
    assert "31-37" in text


@pytest.mark.req("REQ-1.11")
@pytest.mark.req("REQ-0.7b")
def test_empty_preamble_still_emits_three_outputs(tmp_path):
    """No header at all: names go positional and the rejected file is empty."""
    rows = ["aa 11  bb 222"] * 8
    f = tmp_path / "noheader.txt"
    f.write_text("\n".join(rows) + "\n", encoding="ascii")
    status, table, report, rejected = convert(tmp_path, f)
    assert status == ExitCode.SUCCESS
    assert rejected.exists() and rejected.read_text() == ""
    assert len(table.read_text().rstrip("\n").split("\n")) == 8
    assert "no-header" in report.read_text()


@pytest.mark.req("REQ-0.4")
def test_tied_lengths_recorded_in_the_report(tmp_path):
    """Equal counts at two lengths: the greater wins, and the tie is disclosed."""
    f = tmp_path / "tied.txt"
    short = "aa 11  bb 2"
    long_ = "aa 11  bb 222"
    f.write_text("\n".join([short] * 3 + [long_] * 3) + "\n", encoding="ascii")
    _s, _t, report, _j = convert(tmp_path, f)
    text = report.read_text()
    assert "width-tie" in text
    assert f"record width     {len(long_)}" in text


@pytest.mark.req("REQ-0.1")
def test_crlf_input_yields_the_same_table_as_lf(tmp_path, clean):
    """Galaxy's uploader normalises line endings; the CLI must agree with it."""
    crlf = tmp_path / "crlf.txt"
    crlf.write_bytes(clean.read_bytes().replace(b"\n", b"\r\n"))
    _s1, lf_table, _r1, _j1 = convert(tmp_path, clean, prefix="lf")
    _s2, crlf_table, _r2, _j2 = convert(tmp_path, crlf, prefix="crlf")
    assert crlf_table.read_bytes() == lf_table.read_bytes()


@pytest.mark.req("REQ-1.8")
def test_undecodable_input_exits_input_error_naming_the_line(tmp_path, capsys):
    f = tmp_path / "bad.txt"
    f.write_bytes(b"heading line here\n" + b"aaa 111 bbb 22 cc\n" * 3 + b"\xff\xfe bad line\n")
    status, *_ = convert(tmp_path, f)
    assert status == ExitCode.INPUT
    assert "line 5" in capsys.readouterr().err


@pytest.mark.req("REQ-0.12")
def test_tab_in_a_record_is_fatal(tmp_path, capsys):
    """Fatal, pointed, and before any output exists.

    The tabbed line is padded to the record width and carries digits, so it
    passes every classification criterion: it is provably a record, and only
    the TAB guard can stop it.
    """
    good = "aaa 111  bbb 22"
    tabbed = "aaa 111\tbbb 22".ljust(len(good))
    f = tmp_path / "tabbed.txt"
    f.write_text(f"heading\n{good}\n{good}\n{tabbed}\n{good}\n", encoding="ascii")
    status, table, report, rejected = convert(tmp_path, f)
    assert status == ExitCode.INPUT
    err = capsys.readouterr().err
    assert "line 4" in err
    assert "TAB" in err and "convert spaces to tabs" in err
    # No truncated table for a later pipeline step to mistake for a result.
    assert not any(p.exists() for p in (table, report, rejected))


@pytest.mark.req("REQ-4.11")
def test_widths_equal_the_equivalent_columns(tmp_path, clean):
    """A FORTRAN FORMAT transcribes directly, and must mean the same thing."""
    _s1, by_columns, _r1, _j1 = convert(
        tmp_path, clean, "--columns", "1-12:DATE,18-27:TIME", prefix="cols"
    )
    # 1-12 then a 5-position skip then 18-27: exactly what 12,5x,10 says.
    _s2, by_widths, _r2, _j2 = convert(
        tmp_path, clean, "--widths", "12:DATE,5x,10:TIME", prefix="widths"
    )
    assert by_widths.read_text() == by_columns.read_text()


@pytest.mark.req("REQ-4.11")
def test_widths_skip_defaults_to_one(tmp_path, clean):
    from fixed2tab.cli import parse_widths

    assert parse_widths("2,x,3", 10) == parse_widths("2,1x,3", 10)


@pytest.mark.req("REQ-4.11")
@pytest.mark.parametrize("spec", ["5,oops", "0", "5,3000", "", "5,0x,3"])
def test_bad_widths_exit_usage(tmp_path, clean, spec):
    """An empty value is a mistake to report, not a silent fallback to detection."""
    status, *_ = convert(tmp_path, clean, "--widths", spec)
    assert status == ExitCode.USAGE


@pytest.mark.req("REQ-4.9")
def test_empty_columns_is_also_a_usage_error(tmp_path, clean):
    status, *_ = convert(tmp_path, clean, "--columns", "")
    assert status == ExitCode.USAGE


@pytest.mark.req("REQ-4.12")
def test_collapse_spaces_is_opt_in_and_global(tmp_path, clean):
    _s, verbatim, _r, _j = convert(tmp_path, clean, prefix="verbatim")
    _s2, collapsed, _r2, _j2 = convert(tmp_path, clean, "--collapse-spaces", prefix="collapsed")
    # Asserted as a property rather than on one hand-picked cell: some cell must
    # carry a run of two spaces when off, and none may when on.
    verbatim_cells = [
        c for row in verbatim.read_text().rstrip("\n").split("\n") for c in row.split("\t")
    ]
    collapsed_cells = [
        c for row in collapsed.read_text().rstrip("\n").split("\n") for c in row.split("\t")
    ]
    assert any("  " in c for c in verbatim_cells), "fixture has no multi-space cell to test"
    assert not any("  " in c for c in collapsed_cells)
    # and the collapse is only whitespace: the tokens themselves are untouched
    assert [c.split() for c in verbatim_cells] == [c.split() for c in collapsed_cells]


@pytest.mark.req("REQ-5.11")
def test_headingless_fields_are_flagged(tmp_path):
    """In formatted output one heading often covers several columns."""
    rows = [f" {i:4d}   {i * 2:5d}   ABC   {i * 3:4d}" for i in range(8)]
    f = tmp_path / "grouped.txt"
    # A single heading sitting above only the middle of three columns.
    f.write_text("           GROUP LABEL\n" + "\n".join(rows) + "\n", encoding="ascii")
    _s, _t, report, _j = convert(tmp_path, f)
    assert "fields-without-heading" in report.read_text()


@pytest.mark.req("REQ-5.8")
def test_supplied_names_suppress_stale_naming_warnings(tmp_path):
    """A report must not warn about a problem the user's own names resolved.

    Derived names are computed first; if the supplied ones are pasted over them
    afterwards, the diagnostics from derivation survive and the report warns
    about duplicates that no longer exist.
    """
    rows = [f" {i:4d}  {i * 2:5d}  {i * 3:5d} " for i in range(8)]
    f = tmp_path / "dupes.txt"
    f.write_text("  VAL    VAL    VAL\n" + "\n".join(rows) + "\n", encoding="ascii")

    _s, _t, derived_report, _j = convert(tmp_path, f, prefix="derived")
    assert "duplicate-names" in derived_report.read_text()

    _s2, _t2, named_report, _j2 = convert(
        tmp_path, f, "--columns", "2-5:First,8-12:Second,15-19:Third", prefix="named"
    )
    text = named_report.read_text()
    assert "duplicate-names" not in text
    assert "header-token-discarded" not in text  # those tokens named the fields


# The right-trimmed workflow, end to end. Rows of varied length, none as long as
# the longest, so detection picks the mode and the report advises padding.
TRIMMED_HEADING = "  ID   NAME    VAL"
TRIMMED_ROWS = [
    "  1  a      1",
    "  2  bb     22",
    "  3  ccc    333",
    "  4  d      4",
    "  5  eeeee  55555",
    "  6  ff     6",
    "  7  g      77",
    "  8  hhhhhh 8888888",
]


@pytest.fixture
def trimmed(tmp_path):
    f = tmp_path / "trim.txt"
    f.write_text("\n".join([TRIMMED_HEADING, *TRIMMED_ROWS]) + "\n", encoding="ascii")
    return f


@pytest.mark.req("REQ-0.7b")
def test_right_trimmed_advice_names_header_line(tmp_path, trimmed):
    """The printed remediation must be one that works when followed verbatim."""
    status, _t, report, _j = convert(tmp_path, trimmed)
    assert status == ExitCode.SUCCESS
    advice = next(ln for ln in report.read_text().split("\n") if "[right-trimmed]" in ln)
    assert "--short-lines pad --header-line N" in advice


@pytest.mark.req("REQ-0.7b")
def test_padding_without_header_line_is_a_usage_error(tmp_path, trimmed, capsys):
    """Without a stated boundary, padding silently promoted a record to the header."""
    status, table, report, rejected = convert(
        tmp_path, trimmed, "--record-width", "19", "--short-lines", "pad"
    )
    assert status == ExitCode.USAGE
    assert "--header-line" in capsys.readouterr().err
    assert not any(p.exists() for p in (table, report, rejected))


@pytest.mark.req("REQ-0.7b")
def test_right_trimmed_workflow_recovers_every_row(tmp_path, trimmed):
    status, table, report, rejected = convert(
        tmp_path, trimmed, "--record-width", "19", "--short-lines", "pad", "--header-line", "1"
    )
    assert status == ExitCode.SUCCESS
    assert len(table.read_text().rstrip("\n").split("\n")) == len(TRIMMED_ROWS)
    assert rejected.read_text() == f"1\tlength\t{TRIMMED_HEADING}\n"
    text = report.read_text()
    assert f"header           {TRIMMED_HEADING!r}" in text
    assert "padding-and-preamble" in text
    # The stated width and share are the effective ones the columns use.
    assert "record width     19 (detected 13)" in text
    assert "lines at width   1 of 9 lines (11.1%)" in text


@pytest.mark.req("REQ-0.7b")
def test_header_line_via_the_cli(tmp_path, clean):
    """--header-line picks which preamble line names the columns."""
    _s, default_table, _r, _j = convert(tmp_path, clean, prefix="auto")
    status, table, report, _j2 = convert(tmp_path, clean, "--header-line", "1", prefix="forced")
    assert status == ExitCode.SUCCESS
    text = report.read_text()
    assert "header-line      1" in text
    title = clean.read_text(encoding="utf-8").split("\n")[0]
    assert f"header           {title!r}" in text
    # The real heading is not record-width, so it is still rejected, not carved.
    assert table.read_text() == default_table.read_text()


@pytest.mark.req("REQ-0.7b")
def test_header_line_zero_means_no_preamble(tmp_path):
    rows = [f"{i:4d}  {i * 2:6d}  {i * 3:6d}" for i in range(1, 9)]
    # A short first record, which the length rule would have taken as preamble.
    rows[0] = rows[0].rstrip()
    f = tmp_path / "bare.txt"
    f.write_text("\n".join(r.rstrip() for r in rows) + "\n", encoding="ascii")
    status, table, report, rejected = convert(
        tmp_path, f, "--short-lines", "pad", "--header-line", "0"
    )
    assert status == ExitCode.SUCCESS
    assert rejected.read_text() == ""
    assert len(table.read_text().rstrip("\n").split("\n")) == 8
    assert "preamble         none" in report.read_text()


def test_negative_header_line_is_a_usage_error(tmp_path, clean):
    status, *_ = convert(tmp_path, clean, "--header-line", "-1")
    assert status == ExitCode.USAGE
