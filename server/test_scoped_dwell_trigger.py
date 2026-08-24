"""Synthetic validation harness for the PER-VARIABLE dwell trigger.

Standalone -- no real elicitation, no sockets, no live server path. Run it as:

    cd server && python3 test_scoped_dwell_trigger.py    # needs numpy (the venv)

Covers the pieces behind the per-variable dwell trigger:

  * dc_adapter.scoped_detailed_map -- re-pools each teen's DC over just the requested
    variables from their stored consistency/weights, leaving the input map intact.
  * llm_trigger.eligible_dwell_by_teen_by_var / eligible_dwell_seconds_by_var /
    filters_active_as_of -- the per-variable eligible-dwell replay: each variable
    accrues only the hover time spent WHILE IT WAS ACTIVE (its own axes as of that
    hover + filters as of that hover), kept PER TEEN so the gates and the scorer read
    the same numbers.
  * llm_trigger.evaluate_trigger -- readiness (a variable's own eligible dwell >=
    MIN_ELIGIBLE_DWELL_SECONDS), recheck spacing (DWELL_RECHECK_SECONDS of NEW eligible
    dwell for that variable) AND scoring are ALL per-variable. Every ready variable gets
    its OWN percentile, scored against its OWN per-teen dwell; ready variables are never
    pooled into one joint score. The winner is the shared priority hierarchy's, and only
    the winner's cooldown advances on a fire. MIN_UNIQUE_HOVERS is gone.
  * the degenerate-null guard -- a variable whose per-teen DC is constant would score a
    guaranteed 1.0 against a collapsed null, so it is dropped before sampling.

Mirrors test_dc_metric.py's pattern (a nonlocal `check`, pure asserts + prints,
exits non-zero on failure). It does NOT touch the existing get_current_axes tests
(test_llm_intervention.py) or the pooled percentile tests (test_dc_metric.py);
both keep passing unchanged.
"""
import asyncio

import numpy as np

import dc_adapter
import dc_metric
import llm_intervention
import llm_trigger

VARS = ("var_a", "var_b", "var_c")


def make_entry(consistency, weights):
    """One dc_map_detailed entry, with its pooled DC computed the same way
    _consistency_and_weights_for_teen does (Sum_v w_v*C_v / Sum_v w_v)."""
    num = sum(weights[v] * consistency[v] for v in consistency)
    den = sum(weights[v] for v in consistency)
    dc = 0.0 if den == 0.0 else num / den
    return {"dc": dc, "consistency": dict(consistency), "weights": dict(weights)}


def mouseout(teen_id, duration_ms, x=None, y=None, at=None):
    """A completed point-hover carrying dwell (ms) and, optionally, the live axis
    attribute names get_current_axes reads (data.x.name / data.y.name).

    `at` is the hover's client-side interactionAt (epoch ms) -- the timestamp the
    per-variable eligibility replay uses to bound which filters were active as of this
    hover. Omitted (None) means no timestamp, so filters are treated as unbounded (the
    minimal-record behavior); the timestamped tests below set it explicitly."""
    data = {"id": teen_id}
    if x is not None:
        data["x"] = {"name": x}
    if y is not None:
        data["y"] = {"name": y}
    entry = {"interactionType": "mouseout_item",
             "interactionDuration": duration_ms,
             "data": data}
    if at is not None:
        entry["interactionAt"] = at
    return entry


def mouseout_group(x=None, y=None, at=None):
    """A GROUP hover carrying the live axis names but NO scorable dwell.

    get_current_axes accepts mouseout_group as an axis source, while both dwell replays
    (dc_metric.dwell_by_teen and llm_trigger.eligible_dwell_by_teen_by_var) skip it --
    its id is a LIST, and member attribution is an open modelling question. That makes it
    the way to say "these are the axes NOW" without adding dwell to either variable,
    which is what the per-variable-evidence tests need: a variable can be active now yet
    have earned its history under a different axis configuration entirely.
    """
    data = {"id": ["g0", "g1"]}
    if x is not None:
        data["x"] = {"name": x}
    if y is not None:
        data["y"] = {"name": y}
    entry = {"interactionType": "mouseout_group",
             "interactionDuration": 4000,
             "data": data}
    if at is not None:
        entry["interactionAt"] = at
    return entry


