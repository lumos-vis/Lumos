"""Trigger logic for the LLM intervention condition.

Three responsibilities live here, kept small so the socket layer never changes
when the trigger policy does:

1. evaluate_trigger -- the realtime dwell decision. Readiness, recheck spacing AND
   SCORING are all PER VARIABLE, measured on each variable's OWN eligible dwell history:
   a hover counts toward variable v only if v was active (on an axis or filtered) AT THE
   MOMENT of that specific hover (eligible_dwell_by_teen_by_var). A variable is checkable
   once it has accumulated MIN_ELIGIBLE_DWELL_SECONDS of its own eligible dwell
   (first-check floor) and DWELL_RECHECK_SECONDS of NEW eligible dwell since its own last
   check (recheck spacing) -- so a variable can be active right now yet still not ready if
   its own history is thin, and one variable's dwell never advances another's clock. Each
   ready variable is then scored INDEPENDENTLY -- dc_adapter re-pools DC onto that ONE
   variable and dc_metric.dwell_bias_percentile scores it against THAT variable's own
   per-teen dwell -- and the single winner is chosen by the shared priority hierarchy
   (_reduce_by_priority), exactly as the selection trigger picks its target. Ready
   variables are NEVER pooled into one joint score. should_trigger is a thin bool wrapper
   for callers that don't need the reason.

2. evaluate_selection_trigger -- the live mid-task decision: the progressive trigger
   (3) held to a fixed pick schedule (a check at the 5th selection and every
   SELECTION_RECHECK_PICKS after: 5th, 7th, 9th), whether or not an earlier check
   fired.

3. evaluate_selection_progressive_trigger -- a per-selection sibling of the realtime
   dwell trigger (1), scoring the running selection per variable and reducing to one
   winner by the shared priority hierarchy. The scoring is SCOPED to the currently-active
   variables (x/y axes + active filters, via _axis_and_filter_vars) -- the same active set
   the dwell trigger uses -- so a variable the participant is not looking at or filtering
   on neither earns nor blocks a fire. Reached live only through
   evaluate_selection_trigger (2).

Both triggers (1) and (3) now share three helpers rather than each owning a copy:
_axis_and_filter_vars (the active set kept SPLIT into its axis/filter tiers, which the
hierarchy needs), _confidence_for_var, and _reduce_by_priority (the threshold-then-rank
reduction). Dwell and selection differ in HOW the per-variable percentiles are computed;
they agree entirely on how a winner is picked from them.

This module reads from dc_metric (scoring) plus dc_adapter (the scoped-map reshape)
and llm_intervention (get_current_axes / get_current_filters); it modifies none of them.
The per-variable eligible-dwell replay (eligible_dwell_by_teen_by_var /
filters_active_as_of) lives here rather than in dc_adapter so it can reuse
get_current_filters' replay rules (via a bounded prefix) without dc_adapter having to
import the heavier llm_intervention.
"""
import dc_adapter
import dc_metric
import llm_intervention


# --------------------------------------------------------------------------- #
# Realtime dwell gate. Readiness, recheck spacing AND scoring are PER VARIABLE, each
# measured on that variable's OWN eligible dwell (hovers that happened while it was
# active); the fire decision is that variable's DwellBias percentile against its null
# distribution (dc_metric.dwell_bias_percentile), not a raw threshold.
# --------------------------------------------------------------------------- #
MIN_ELIGIBLE_DWELL_SECONDS = 20.0  # a variable's OWN eligible dwell before its first check
DWELL_PERCENTILE_THRESHOLD = 0.80  # fire when DwellBias is at/above this percentile
DWELL_RECHECK_SECONDS = 10.0       # min NEW ELIGIBLE dwell for the SAME variable between checks
SYSTEM_FIRE_COOLDOWN_MS = 30_000   # min REAL (wall-clock) time between any two fired
                                   # interventions, layered on top of the per-variable
                                   # recheck spacing above (Shiyao's system-wide cooldown)

# A variable whose re-pooled per-teen DC is CONSTANT carries no signal, and its null
# distribution collapses onto its real value -- every draw ties, so the percentile is a
# guaranteed 1.0 and the variable would fire on every single check. The clearest way in
# is w_v == 0 (the participant drew the diagnosed and non-diagnosed distributions
# identically for that variable, so vba returns log(1)=0 in every bin), which
# scoped_detailed_map's 0/0 guard turns into dc 0.0 for all teens. Such a variable is
# dropped from candidacy BEFORE any null sampling rather than scored. The tolerance is
# a float-noise margin, not a modelling knob: a spread this small is not a real signal.
DEGENERATE_DC_EPSILON = 1e-12

# --------------------------------------------------------------------------- #
# Selection gate -- scored on the participant's RUNNING SELECTION rather than on
# live dwell. Same percentile test (dc_metric.selection_bias_percentile), applied
# per variable by the progressive trigger below.
# --------------------------------------------------------------------------- #
MIN_SELECTIONS = 5                     # too few picks makes the mean DC meaningless
SELECTION_PERCENTILE_THRESHOLD = 0.80  # fire when SelectionBias is at/above this percentile
SELECTION_RECHECK_PICKS = 2            # picks between two checks (5th, 7th, 9th ...)


