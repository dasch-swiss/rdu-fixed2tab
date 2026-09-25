"""Detection, classification and naming."""

from __future__ import annotations

from collections import Counter

import pytest
from conftest import EXPECTED_NAMES, EXPECTED_RANGES, REFERENCE_TXT, covers, needs_reference

from fixed2tab.carve import carve_cells
from fixed2tab.detect import (
    LineSource,
    ShortLinePolicy,
    classify_input,
    derive_fields,
    detect_record_width,
    profile_lengths,
)
from fixed2tab.header import name_fields, sanitise, tokenize
from fixed2tab.model import Field, InputError, RejectReason


def geometry_of(path, min_gutter=2, policy=ShortLinePolicy.DEFAULT, header_line=None):
    src = LineSource(path)
    profile, _ = profile_lengths(src)
    classified, _ = classify_input(src, profile.record_width, policy, header_line)
    fields, runs = derive_fields(classified.blank, min_gutter)
    named, notes = name_fields(fields, classified.preamble.header)
    return profile, classified, named, runs, notes


# --------------------------------------------------------------- the line model


@pytest.mark.req("REQ-0.1")
@pytest.mark.req("REQ-0.2")
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"aaa\nbbb", ["aaa", "bbb"]),  # unterminated final line counts
        (b"aaa\nbbb\n", ["aaa", "bbb"]),  # trailing LF adds no line
        (b"aaa\r\nbbb\r\n", ["aaa", "bbb"]),  # one trailing CR stripped
        (b"aa\rbb\ncc\n", ["aa\rbb", "cc"]),  # a lone CR is not a terminator
        ("﻿aaa\nbbb\n".encode(), ["aaa", "bbb"]),  # BOM removed before measuring
    ],
)
def test_line_model(tmp_path, raw, expected):
    f = tmp_path / "t.txt"
    f.write_bytes(raw)
    assert [text for _n, text in LineSource(f)] == expected


@pytest.mark.req("REQ-1.8")
def test_decode_failure_names_the_line(tmp_path):
    f = tmp_path / "bad.txt"
    f.write_bytes(b"ok\n\xff\xfe bad\nok\n")
    with pytest.raises(InputError, match="line 2"):
        list(LineSource(f, "utf-8"))


@pytest.mark.req("REQ-0.3")
@pytest.mark.req("REQ-0.4")
def test_record_width_ties_break_to_the_greatest():
    assert detect_record_width(Counter({10: 5, 20: 3})) == (10, ())
    assert detect_record_width(Counter({10: 3, 20: 3})) == (20, (10, 20))


@pytest.mark.req("REQ-3.5")
def test_empty_input_is_an_error(tmp_path):
    f = tmp_path / "e.txt"
    f.write_bytes(b"")
    with pytest.raises(InputError):
        profile_lengths(LineSource(f))


# ------------------------------------------------------------------- geometry


@pytest.mark.req("REQ-1.2")
@pytest.mark.req("REQ-1.3")
def test_exact_ranges_on_the_committed_fixtures(clean, messy):
    for path in (clean, messy):
        _p, _c, named, _r, _n = geometry_of(path)
        assert [(f.start, f.end) for f in named] == EXPECTED_RANGES, path.name


@pytest.mark.req("REQ-4.5")
def test_min_gutter_sensitivity(clean):
    counts = {}
    for mg in (1, 2, 3, 4):
        _p, _c, named, _r, _n = geometry_of(clean, min_gutter=mg)
        counts[mg] = len(named)
    # Load-bearing and file-dependent: at 1 it splits inside "4h  6m 22s"; at 3
    # it merges q and code, whose separating run is exactly two wide.
    assert counts == {1: 16, 2: 11, 3: 10, 4: 8}


@pytest.mark.req("DC-4")
def test_fixtures_are_coverage_complete(clean, messy):
    if not REFERENCE_TXT.exists():
        pytest.skip("reference file absent")
    assert covers(clean, REFERENCE_TXT)
    assert covers(messy, REFERENCE_TXT)


