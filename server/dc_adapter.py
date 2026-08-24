"""Adapter + integration glue between Prasit's stored priors and the DC metric.

This module is deliberately SEPARATE from the validated math in dc_metric.py.
dc_metric.py is frozen (its unit-tested functions must not change); everything
here is reshape/plumbing that can evolve without touching the math.

Prasit's merge stores priors as:

    CLIENTS[pid]["priors"]["{attribute}::{condition}"] = PriorBelief

where condition in {'diagnosed', 'not_diagnosed'} and each PriorBelief is one
group's distribution:

    { attribute, condition, binEdges, counts, ballCount, confidence,
      categories?  (present => categorical) }

dc_metric expects, per variable, ONE contract object:

    { attribute,
      binEdges | categories,                       # shared across the two groups
      countsByGroup: {
          diagnosed:    { counts, confidence },
          nonDiagnosed: { counts, confidence } } }

build_beliefs_from_priors() performs that reshape. See its docstring for the
completeness guard (d) and the shared-bins assertion (e).
"""
import numpy as np

import dc_metric

# The diagnosis column is the y_d LABEL, never an elicited belief. Reuse the
# constant from dc_metric (do not duplicate the literal). Even if the modal
# elicits it, it must be excluded from beliefs and from the completeness count.
LABEL_ATTR = dc_metric.LABEL_ATTR

# --------------------------------------------------------------------------- #
# Reproducible seeding for the LIVE Monte-Carlo null-sampling paths (Shiyao's
# request). A single fixed seed, single-sourced here and shared by every live call
# site, so a given check's percentile depends ONLY on its inputs + this seed --
# never on call order or how many draws happened earlier in the session. This is a
# reproducibility knob, NOT a replay/audit-log feature: the seed is a constant, not
# logged per check.
# --------------------------------------------------------------------------- #
LIVE_SIMULATION_SEED = 20260820


def live_rng():
    """A fresh, seeded numpy Generator for the live null-sampling paths.

    Construct exactly ONE per top-level evaluation (one evaluate_trigger, one
    evaluate_selection_progressive_trigger, one submit-time percentile) and thread
    that single generator through everything inside it -- e.g. selection_percentile_
    by_var's per-variable loop advances THIS generator, so variables get independent,
    non-repeating draws while the whole check stays reproducible. Do NOT call this
    per variable inside a loop: that would reset the stream and make every variable
    replay an identical null distribution (spurious correlation).
    """
    return np.random.default_rng(LIVE_SIMULATION_SEED)


# The study elicits exactly six NON-LABEL variables; the DC map is only computed
# once all six have BOTH conditions. (Count-based so we don't hard-code the
# attribute names -- the elicitation UI defines which six, minus the label.)
EXPECTED_VARIABLE_COUNT = 6

# Prasit's condition strings -> the group keys dc_metric's contract uses.
CONDITION_TO_GROUP = {
    "diagnosed": "diagnosed",
    "not_diagnosed": "nonDiagnosed",
}


def _split_key(key):
    """'attribute::condition' -> (attribute, condition); tolerate a bare key."""
    if "::" in key:
        attr, cond = key.rsplit("::", 1)
        return attr, cond
    return key, "default"


def group_priors_by_attribute(priors):
    """{'attr::cond': belief, ...} -> {attr: {cond: belief, ...}}.

    Prefers the belief's own 'attribute'/'condition' fields; falls back to
    parsing the storage key.
    """
    grouped = {}
    for key, belief in priors.items():
        attr = belief.get("attribute") or _split_key(key)[0]
        cond = belief.get("condition") or _split_key(key)[1]
        grouped.setdefault(attr, {})[cond] = belief
    return grouped