# --------------------------------------------------------------------------- #
# SHARED between the dwell trigger and the selection trigger.
#
# Both score their active variables INDEPENDENTLY, one percentile each, and then
# have to reduce that {variable: percentile} dict to one target. That reduction --
# Shiyao's priority hierarchy -- is identical for the two; only the way the
# percentiles were computed differs. It lives here, once, rather than as a closure
# inside either trigger.
# --------------------------------------------------------------------------- #
def _axis_and_filter_vars(client_record):
    """The currently-active variables, kept SPLIT by tier -> (axis_vars, filter_vars).

    The x/y axis attributes (Nones dropped) and the attributes with an active filter,
    as two sets rather than their union: the priority hierarchy classifies a candidate
    by WHICH of the two it came from, so collapsing them (as
    llm_intervention.get_currently_active_variables does) throws away exactly the
    information the reduction needs. Callers that want the plain active set take the
    union themselves.

    A variable that is BOTH on an axis and filtered appears in both sets; _reduce_by_
    priority resolves that to the AXIS tier (axis membership wins).
    """
    axes = llm_intervention.get_current_axes(client_record)
    axis_vars = {v for v in (axes.get("x"), axes.get("y")) if v is not None}
    return axis_vars, llm_intervention.get_current_filters(client_record)


def _confidence_for_var(client_record, var):
    """The elicited confidence (1-100) for a belief variable, for the priority tiebreak.

    Read straight from the cached, reshaped beliefs
    (client_record["beliefs"][var]["countsByGroup"]["diagnosed"]["confidence"]);
    beliefs is only ever populated alongside dc_map_detailed (server.maybe_compute_dc_map),
    and is already scoped to the six complete belief variables -- exactly the pool a
    candidate variable comes from -- so this is a safe direct read. Both conditions
    carry the SAME slider value (one confidence per variable), so "diagnosed" is
    representative.

    Missing/None (legacy priors saved before the confidence step, or beliefs not yet
    built) -> 0, a sentinel below the real 1-100 range so the variable sorts LAST
    within its tier without being dropped from candidacy. Never raises.
    """
    try:
        conf = (client_record.get("beliefs", {})[var]
                ["countsByGroup"]["diagnosed"]["confidence"])
        return 0.0 if conf is None else float(conf)
    except (KeyError, TypeError, ValueError):
        return 0.0


def _reduce_by_priority(percentile_by_var, axis_vars, client_record, threshold):
    """Shiyao's PRIORITY HIERARCHY -> the winning variable, or None.

    THRESHOLD FIRST, THEN RANK. The candidate set is every scored variable that CLEARS
    the threshold (percentile not None and >= threshold), built before any tier/
    confidence/percentile comparison -- so a lower-percentile AXIS variable that cleared
    can still beat a higher-percentile FILTER variable that also cleared. Taking a global
    max and thresholding only the winner would let the filter variable shadow it, which
    is the whole thing this ordering exists to prevent.

    Among the candidates, the winner is picked by, in order:
      1. tier -- AXIS variables (on x/y) beat FILTER-only variables. A variable that is
         both on an axis and filtered counts as axis (axis membership wins).
      2. confidence -- higher elicited confidence (1-100, per variable) wins within a
         tier. Missing confidence (legacy data) sorts last within its tier, but the
         variable is still a candidate.
      3. percentile -- higher percentile wins when tier and confidence tie.
      4. variable name -- ascending, a deterministic final fallback so a full tie
         (same tier, confidence, and percentile) always resolves the same way.

    Variables whose percentile is None never enter the candidate set. Returns None when
    nothing cleared -- which both callers read as "do not fire".

    threshold is the CALLER's constant (DWELL_PERCENTILE_THRESHOLD /
    SELECTION_PERCENTILE_THRESHOLD): the two happen to be equal today, but they are
    separate policy knobs and this helper must not pick one for them.
    """
    candidates = [v for v, p in percentile_by_var.items()
                  if p is not None and p >= threshold]
    if not candidates:
        return None

    def _priority(v):
        # Sort ASCENDING: axis tier (0) before filter tier (1); then negate the
        # descending keys (confidence, percentile) so higher wins; variable name last,
        # ascending, as the deterministic final tiebreak.
        tier = 0 if v in axis_vars else 1        # axis membership wins over filter-only
        confidence = _confidence_for_var(client_record, v)
        return (tier, -confidence, -percentile_by_var[v], v)

    return min(candidates, key=_priority)


# --------------------------------------------------------------------------- #
# Per-variable eligible-dwell replay. This is the evidence base for the dwell
# trigger's per-variable readiness, recheck spacing AND scoring: how much hover time
# each variable earned WHILE IT WAS ACTIVE, and on which teens -- not the global
# pooled dwell re-sliced by whatever is active now.
# --------------------------------------------------------------------------- #
def filters_active_as_of(response_list, bound_ms):
    """The active-filter set as of a past moment -> set of attribute names.

    The time-bounded generalization of llm_intervention.get_current_filters, which
    always replays response_list to the END (= "now"). Rather than duplicate its
    add/remove/change/clear rules, this slices response_list to the entries at or
    before bound_ms and delegates to the unmodified get_current_filters over that
    prefix, so the two can never drift on what "a filter is on" means.

    bound_ms is a hover's client-side interactionAt (epoch ms). A response_list entry
    carries its own client timestamp under input_data.interactionAt (the same clock as
    the hover), which is how the two lists -- filters live only in response_list, hovers
    only in bias_logs -- are correlated. bound_ms None (a hover with no timestamp, as in
    minimal unit records) means "no bound" -> every filter, identical to get_current_
    filters. A response_list entry with no timestamp is treated as pre-existing (kept),
    so untimestamped test records behave as they did before this change.
    """
    if bound_ms is None:
        prefix = response_list
    else:
        prefix = [e for e in response_list
                  if (_response_interaction_at(e) is None
                      or _response_interaction_at(e) <= bound_ms)]
    return llm_intervention.get_current_filters({"response_list": prefix})