@pytest.mark.req("DC-4")
def test_a_prefix_excerpt_would_have_been_wrong(tmp_path):
    """The trap DC-4 exists to prevent, demonstrated rather than asserted."""
    if not REFERENCE_TXT.exists():
        pytest.skip("reference file absent")
    lines = REFERENCE_TXT.read_text(encoding="ascii").split("\n")
    data = [ln for ln in lines if len(ln) == 128]
    for n in (5, 10, 20):
        f = tmp_path / f"prefix{n}.txt"
        f.write_text("\n".join(lines[:2] + data[:n]) + "\n", encoding="ascii")
        _p, _c, named, _r, _n = geometry_of(f)
        assert [(x.start, x.end) for x in named] != EXPECTED_RANGES, (
            f"a {n}-record prefix unexpectedly matched; DC-4's premise would be wrong"
        )


# ------------------------------------------------------------- classification


@pytest.mark.req("REQ-2.4")
def test_blank_padded_repeated_header_is_rejected(tmp_path):
    """The path that had no validation evidence and no oracle until REQ-2.4 was amended.

    A paginated repeat of the header is padded out to the record width, so it is
    length-conforming and not byte-identical to the original. Before the
    amendment it passed every criterion and was carved as a data row, filling
    the gutters and corrupting the geometry.
    """
    if not REFERENCE_TXT.exists():
        pytest.skip("reference file absent")
    lines = REFERENCE_TXT.read_text(encoding="ascii").split("\n")
    title, header = lines[0], lines[1]
    data = [ln for ln in lines if len(ln) == 128][:300]

    out = [title, header]
    for i, line in enumerate(data):
        if i and i % 50 == 0:
            out += [header.ljust(128), title.ljust(128)]
        out.append(line)
    f = tmp_path / "paginated.txt"
    f.write_text("\n".join(out) + "\n", encoding="ascii")

    _p, classified, named, _r, _n = geometry_of(f)
    assert classified.counts.table_rows == 300
    assert classified.by_reason["preamble-identity"] == 10
    assert [(x.start, x.end) for x in named] == EXPECTED_RANGES


@pytest.mark.req("REQ-2.3")
def test_all_zero_record_survives(tmp_path):
    """Two distinct non-blank characters, but a legitimate record.

    Rejecting on the distinct-character test alone would discard this — the same
    class of silent, biased loss that thresholded gutter detection caused.
    """
    f = tmp_path / "zeros.txt"
    rows = ["  0     0       0.0      0" + " " * 10] * 4
    f.write_text("HEADING\n" + "\n".join(rows) + "\n", encoding="ascii")
    _p, classified, _n, _r, _x = geometry_of(f)
    assert classified.counts.table_rows == 4


@pytest.mark.req("REQ-2.8")
def test_control_character_line_is_rejected(tmp_path):
    good = "abc 123  def 45  xy"
    ctl = "abc \f23  def 45  xy"  # same length, so length cannot claim it first
    assert len(good) == len(ctl)
    f = tmp_path / "ctl.txt"
    f.write_text(f"heading\n{good}\n{ctl}\n{good}\n{good}\n", encoding="ascii")
    _p, classified, _n, _r, _x = geometry_of(f)
    assert classified.by_reason["control-char"] == 1
    assert classified.counts.table_rows == 3


@pytest.mark.req("REQ-2.6")
def test_attribution_follows_the_declared_order(tmp_path):
    """A line matching several criteria is attributed to the first."""
    row = "aaa 111  bbb 222  cc"
    f = tmp_path / "multi.txt"
    # "--" is short AND a rule line. Length is declared first, so it must win.
    f.write_text("hdr\n" + "\n".join([row] * 3) + "\n--\n", encoding="ascii")
    _p, classified, _n, _r, _x = geometry_of(f)
    assert classified.by_reason["length"] == 2  # the heading and the short rule
    assert classified.by_reason["rule-line"] == 0  # never reached for that line
    assert classified.counts.table_rows == 3


@pytest.mark.req("REQ-3.2")
def test_reconciliation_holds(clean, messy):
    for path in (clean, messy):
        _p, classified, _n, _r, _x = geometry_of(path)
        assert classified.counts.reconciles, path.name


@pytest.mark.req("REQ-2.7")
def test_every_reason_is_reported_including_zeros(clean):
    _p, classified, _n, _r, _x = geometry_of(clean)
    assert set(classified.by_reason) == set(RejectReason.names())


# -------------------------------------------------------------------- naming