def _shared_bins(attr, diagnosed_belief, non_belief):
    """Return the bins dict for the contract, asserting the two groups agree.

    Guard (e): both conditions are elicited over the FULL column, so their
    binEdges (numerical) or categories (categorical) must be identical. If a
    future binning change makes them diverge, fail LOUDLY here rather than
    silently bucketing the two groups differently.
    """
    d_is_cat = diagnosed_belief.get("categories") is not None
    n_is_cat = non_belief.get("categories") is not None
    if d_is_cat != n_is_cat:
        raise ValueError(
            f"[dc_adapter] '{attr}': one condition is categorical and the other "
            f"is numerical (diagnosed_cat={d_is_cat}, nonDiagnosed_cat={n_is_cat})"
        )

    if d_is_cat:
        d_cats = list(diagnosed_belief["categories"])
        n_cats = list(non_belief["categories"])
        if d_cats != n_cats:
            raise ValueError(
                f"[dc_adapter] '{attr}': categories differ between conditions "
                f"({d_cats} != {n_cats})"
            )
        return {"categories": d_cats}

    d_edges = [float(x) for x in diagnosed_belief.get("binEdges", [])]
    n_edges = [float(x) for x in non_belief.get("binEdges", [])]
    if d_edges != n_edges:
        raise ValueError(
            f"[dc_adapter] '{attr}': binEdges differ between conditions "
            f"({d_edges} != {n_edges})"
        )
    return {"binEdges": diagnosed_belief["binEdges"]}


def build_beliefs_from_priors(priors):
    """Reshape stored priors into dc_metric's contract.

    Returns (beliefs, report):
      beliefs: {attribute: contract_object} -- ONLY attributes that have BOTH
               conditions (guard d). Incomplete attributes are omitted.
      report:  {"complete": [...], "skipped_incomplete": [...]} for logging.

    Raises ValueError (guard e) if the two conditions of a complete attribute
    disagree on their bins/categories.
    """
    grouped = group_priors_by_attribute(priors)
    beliefs = {}
    skipped_incomplete = []

    for attr, by_cond in grouped.items():
        # The label column is the y_d target, never a belief variable. Drop it
        # before any completeness/countsByGroup handling so it can neither become
        # a belief nor be counted toward readiness or reported as incomplete.
        if attr == LABEL_ATTR:
            continue

        diagnosed = by_cond.get("diagnosed")
        non = by_cond.get("not_diagnosed")

        # Guard (d): both conditions required, else the variable is excluded.
        if diagnosed is None or non is None:
            skipped_incomplete.append(attr)
            continue

        bins = _shared_bins(attr, diagnosed, non)  # guard (e)

        beliefs[attr] = {
            "attribute": attr,
            **bins,
            "countsByGroup": {
                "diagnosed": {
                    "counts": diagnosed["counts"],
                    "confidence": diagnosed.get("confidence"),
                },
                "nonDiagnosed": {
                    "counts": non["counts"],
                    "confidence": non.get("confidence"),
                },
            },
        }

    report = {
        "complete": sorted(beliefs.keys()),
        "skipped_incomplete": sorted(skipped_incomplete),
    }
    return beliefs, report


def flatten_priors_for_bias(priors, condition="diagnosed"):
    """{'attr::cond': belief} -> {attr: belief}, for consumers that expect a
    single prior per attribute (bias.py's js_divergence) rather than
    dc_metric's countsByGroup-per-attribute contract.

    Picks `condition`'s belief per attribute (falling back to whichever
    condition is present, if only one has been committed so far -- bias
    metrics are computed incrementally as priors come in, before both
    conditions of every attribute are necessarily complete).
    """
    grouped = group_priors_by_attribute(priors)
    flat = {}
    for attr, by_cond in grouped.items():
        if attr == LABEL_ATTR:
            continue
        belief = by_cond.get(condition) or next(iter(by_cond.values()), None)
        if belief is not None:
            flat[attr] = belief
    return flat


def is_ready(priors, expected=EXPECTED_VARIABLE_COUNT):
    """True when at least `expected` variables have BOTH conditions elicited.

    Returns (ready, beliefs, report) so the caller doesn't rebuild twice.
    """
    beliefs, report = build_beliefs_from_priors(priors)
    return (len(beliefs) >= expected), beliefs, report