def _response_interaction_at(entry):
    """A response_list entry's client-side interactionAt (ms), or None if absent.

    response_list wraps the raw frontend message under "input_data" (unlike bias_logs,
    which stores it flat), so the timestamp is read from there."""
    return entry.get("input_data", {}).get("interactionAt")


def eligible_dwell_by_teen_by_var(bias_logs, response_list):
    """Per-variable, PER-TEEN eligible dwell -> {variable: {teen_id: ms}}.

    THE evidence base for the whole dwell trigger -- both the readiness/recheck gates
    (which want it summed per variable, via eligible_dwell_seconds_by_var below) and the
    per-variable SCORING (which wants the per-teen granularity kept, as the dwell weights
    dc_metric.dwell_bias_percentile scores variable v against). Producing both from ONE
    structure is the point: gating and scoring then read literally the same numbers, so a
    variable can never look ready on dwell its own score never sees. Before this, the
    score read dc_metric.dwell_by_teen -- the GLOBAL pooled dwell -- so hovers made while
    v was inactive still weighted v's score.

    Replays bias_logs' hover entries in order. For each hover, the variables it counts
    toward are the ones that were ACTIVE AT THE MOMENT OF THAT HOVER:

        as-of active set = {the hover's own data.x.name, data.y.name}   (axes, carried
                            directly on the entry at emit time)
                         UNION filters_active_as_of(response_list, the hover's
                            interactionAt)                              (filters replayed
                            only up to that hover, not to "now")

    The hover's interactionDuration is added, UNDER ITS OWN TEEN ID, to EVERY variable in
    that set, so a hover made while two variables were simultaneously active (e.g. one on
    an axis, one filtered) contributes to both.

    ONE PASS over bias_logs, emitting every variable at once: filters_active_as_of is the
    expensive part (it re-slices response_list per hover), so calling this once per
    variable would multiply the whole replay by the variable count for no new information.

    SCOPE: mirrors dc_metric.dwell_by_teen exactly -- scalar mouseout_item entries only
    (group hovers carry a LIST id and an unresolved member attribution, so they are
    skipped there and here) -- so the per-variable dwell is always a SUBSET of the global
    dwell, which is what keeps dwell_bias's fail-loud "every dwelled id is in the map"
    guarantee intact. Variables never active during any hover are simply absent.
    Durations sum in ms (dwell_bias's own unit); the seconds view divides below.
    """
    by_var = {}
    for entry in bias_logs:
        if entry.get("interactionType") != dc_metric.MOUSEOUT_ITEM:
            continue
        data = entry.get("data", {})
        tid = data.get("id")
        # scalar ids only: skip list (group) ids and the "-" / None placeholders,
        # matching dwell_by_teen so the two stay on the same evidence base.
        if isinstance(tid, list) or tid is None or tid == "-":
            continue
        duration = float(entry.get("interactionDuration", 0) or 0)
        x = data.get("x") if isinstance(data.get("x"), dict) else {}
        y = data.get("y") if isinstance(data.get("y"), dict) else {}
        active = {n for n in (x.get("name"), y.get("name")) if n is not None}
        active |= filters_active_as_of(response_list, entry.get("interactionAt"))
        for var in active:
            per_teen = by_var.setdefault(var, {})
            per_teen[tid] = per_teen.get(tid, 0.0) + duration
    return by_var


def eligible_dwell_seconds_by_var(bias_logs, response_list):
    """Per-variable eligible dwell seconds -> {variable: seconds}.

    The readiness/cooldown-facing view of eligible_dwell_by_teen_by_var: the same replay,
    summed over teens and converted to seconds (the unit MIN_ELIGIBLE_DWELL_SECONDS and
    DWELL_RECHECK_SECONDS are expressed in, and the unit dwell_last_checked_by_var
    stores). DERIVED rather than separately computed, so the gates and the scorer can
    never drift apart on what a variable's eligible dwell is.

    Variables active now but never active during any hover are simply absent (0 via .get).
    """
    return {var: sum(per_teen.values()) / 1000.0
            for var, per_teen in
            eligible_dwell_by_teen_by_var(bias_logs, response_list).items()}


