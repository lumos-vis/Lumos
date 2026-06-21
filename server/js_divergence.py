"""Standalone Jensen-Shannon confirmation-bias scoring.

This module is intentionally *pure*: no sockets, no server state, no dataset
lookups. It takes two raw count vectors defined over the SAME bins (a prior
elicited via the balls-into-bins interface, and the user's interaction counts
bucketed into those same bins) and returns a single [0, 1] confirmation-bias
score.

Keeping the math isolated here lets it be unit-tested against known
mismatch cases independently of the transport/pipeline wiring. The socket
handler and bias.compute_metrics() should call js_confirmation_score(); they
must not inline this math.

Sign convention (important -- do not "fix" the inversion):
    Raw JS divergence is SMALL when the interaction distribution mirrors the
    prior, which means the user only explored what they already believed --
    i.e. HIGH confirmation bias. So the reported score INVERTS the normalized
    divergence: score = 1 - D/ln2. A score near 1.0 => high confirmation bias;
    a score near 0.0 => the user explored against their prior.
"""
import numpy as np

# Smoothing constant added to the normalized probability vectors before the
# KL terms so that empty bins (common: 30 balls over 10 bins) don't make the
# KL divergence undefined. Small enough not to distort the elicited shape.
EPSILON = 1e-9

# Jensen-Shannon divergence in nats is bounded above by ln(2); dividing by it
# rescales the raw divergence to [0, 1] before the sign inversion.
_LN2 = np.log(2.0)


def _normalize(counts):
    """Turn a non-negative count vector into a probability vector.

    An all-zero vector (e.g. an attribute the user never interacted with)
    carries no information, so it is treated as uniform rather than producing
    a divide-by-zero.
    """
    arr = np.asarray(counts, dtype=float)
    total = arr.sum()
    if total <= 0:
        return np.full(arr.shape, 1.0 / arr.size)
    return arr / total


def _kl(p, m):
    """KL(p || m) in nats. Assumes p, m are strictly positive (post-smoothing)."""
    return float(np.sum(p * np.log(p / m)))


def js_confirmation_score(prior_counts, interaction_counts):
    """Map two count vectors over the same bins to a [0, 1] confirmation score.

    Args:
        prior_counts: elicited prior, one count per bin (need not sum to a
            fixed total -- it is normalized internally).
        interaction_counts: user interactions bucketed into the SAME bins, in
            the SAME order. Must be the same length as prior_counts.

    Returns:
        float in [0, 1]. ~1.0 => interactions mirror the prior => HIGH
        confirmation bias. ~0.0 => interactions diverge from the prior.

    Raises:
        ValueError: if the two vectors differ in length (bins must align) or a
            vector is empty.
    """
    p = np.asarray(prior_counts, dtype=float)
    q = np.asarray(interaction_counts, dtype=float)
    if p.size == 0 or q.size == 0:
        raise ValueError("count vectors must be non-empty")
    if p.shape != q.shape:
        raise ValueError(
            f"bins must align: prior has {p.size} bins, "
            f"interactions have {q.size} bins"
        )

    # Normalize to probability vectors, then epsilon-smooth the normalized
    # vectors and renormalize so every bin is strictly positive for the KL terms.
    p = _normalize(p)
    q = _normalize(q)
    p = _normalize(p + EPSILON)
    q = _normalize(q + EPSILON)

    # Jensen-Shannon divergence (nats): D = 1/2 KL(p||m) + 1/2 KL(q||m).
    m = 0.5 * (p + q)
    divergence = 0.5 * _kl(p, m) + 0.5 * _kl(q, m)

    # Rescale [0, ln2] -> [0, 1], then INVERT so small divergence (interactions
    # match prior) maps to a HIGH confirmation-bias score. See module docstring.
    normalized = divergence / _LN2
    score = 1.0 - normalized

    # Clamp to guard against tiny floating-point excursions outside [0, 1].
    return float(min(1.0, max(0.0, score)))