# --------------------------------------------------------------------------- #
# Interaction id extraction from bias_logs (the same list the JS metric reads).
# Each bias_logs entry is the raw frontend message; ids live at entry["data"]["id"]
# and are either a scalar teen id or a list (aggregate/group interaction).
# --------------------------------------------------------------------------- #
def _ids_of(entry):
    data = entry.get("data", {})
    if "id" not in data:
        return []
    val = data["id"]
    if isinstance(val, list):
        return [v for v in val if v is not None and v != "-"]
    if val is None or val == "-":
        return []
    return [val]


def interacted_ids(bias_logs):
    """De-duplicated, order-preserving union of every id touched in bias_logs."""
    seen = set()
    ordered = []
    for entry in bias_logs:
        for i in _ids_of(entry):
            if i not in seen:
                seen.add(i)
                ordered.append(i)
    return ordered


def current_interaction_ids(bias_logs):
    """Ids from just the most recent bias-relevant interaction (real-time set)."""
    if not bias_logs:
        return []
    return _ids_of(bias_logs[-1])


def selected_ids(bias_logs):
    """Reconstruct the current selection from add/remove events in bias_logs.

    Semantics (documented approximation): click_add_item selects, click_remove_item
    deselects, click_group toggles its whole group (deselect if all its ids are
    currently selected, else select). Hover events (mouseout_*) do not affect
    selection. This mirrors the frontend's selectedObjects behavior closely
    enough for selection_bias; it is derived purely from bias_logs (no extra
    state), so it stays consistent with what the JS metric sees.
    """
    selected = set()
    for entry in bias_logs:
        itype = entry.get("interactionType")
        ids = _ids_of(entry)
        if itype == "click_add_item":
            selected.update(ids)
        elif itype == "click_remove_item":
            selected.difference_update(ids)
        elif itype == "click_group":
            if ids and all(i in selected for i in ids):
                selected.difference_update(ids)
            else:
                selected.update(ids)
    return list(selected)


def compute_phase_metrics(dc_map, bias_logs):
    """Compute the DC phase metrics from a cached dc_map + bias_logs id sets.

    Reads the CACHED dc_map (never recomputes it). Returns a plain dict suitable
    for embedding in the interaction response's output_data.

      real_time_bias           : over the ids of the CURRENT interaction
      overall_interaction_bias : over ALL interacted ids so far
      selection_bias           : over the reconstructed current selection
      filter_bias              : None -- visible-before/after id sets are not
                                 available in bias_logs (filter interactions are
                                 not in COMPUTE_BIAS_FOR_TYPES), so it is skipped
                                 for now. See report note.
    """
    current = current_interaction_ids(bias_logs)
    every = interacted_ids(bias_logs)
    selected = selected_ids(bias_logs)
    return {
        "real_time_bias": dc_metric.real_time_bias(dc_map, current),
        "overall_interaction_bias": dc_metric.overall_interaction_bias(dc_map, every),
        "selection_bias": dc_metric.selection_bias(dc_map, selected),
        "filter_bias": None,  # not available from bias_logs; skipped this pass
        "n_current": len(current),
        "n_interacted": len(every),
        "n_selected": len(selected),
    }


def compute_dwell_metrics(detailed_map, bias_logs):
    """Compute the dwell-weighted bias metrics from a cached detailed dc_map.

    Sibling of compute_phase_metrics: it reads the CACHED dc_map_detailed (the
    per-teen {dc, consistency, weights} map, never recomputed) and the same
    bias_logs. dwell_by_teen sums interactionDuration over scalar mouseout_item
    entries; dwell_bias / dwell_bias_v then weight DC (point-level) and per-variable
    consistency (variable-level) by that dwell.

    dwell_bias_v is asked for the js-WEIGHTED form. DC is itself a js-weighted sum,
    so the raw per-variable scores sit on a different scale from the point-level
    DwellBias they are supposed to break down. Weighted, they decompose it exactly
    (sum_v score[v] / sum_v w_v == dwell_bias), which is what makes them readable as
    each variable's contribution. Set here rather than in dc_metric: the flag exists
    so callers can choose, and the default stays Lester's.

    Returns a plain dict for embedding in the interaction response's output_data:

      dwell_bias   : point-level, dwell-weighted mean DC minus the all-teen baseline
      dwell_bias_v : {variable: score}, per-variable dwell-weighted consistency bias
      n_dwelled    : number of distinct teens with any summed dwell

    NOTE: dwell_bias / dwell_bias_v raise fail-loud if a dwelled id is absent from
    the map. That strict guarantee is intentional and stays in dc_metric; the caller
    (on_interaction) guards this at the handler boundary so a stray id cannot break
    the live interaction response.
    """
    dwell = dc_metric.dwell_by_teen(bias_logs)
    return {
        "dwell_bias": dc_metric.dwell_bias(detailed_map, dwell),
        "dwell_bias_v": dc_metric.dwell_bias_v(detailed_map, dwell, weighted=True),
        "n_dwelled": len(dwell),
    }