def evaluate_trigger(client_record, dwell_metrics, now_ms=None):
    """Decide whether to fire a realtime intervention, AND say why not.

    Returns (fired, reason, trace):
      * fired  -- bool.
      * reason -- a short code so a server log makes it obvious which condition
        blocked it: "ok" | "no_dwell_bias" | "not_ready (...)" | "system_cooldown (...)" |
        "too_soon (...)" | "no_visible_axes" | "no_belief_vars (...)" |
        "degenerate_null (...)" | "below_percentile (...)".
      * trace  -- {"percentile_by_var", "target_var", "target_percentile",
        "excluded_vars", "n_dwelled", "total_dwell_seconds"}, the diagnostic values
        behind the decision (persisted on every call for trigger-policy analysis).
        percentile_by_var is the FULL per-variable breakdown -- every ready variable that
        was actually scored, mapped to its own percentile -- and is None (not {}) whenever
        the gates stopped short of scoring anything, which distinguishes "never scored"
        from "scored, everything excluded". target_var / target_percentile name the
        hierarchy winner, and are set only on a fire. excluded_vars is {variable: code}
        for ready variables dropped BEFORE sampling (see the degenerate guard below), so
        a log can tell that apart from a genuine below-threshold result. n_dwelled /
        total_dwell_seconds are the GLOBAL pooled figures, kept for the log only -- they
        are neither readiness nor scoring inputs.

    Policy: readiness, recheck spacing AND scoring are ALL PER VARIABLE, run on each
    variable's OWN eligible dwell (eligible_dwell_by_teen_by_var -- hovers that happened
    while that variable was active). First a SYSTEM-WIDE wall-clock cooldown (>=
    SYSTEM_FIRE_COOLDOWN_MS of REAL time since the last fire); then, among the currently-
    active variables, a variable is checkable this call once it has (a) >= MIN_ELIGIBLE_
    DWELL_SECONDS of its own eligible dwell -- the first-check readiness floor -- AND (b)
    >= DWELL_RECHECK_SECONDS of NEW eligible dwell since its OWN last check. A variable
    can be active right now yet not checkable if its own eligible history is thin (e.g.
    the axes just switched to it), while another variable with enough history fires on its
    own; and one variable's dwell never advances another's clock.

    Each checkable variable is then scored INDEPENDENTLY: DC is re-pooled onto that ONE
    variable and its DwellBias is scored against a null built from THAT variable's own
    per-teen dwell. Ready variables are never pooled into a joint score -- two variables
    that disagree no longer cancel each other out, and a variable is judged only on the
    attention it actually received. The winner is then the priority hierarchy's
    (_reduce_by_priority, shared with the selection trigger): threshold first, then axis-
    tier > filter-tier > confidence > percentile > name. The raw score's sign is NOT
    gated: a high enough percentile fires even when DwellBias is negative (the
    positive-score requirement was removed in pilot round 2).

    System cooldown vs per-variable spacing: these are two DIFFERENT clocks, layered.
    The per-variable gate below counts ACCUMULATED ELIGIBLE DWELL (only advances while
    hovering with that variable active); this system gate counts REAL elapsed time
    (now_ms - llm_last_fired_at), so it also covers the participant reading the panel.
    It is placed BEFORE any scoping/scoring so a suppressed check does no work and --
    critically -- mutates NO cooldown state: dwell_last_checked_by_var and
    dwell_last_fired_vars are left exactly as if scoring never ran, so nothing "earns"
    toward the next fire while cooling.

    Side effect -- dwell_last_checked_by_var[v], in v's OWN eligible-seconds units.
    Confirmed with Shiyao, and asymmetric between the two outcomes:
      * ON FIRE, only the WINNING variable's clock advances, and dwell_last_fired_vars
        holds exactly [winner]. The variables that were scored but lost were not what the
        intervention was about, so that evaluation must not spend their recheck budget.
      * ON A NON-FIRE, every variable actually SCORED this call has its clock advanced
        ("after each check, we just want to accumulate additional 10s dwell time for that
        variable when it is active"), which is what keeps the recheck spacing working for
        non-winners. Variables excluded before sampling (the degenerate guard) are NOT
        advanced -- nothing about them was meaningfully evaluated.
    A call blocked by an earlier gate mutates nothing at all. On fire, the caller
    (on_interaction) refreshes llm_last_fired_at and pins the intervention's message to
    trace["target_var"].

    client_record: the CLIENTS[pid] dict (reads bias_logs / response_list /
                   dc_map_detailed / dwell_last_checked_by_var / llm_last_fired_at).
    dwell_metrics: the dict from dc_adapter.compute_dwell_metrics, i.e.
                   {"dwell_bias", "dwell_bias_v", "n_dwelled"}.
    now_ms:        current wall-clock time in epoch ms (bias_util.get_current_time()'s
                   unit), passed in by the caller so this module imports no clock and
                   its tests stay deterministic. None (no clock supplied) skips the
                   system-cooldown gate entirely -- the live path always passes it.
    """
    bias_logs = client_record.get("bias_logs", [])
    # total_dwell_seconds / n_dwelled are the GLOBAL pooled figures, kept for the
    # diagnostic trace ONLY (server persists them). They gate nothing and -- since the
    # per-variable restructuring -- score nothing either: each variable is scored against
    # its OWN per-teen dwell below. MIN_UNIQUE_HOVERS was removed with the global gate.
    dwell = dc_metric.dwell_by_teen(bias_logs)
    total_dwell_seconds = sum(dwell.values()) / 1000.0  # dwell_by_teen sums ms
    n_dwelled = dwell_metrics.get("n_dwelled", 0)
    trace = {"percentile_by_var": None,
             "target_var": None,
             "target_percentile": None,
             "excluded_vars": {},
             "n_dwelled": n_dwelled,
             "total_dwell_seconds": total_dwell_seconds}

    observed = dwell_metrics.get("dwell_bias")
    if observed is None:
        return False, "no_dwell_bias", trace

    # --- SYSTEM-WIDE wall-clock cooldown: no two fires within SYSTEM_FIRE_COOLDOWN_MS
    # of REAL time. Placed here (before ANY scoping/scoring) so a suppressed check does
    # no work and touches NO cooldown state -- dwell_last_checked_by_var /
    # dwell_last_fired_vars stay exactly as if scoring never ran. Skipped when no clock
    # is supplied (now_ms is None) or there has been no fire yet this session. The
    # boundary matches the per-variable ">= is ready": elapsed < cooldown suppresses,
    # so at EXACTLY SYSTEM_FIRE_COOLDOWN_MS it is allowed through.
    last_fired = client_record.get("llm_last_fired_at")
    if now_ms is not None and last_fired is not None:
        elapsed_ms = now_ms - last_fired
        if elapsed_ms < SYSTEM_FIRE_COOLDOWN_MS:
            return False, (f"system_cooldown ({elapsed_ms / 1000.0:.1f}s < "
                           f"{SYSTEM_FIRE_COOLDOWN_MS / 1000.0:.0f}s since last fire)"), trace

    # --- resolve the CURRENTLY ACTIVE variables ONCE: the x/y axis attributes PLUS
    # any attribute with an active filter (Shiyao's request). This governs which
    # variables are even CANDIDATES to check right now; how much EVIDENCE each has is a
    # separate, historical question answered by `eligible` below. The two tiers are kept
    # APART (not unioned away) because the priority hierarchy classifies the winner by
    # which of them it came from. sorted() only for a deterministic order (the source is
    # a set); order does not affect scoring/cooldown.
    axis_vars, filter_vars = _axis_and_filter_vars(client_record)
    visible_vars = sorted(axis_vars | filter_vars)
    if not visible_vars:
        return False, "no_visible_axes", trace

    # Per-variable, per-teen eligible dwell: how much hover time each variable earned
    # WHILE ACTIVE (axes carried on each hover + filters as of that hover), and on which
    # teens. ONE replay feeds both uses -- summed to seconds for the readiness/recheck
    # gates just below, kept per-teen as the scoring weights further down -- so the gates
    # and the scorer are guaranteed to be looking at the same evidence.
    # Computed here (not at the top) so the frequently-hit early gates above -- system
    # cooldown especially -- skip its O(hovers x response_list) replay; it is pure, so
    # deferring it changes nothing but wasted work.
    dwell_by_var = eligible_dwell_by_teen_by_var(
        bias_logs, client_record.get("response_list", []))
    eligible = {v: sum(per_teen.values()) / 1000.0
                for v, per_teen in dwell_by_var.items()}

    # --- readiness + recheck spacing, PER ACTIVE VARIABLE, on its OWN eligible dwell --
    # A variable is checkable this call once it has both: (a) MIN_ELIGIBLE_DWELL_SECONDS
    # of its own eligible dwell (the first-check floor -- an active-but-thin variable is
    # held back), and (b) DWELL_RECHECK_SECONDS of NEW eligible dwell since its own last
    # check (recheck spacing). Each variable carries its own "last checked at" in its own
    # eligible-seconds units, so one variable cooling never blocks another.
    checked_at = client_record.setdefault("dwell_last_checked_by_var", {})
    ready_vars = [v for v in visible_vars
                  if eligible.get(v, 0.0) >= MIN_ELIGIBLE_DWELL_SECONDS
                  and eligible.get(v, 0.0) - checked_at.get(v, 0.0) >= DWELL_RECHECK_SECONDS]
    if not ready_vars:
        # Distinguish the two blocking reasons for a useful log. If NO active variable
        # has cleared the first-check floor yet, it is not_ready (thin own history);
        # otherwise some are past the floor but all are within recheck spacing (too_soon).
        past_floor = [v for v in visible_vars
                      if eligible.get(v, 0.0) >= MIN_ELIGIBLE_DWELL_SECONDS]
        if not past_floor:
            best = max((eligible.get(v, 0.0) for v in visible_vars), default=0.0)
            return False, (f"not_ready ({best:.1f}s < {MIN_ELIGIBLE_DWELL_SECONDS}s "
                           f"eligible dwell for any active var)"), trace
        best_new = max(eligible.get(v, 0.0) - checked_at.get(v, 0.0) for v in past_floor)
        return False, (f"too_soon ({best_new:.1f}s < {DWELL_RECHECK_SECONDS}s of new "
                       f"eligible dwell for any active var)"), trace

    # --- drop ready variables the belief map cannot score, BEFORE any sampling ---------
    # An active attribute need not be a belief variable at all (the participant can put
    # child_id on an axis, or filter on it). Intersecting with the map's real belief keys
    # up front is how selection_percentile_by_var already handles this; relying instead on
    # scoped_detailed_map's KeyError would be wrong now that each variable is scoped ALONE
    # -- what used to be a survivable partial miss (some other var in the joint scope was
    # valid) is a total miss for that variable's own scope.
    belief_vars = _belief_vars_in(client_record.get("dc_map_detailed", {}))
    scorable = [v for v in ready_vars if v in belief_vars]
    if not scorable:
        return False, (f"no_belief_vars (ready {ready_vars} not in the belief map)"), trace

    # --- C1: score EACH ready variable INDEPENDENTLY, never pooled --------------------
    # One percentile per variable, each against a null built from that variable's OWN
    # per-teen dwell. A cooling or thin variable is not scored at all, so its attention
    # neither earns nor blocks a fire. ONE fresh seeded generator for the whole
    # evaluation, threaded through the loop (it advances, so each variable draws an
    # independent, non-repeating null while the check stays reproducible from its inputs).
    percentile_by_var, excluded = _dwell_percentile_by_var(
        client_record["dc_map_detailed"], scorable, dwell_by_var, dc_adapter.live_rng())
    trace["percentile_by_var"] = percentile_by_var
    trace["excluded_vars"] = excluded

    if not percentile_by_var:
        # Every scorable variable was dropped before sampling. Nothing was meaningfully
        # evaluated, so NO clock advances -- unlike a real below-threshold check. The
        # re-check costs no sampling (the guard runs before it), so repeating it is cheap.
        return False, f"degenerate_null (nothing scorable: {_excluded_note(excluded)})", trace

    # --- Shiyao's PRIORITY HIERARCHY over the per-variable percentiles ----------------
    winner = _reduce_by_priority(percentile_by_var, axis_vars, client_record,
                                 DWELL_PERCENTILE_THRESHOLD)

    if winner is None:
        # A real check ran: advance the clock of every variable actually SCORED, so each
        # has to earn DWELL_RECHECK_SECONDS of its own new eligible dwell before being
        # asked again. Excluded (unscored) variables are deliberately left alone.
        for v in percentile_by_var:
            checked_at[v] = eligible.get(v, 0.0)
        reason = (f"below_percentile ({_best_note(percentile_by_var)}"
                  f"{_excluded_note(excluded, prefix='; ')})")
        return False, reason, trace

    # Fired. ONLY the winner's clock advances and only the winner is recorded, so a later
    # dismiss rebases exactly it -- the variables that were scored but lost keep their
    # recheck budget, since this intervention was not about them.
    trace["target_var"] = winner
    trace["target_percentile"] = percentile_by_var[winner]
    checked_at[winner] = eligible.get(winner, 0.0)
    client_record["dwell_last_fired_vars"] = [winner]
    return True, "ok", trace


