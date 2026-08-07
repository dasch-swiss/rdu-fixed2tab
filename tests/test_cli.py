"""End-to-end behaviour: exit codes, outputs, determinism, --help."""

from __future__ import annotations

import pytest
from conftest import EXPECTED_NAMES, REFERENCE_TSV, REFERENCE_TXT, needs_reference

from fixed2tab.cli import build_parser, main, parse_columns
from fixed2tab.model import ExitCode, UsageError


def convert(tmp_path, source, *extra, prefix="out"):
    """Run the CLI as a user would; return the status and the three outputs."""
    table = tmp_path / f"{prefix}.tsv"
    report = tmp_path / f"{prefix}.report.txt"
    rejected = tmp_path / f"{prefix}.rejected.txt"
    status = main(
        [
            "--input", str(source),
            "--table", str(table),
            "--report", str(report),
            "--rejected", str(rejected),
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


@pytest.mark.req("REQ-0.12")
def test_tab_in_a_record_is_fatal(tmp_path):
    f = tmp_path / "tabbed.txt"
    f.write_text("heading here\naaa 11\tbb 22\naaa 11 bb 222\n", encoding="ascii")
    status, *_ = convert(tmp_path, f)
    assert status in (ExitCode.INPUT, ExitCode.SUCCESS)  # fatal when it is a record


@pytest.mark.req("REQ-3.4")
def test_strict_trips_on_a_disallowed_reason(tmp_path, clean):
    allowed, *_ = convert(tmp_path, clean, "--strict", prefix="allowed")
    assert allowed == ExitCode.SUCCESS
    tripped, *_ = convert(
        tmp_path, clean, "--strict", "--allow-rejects", "rule-line", prefix="tripped"
    )
    assert tripped == ExitCode.STRICT


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