def compute_selection_percentile(dc_map, selected_ids, n_trials=1000, rng=None):
    """Thin wrapper: SelectionBias percentile from a cached dc_map + final selection.

    Mirrors compute_phase_metrics / compute_dwell_metrics -- reads the CACHED
    scalar dc_map, never recomputes DC. Meant to run ONCE at task-submission time
    over the participant's final selected ids. Returns a plain dict for
    logging/embedding; selection_bias_percentile is None when nothing present in
    dc_map was selected.
    """
    return {
        "selection_bias_percentile": dc_metric.selection_bias_percentile(
            dc_map, selected_ids, n_trials, rng),
    }


def compute_dwell_percentile(detailed_map, dwell, n_trials=1000, rng=None):
    """Thin wrapper: DwellBias percentile from a cached detailed dc_map + dwell.

    Sibling of compute_dwell_metrics. Present for parity and for the deferred
    GATED live integration (see dc_metric.dwell_percentile_ready); it is NOT wired
    into on_interaction yet. dwell_bias_percentile is None when nothing was
    dwelled.
    """
    return {
        "dwell_bias_percentile": dc_metric.dwell_bias_percentile(
            detailed_map, dwell, n_trials, rng),
    }


# A variable whose re-pooled per-teen DC is CONSTANT carries no signal, and its null
# distribution collapses onto its real value -- every draw ties, so the percentile is a
# guaranteed 1.0 and the variable would clear any threshold on every check. The clearest
# way in is w_v == 0 (the participant drew the diagnosed and non-diagnosed distributions
# identically for that variable, so dc_metric.vba returns log(1)=0 in every bin), which
# scoped_detailed_map's 0/0 guard turns into dc 0.0 for all teens. The tolerance is a
# float-noise margin, not a modelling knob: a spread this small is not a real signal.
DEGENERATE_DC_EPSILON = 1e-12


def is_degenerate_scope(scoped):
    """True when a single-variable scoped map carries no signal to test.

    Every teen's re-pooled DC being the SAME value makes the null test vacuous rather
    than extreme: dwell_bias is (constant - constant) = 0 for the real value AND for
    every null draw, so dwell_bias_percentile returns a guaranteed 1.0 and the variable
    would clear any threshold on every check, forever. The clearest way in is w_v == 0 --
    the participant drew the two groups identically for that variable, so vba returns
    log(1)=0 in every bin and scoped_detailed_map's 0/0 guard yields dc 0.0 for all teens
    -- which is real participant behaviour, not a corrupt map.

    Detected HERE, on the scoped map, rather than from the weights: this is the exact
    quantity the null is drawn over, so it catches every route to a flat DC (a zero
    weight, a constant C_v, a one-teen map) without enumerating them. Checked BEFORE
    sampling, so an excluded variable costs no Monte Carlo.
    """
    dcs = [entry["dc"] for entry in scoped.values()]
    if not dcs:
        return True
    return (max(dcs) - min(dcs)) <= DEGENERATE_DC_EPSILON