def _belief_vars_in(dc_map_detailed):
    """The belief variables the cached detailed map actually carries -> set of names.

    Read from any entry's consistency keys -- the same way dc_metric.dwell_bias_v and
    dc_adapter.selection_percentile_by_var establish "all variables", so the three stay
    in lockstep on what counts as a belief variable. Empty map -> empty set (the caller
    treats that as nothing to score, never as an error).
    """
    if not dc_map_detailed:
        return set()
    return set(next(iter(dc_map_detailed.values()))["consistency"].keys())


def _is_degenerate_scope(scoped):
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


def _dwell_percentile_by_var(dc_map_detailed, scope_vars, dwell_by_var, rng=None):
    """DwellBias percentile per variable, each scored INDEPENDENTLY.

    -> (percentile_by_var, excluded)
       percentile_by_var: {variable: percentile} over the variables actually scored.
       excluded:          {variable: code} for those dropped BEFORE sampling, so the
                          caller can log why a ready variable never got a percentile.
                          Codes: "degenerate_null" (flat DC, see _is_degenerate_scope)
                          and "scope_failed" (the map could not be scoped to it).

    The dwell sibling of dc_adapter.selection_percentile_by_var, and deliberately the
    same shape: for each variable v, scoped_detailed_map(detailed, [v]) re-pools DC onto
    that ONE variable and the unmodified dc_metric.dwell_bias_percentile scores it. Two
    differences follow from dwell not being selection:

      * the weights are PER VARIABLE. v is scored against dwell_by_var[v] -- the teens
        hovered while v was active, and only those -- not against one shared global
        dwell. k and the total dwell budget the null is drawn with therefore also come
        from v's own history, so the null matches the score's scale.
      * scoping to a single variable makes w_v cancel exactly (Sum w_v*C_v / Sum w_v over
        one term is C_v), so these percentiles are scored on the RAW per-variable
        consistency with no js-weighting anywhere in the path. That is Shiyao's
        "use VC_v, not VC_v x w_v" -- it falls out of the restructuring rather than
        needing a flag.

    scope_vars is the caller's already-intersected list of ready BELIEF variables, so a
    scoped_detailed_map failure here means an internally inconsistent map rather than a
    non-belief attribute; it is caught per variable (excluding just that one) rather than
    lost the whole check, the same handler-boundary posture on_interaction takes.

    rng is the ONE seeded generator the caller builds per evaluation (dc_adapter.
    live_rng()), advanced across the loop so each variable draws an independent null;
    None falls back to the global np.random state (used by unseeded unit tests).
    """
    percentile_by_var = {}
    excluded = {}
    for v in scope_vars:
        try:
            scoped = dc_adapter.scoped_detailed_map(dc_map_detailed, [v])
        except Exception as e:
            print(f"[DWELL] scoped map failed for {v!r}: {e}", flush=True)
            excluded[v] = "scope_failed"
            continue
        if _is_degenerate_scope(scoped):
            excluded[v] = "degenerate_null"
            continue
        percentile_by_var[v] = dc_metric.dwell_bias_percentile(
            scoped, dwell_by_var.get(v, {}), rng=rng)
    return percentile_by_var, excluded


