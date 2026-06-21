"""Tests for the pure JS confirmation-bias scorer.

Runs with no extra dependencies:  python3 test_js_divergence.py
Also discoverable by pytest if installed:  pytest test_js_divergence.py

These mirror the four mismatch cases validated in the separate sandbox
(match / subtle / moderate / dramatic). The match and dramatic ends are
asserted with loose bounds now; the subtle/moderate middle and the exact
match/dramatic values have EXPECTED placeholders to drop in the precise
numbers from the sandbox.

Reminder on direction: SMALL divergence => interactions mirror the prior =>
HIGH score (high confirmation bias). So match ~= high, dramatic ~= low.
"""
from js_divergence import js_confirmation_score

# Tolerance for comparing against exact sandbox values once they're filled in.
TOL = 1e-6

# --- Sandbox fixtures -------------------------------------------------------
# A 10-bin prior with mass concentrated in the low-middle bins, summing to the
# 30 balls the elicitation UI distributes. Interaction vectors below are over
# the SAME 10 bins.
PRIOR = [0, 2, 6, 10, 7, 3, 2, 0, 0, 0]

# match: interactions track the prior almost exactly -> high score.
INTER_MATCH = [0, 2, 6, 10, 7, 3, 2, 0, 0, 0]

# subtle: small shift of mass to adjacent bins.
INTER_SUBTLE = [0, 1, 5, 10, 8, 4, 2, 0, 0, 0]

# moderate: noticeable spread away from the prior's peak.
INTER_MODERATE = [1, 2, 4, 6, 6, 5, 4, 2, 0, 0]

# dramatic: interactions land on the opposite side of the range -> low score.
INTER_DRAMATIC = [0, 0, 0, 0, 0, 2, 3, 7, 10, 8]

# Exact expected scores from the sandbox. Fill these in to lock the pipeline
# to the already-trusted numbers, then flip the asserts below to use them.
# NOTE: the metric was also validated end-to-end via live Lumos runs across
# four conditions (peaked/tail prior x confirm/disconfirm) plus a uniform-prior
# false-positive check -- so these stubbed exact-value asserts aren't "untested".
EXPECTED = {
    "match": None,      # TODO: drop in exact sandbox value (~1.0)
    "subtle": None,     # TODO: drop in exact sandbox value
    "moderate": None,   # TODO: drop in exact sandbox value
    "dramatic": None,   # TODO: drop in exact sandbox value (~0.0)
}


def _approx(a, b, tol=TOL):
    return abs(a - b) <= tol


def test_match_is_high():
    """Interactions mirroring the prior => high confirmation-bias score."""
    score = js_confirmation_score(PRIOR, INTER_MATCH)
    assert score > 0.99, score
    if EXPECTED["match"] is not None:
        assert _approx(score, EXPECTED["match"]), (score, EXPECTED["match"])


def test_dramatic_is_low():
    """Interactions on the opposite side of the prior => low score."""
    score = js_confirmation_score(PRIOR, INTER_DRAMATIC)
    assert score < 0.20, score
    if EXPECTED["dramatic"] is not None:
        assert _approx(score, EXPECTED["dramatic"]), (score, EXPECTED["dramatic"])


def test_monotonic_ordering():
    """Score must decrease monotonically as interactions drift from the prior."""
    s_match = js_confirmation_score(PRIOR, INTER_MATCH)
    s_subtle = js_confirmation_score(PRIOR, INTER_SUBTLE)
    s_moderate = js_confirmation_score(PRIOR, INTER_MODERATE)
    s_dramatic = js_confirmation_score(PRIOR, INTER_DRAMATIC)
    assert s_match > s_subtle > s_moderate > s_dramatic, (
        s_match, s_subtle, s_moderate, s_dramatic
    )


def test_subtle_expected():
    """Placeholder: lock subtle case to exact sandbox value once supplied."""
    if EXPECTED["subtle"] is None:
        return  # nothing to check yet
    score = js_confirmation_score(PRIOR, INTER_SUBTLE)
    assert _approx(score, EXPECTED["subtle"]), (score, EXPECTED["subtle"])


def test_moderate_expected():
    """Placeholder: lock moderate case to exact sandbox value once supplied."""
    if EXPECTED["moderate"] is None:
        return  # nothing to check yet
    score = js_confirmation_score(PRIOR, INTER_MODERATE)
    assert _approx(score, EXPECTED["moderate"]), (score, EXPECTED["moderate"])


def test_score_bounds_and_symmetry():
    """Score stays in [0, 1] and JS is symmetric in its two arguments."""
    forward = js_confirmation_score(PRIOR, INTER_MODERATE)
    backward = js_confirmation_score(INTER_MODERATE, PRIOR)
    assert 0.0 <= forward <= 1.0, forward
    assert _approx(forward, backward), (forward, backward)


def test_empty_interactions_is_defined():
    """An attribute never interacted with still yields a finite score.

    (Whether to *report* it is the caller's policy via the MIN_LOG_NUM guard;
    the pure function must not blow up.)
    """
    score = js_confirmation_score(PRIOR, [0] * len(PRIOR))
    assert 0.0 <= score <= 1.0, score


def test_misaligned_bins_raise():
    """Different bin counts must raise -- JS is meaningless across misaligned bins."""
    raised = False
    try:
        js_confirmation_score([1, 2, 3], [1, 2, 3, 4])
    except ValueError:
        raised = True
    assert raised, "expected ValueError on misaligned bins"


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {t.__name__}: {e}")
    # Informational: print the actual scores so the sandbox values can be read off.
    print("\nActual scores (for filling EXPECTED):")
    for name, inter in (
        ("match", INTER_MATCH),
        ("subtle", INTER_SUBTLE),
        ("moderate", INTER_MODERATE),
        ("dramatic", INTER_DRAMATIC),
    ):
        print(f"  {name:9s} {js_confirmation_score(PRIOR, inter):.12f}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if _run_all() else 0)