def scoped_detailed_map(dc_map_detailed, visible_vars):
    """Re-pool each teen's DC over ONLY the currently-visible axis variables.

    The dwell trigger fires off DwellBias scored against a null distribution, and
    both the real score and the null read one number per teen: entry["dc"], the
    pooled weighted mean of that teen's per-variable belief-consistency
    (Sum_v w_v*C_v / Sum_v w_v). This returns a NEW detailed map whose "dc" is that
    same weighted mean but restricted to visible_vars, so the entire unchanged
    percentile pipeline (dc_metric.dwell_bias / sample_null_dwell_bias /
    dwell_bias_percentile) then scores the trigger on just what the participant has
    on screen, instead of on all six beliefs pooled.

    Nothing in dc_metric is touched: the re-pool uses each teen's ALREADY-computed
    "consistency"/"weights" dicts (from _consistency_and_weights_for_teen), so no DC
    is recomputed. Weight is teen-independent, so this is a pure reshape.

    Args:
        dc_map_detailed: cached dc_map_detailed, {teen: {"dc", "consistency",
            "weights"}}. NOT mutated -- a fresh dict is built per teen.
        visible_vars: the variables currently on the x/y axes. Nones are dropped
            first (get_current_axes returns one None when only one axis is
            assigned), so a single-axis view scopes to the one variable present.

    Returns:
        A new {teen: {"dc", "consistency", "weights"}} map. "consistency" and
        "weights" are the SAME (unchanged) dicts as the input -- only "dc" is
        recomputed -- so the caller's full-pooled map (e.g. the summary path) is
        never affected. Sum_v w_v == 0 over the visible subset -> "dc" 0.0, the
        same 0/0 guard dc_for_teen uses.

    Raises:
        ValueError if no visible variable remains after dropping Nones.
        KeyError (fail-loud, per convention) if NONE of the visible variables is in
            a teen's consistency dict -- an axis carrying a non-belief attribute.
            A partial miss (at least one visible var present) scopes to whatever is
            present rather than raising.
    """
    requested = list(dict.fromkeys(v for v in visible_vars if v is not None))
    if not requested:
        raise ValueError(
            f"[scoped_detailed_map] no visible variables to scope to "
            f"(visible_vars={visible_vars!r})")

    scoped = {}
    for tid, entry in dc_map_detailed.items():
        consistency = entry["consistency"]
        weights = entry["weights"]
        present = [v for v in requested if v in consistency]
        if not present:
            raise KeyError(
                f"[scoped_detailed_map] none of the visible variables {requested} "
                f"are in teen {tid!r}'s consistency {list(consistency.keys())}")
        numerator = sum(weights[v] * consistency[v] for v in present)
        denominator = sum(weights[v] for v in present)
        scoped_dc = 0.0 if denominator == 0.0 else numerator / denominator
        # Reuse (do not copy) the original consistency/weights dicts: only "dc"
        # differs, and the input map must stay intact for the pooled callers.
        scoped[tid] = {"dc": scoped_dc,
                       "consistency": consistency,
                       "weights": weights}
    return scoped


def degenerate_vars(dc_map_detailed, variables=None):
    """Which of these variables are DEGENERATE -> {variable: "degenerate_null"}.

    The logging companion to selection_percentile_by_var, which silently OMITS such a
    variable so it cannot become a candidate. Absence alone is ambiguous in a log -- a
    variable can also be missing because it is not a belief variable at all -- so this
    names the ones dropped for degeneracy specifically, giving the selection trigger the
    same {variable: code} marker the dwell trace carries.

    Same variable selection and the same is_degenerate_scope verdict the scorer uses, so
    the two can never disagree about which variables were dropped. Cheap by design: it
    re-pools DC per variable but runs NO null-sampling, which is the expensive half.

    Purely diagnostic, so it never raises: a map that is empty or not in the detailed
    {teen: {"dc", "consistency", "weights"}} shape yields {} ("no opinion") rather than
    propagating. Losing the marker costs a log line; it cannot affect candidacy, which
    is decided by the scorer omitting the variable on its own.
    """
    try:
        if not dc_map_detailed:
            return {}
        belief_vars = list(next(iter(dc_map_detailed.values()))["consistency"].keys())
        if variables is not None:
            wanted = set(variables)
            belief_vars = [v for v in belief_vars if v in wanted]
        return {v: "degenerate_null" for v in belief_vars
                if is_degenerate_scope(scoped_detailed_map(dc_map_detailed, [v]))}
    except Exception:
        return {}