def _best_note(percentile_by_var):
    """'best var=pct of N scored' for a below_percentile log line."""
    scored = {v: p for v, p in percentile_by_var.items() if p is not None}
    if not scored:
        return f"no scorable percentile of {len(percentile_by_var)}"
    best = max(scored, key=lambda v: (scored[v], v))
    return (f"best {best}={scored[best]:.3f} < {DWELL_PERCENTILE_THRESHOLD} "
            f"of {len(percentile_by_var)} scored")


def _excluded_note(excluded, prefix=""):
    """'excluded a,b (degenerate_null)' for a log line, or '' when nothing was excluded.

    Keeps a pre-sampling exclusion visibly distinct from a variable that was scored and
    simply fell short -- they mean very different things for trigger-policy analysis.
    """
    if not excluded:
        return ""
    listed = ", ".join(f"{v}:{code}" for v, code in sorted(excluded.items()))
    return f"{prefix}excluded {listed}"


def should_trigger(client_record, dwell_metrics, now_ms=None):
    """Whether to fire an intervention for this interaction (bool only).

    Thin wrapper over evaluate_trigger, kept so the swap-in point for the real
    percentile test has a stable, reason-free signature. Callers that want to
    log WHY it did not fire call evaluate_trigger directly. now_ms is threaded
    through so this wrapper honours the same system-wide cooldown.
    """
    fired, _reason, _trace = evaluate_trigger(client_record, dwell_metrics, now_ms)
    return fired