def main():
    failures = 0

    def check(label, cond):
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    def raises(exc, fn):
        try:
            fn()
            return False
        except exc:
            return True

    # ===================================================================== #
    # scoped_detailed_map: re-pool DC over a variable subset
    # ===================================================================== #
    print("scoped_detailed_map -- re-pool over the visible subset:")

    # One teen, 3 variables, DELIBERATELY unequal weights so a wrong (unweighted)
    # mean would be caught. Hand-computed targets:
    #   pooled  = (2*0.5 + 1*-0.2 + 3*0.9) / (2+1+3) = 3.5/6      = 0.5833333...
    #   {a,b}   = (2*0.5 + 1*-0.2)         / (2+1)   = 0.8/3      = 0.2666666...
    #   {a}     = (2*0.5)                  / 2       = 0.5
    #   {c}     = (3*0.9)                  / 3       = 0.9
    consistency = {"var_a": 0.5, "var_b": -0.2, "var_c": 0.9}
    weights = {"var_a": 2.0, "var_b": 1.0, "var_c": 3.0}
    detailed = {"u": make_entry(consistency, weights)}
    pooled_dc = detailed["u"]["dc"]

    s_ab = dc_adapter.scoped_detailed_map(detailed, ["var_a", "var_b"])
    check("scoped {a,b} dc == hand-computed weighted mean over a,b",
          abs(s_ab["u"]["dc"] - 0.8 / 3.0) < 1e-9)

    s_a = dc_adapter.scoped_detailed_map(detailed, ["var_a", None])   # one axis None
    check("single visible var (other axis None) -> dc off that one var",
          abs(s_a["u"]["dc"] - 0.5) < 1e-9)

    s_c = dc_adapter.scoped_detailed_map(detailed, ["var_c"])
    check("scoped {c} dc == its own weighted value (0.9)",
          abs(s_c["u"]["dc"] - 0.9) < 1e-9)

    # Full-subset scope reproduces the pooled DC exactly (sanity that the re-pool
    # is the same formula, just over fewer terms).
    s_all = dc_adapter.scoped_detailed_map(detailed, list(VARS))
    check("scoped over ALL vars reproduces the pooled dc",
          abs(s_all["u"]["dc"] - pooled_dc) < 1e-9)

    # --- input map is never mutated; consistency/weights preserved -------------
    check("input map's pooled dc unchanged after scoping",
          detailed["u"]["dc"] == pooled_dc)
    check("scoped entry keeps original consistency dict (unchanged)",
          s_ab["u"]["consistency"] == consistency)
    check("scoped entry keeps original weights dict (unchanged)",
          s_ab["u"]["weights"] == weights)
    check("scoped 'dc' actually differs from pooled (proves it re-pooled)",
          s_ab["u"]["dc"] != pooled_dc)

    # --- partial miss: at least one visible var present -> scope to it ----------
    s_partial = dc_adapter.scoped_detailed_map(detailed, ["var_a", "not_a_belief"])
    check("partial miss (one present, one absent) scopes to the present one",
          abs(s_partial["u"]["dc"] - 0.5) < 1e-9)

    # --- fail-loud / guard edges ----------------------------------------------
    check("empty visible_vars raises ValueError",
          raises(ValueError, lambda: dc_adapter.scoped_detailed_map(detailed, [])))
    check("all-None visible_vars raises ValueError",
          raises(ValueError,
                 lambda: dc_adapter.scoped_detailed_map(detailed, [None, None])))
    check("no visible var in a teen's consistency raises KeyError",
          raises(KeyError,
                 lambda: dc_adapter.scoped_detailed_map(detailed, ["nope", "gone"])))

    # --- zero-weight subset -> 0.0 (the same 0/0 guard dc_for_teen uses) --------
    zero_w = {"u": make_entry({"var_a": 0.7, "var_b": 0.3},
                              {"var_a": 0.0, "var_b": 0.0})}
    s_zero = dc_adapter.scoped_detailed_map(zero_w, ["var_a", "var_b"])
    check("zero total weight over the subset -> dc 0.0 (no divide-by-zero)",
          s_zero["u"]["dc"] == 0.0)

    # ===================================================================== #
    # evaluate_trigger: fires off PER-VARIABLE scores, never a pooled one
    # ===================================================================== #
    print("\nevaluate_trigger -- per-variable fire decision:")

    # 12 teens, weights 1 each. Built so that on the {var_a, var_b} axes the six
    # "target" teens are the population MAX, but pooled over all three they are the
    # population MIN (var_c cancels/inverts the a,b signal):
    #   target: a=+1 b=+1 c=-1  -> scoped{a,b}=+1.0 (max) , pooled=+1/3 (min)
    #   other : a=-1 b=-1 c=+5  -> scoped{a,b}=-1.0       , pooled=+1.0
    target_c = {"var_a": 1.0, "var_b": 1.0, "var_c": -1.0}
    other_c = {"var_a": -1.0, "var_b": -1.0, "var_c": 5.0}
    w1 = {"var_a": 1.0, "var_b": 1.0, "var_c": 1.0}

    detailed12 = {}
    targets = [f"t{i}" for i in range(6)]
    others = [f"t{i}" for i in range(6, 12)]
    for tid in targets:
        detailed12[tid] = make_entry(target_c, w1)
    for tid in others:
        detailed12[tid] = make_entry(other_c, w1)

    # Dwell ONLY on the targets, var_a/var_b as the live axes on every hover: each of
    # var_a and var_b accrues 30s of its own eligible dwell (>= MIN_ELIGIBLE_DWELL_
    # SECONDS), so per-variable readiness clears for both.
    fire_logs = [mouseout(tid, 5000, x="var_a", y="var_b") for tid in targets]
    record = {"bias_logs": fire_logs, "dc_map_detailed": detailed12}
    metrics = dc_adapter.compute_dwell_metrics(detailed12, fire_logs)

    fired, reason, trace = llm_trigger.evaluate_trigger(record, metrics)
    pbv = trace["percentile_by_var"]
    print(f"    percentile_by_var={pbv}  target={trace['target_var']}  reason={reason!r}")
    check("both ready vars were scored INDEPENDENTLY (one percentile each, no pooling)",
          pbv is not None and set(pbv) == {"var_a", "var_b"})
    check("per-variable percentiles at/above threshold -> FIRES",
          fired is True and reason == "ok")
    check("the trace names the winning variable and ITS percentile",
          trace["target_var"] in ("var_a", "var_b")
          and trace["target_percentile"] == pbv[trace["target_var"]]
          and trace["target_percentile"] >= llm_trigger.DWELL_PERCENTILE_THRESHOLD)
    check("both vars drew INDEPENDENT nulls (the single rng is advanced across the "
          "loop, not reset per variable) -- their percentiles are not identical",
          pbv["var_a"] != pbv["var_b"])
    check("tier and confidence tie, so the hierarchy's PERCENTILE step picks the "
          "winner -- the higher of the two",
          trace["target_var"] == max(pbv, key=lambda v: pbv[v]))

    # Contrast (PRESERVED from the pooled era, and now proving more): the pooled score
    # over the same dwell sits at the population FLOOR, so a pooled trigger would not
    # have fired at all. Seeded for determinism.
    dwell = dc_metric.dwell_by_teen(fire_logs)
    pooled_pct = dc_metric.dwell_bias_percentile(
        detailed12, dwell, n_trials=2000, rng=np.random.default_rng(0))
    print(f"    pooled percentile={pooled_pct} (same dwell, pooled DC)")
    check("pooled percentile on the same dwell is well BELOW threshold "
          "(proves the fire came off the per-variable scores, not a pooled one)",
          pooled_pct is not None and pooled_pct < 0.10)

    # --- below_percentile names the VARIABLE and its own percentile -------------
    # Same dwell, but axis var_c only: on its own scope the dwelled teens sit at the
    # floor, so var_c's percentile is far below threshold and nothing fires.
    c_logs = [mouseout(tid, 5000, x="var_c") for tid in targets]  # y axis unassigned
    rec_c = {"bias_logs": c_logs, "dc_map_detailed": detailed12}
    m_c = dc_adapter.compute_dwell_metrics(detailed12, c_logs)
    f_c, r_c, tr_c = llm_trigger.evaluate_trigger(rec_c, m_c)
    print(f"    reason={r_c!r}  percentile_by_var={tr_c['percentile_by_var']}")
    check("axis var_c: does not fire, reason is below_percentile",
          f_c is False and r_c.startswith("below_percentile"))
    check("only the one active variable was scored",
          set(tr_c["percentile_by_var"]) == {"var_c"})
    check("reason names the best variable and its OWN percentile",
          f"best var_c={tr_c['percentile_by_var']['var_c']:.3f}" in r_c)
    check("no winner on a below-threshold check (target_var stays None)",
          tr_c["target_var"] is None and tr_c["target_percentile"] is None)

    # --- guard: no axes established yet -> not-ready, does not fire -------------
    # Same dwell (readiness clears) but the hovers carry no axis names.
    noaxis_logs = [mouseout(tid, 5000) for tid in targets]
    rec_noaxis = {"bias_logs": noaxis_logs, "dc_map_detailed": detailed12}
    m_noaxis = dc_adapter.compute_dwell_metrics(detailed12, noaxis_logs)
    f2, r2, tr2 = llm_trigger.evaluate_trigger(rec_noaxis, m_noaxis)
    check("no visible axes -> not fired, reason 'no_visible_axes', nothing scored",
          f2 is False and r2 == "no_visible_axes"
          and tr2["percentile_by_var"] is None)

    # --- guard: axis carries a non-belief attribute -----------------------------
    # Under per-variable scoring this is caught by the belief-var INTERSECTION before
    # any scoping, not by scoped_detailed_map raising -- so the reason is the
    # not-ready variant 'no_belief_vars', not the old 'scope_failed'.
    badaxis_logs = [mouseout(tid, 5000, x="child_id") for tid in targets]
    rec_bad = {"bias_logs": badaxis_logs, "dc_map_detailed": detailed12}
    m_bad = dc_adapter.compute_dwell_metrics(detailed12, badaxis_logs)
    f3, r3, tr3 = llm_trigger.evaluate_trigger(rec_bad, m_bad)
    print(f"    non-belief axis child_id -> reason={r3!r}")
    check("non-belief axis -> not fired, reason starts 'no_belief_vars', nothing scored",
          f3 is False and r3.startswith("no_belief_vars")
          and tr3["percentile_by_var"] is None)

    # ===================================================================== #
    # THE ITEM-1 FIX: a variable is scored against ITS OWN per-teen dwell, not the
    # global pooled dwell. Hovers made while a variable was INACTIVE must not weight
    # that variable's score at all.
    # ===================================================================== #
    print("\nper-variable dwell EVIDENCE (scoring reads each var's own hovers):")

    # 12 teens: hot t0..t5 are the population MAX on BOTH vars, cold t6..t11 the MIN.
    # var_a is on the axis for the hot hovers ONLY; var_b for the cold hovers ONLY.
    #   var_a's own dwell = {t0..t5} (the MAX teens)  -> extreme  -> fires
    #   var_b's own dwell = {t6..t11} (the MIN teens) -> anti-extreme -> does not
    #   GLOBAL dwell (what the old scorer used) = all 12, i.e. the whole population,
    #   which is the null's own mean -> DwellBias 0 -> nothing would fire on it.
    ev_map = {}
    for i in range(6):
        ev_map[f"t{i}"] = make_entry({"var_a": 1.0, "var_b": 1.0},
                                     {"var_a": 1.0, "var_b": 1.0})
    for i in range(6, 12):
        ev_map[f"t{i}"] = make_entry({"var_a": -1.0, "var_b": -1.0},
                                     {"var_a": 1.0, "var_b": 1.0})
    ev_logs = [mouseout(f"t{i}", 5000, x="var_a") for i in range(6)]        # 30s -> var_a
    ev_logs += [mouseout(f"t{i}", 5000, x="var_b") for i in range(6, 12)]   # 30s -> var_b
    # Both variables are on the axes NOW (so both are candidates), but each one's
    # HISTORY was earned under its own configuration. A group hover carries the axes
    # without adding dwell to either -- see mouseout_group.
    ev_logs.append(mouseout_group(x="var_a", y="var_b"))

    by_teen = llm_trigger.eligible_dwell_by_teen_by_var(ev_logs, [])
    check("eligible dwell is kept PER TEEN per variable",
          by_teen["var_a"] == {f"t{i}": 5000.0 for i in range(6)}
          and by_teen["var_b"] == {f"t{i}": 5000.0 for i in range(6, 12)})
    check("the seconds view is DERIVED from it (same numbers, summed)",
          llm_trigger.eligible_dwell_seconds_by_var(ev_logs, [])
          == {"var_a": 30.0, "var_b": 30.0})

    ev_rec = {"bias_logs": ev_logs, "dc_map_detailed": ev_map}
    ev_m = dc_adapter.compute_dwell_metrics(ev_map, ev_logs)
    ev_f, ev_r, ev_tr = llm_trigger.evaluate_trigger(ev_rec, ev_m)
    print(f"    per-var dwell -> fired={ev_f} target={ev_tr['target_var']} "
          f"pbv={ev_tr['percentile_by_var']}")
    check("var_a, scored on ITS OWN hovers (the MAX teens), clears the threshold",
          ev_tr["percentile_by_var"]["var_a"] >= llm_trigger.DWELL_PERCENTILE_THRESHOLD)
    check("var_b, scored on ITS OWN hovers (the MIN teens), does NOT",
          ev_tr["percentile_by_var"]["var_b"] < llm_trigger.DWELL_PERCENTILE_THRESHOLD)
    check("so it fires on var_a alone",
          ev_f is True and ev_tr["target_var"] == "var_a")

    # The proof this is the per-variable dwell and not the global one: score the SAME
    # variable against the GLOBAL dwell (all 12 teens, what the old code passed) and
    # it lands nowhere near the threshold.
    global_dwell = dc_metric.dwell_by_teen(ev_logs)
    scoped_a = dc_adapter.scoped_detailed_map(ev_map, ["var_a"])
    global_pct = dc_metric.dwell_bias_percentile(
        scoped_a, global_dwell, n_trials=2000, rng=np.random.default_rng(0))
    print(f"    same var_a scope, GLOBAL dwell -> pct={global_pct} "
          f"(vs {ev_tr['percentile_by_var']['var_a']} on var_a's own dwell)")
    check("var_a scored on the GLOBAL dwell would NOT have fired -- the fire comes "
          "from var_a's own eligible dwell (this is Shiyao's item 1)",
          global_pct is not None
          and global_pct < llm_trigger.DWELL_PERCENTILE_THRESHOLD
          and ev_tr["percentile_by_var"]["var_a"] > global_pct)

    # ===================================================================== #
    # PER-VARIABLE recheck cooldown: one axis cooling never blocks the other
    # ===================================================================== #
    print("\nper-variable recheck cooldown:")
    RECHECK = llm_trigger.DWELL_RECHECK_SECONDS  # 10.0s of new ELIGIBLE dwell per var
    # In this block each variable is on an axis for EVERY hover, so its eligible dwell
    # equals the global total -- the numbers coincide; the per-variable-only difference
    # is exercised in the "per-variable eligible dwell" section further down.

    def hotcold_map(hot_c, cold_c, w):
        """12 teens: t0..t5 'hot' (dwelled), t6..t11 'cold' (the negation-ish)."""
        m = {}
        for i in range(6):
            m[f"t{i}"] = make_entry(hot_c, w)
        for i in range(6, 12):
            m[f"t{i}"] = make_entry(cold_c, w)
        return m

    def hot_dwell(x, y):
        """30s of dwell (6 unique teens x 5s) on the hot teens -> readiness clears."""
        return [mouseout(f"t{i}", 5000, x=x, y=y) for i in range(6)]

    # Map A: hot = pop MAX on both a and b (and pooled). Any scope over a/b fires.
    wAB = {"var_a": 1.0, "var_b": 1.0}
    mapA = hotcold_map({"var_a": 1.0, "var_b": 1.0},
                       {"var_a": -1.0, "var_b": -1.0}, wAB)

    # --- independent clocks: var_a cooling, var_b ready -> var_b still checked ---
    logsA = hot_dwell("var_a", "var_b")             # total 30.0s
    recA = {"bias_logs": logsA, "dc_map_detailed": mapA,
            "dwell_last_checked_by_var": {"var_a": 25.0}}   # a: 30-25=5 <10 (cooling)
    mA = dc_adapter.compute_dwell_metrics(mapA, logsA)
    fA, rA, trA = llm_trigger.evaluate_trigger(recA, mA)
    print(f"    a cooling / b ready -> fired={fA} reason={rA!r} "
          f"pbv={trA['percentile_by_var']}")
    check("var_a cooling does NOT block var_b -> a check runs",
          trA["percentile_by_var"] is not None)
    check("a COOLING variable is not scored at all (it never enters percentile_by_var)",
          set(trA["percentile_by_var"]) == {"var_b"})
    check("ready var (b) fires on its own score",
          fA is True and rA == "ok" and trA["target_var"] == "var_b")
    check("cooling var_a's clock is left untouched (25.0)",
          recA["dwell_last_checked_by_var"]["var_a"] == 25.0)
    check("the winner var_b's clock advanced to its own eligible dwell (30.0)",
          recA["dwell_last_checked_by_var"]["var_b"] == 30.0)
    check("fired check recorded ONLY var_b (the winner), for the dismiss rebase",
          recA["dwell_last_fired_vars"] == ["var_b"])

    # --- contrast: BOTH cooling -> too_soon, no check, no writes ----------------
    recA2 = {"bias_logs": logsA, "dc_map_detailed": mapA,
             "dwell_last_checked_by_var": {"var_a": 25.0, "var_b": 25.0}}
    fA2, rA2, trA2 = llm_trigger.evaluate_trigger(recA2, mA)
    check("both vars cooling -> too_soon, no check (nothing scored), no fire",
          fA2 is False and rA2.startswith("too_soon")
          and trA2["percentile_by_var"] is None)
    check("too_soon writes nothing: both clocks still 25.0",
          recA2["dwell_last_checked_by_var"] == {"var_a": 25.0, "var_b": 25.0})

    # Map B: unequal weights so scope {a} and pooled {a,b} DISAGREE on the hot teens.
    #   hot  a=+1 b=-1  w=(1,4): scoped{a}=+1 (MAX), pooled=(1-4)/5=-0.6 (MIN)
    #   cold a=-1 b=+1        : scoped{a}=-1 (MIN), pooled=+0.6 (MAX)
    wB = {"var_a": 1.0, "var_b": 4.0}
    mapB = hotcold_map({"var_a": 1.0, "var_b": -1.0},
                       {"var_a": -1.0, "var_b": 1.0}, wB)
    logsB = hot_dwell("var_a", "var_b")             # total 30.0s
    dwellB = dc_metric.dwell_by_teen(logsB)
    mB = dc_adapter.compute_dwell_metrics(mapB, logsB)

    # --- INVERTED from the pooled era: both ready -> two INDEPENDENT scores --------
    # This map is the one built to make pooling and per-variable scoring DISAGREE, and
    # it used to prove the trigger pooled: the hot teens are the pooled {a,b} MINIMUM
    # (var_b's weight of 4 swamps var_a's 1), so a joint score could not fire no matter
    # how extreme var_a was on its own. Now each variable is scored alone -- w_v cancels,
    # var_b cannot swamp anything -- and var_a fires on its own merits. Same map, same
    # dwell, opposite outcome: that is the restructuring.
    recB_both = {"bias_logs": logsB, "dc_map_detailed": mapB}   # no clocks: both ready
    fB, rB, trB = llm_trigger.evaluate_trigger(recB_both, mB)
    pooled_obs = dc_metric.dwell_bias(
        dc_adapter.scoped_detailed_map(mapB, ["var_a", "var_b"]), dwellB)
    pooled_pct_B = dc_metric.dwell_bias_percentile(
        dc_adapter.scoped_detailed_map(mapB, ["var_a", "var_b"]), dwellB,
        n_trials=2000, rng=np.random.default_rng(0))
    print(f"    both ready -> fired={fB} target={trB['target_var']} "
          f"pbv={trB['percentile_by_var']}")
    print(f"    (the pooled {{a,b}} score on the same dwell: observed={pooled_obs:+.4f} "
          f"pct={pooled_pct_B})")
    check("both ready -> BOTH scored independently, neither pooled away",
          set(trB["percentile_by_var"]) == {"var_a", "var_b"})
    check("var_a, on its own, is the population MAX -> clears the threshold",
          trB["percentile_by_var"]["var_a"] >= llm_trigger.DWELL_PERCENTILE_THRESHOLD)
    check("var_b, on its own, is the population MIN -> does not",
          trB["percentile_by_var"]["var_b"] < llm_trigger.DWELL_PERCENTILE_THRESHOLD)
    check("so it FIRES on var_a -- where the pooled score was below threshold and "
          "could not fire at all (the inversion this restructuring is for)",
          fB is True and rB == "ok" and trB["target_var"] == "var_a"
          and pooled_pct_B < llm_trigger.DWELL_PERCENTILE_THRESHOLD)

    # --- WINNER-ONLY cooldown on a fire (Shiyao's item 5) -------------------------
    # var_b WAS scored this check, and lost. Its recheck clock must not be spent on an
    # intervention that was not about it.
    check("on a fire, ONLY the winner's clock advances",
          recB_both["dwell_last_checked_by_var"] == {"var_a": 30.0})
    check("the scored-but-losing var_b keeps its recheck budget (no clock entry)",
          "var_b" not in recB_both["dwell_last_checked_by_var"])
    check("dwell_last_fired_vars records the winner ALONE, not every scored var",
          recB_both["dwell_last_fired_vars"] == ["var_a"])

    # --- ALL-SCORED cooldown on a NON-fire (the confirmed asymmetry) ---------------
    # Nothing clears the threshold, so no intervention was about anything -- but a real
    # check DID run over both variables, so both must earn DWELL_RECHECK_SECONDS of new
    # eligible dwell before being asked again. Map: hot teens are the MIN on both vars,
    # so neither can clear.
    mapN = hotcold_map({"var_a": -1.0, "var_b": -1.0},
                       {"var_a": 1.0, "var_b": 1.0}, wAB)
    logsN = hot_dwell("var_a", "var_b")
    mN = dc_adapter.compute_dwell_metrics(mapN, logsN)
    recN2 = {"bias_logs": logsN, "dc_map_detailed": mapN}
    fN2, rN2, trN2 = llm_trigger.evaluate_trigger(recN2, mN)
    print(f"    non-fire -> reason={rN2!r} pbv={trN2['percentile_by_var']}")
    check("neither var clears -> no fire, below_percentile",
          fN2 is False and rN2.startswith("below_percentile"))
    check("on a NON-fire, EVERY scored variable's clock advances (recheck spacing is "
          "preserved for non-winners)",
          recN2["dwell_last_checked_by_var"] == {"var_a": 30.0, "var_b": 30.0})
    check("a non-fire records no fired-vars marker",
          "dwell_last_fired_vars" not in recN2)

    # ===================================================================== #
    # reset_dwell_watermark rebases ONLY the variable the intervention was ABOUT
    # ===================================================================== #
    print("\nreset_dwell_watermark -- rebase only the WINNING var:")

    # Fire covered var_b only (a was cooling). Then more dwell accrues while the
    # panel is up; dismiss must rebase var_b (to the new total) and leave var_a.
    logsR = hot_dwell("var_a", "var_b")
    recR = {"bias_logs": logsR, "dc_map_detailed": mapA,
            "dwell_last_checked_by_var": {"var_a": 25.0}}
    mR = dc_adapter.compute_dwell_metrics(mapA, logsR)
    fR, rR, _ = llm_trigger.evaluate_trigger(recR, mR)
    check("setup: fires on var_b only, records dwell_last_fired_vars=[var_b]",
          fR is True and recR["dwell_last_fired_vars"] == ["var_b"])
    # participant reads the intervention: +20s of dwell (t0) -> total 50s
    recR["bias_logs"].append(mouseout("t0", 20000, x="var_a", y="var_b"))
    llm_trigger.reset_dwell_watermark(recR)
    check("dismiss rebases the fired var_b to its own new eligible dwell (50.0)",
          recR["dwell_last_checked_by_var"]["var_b"] == 50.0)
    check("dismiss leaves the non-fired var_a exactly where it was (25.0)",
          recR["dwell_last_checked_by_var"]["var_a"] == 25.0)
    check("dismiss consumes the fired-vars marker (no repeat rebase)",
          "dwell_last_fired_vars" not in recR)

    # INVERTED from the pooled era: with BOTH vars ready and both scored, the fire is
    # still about ONE of them, so dismiss rebases that one alone. The loser was never
    # given a clock by the firing check (winner-only), so it has no entry to rebase --
    # it is still owed only its original readiness floor, not a post-panel rebase.
    logsR2 = hot_dwell("var_a", "var_b")
    recR2 = {"bias_logs": logsR2, "dc_map_detailed": mapA}
    mR2 = dc_adapter.compute_dwell_metrics(mapA, logsR2)
    fR2, _, trR2 = llm_trigger.evaluate_trigger(recR2, mR2)
    winnerR2 = trR2["target_var"]
    loserR2 = next(v for v in ("var_a", "var_b") if v != winnerR2)
    recR2["bias_logs"].append(mouseout("t0", 20000, x="var_a", y="var_b"))  # -> 50s
    llm_trigger.reset_dwell_watermark(recR2)
    print(f"    both scored, winner={winnerR2} -> dismiss rebases "
          f"{recR2['dwell_last_checked_by_var']}")
    check("both-var check then dismiss rebases ONLY the winner to 50.0",
          fR2 is True
          and recR2["dwell_last_checked_by_var"] == {winnerR2: 50.0})
    check("the scored-but-losing var is not rebased (and was never clocked at all)",
          loserR2 not in recR2["dwell_last_checked_by_var"])

    # No fire on record -> reset is a no-op (nothing to rebase).
    recR3 = {"bias_logs": hot_dwell("var_a", "var_b"), "dc_map_detailed": mapA,
             "dwell_last_checked_by_var": {"var_a": 7.0}}
    llm_trigger.reset_dwell_watermark(recR3)
    check("reset with no dwell_last_fired_vars is a no-op",
          recR3["dwell_last_checked_by_var"] == {"var_a": 7.0})

    # ===================================================================== #
    # Active-filter scope: a filtered variable joins x/y as an "active" variable
    # (_axis_and_filter_vars keeps the two tiers apart but both are candidates).
    # ===================================================================== #
    print("\nactive-filter scope (filtered vars join the axis vars):")

    def filter_log(itype, attribute=None):
        """A response_list entry (a wrapped message) for a filter interaction."""
        data = {} if attribute is None else {"attribute": attribute}
        return {"input_data": {"interactionType": itype, "data": data}}

    # Map C: 3 vars, hot teens are the population MAX on each -> any single-var scope
    # fires on the hot dwell.
    wC = {"var_a": 1.0, "var_b": 1.0, "var_c": 1.0}
    mapC = hotcold_map({"var_a": 1.0, "var_b": 1.0, "var_c": 1.0},
                       {"var_a": -1.0, "var_b": -1.0, "var_c": -1.0}, wC)
    noaxis_dwell = [mouseout(f"t{i}", 5000) for i in range(6)]   # 30s, NO axis names
    mC = dc_adapter.compute_dwell_metrics(mapC, noaxis_dwell)

    # --- a filtered-but-not-on-axis variable is scored and fires on its own ------
    recF = {"bias_logs": noaxis_dwell, "dc_map_detailed": mapC,
            "response_list": [filter_log("filter_added", "var_c")]}
    fF, rF, trF = llm_trigger.evaluate_trigger(recF, mC)
    print(f"    filter-only var_c (no axes) -> fired={fF} reason={rF!r}")
    check("no axes but var_c filtered -> it is the active scope and FIRES",
          fF is True and rF == "ok" and trF["target_var"] == "var_c")
    check("the filtered var got scored",
          trF["percentile_by_var"] is not None
          and trF["percentile_by_var"].get("var_c") is not None)
    check("the filtered var got its own cooldown entry (30.0)",
          recF["dwell_last_checked_by_var"]["var_c"] == 30.0)
    check("fired check recorded the filtered var",
          recF["dwell_last_fired_vars"] == ["var_c"])

    # --- filter var has an INDEPENDENT clock from an axis var --------------------
    # var_a on the x axis is cooling; var_c (filter-only) is fresh -> var_c is checked
    # and var_a is left alone. Same independent-clock guarantee as two axis vars.
    axis_a_dwell = [mouseout(f"t{i}", 5000, x="var_a") for i in range(6)]   # 30s
    recFI = {"bias_logs": axis_a_dwell, "dc_map_detailed": mapC,
             "response_list": [filter_log("filter_added", "var_c")],
             "dwell_last_checked_by_var": {"var_a": 25.0}}   # var_a cooling (5 < 10)
    mFI = dc_adapter.compute_dwell_metrics(mapC, axis_a_dwell)
    fFI, rFI, _ = llm_trigger.evaluate_trigger(recFI, mFI)
    check("cooling axis var_a does NOT block the filtered var_c -> fires on var_c",
          fFI is True and rFI == "ok")
    check("cooling axis var_a's clock is untouched (25.0)",
          recFI["dwell_last_checked_by_var"]["var_a"] == 25.0)
    check("filter var_c got its own independent clock (30.0)",
          recFI["dwell_last_checked_by_var"]["var_c"] == 30.0)
    check("fired check recorded ONLY the ready filter var (var_c)",
          recFI["dwell_last_fired_vars"] == ["var_c"])

    # --- non-belief filter var alongside a valid belief var -> dropped by the
    # belief-var INTERSECTION (not by a scoped_detailed_map rescue) -----------------
    # var_a is on the axis (a real belief var); "child_id" is filtered but is NOT a
    # belief variable, so it never reaches scoring at all and var_a is scored alone.
    recND = {"bias_logs": axis_a_dwell, "dc_map_detailed": mapC,
             "response_list": [filter_log("filter_added", "child_id")]}
    mND = dc_adapter.compute_dwell_metrics(mapC, axis_a_dwell)
    fND, rND, trND = llm_trigger.evaluate_trigger(recND, mND)
    print(f"    var_a axis + child_id filter (non-belief) -> fired={fND} reason={rND!r}")
    check("non-belief filter var alongside a valid belief var -> scores the valid "
          "one and FIRES",
          fND is True and rND == "ok" and trND["target_var"] == "var_a")
    check("the non-belief var was never scored (dropped by the intersection, so it "
          "is not even an 'excluded' entry -- it was never a candidate)",
          set(trND["percentile_by_var"]) == {"var_a"}
          and "child_id" not in trND["excluded_vars"])

    # --- filter-only on a non-belief var (TOTAL miss) -> scope_failed, no fire ----
    recNO = {"bias_logs": noaxis_dwell, "dc_map_detailed": mapC,
             "response_list": [filter_log("filter_added", "child_id")]}
    mNO = dc_adapter.compute_dwell_metrics(mapC, noaxis_dwell)
    fNO, rNO, tNO = llm_trigger.evaluate_trigger(recNO, mNO)
    print(f"    filter-only child_id (total miss) -> reason={rNO!r}")
    check("filter-only on a non-belief var (total miss) -> no_belief_vars, no fire. "
          "The intersection catches this BEFORE scoping, so the old scope_failed "
          "(scoped_detailed_map raising) is no longer the path taken",
          fNO is False and rNO.startswith("no_belief_vars")
          and tNO["percentile_by_var"] is None)

    # ===================================================================== #
    # SYSTEM-WIDE DISPLAY PAUSE: nothing new fires while an intervention is still on
    # the participant's screen. This replaced a fixed 30s wall-clock cooldown, so the
    # question is no longer "how much time has passed" but "is the panel still up" --
    # an open/closed state, opened at the fire decision and closed on dismiss. The old
    # boundary tests (29.999s / 30.000s / 30.001s) tested arithmetic that no longer
    # exists; these test the state machine, including the two ways it can be left set
    # with no panel behind it.
    # ===================================================================== #
    print("\nsystem-wide display pause (closed while a panel is displayed):")
    WATCHDOG = llm_trigger.PANEL_FLAG_WATCHDOG_MS   # 120_000 ms
    T0 = 1_000_000                                  # an arbitrary epoch-ms "now"

    def fireable(open_since=None, checked=None):
        """A record that WOULD fire on Map A (both a/b are pop MAX, 30s dwell), with an
        optional open display pause and optional per-variable clocks."""
        logs = hot_dwell("var_a", "var_b")      # 30s, axes var_a/var_b
        rec = {"bias_logs": logs, "dc_map_detailed": mapA}
        if open_since is not None:
            rec["llm_panel_open_since"] = open_since
        if checked is not None:
            rec["dwell_last_checked_by_var"] = dict(checked)
        return rec, dc_adapter.compute_dwell_metrics(mapA, logs)

    # --- first call fires (no panel up) -> the caller opens the pause ----------------
    recS, mS = fireable()
    fS1, rS1, _ = llm_trigger.evaluate_trigger(recS, mS, T0)
    check("first call fires (no llm_panel_open_since yet -> gate open)",
          fS1 is True and rS1 == "ok")
    llm_trigger.open_display_pause(recS, T0)             # mirrors server.py on fire
    check("open_display_pause stamps the fire instant it was given",
          recS["llm_panel_open_since"] == T0)
    checked_after_fire = dict(recS["dwell_last_checked_by_var"])
    fired_vars_after_fire = list(recS["dwell_last_fired_vars"])

    # --- while the panel is up -> suppressed, and NO state mutated -------------------
    fS2, rS2, trS2 = llm_trigger.evaluate_trigger(recS, mS, T0 + 10_000)
    print(f"    fired at T0, re-checked +10s with the panel still up -> "
          f"fired={fS2} reason={rS2!r}")
    check("panel still displayed -> suppressed, reason 'panel_displayed'",
          fS2 is False and rS2.startswith("panel_displayed"))
    check("suppressed display-pause call did NOT advance dwell_last_checked_by_var",
          recS["dwell_last_checked_by_var"] == checked_after_fire)
    check("suppressed display-pause call did NOT change dwell_last_fired_vars",
          recS["dwell_last_fired_vars"] == fired_vars_after_fire)
    check("suppressed call still returns the trace shape (nothing scored, gate "
          "stopped short)",
          trS2["percentile_by_var"] is None and trS2["target_var"] is None)

    # --- NO expiry: elapsed time alone never reopens the gate ------------------------
    # The whole point of the redesign. Under the old 30s cooldown the third and fourth
    # of these would have fired; a panel left up is a panel left up.
    for elapsed in (1, 10_000, 30_000, 60_000, WATCHDOG - 1):
        recW, mW = fireable(open_since=T0)
        fW, rW, _ = llm_trigger.evaluate_trigger(recW, mW, T0 + elapsed)
        check(f"panel open for {elapsed / 1000.0:.3f}s -> still suppressed "
              f"(no duration reopens it)",
              fW is False and rW.startswith("panel_displayed"))

    # --- dismiss clears it, and the very next check may fire immediately -------------
    # No wall-clock gap: once the panel is gone there is nothing left to wait for. This
    # is the accepted tradeoff of dropping the fixed cooldown, tested rather than left
    # implicit -- see the KNOWN CONSEQUENCE note in evaluate_trigger's docstring.
    recD, mD = fireable(open_since=T0)
    llm_trigger.release_display_pause(recD)
    check("release_display_pause removes the flag entirely (absent, not None)",
          "llm_panel_open_since" not in recD)
    fD, rD, _ = llm_trigger.evaluate_trigger(recD, mD, T0 + 1)
    check("dismissed 1ms later -> the immediately following check fires (the gate "
          "tracks the panel, not a clock)",
          fD is True and rD == "ok")
    llm_trigger.release_display_pause(recD)      # idempotent: no KeyError on a repeat
    check("release_display_pause is idempotent (a duplicate dismiss is a no-op)",
          "llm_panel_open_since" not in recD)

    # --- NON-DELIVERY: generation produced nothing, so no dismiss will ever arrive ----
    # server.py's _fire_dwell_intervention calls exactly this on a falsy delivered flag.
    # Without it the participant is muted for the whole session -- the failure mode the
    # old self-expiring cooldown concealed.
    recX, mX = fireable(open_since=T0)
    fX0, rX0, _ = llm_trigger.evaluate_trigger(recX, mX, T0 + 5_000)
    check("setup: undelivered intervention leaves the pause closed",
          fX0 is False and rX0.startswith("panel_displayed"))
    llm_trigger.release_display_pause(recX)      # what the non-delivery path does
    fX1, rX1, _ = llm_trigger.evaluate_trigger(recX, mX, T0 + 5_001)
    check("releasing on non-delivery reopens the gate (no permanent lockout)",
          fX1 is True and rX1 == "ok")

    # --- WATCHDOG: a flag older than any real panel is cleared, not obeyed ------------
    # The dismiss is client-originated and can simply never arrive (refresh mid-panel,
    # socket dropped before the emit flushed). Sized well past 30s of display + 20s of
    # generation, so reaching it means the clear was lost, never that a panel is slow.
    recV, mV = fireable(open_since=T0, checked={"var_a": 25.0})   # var_a cooling
    fV, rV, _ = llm_trigger.evaluate_trigger(recV, mV, T0 + WATCHDOG)
    print(f"    flag {WATCHDOG / 1000.0:.0f}s old -> fired={fV} reason={rV!r}")
    check("at exactly PANEL_FLAG_WATCHDOG_MS the stale flag is cleared and the call "
          "falls through to scoring",
          fV is True and rV == "ok")
    check("the watchdog CLEARS the flag rather than leaving it to re-suppress",
          "llm_panel_open_since" not in recV)
    check("the cleared call scores normally -- per-variable spacing still governs "
          "WHICH var (var_a cooling -> var_b)",
          recV["dwell_last_fired_vars"] == ["var_b"])
    recV2, mV2 = fireable(open_since=T0)
    fV2, rV2, _ = llm_trigger.evaluate_trigger(recV2, mV2, T0 + WATCHDOG + 60_000)
    check("well past the watchdog -> also cleared and fires",
          fV2 is True and rV2 == "ok")

    # --- no clock supplied: an open pause still suppresses, but cannot self-heal ------
    # The presence check needs no clock; only the watchdog does. Suppressing is the safe
    # direction -- the live path always passes now_ms, so this cannot strand anyone.
    recC, mC = fireable(open_since=T0)
    fC, rC, _ = llm_trigger.evaluate_trigger(recC, mC)          # now_ms omitted
    check("no clock + open pause -> still suppressed (presence check needs no clock)",
          fC is False and rC.startswith("panel_displayed"))
    check("the flag survives a clockless call (the watchdog had nothing to measure)",
          recC["llm_panel_open_since"] == T0)
    recC2, mC2 = fireable()
    fC2, rC2, _ = llm_trigger.evaluate_trigger(recC2, mC2)      # no clock, no flag
    check("no clock + no pause -> gate open, fires (unchanged from before)",
          fC2 is True and rC2 == "ok")

    # --- LAYERED on top of per-variable spacing: a var whose OWN clock is ready is
    # still suppressed while a panel is displayed, and its clock is left untouched ------
    recL, mL = fireable(open_since=T0, checked={"var_a": 25.0})  # var_a cooling; var_b ready
    checked_before = dict(recL["dwell_last_checked_by_var"])
    fL, rL, trL = llm_trigger.evaluate_trigger(recL, mL, T0 + 10_000)   # panel still up
    print(f"    var_b per-var-ready but a panel is displayed -> fired={fL} reason={rL!r}")
    check("per-variable clock says var_b is ready, but a panel is DISPLAYED "
          "-> suppressed (layered on top of, not replaced by, per-variable spacing)",
          fL is False and rL.startswith("panel_displayed"))
    check("display-suppressed call left the per-variable clocks untouched (var_b NOT advanced)",
          recL["dwell_last_checked_by_var"] == checked_before
          and "dwell_last_fired_vars" not in recL)
    # Contrast: once the panel is dismissed the SAME record fires -- the per-variable
    # spacing still governs WHICH var is checked (var_a cooling -> scope {var_b}).
    llm_trigger.release_display_pause(recL)
    fL2, rL2, _ = llm_trigger.evaluate_trigger(recL, mL, T0 + 10_001)
    check("after dismiss the gate opens -> fires, per-variable spacing still governs "
          "(checks var_b only, records it)",
          fL2 is True and rL2 == "ok" and recL["dwell_last_fired_vars"] == ["var_b"])

    # ===================================================================== #
    # PER-VARIABLE ELIGIBLE DWELL (Shiyao's spec): a hover counts toward a variable
    # only if that variable was active AT THE MOMENT of that hover; readiness and
    # recheck spacing run on each variable's OWN eligible seconds, not global dwell.
    # ===================================================================== #
    print("\nper-variable eligible dwell (readiness/cooldown on a variable's OWN history):")

    def resp_filter(itype, attribute, at=None):
        """A response_list filter entry with an optional client interactionAt (ms),
        the timestamp filters_active_as_of bounds the replay on."""
        msg = {"interactionType": itype, "data": {"attribute": attribute}}
        if at is not None:
            msg["interactionAt"] = at
        return {"input_data": msg}

    # --- (1) eligible-seconds replay across changing axes + a bounded filter --------
    # var_c is filtered from t=100 to t=300; three hovers under different axis configs.
    #   H1 @150 (dur 4s) x=var_a y=var_b, var_c filtered  -> {var_a,var_b,var_c} +4
    #   H2 @250 (dur 6s) x=var_a,        var_c filtered  -> {var_a,var_c}       +6
    #   H3 @350 (dur 5s) x=var_d y=var_a, var_c REMOVED   -> {var_a,var_d}       +5
    # Hand-computed: a=4+6+5=15, b=4, c=4+6=10 (NOT 15 -- removed before H3), d=5.
    rlist = [resp_filter("filter_added", "var_c", at=100),
             resp_filter("filter_removed", "var_c", at=300)]
    blogs = [mouseout("t0", 4000, x="var_a", y="var_b", at=150),
             mouseout("t1", 6000, x="var_a", at=250),
             mouseout("t2", 5000, x="var_d", y="var_a", at=350)]
    elig = llm_trigger.eligible_dwell_seconds_by_var(blogs, rlist)
    check("eligible replay: var_a = 4+6+5 = 15s (active on every hover)",
          abs(elig.get("var_a", 0.0) - 15.0) < 1e-9)
    check("eligible replay: var_b = 4s (only the first hover's y axis)",
          abs(elig.get("var_b", 0.0) - 4.0) < 1e-9)
    check("eligible replay: var_c = 10s (filtered for H1+H2, NOT H3 -> bound works)",
          abs(elig.get("var_c", 0.0) - 10.0) < 1e-9)
    check("eligible replay: var_d = 5s (only the last hover's x axis)",
          abs(elig.get("var_d", 0.0) - 5.0) < 1e-9)

    # --- (4) multi-variable hover attribution: H1 was axis {a,b} AND filter {c} ------
    # so that ONE hover's 4s landed on all three simultaneously (a,b via axes, c via
    # filter). Proven by var_b (4s, its only hover is H1) and var_c including H1's 4s.
    check("multi-var attribution: one hover under axis+filter feeds every active var "
          "(H1's 4s reached var_a, var_b AND var_c)",
          abs(elig["var_b"] - 4.0) < 1e-9 and elig["var_c"] >= 4.0 - 1e-9)

    # --- filters_active_as_of is the time-bounded get_current_filters ---------------
    check("filters as-of 150ms -> {var_c} (added @100, not yet removed)",
          llm_trigger.filters_active_as_of(rlist, 150) == {"var_c"})
    check("filters as-of 350ms -> {} (removed @300)",
          llm_trigger.filters_active_as_of(rlist, 350) == set())
    check("filters as-of None (unbounded) == get_current_filters (added then removed "
          "-> empty)",
          llm_trigger.filters_active_as_of(rlist, None) == set())

    # --- (2) active-now but thin -> NOT ready, while a var with its own history fires -
    # Shiyao's example: dwell builds on one variable, then the participant switches to a
    # new one; the new one is active but must NOT be checkable until it has its own 20s.
    # Here var_a is filtered throughout and accrues 28s; var_b is switched onto the axis
    # only at the very end and has just 3s. Both are active now; only var_a is ready.
    mapT = hotcold_map({"var_a": 1.0, "var_b": 1.0},
                       {"var_a": -1.0, "var_b": -1.0},
                       {"var_a": 1.0, "var_b": 1.0})
    rlistT = [resp_filter("filter_added", "var_a", at=10)]     # var_a filtered from the start
    blogsT = [mouseout(f"t{i}", 5000, at=100 + 10 * i) for i in range(5)]  # 25s, no axis -> {var_a}
    blogsT.append(mouseout("t5", 3000, x="var_b", at=200))     # +3s: axis var_b, still filter var_a
    recT = {"bias_logs": blogsT, "dc_map_detailed": mapT, "response_list": rlistT}
    eligT = llm_trigger.eligible_dwell_seconds_by_var(blogsT, rlistT)
    check("switch scenario: var_a accrued its own 28s (filtered on every hover)",
          abs(eligT.get("var_a", 0.0) - 28.0) < 1e-9)
    check("switch scenario: var_b has only 3s of its OWN eligible dwell (just switched to)",
          abs(eligT.get("var_b", 0.0) - 3.0) < 1e-9)
    mT = dc_adapter.compute_dwell_metrics(mapT, blogsT)
    fT, rT, trT = llm_trigger.evaluate_trigger(recT, mT)
    print(f"    var_a 28s / var_b 3s, both active -> fired={fT} reason={rT!r}")
    check("var_a (its own 28s) is checked and FIRES; thin var_b does not block it",
          fT is True and rT == "ok")
    check("the fire covered ONLY var_a -- var_b was too thin to be checked",
          recT["dwell_last_fired_vars"] == ["var_a"])
    check("thin var_b never earned a cooldown entry (not checked this call)",
          "var_b" not in recT["dwell_last_checked_by_var"])

    # var_b ALONE (the only active variable, still thin) -> not_ready, no fire.
    recTb = {"bias_logs": [mouseout("t0", 3000, x="var_b")], "dc_map_detailed": mapT}
    mTb = dc_adapter.compute_dwell_metrics(mapT, recTb["bias_logs"])
    fTb, rTb, _ = llm_trigger.evaluate_trigger(recTb, mTb)
    check("var_b active but only 3s of its own eligible dwell -> not_ready (not fired)",
          fTb is False and rTb.startswith("not_ready"))

    # --- (3) recheck cooldown is measured in the variable's OWN eligible seconds ------
    # var_a is checked at 25s; then 25s of dwell accrues on var_b ONLY (var_a is off the
    # axis and unfiltered during it); finally var_a returns to the axis for 1s. var_a's
    # own new eligible dwell is just 1s (< 10) -> it stays cooling, EVEN THOUGH global
    # dwell grew by 26s. A global clock would have re-checked it; a per-variable one does
    # not. This is the whole point: one variable's dwell never advances another's clock.
    mapCD = hotcold_map({"var_a": 1.0, "var_b": 1.0},
                        {"var_a": -1.0, "var_b": -1.0},
                        {"var_a": 1.0, "var_b": 1.0})
    blogsCD = [mouseout(f"t{i}", 5000, x="var_a") for i in range(5)]      # 25s -> var_a
    blogsCD += [mouseout(f"t{i}", 5000, x="var_b") for i in range(5, 10)]  # 25s -> var_b ONLY
    blogsCD.append(mouseout("t0", 1000, x="var_a"))                        # +1s -> var_a back on axis
    eligCD = llm_trigger.eligible_dwell_seconds_by_var(blogsCD, [])
    check("cooldown units: var_a earned only its own 26s (25 + 1), NOT the 25s of var_b",
          abs(eligCD.get("var_a", 0.0) - 26.0) < 1e-9
          and abs(eligCD.get("var_b", 0.0) - 25.0) < 1e-9)
    recCD = {"bias_logs": blogsCD, "dc_map_detailed": mapCD,
             "dwell_last_checked_by_var": {"var_a": 25.0}}   # var_a last checked at its own 25s
    mCD = dc_adapter.compute_dwell_metrics(mapCD, blogsCD)
    fCD, rCD, trCD = llm_trigger.evaluate_trigger(recCD, mCD)
    print(f"    var_a +1s own / +25s of var_b between -> fired={fCD} reason={rCD!r} "
          f"(global total={trCD['total_dwell_seconds']}s)")
    check("var_a stays cooling: only 1s of its OWN new eligible dwell (< 10s) -> too_soon",
          fCD is False and rCD.startswith("too_soon"))
    check("proof it is NOT a global clock: global dwell grew to 51s, yet var_a's own "
          "clock advanced just 1s",
          abs(trCD["total_dwell_seconds"] - 51.0) < 1e-9)
    check("var_a's cooldown entry was NOT advanced (still 25.0, earned < 10s of its own)",
          recCD["dwell_last_checked_by_var"]["var_a"] == 25.0)

    # --- (5) MIN_UNIQUE_HOVERS is gone; distinct-teen count is no longer a gate -------
    check("MIN_UNIQUE_HOVERS constant removed from llm_trigger",
          not hasattr(llm_trigger, "MIN_UNIQUE_HOVERS"))
    check("MIN_TOTAL_DWELL_SECONDS (the old global gate) removed",
          not hasattr(llm_trigger, "MIN_TOTAL_DWELL_SECONDS"))
    check("MIN_ELIGIBLE_DWELL_SECONDS is the readiness floor (20.0)",
          getattr(llm_trigger, "MIN_ELIGIBLE_DWELL_SECONDS", None) == 20.0)
    # A SINGLE teen hovered 20s on one axis makes that variable ready and fires -- the
    # old gate needed >= 5 distinct teens, so this is the concrete proof it is gone.
    mapU = hotcold_map({"var_a": 1.0}, {"var_a": -1.0}, {"var_a": 1.0})
    blogsU = [mouseout("t0", 20000, x="var_a")]            # one teen, 20s, one variable
    recU2 = {"bias_logs": blogsU, "dc_map_detailed": mapU}
    mU2 = dc_adapter.compute_dwell_metrics(mapU, blogsU)
    fU2, rU2, trU2 = llm_trigger.evaluate_trigger(recU2, mU2)
    print(f"    one teen, 20s, one var -> fired={fU2} reason={rU2!r} (n_dwelled={trU2['n_dwelled']})")
    check("one distinct hover of 20s makes its variable ready and FIRES "
          "(no distinct-teen minimum any more)",
          fU2 is True and rU2 == "ok" and trU2["n_dwelled"] == 1)

    # ===================================================================== #
    # DEGENERATE-NULL GUARD: a variable whose per-teen DC is CONSTANT would score a
    # guaranteed 1.0 against a collapsed null and fire on every single check. It must
    # be dropped from candidacy BEFORE sampling, not scored.
    # ===================================================================== #
    print("\ndegenerate-null guard (a flat variable must not be a free fire):")

    # var_flat: the participant drew the two groups IDENTICALLY, so vba returns
    # log(1)=0 in every bin -> C_v is 0 for every teen AND w_v is 0. scoped_detailed_
    # map's 0/0 guard then yields dc 0.0 for all teens: a completely flat scope.
    mapD = {}
    for i in range(6):
        mapD[f"t{i}"] = make_entry({"var_flat": 0.0, "var_real": 1.0},
                                   {"var_flat": 0.0, "var_real": 1.0})
    for i in range(6, 12):
        mapD[f"t{i}"] = make_entry({"var_flat": 0.0, "var_real": -1.0},
                                   {"var_flat": 0.0, "var_real": 1.0})

    # First, the failure this guard exists to prevent, demonstrated on the raw metric:
    # scored directly, a flat variable's percentile is a guaranteed 1.0.
    scoped_flat = dc_adapter.scoped_detailed_map(mapD, ["var_flat"])
    flat_pct = dc_metric.dwell_bias_percentile(
        scoped_flat, {f"t{i}": 5000.0 for i in range(6)},
        n_trials=500, rng=np.random.default_rng(0))
    print(f"    unguarded, a flat variable scores pct={flat_pct} (every null draw ties)")
    check("WITHOUT the guard a flat variable would score a guaranteed 1.0 -- i.e. it "
          "would clear any threshold on every check, forever",
          flat_pct == 1.0)
    check("_is_degenerate_scope recognises that scope",
          llm_trigger._is_degenerate_scope(scoped_flat) is True)
    check("_is_degenerate_scope does NOT flag a scope with real spread",
          llm_trigger._is_degenerate_scope(
              dc_adapter.scoped_detailed_map(mapD, ["var_real"])) is False)

    # Now through the trigger: both vars are active and ready, but var_flat must be
    # excluded from candidacy rather than winning on its free 1.0.
    logsD = [mouseout(f"t{i}", 5000, x="var_flat", y="var_real") for i in range(6)]
    recD = {"bias_logs": logsD, "dc_map_detailed": mapD}
    mD = dc_adapter.compute_dwell_metrics(mapD, logsD)
    fD, rD, trD = llm_trigger.evaluate_trigger(recD, mD)
    print(f"    through the trigger -> fired={fD} target={trD['target_var']} "
          f"pbv={trD['percentile_by_var']} excluded={trD['excluded_vars']}")
    check("the flat variable is never scored (excluded before null-sampling)",
          "var_flat" not in trD["percentile_by_var"])
    check("and it is marked as such, distinguishably from a below-threshold miss",
          trD["excluded_vars"] == {"var_flat": "degenerate_null"})
    check("the real variable is still scored normally alongside it",
          set(trD["percentile_by_var"]) == {"var_real"})
    check("so the winner is the REAL variable, never the flat free-1.0 one",
          fD is True and trD["target_var"] == "var_real")
    check("the excluded variable earns NO cooldown entry (it was not evaluated)",
          "var_flat" not in recD["dwell_last_checked_by_var"])

    # Every candidate degenerate -> a distinct reason, and NO clock advances at all.
    mapD2 = {f"t{i}": make_entry({"var_flat": 0.0}, {"var_flat": 0.0})
             for i in range(12)}
    logsD2 = [mouseout(f"t{i}", 5000, x="var_flat") for i in range(6)]
    recD2 = {"bias_logs": logsD2, "dc_map_detailed": mapD2}
    mD2 = dc_adapter.compute_dwell_metrics(mapD2, logsD2)
    fD2, rD2, trD2 = llm_trigger.evaluate_trigger(recD2, mD2)
    print(f"    only a flat var active -> reason={rD2!r}")
    check("nothing scorable left -> no fire, reason 'degenerate_null' (NOT "
          "below_percentile: nothing was actually evaluated)",
          fD2 is False and rD2.startswith("degenerate_null"))
    check("the reason names the excluded variable and why",
          "var_flat:degenerate_null" in rD2)
    check("no clock advances when nothing was meaningfully evaluated",
          recD2.get("dwell_last_checked_by_var") == {})

    # ===================================================================== #
    # TARGETING: the realtime path pins its message to the hierarchy winner via
    # force_variable, exactly as the selection path does -- it no longer lets
    # top_variable re-rank dwell_bias_v and possibly name a different variable.
    # ===================================================================== #
    print("\nforce_variable targeting (the message is about the variable that fired):")

    captured = {}

    async def fake_core(sio, sid_by_pid, pid, client_record, teens,
                        weights, attention, bias_v, phase, trigger_signal, event,
                        axes=None, force_variable=None):
        captured.update(dict(weights=weights, attention=attention, bias_v=bias_v,
                             phase=phase, event=event, axes=axes,
                             force_variable=force_variable))
        return True

    # bias_v deliberately RANKS A DIFFERENT VARIABLE FIRST than the one that fired:
    # if the old top_variable ranking still drove targeting, the message would be
    # about var_b while the trigger fired on var_a.
    dwell_metrics_stub = {"dwell_bias": 0.2,
                          "dwell_bias_v": {"var_a": 0.1, "var_b": 0.9},
                          "n_dwelled": 6}
    logsT2 = [mouseout(f"t{i}", 5000, x="var_a", y="var_b") for i in range(6)]
    recT2 = {"bias_logs": logsT2, "beliefs": {}}

    orig_core = llm_intervention._generate_and_emit
    llm_intervention._generate_and_emit = fake_core
    try:
        returned = asyncio.run(llm_intervention.generate_and_emit(
            "SIO", "SIDMAP", "pid1", recT2, dwell_metrics_stub, {"t0": {}}, "var_a"))
    finally:
        llm_intervention._generate_and_emit = orig_core

    check("the fired variable is threaded through as force_variable (hard override)",
          captured["force_variable"] == "var_a")
    check("even though bias_v's own ranking would have picked var_b -- targeting no "
          "longer depends on that ranking",
          max(dwell_metrics_stub["dwell_bias_v"],
              key=lambda v: dwell_metrics_stub["dwell_bias_v"][v]) == "var_b")
    check("axes is None on this path now (force_variable replaces the soft steer)",
          captured["axes"] is None)
    check("the realtime weights/attention/phase/event are otherwise unchanged",
          captured["weights"] == dc_metric.dwell_by_teen(logsT2)
          and captured["attention"] == {"dwell": dc_metric.dwell_by_teen(logsT2)}
          and captured["phase"] == "realtime"
          and captured["event"] == "llm_intervention")

    # --- the DELIVERED flag now propagates out of the realtime path ------------------
    # It used to be awaited and dropped. server.py's _fire_dwell_intervention reads it to
    # decide whether to release the display pause, so a swallowed False here is a
    # participant muted for the rest of the session, not just a missing log line.
    check("generate_and_emit RETURNS the delivered flag (it used to discard it)",
          returned is True)

    async def fake_core_undelivered(*a, **kw):
        return False

    llm_intervention._generate_and_emit = fake_core_undelivered
    try:
        undelivered = asyncio.run(llm_intervention.generate_and_emit(
            "SIO", "SIDMAP", "pid1", recT2, dwell_metrics_stub, {"t0": {}}, "var_a"))
    finally:
        llm_intervention._generate_and_emit = orig_core
    check("a generation that delivered nothing returns False (what the pause release "
          "keys off)",
          undelivered is False)

    # --- END TO END: server.py's wrapper is what connects those two halves -----------
    # A False return and a release that reopens the gate are each only useful if
    # something actually joins them, and that wiring lives in server.py. This suite
    # otherwise stays off that module (it pulls in aiohttp/socketio/pandas), so the
    # check is guarded rather than made a hard dependency of a pure-logic suite.
    try:
        import server as _server
    except Exception as e:                                  # pragma: no cover
        print(f"    (skipped: server.py not importable here -- {type(e).__name__})")
    else:
        recW1 = {"llm_panel_open_since": 1_000_000}
        llm_intervention._generate_and_emit = fake_core                 # delivers
        try:
            asyncio.run(_server._fire_dwell_intervention(
                "pid1", recW1, dwell_metrics_stub, {"t0": {}}, "var_a"))
        finally:
            llm_intervention._generate_and_emit = orig_core
        check("delivered -> the wrapper LEAVES the pause closed (a panel is up, and "
              "only a dismiss should reopen it)",
              recW1.get("llm_panel_open_since") == 1_000_000)

        recW2 = {"llm_panel_open_since": 1_000_000}
        llm_intervention._generate_and_emit = fake_core_undelivered     # delivers nothing
        try:
            asyncio.run(_server._fire_dwell_intervention(
                "pid1", recW2, dwell_metrics_stub, {"t0": {}}, "var_a"))
        finally:
            llm_intervention._generate_and_emit = orig_core
        check("NOT delivered -> the wrapper releases the pause, so no panel and no "
              "dismiss cannot mute the participant for the session",
              "llm_panel_open_since" not in recW2)

    print("\n" + "=" * 72)
    print(f"{'ALL CHECKS PASSED' if failures == 0 else str(failures) + ' CHECK(S) FAILED'}")
    print("=" * 72)
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if main() else 0)