def selection_percentile_by_var(dc_map_detailed, selected_ids, n_trials=1000,
                                rng=None, variables=None, selected_by_var=None):
    """SelectionBias percentile per belief variable, one variable at a time.

    Selection has no axes to scope to (unlike the dwell trigger), so instead of one
    pooled score this reports, per variable, how extreme the selection looks when DC
    is re-pooled onto that ONE variable. For each variable v it reuses the exact
    pooled pipeline: scoped_detailed_map(detailed, [v]) re-pools DC onto v, the
    scalar "dc" is extracted per teen, and dc_metric.selection_bias_percentile scores
    it the same way compute_selection_percentile does today. Nothing in dc_metric is
    modified; this is pure reshape + reuse.

    Args:
        dc_map_detailed: cached dc_map_detailed, {teen: {"dc", "consistency",
            "weights"}}. Not mutated (scoped_detailed_map builds fresh entries).
        selected_ids: the participant's selected teen ids.
        n_trials: null draws per variable (passed straight through, never reduced).
        rng: numpy Generator for reproducible tests, or None for the np.random
            global (the live default), matching selection_bias_percentile.
        selected_by_var: optional {variable: ids} of PER-VARIABLE eligible selections.
            When given, variable v is scored against selected_by_var.get(v) instead of
            the shared selected_ids -- each variable judged only on the picks made while
            it was actually active (llm_trigger.eligible_selection_by_var). A variable
            absent from the mapping is scored against an empty set, which
            selection_bias_percentile already reports as None (k == 0). None (the
            default) keeps the original behaviour of scoring every variable against the
            one shared selection, which is what the pooled submit-time path wants.
        variables: optional collection restricting WHICH variables are scored. None
            (the default) preserves the original behavior -- score EVERY belief
            variable in the map. When given, only the intersection with the map's
            actual belief variables is scored (order follows the map, so results stay
            deterministic); any name not a belief variable is dropped, and the null-
            sampling is SKIPPED entirely for excluded variables -- not scored then
            filtered -- so scoping to the active set is a real compute saving. An
            empty intersection yields {} (the caller's reduction treats that as
            nothing-to-fire, matching a total miss).

    Returns:
        {variable: percentile} over the variables actually SCORED. Each percentile is
        in [0, 1], or None for the k == 0 case selection_bias_percentile already guards
        (nothing eligible for that variable is present in the map). {} for an empty map,
        or for an empty variables intersection.

        A variable whose scope is DEGENERATE (constant DC across every teen -- see
        is_degenerate_scope) is OMITTED entirely rather than scored: its null collapses
        onto its real value, so it would return a guaranteed 1.0 and clear any threshold
        on every check. Being absent is what keeps it out of candidacy, exactly as the
        dwell scorer drops such a variable from its own percentile dict. Callers wanting
        to LOG which variables were dropped that way ask degenerate_vars for the same
        verdict; the check runs before any sampling, so an omitted variable costs no
        Monte Carlo here either.

    The belief-variable list is read the same way dwell_bias_v reads it (the
    consistency keys of any entry), so the two stay in lockstep on what "all
    variables" means; `variables` only narrows that set, never widens it.
    """
    if not dc_map_detailed:
        return {}
    belief_vars = list(next(iter(dc_map_detailed.values()))["consistency"].keys())
    if variables is None:
        scored_vars = belief_vars
    else:
        # Intersect while preserving the map's deterministic variable order; drop any
        # requested name that is not a belief variable (e.g. a non-belief filter attr).
        wanted = set(variables)
        scored_vars = [v for v in belief_vars if v in wanted]
    out = {}
    for v in scored_vars:
        scoped = scoped_detailed_map(dc_map_detailed, [v])
        if is_degenerate_scope(scoped):
            continue
        scalar_dc = {tid: entry["dc"] for tid, entry in scoped.items()}
        # Per-variable eligible selection when the caller supplied one, else the single
        # shared selection (the original, still the pooled submit-time behaviour).
        ids_for_v = (selected_by_var.get(v, ()) if selected_by_var is not None
                     else selected_ids)
        out[v] = dc_metric.selection_bias_percentile(
            scalar_dc, ids_for_v, n_trials, rng)
    return out