def reset_dwell_watermark(client_record):
    """Rebase the recheck window for the variable the dismissed intervention was ABOUT,
    from that variable's OWN eligible dwell accumulated so far.

    Called when the participant's panel goes away (on_llm_dismissed). The spacing is
    measured in NEW eligible hover time, so without this the seconds spent reading one
    intervention (while that variable is still active) would count toward earning the
    next -- they would be paying for a reminder they were still looking at.

    Rebases ONLY what dwell_last_fired_vars records, which since the per-variable
    restructuring is exactly [the winning variable] -- not every variable that happened
    to be scored by the firing check, and not whatever is active at dismiss time (the
    participant may have switched axes/filters while the panel was up). The list shape is
    kept rather than collapsed to a scalar so the stored key stays readable across the
    pilot data written before the change. The fired variable's clock is set to ITS OWN
    current eligible seconds (the same units the recheck gate compares against); every
    other variable's clock is left exactly where it was. The marker is consumed here so a
    stray repeat dismiss cannot re-rebase.
    """
    fired_vars = client_record.pop("dwell_last_fired_vars", None)
    if not fired_vars:
        return
    eligible = eligible_dwell_seconds_by_var(
        client_record.get("bias_logs", []), client_record.get("response_list", []))
    checked_at = client_record.setdefault("dwell_last_checked_by_var", {})
    for v in fired_vars:
        checked_at[v] = eligible.get(v, 0.0)


def evaluate_selection_trigger(client_record, selected_ids):
    """The live mid-task decision: the progressive trigger on a fixed pick schedule.

    Thin wrapper over evaluate_selection_progressive_trigger (below), which owns
    the readiness gate and the per-variable fire decision. This adds the schedule:
    a check runs at MIN_SELECTIONS and then every SELECTION_RECHECK_PICKS picks
    after it (the 5th, 7th and 9th selections), whether or not an earlier check
    fired. Two additional picks are the unit of NEW EVIDENCE between checks, not
    a post-fire penalty -- checking one pick after a non-fire would re-ask the
    same question of nearly the same selection.

    Each checkpoint fires AT MOST ONCE per session (Shiyao: "we just check their
    selections once at 5, once at 7, and once at 9"), tracked as the set of
    consumed checkpoints (selection_checkpoints_checked): deselecting below one
    and re-selecting back to it does not re-run it, because reaching the same
    count again is a reshuffle of an already-checked selection, not two
    selections of new evidence.

    Returns the progressive trigger's dict unchanged; on a skipped check, the
    same shape as its not-ready case with an "off_schedule (...)" or
    "already_checked (...)" reason.
    """
    n_selected = len(set(selected_ids))
    if n_selected >= MIN_SELECTIONS:
        past = (n_selected - MIN_SELECTIONS) % SELECTION_RECHECK_PICKS
        if past != 0:
            return {"ready": False,
                    "reason": (f"off_schedule (n={n_selected}, next check at "
                               f"{n_selected + SELECTION_RECHECK_PICKS - past})"),
                    "n_selected": n_selected,
                    "percentile_by_var": None}
        checked = client_record.setdefault("selection_checkpoints_checked", set())
        if n_selected in checked:
            return {"ready": False,
                    "reason": f"already_checked (checkpoint {n_selected} consumed)",
                    "n_selected": n_selected,
                    "percentile_by_var": None}
        checked.add(n_selected)

    return evaluate_selection_progressive_trigger(client_record, selected_ids)


