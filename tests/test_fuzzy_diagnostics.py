from __future__ import annotations

from app.fuzzy_diagnostics import score_fuzzy_match


def test_score_fuzzy_match_marks_exact_reference_and_descriptor_overlap() -> None:
    score = score_fuzzy_match(
        "5205r green",
        "Patek 5205R green New 4/2026 415.000 HKD",
    )

    assert score.reference_score == 100
    assert score.descriptor_overlap_score == 100
    assert "exact_reference_match" in score.reason_codes


def test_score_fuzzy_match_marks_low_descriptor_overlap() -> None:
    score = score_fuzzy_match(
        "5205r green",
        "Patek 5205R black dial 2026 415.000 HKD",
    )

    assert score.reference_score == 100
    assert score.descriptor_overlap_score == 0
    assert "descriptor_overlap_low" in score.reason_codes


def test_score_fuzzy_match_uses_canonical_descriptor_aliases() -> None:
    score = score_fuzzy_match(
        "7118/1200a grey",
        "PP 7118/1200A gray N1/2026 790 000HKD",
    )

    assert score.reference_score == 100
    assert score.descriptor_overlap_score == 100
    assert "descriptor_overlap_low" not in score.reason_codes


def test_score_fuzzy_match_uses_material_alias_phrases() -> None:
    score = score_fuzzy_match(
        "rm07-01 rg snow",
        "RM07-01 Rose Gold Diamonds Snow Setting Red Jasper USD328000",
    )

    assert score.reference_score == 100
    assert score.descriptor_overlap_score == 100
    assert "descriptor_overlap_low" not in score.reason_codes


def test_score_fuzzy_match_uses_material_phrase_queries() -> None:
    score = score_fuzzy_match(
        "rm07-01 rose gold snow",
        "RM07-01 RG Snow Diamonds Red Lips Good Condition 260000US",
    )

    assert score.reference_score == 100
    assert score.descriptor_overlap_score == 100
    assert "descriptor_overlap_low" not in score.reason_codes