@pytest.mark.req("REQ-5.1")
def test_names_derive_by_overlap(clean):
    _p, _c, named, _r, _n = geometry_of(clean)
    assert [f.name for f in named] == EXPECTED_NAMES


@pytest.mark.req("REQ-5.1")
def test_slicing_the_header_would_be_wrong(clean):
    """Why overlap rather than slicing: five of eleven names come out corrupt."""
    _p, classified, named, _r, _n = geometry_of(clean)
    header = (classified.preamble.header or "").ljust(128)
    sliced = [header[f.start - 1 : f.end].strip().replace(" ", "_") for f in named]
    wrong = sum(1 for got, want in zip(sliced, EXPECTED_NAMES) if got != want)
    assert wrong == 5


@pytest.mark.req("REQ-5.2")
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("DELTAT[s]", "DELTAT_s"),  # the worked example: not DELTATs, not DELTAT_s_
        ("NEW MOON DATE", "NEW_MOON_DATE"),
        ("a--b", "a_b"),
        ("  padded  ", "padded"),
        ("&&&", ""),
    ],
)
def test_sanitise(raw, expected):
    assert sanitise(raw) == expected


@pytest.mark.req("REQ-5.3")
def test_orphan_header_token_warns(clean):
    _p, _c, _n, _r, notes = geometry_of(clean)
    assert any(d.code == "header-token-discarded" and "&" in d.message for d in notes)


@pytest.mark.req("REQ-5.7")
def test_no_header_names_positionally():
    named, notes = name_fields([Field(1, 5), Field(7, 11)], None)
    assert [f.name for f in named] == ["col1", "col2"]
    assert any(d.code == "no-header" for d in notes)


@pytest.mark.req("REQ-5.8")
def test_duplicate_names_are_disambiguated():
    named, notes = name_fields([Field(1, 3), Field(5, 7)], "AAA AAA")
    assert [f.name for f in named] == ["AAA", "AAA_2"]
    assert any(d.code == "duplicate-names" for d in notes)


@pytest.mark.req("REQ-5.1")
def test_tokenize_spans_are_1_based_inclusive():
    assert tokenize("  ab  cde ") == ((3, 4, "ab"), (7, 9, "cde"))


# -------------------------------------------------------------------- carving


@pytest.mark.req("REQ-1.4")
@pytest.mark.req("REQ-1.5")
@pytest.mark.req("REQ-1.6")
def test_cells_are_verbatim_but_edge_trimmed():
    record = " 25. 1.-2000  4h  6m 22s    "
    fields = (Field(1, 12), Field(13, 24), Field(25, 28))
    assert carve_cells(record, fields) == ["25. 1.-2000", "4h  6m 22s", ""]


@pytest.mark.req("REQ-1.6")
def test_no_numeric_coercion():
    fields = (Field(1, 6), Field(7, 13))
    assert carve_cells("-.075  -2000  ", fields) == ["-.075", "-2000"]


@pytest.mark.req("REQ-0.7")
def test_header_line_moves_the_preamble_boundary(tmp_path):
    """Without this, --header-line cannot rescue a full-width heading at all."""
    rows = [f"{i:4d}  {i * 2:6d}  {i * 3:8.2f}  " for i in range(20)]
    width = len(rows[0])
    # The heading is padded to exactly the record width — the case REQ-0.7b
    # names --header-line for, and the one it could not previously rescue.
    heading = "  ID     VAL   SCORE".ljust(width)
    assert len(heading) == width
    f = tmp_path / "r.txt"
    f.write_text("TITLE\n" + heading + "\n" + "\n".join(rows) + "\n", encoding="ascii")
    src = LineSource(f)
    without, _ = classify_input(src, width, ShortLinePolicy.PAD)
    withflag, _ = classify_input(src, width, ShortLinePolicy.PAD, header_line=2)
    assert without.counts.table_rows == withflag.counts.table_rows + 1
    assert withflag.preamble.header is not None
    assert "ID" in withflag.preamble.header


@needs_reference
@pytest.mark.req("REQ-6.1")
def test_reference_file_geometry():
    _p, classified, named, _r, _n = geometry_of(REFERENCE_TXT)
    assert classified.counts.table_rows == 49486
    assert classified.counts.rejected_lines == 2
    assert [(f.start, f.end) for f in named] == EXPECTED_RANGES
    assert [f.name for f in named] == EXPECTED_NAMES