# --------------------------------------------------------------------------- #
# Progressive selection gate.
#
# A non-blocking, per-selection sibling of the realtime dwell trigger, scoring the
# running selection instead of dwell and PER VARIABLE instead of pooled, firing on
# the single most extreme variable (Shiyao's rule). Reached live only through
# evaluate_selection_trigger above, whose pick-counted cooldown keeps it from
# firing on every selection past the 5th.
# --------------------------------------------------------------------------- #
def evaluate_selection_progressive_trigger(client_record, selected_ids):
    """Per-variable selection-bias fire decision for the running selection.

    Sibling of evaluate_trigger (the realtime dwell gate) in shape -- a readiness
    gate first, then the scoped scoring -- and, like it, scored only on the CURRENTLY
    ACTIVE variables (see SCOPE below), but on the SELECTION rather than dwell.

    Returns a dict. Below readiness / no active variables:
      {"ready": False, "reason": "not_ready (...)" | "no_active_vars",
       "n_selected", "percentile_by_var": None}
    At/above readiness with an active variable set:
      {"ready":             True,
       "reason":            "ok",
       "fired":             bool,
       "target_var":        variable | None,   # the winning variable, only on a fire
       "target_percentile": float | None,      # its percentile, only on a fire
       "n_selected":        int,                # unique selected ids
       "percentile_by_var": {variable: percentile}}   # full dict, for logging

    SCOPE (Shiyao's rule, mirroring the dwell trigger): only the CURRENTLY ACTIVE
    variables are scored -- the x/y axis attributes plus any attribute with an active
    filter (get_current_axes | get_current_filters, the same active set the dwell
    trigger scopes on). The pre-scoping behavior scored every belief variable; now a
    variable the participant is not looking at or filtering on can neither earn nor
    block a fire. An empty active set is treated like dwell's no_visible_axes guard:
    not-ready, no scoring attempted.

    Reduction (Shiyao's PRIORITY HIERARCHY): delegated to _reduce_by_priority, the
    threshold-then-rank helper this gate SHARES with the dwell trigger -- threshold
    first, then axis-tier > filter-tier > confidence > percentile > name. See that
    function for the ordering and why it is that way. This gate contributes only its
    own threshold (SELECTION_PERCENTILE_THRESHOLD) and its axis/filter split.
    fired = the candidate set is non-empty; target_var / target_percentile name the
    winner (or None/None when nothing cleared). percentile_by_var is always returned
    in full so a log can show how close it got. Variables whose percentile is None
    (nothing selected present in the map -- uniform across variables) never enter the
    candidate set. A total miss (active vars present but none are belief variables)
    yields an empty percentile_by_var via the intersection in
    selection_percentile_by_var, which reduces to fired=False -- no separate path.

    Readiness: n_selected >= MIN_SELECTIONS, which is what makes this "start at
    the 5th selection."

    client_record: the CLIENTS[pid] dict (reads dc_map_detailed and beliefs, plus
                   bias_logs / response_list via get_current_axes / get_current_filters).
    selected_ids:  the participant's currently-selected teen ids.
    """
    n_selected = len(set(selected_ids))
    if n_selected < MIN_SELECTIONS:
        return {"ready": False,
                "reason": f"not_ready ({n_selected} < {MIN_SELECTIONS} selections)",
                "n_selected": n_selected,
                "percentile_by_var": None}

    # Resolve the currently-active variables (axes + active filters), the same set the
    # dwell trigger scopes on, via the SAME shared helper. Kept as TWO sets, not just
    # their union, so each candidate can be classified into a tier below (axis membership
    # takes priority). Empty union -> nothing to score, so guard exactly like dwell's
    # no_visible_axes and do not attempt the (expensive) null-sampling.
    axis_vars, filter_vars = _axis_and_filter_vars(client_record)
    active_vars = axis_vars | filter_vars
    if not active_vars:
        return {"ready": False,
                "reason": "no_active_vars",
                "n_selected": n_selected,
                "percentile_by_var": None}

    # One fresh seeded generator for this whole evaluation, threaded through
    # selection_percentile_by_var's per-variable loop (it advances this single
    # generator, so each variable draws an independent, non-repeating null while the
    # check stays reproducible). Built ONCE here -- never per variable -- so the
    # variables do not replay an identical null distribution.
    percentile_by_var = dc_adapter.selection_percentile_by_var(
        client_record["dc_map_detailed"], selected_ids, variables=active_vars,
        rng=dc_adapter.live_rng())

    # --- Shiyao's PRIORITY HIERARCHY: THRESHOLD FIRST, then rank -----------------
    # Threshold-then-rank (axis tier > filter tier > confidence > percentile > name),
    # now the SHARED reduction the dwell trigger also runs -- see _reduce_by_priority.
    # Behaviour here is unchanged by that extraction; only the threshold, which stays
    # this gate's own constant, is passed in.
    winner = _reduce_by_priority(percentile_by_var, axis_vars, client_record,
                                 SELECTION_PERCENTILE_THRESHOLD)
    fired = winner is not None

    return {"ready": True,
            "reason": "ok",
            "fired": fired,
            # Name a target only on a fire; percentile_by_var carries the rest.
            "target_var": winner,
            "target_percentile": percentile_by_var[winner] if fired else None,
            "n_selected": n_selected,
            "percentile_by_var": percentile_by_var}
