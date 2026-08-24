"""Synthetic validation harness for PER-VARIABLE eligible selection sets (items 7-8).

Standalone -- no sockets, no live server path. Run it as:

    cd server && python3 test_scoped_selection.py      # needs numpy (the venv)

A selection is evidence about the variable the participant was LOOKING AT WHEN THEY
MADE IT. Scoring every variable against one global selection attributes every pick to
every variable at once, so a variable inherits picks made while it was nowhere in view.

Covers:
  * llm_trigger.eligible_selection_by_var -- the replay: attribution FIXED at add time
    from the event's own axes + the filters constraining as of that event, deselection
    removing UNCONDITIONALLY (not re-derived from what is active now), re-selection
    earning a fresh tag, and click_group's toggle semantics.
  * dc_adapter.selection_percentile_by_var's selected_by_var -- per-variable scoring,
    and that omitting it leaves the pooled/shared behaviour byte-identical.
  * the wiring through evaluate_selection_progressive_trigger, whose readiness and
    checkpoint schedule stay on the GLOBAL pick count.

Mirrors test_dc_metric.py's pattern (a nonlocal `check`, pure asserts + prints, exits
non-zero on failure). Touches none of the existing suites.
"""
import numpy as np

import dc_adapter
import dc_metric
import llm_trigger


def make_entry(consistency, weights):
    """One dc_map_detailed entry, with its pooled DC computed the same way
    _consistency_and_weights_for_teen does (Sum_v w_v*C_v / Sum_v w_v)."""
    num = sum(weights[v] * consistency[v] for v in consistency)
    den = sum(weights[v] for v in consistency)
    dc = 0.0 if den == 0.0 else num / den
    return {"dc": dc, "consistency": dict(consistency), "weights": dict(weights)}


def click(itype, ids, x=None, y=None, at=None):
    """A selection message as the frontend emits it.

    All three selection clicks carry the live axis attribute names (data.x.name /
    data.y.name) and an interactionAt, exactly as hovers do -- they come from the same
    initializeNewMessage. click_group carries a LIST id; the other two a scalar.
    """
    data = {"id": ids}
    if x is not None:
        data["x"] = {"name": x}
    if y is not None:
        data["y"] = {"name": y}
    entry = {"interactionType": itype, "data": data}
    if at is not None:
        entry["interactionAt"] = at
    return entry


def filter_log(itype, attribute, value=None, at=None):
    """A response_list entry for a filter interaction."""
    data = {"attribute": attribute}
    if value is not None:
        data["value"] = value
    message = {"interactionType": itype, "data": data}
    if at is not None:
        message["interactionAt"] = at
    return {"input_data": message}


def main():
    failures = 0

    def check(label, cond):
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    # ===================================================================== #
    # THE CORE CASE (Shiyao's item 7): pick under var_a, switch axes, pick under
    # var_b. Neither variable may inherit the other's point.
    # ===================================================================== #
    print("eligible_selection_by_var -- attribution is fixed at selection time:")

    logs = [click("click_add_item", "p1", x="var_a", at=100),
            click("click_add_item", "p2", x="var_b", at=200)]
    by_var = llm_trigger.eligible_selection_by_var(logs, [])
    print(f"    {by_var}")
    check("p1 was picked under var_a -> only var_a's set holds it",
          by_var["var_a"] == {"p1"})
    check("p2 was picked under var_b -> only var_b's set holds it",
          by_var["var_b"] == {"p2"})
    check("neither variable inherits the other's pick (the whole point of item 7)",
          "p2" not in by_var["var_a"] and "p1" not in by_var["var_b"])
    check("a variable never active during any selection is absent entirely",
          "var_c" not in by_var)

    # Both axes at once -> the pick counts for both, as it genuinely was made under both.
    both = llm_trigger.eligible_selection_by_var(
        [click("click_add_item", "p1", x="var_a", y="var_b", at=100)], [])
    check("a pick made with two axes assigned counts for BOTH of them",
          both["var_a"] == {"p1"} and both["var_b"] == {"p1"})

    # A CONSTRAINING filter as of the pick also attributes it (item 9 feeding item 7).
    filtered = llm_trigger.eligible_selection_by_var(
        [click("click_add_item", "p1", x="var_a", at=200)],
        [filter_log("filter_added", "var_f", at=100)])
    check("a filter active as of the pick attributes it too (axes UNION filters)",
          filtered["var_a"] == {"p1"} and filtered["var_f"] == {"p1"})
    later_filter = llm_trigger.eligible_selection_by_var(
        [click("click_add_item", "p1", x="var_a", at=100)],
        [filter_log("filter_added", "var_f", at=500)])
    check("a filter turned on AFTER the pick does not retroactively claim it",
          later_filter["var_a"] == {"p1"} and "var_f" not in later_filter)

    # ===================================================================== #
    # ITEM 8: deselection removes UNCONDITIONALLY, from wherever the point sits
    # ===================================================================== #
    print("\ndeselection removes from every set, regardless of what is active now:")

    logs = [click("click_add_item", "p1", x="var_a", at=100),
            click("click_add_item", "p2", x="var_b", at=200),
            # axes have long since moved on; the removal still finds p1 under var_a
            click("click_remove_item", "p1", x="var_b", at=300)]
    by_var = llm_trigger.eligible_selection_by_var(logs, [])
    print(f"    {by_var}")
    check("deselecting p1 removes it from var_a even though var_a is NOT active now "
          "-- removal is unconditional, not re-derived from current state",
          by_var["var_a"] == set())
    check("the untouched var_b keeps its own point",
          by_var["var_b"] == {"p2"})

    # Re-selection is a fresh add with a FRESH as-of tag.
    logs = [click("click_add_item", "p1", x="var_a", at=100),
            click("click_remove_item", "p1", x="var_a", at=200),
            click("click_add_item", "p1", x="var_c", at=300)]
    by_var = llm_trigger.eligible_selection_by_var(logs, [])
    print(f"    {by_var}")
    check("re-selecting p1 after the axes moved attributes it to var_c",
          by_var["var_c"] == {"p1"})
    check("and NOT back to var_a, where it was originally picked",
          by_var.get("var_a", set()) == set())

    # ===================================================================== #
    # click_group: every id in the group gets the same attribution, and the
    # message is a TOGGLE (deselects only when all its ids are already selected).
    # ===================================================================== #
    print("\nclick_group -- list ids, one attribution, toggle semantics:")

    grp = llm_trigger.eligible_selection_by_var(
        [click("click_group", ["g1", "g2", "g3"], x="var_a", at=100)], [])
    check("every id in the group is attributed to the same variable set",
          grp["var_a"] == {"g1", "g2", "g3"})

    grp = llm_trigger.eligible_selection_by_var(
        [click("click_group", ["g1", "g2"], x="var_a", at=100),
         click("click_group", ["g1", "g2"], x="var_a", at=200)], [])
    check("re-clicking a fully-selected group DESELECTS it (toggle), emptying the set",
          grp["var_a"] == set())

    grp = llm_trigger.eligible_selection_by_var(
        [click("click_add_item", "g1", x="var_a", at=100),
         click("click_group", ["g1", "g2"], x="var_a", at=200)], [])
    check("a group only PARTLY selected is selecting, not deselecting",
          grp["var_a"] == {"g1", "g2"})

    check("placeholder / None ids are dropped, matching dc_adapter.selected_ids",
          llm_trigger.eligible_selection_by_var(
              [click("click_add_item", "-", x="var_a", at=100),
               click("click_group", ["g1", None, "-"], x="var_a", at=200)],
              [])["var_a"] == {"g1"})

    # The global selection this replay tracks internally must agree with the
    # independently-maintained dc_adapter.selected_ids, or the two views of the same
    # session would disagree about what is selected at all.
    mixed = [click("click_add_item", "p1", x="var_a", at=100),
             click("click_group", ["p2", "p3"], x="var_a", at=200),
             click("click_remove_item", "p2", x="var_a", at=300),
             click("click_group", ["p3"], x="var_a", at=400)]
    union = set().union(*llm_trigger.eligible_selection_by_var(mixed, []).values())
    check("the union of all per-variable sets == dc_adapter.selected_ids for the same "
          "log (one variable active throughout, so they must coincide exactly)",
          union == set(dc_adapter.selected_ids(mixed)))

    # ===================================================================== #
    # selection_percentile_by_var: per-variable ids, and untouched pooled default
    # ===================================================================== #
    print("\nselection_percentile_by_var -- selected_by_var scoring:")

    # 12 teens: hot t0..t5 are the population MAX on var_a and the MIN on var_b.
    detailed = {}
    for i in range(6):
        detailed[f"t{i}"] = make_entry({"var_a": 1.0, "var_b": -1.0},
                                       {"var_a": 1.0, "var_b": 1.0})
    for i in range(6, 12):
        detailed[f"t{i}"] = make_entry({"var_a": -1.0, "var_b": 1.0},
                                       {"var_a": 1.0, "var_b": 1.0})
    hot = [f"t{i}" for i in range(6)]

    per_var = dc_adapter.selection_percentile_by_var(
        detailed, [], n_trials=500, rng=np.random.default_rng(0),
        selected_by_var={"var_a": set(hot), "var_b": set()})
    print(f"    per-variable: {per_var}")
    check("var_a scored on its OWN eligible picks (the MAX teens) -> extreme",
          per_var["var_a"] is not None and per_var["var_a"] >= 0.80)
    check("var_b, with an EMPTY eligible set, scores None (k == 0) -- never a candidate",
          per_var["var_b"] is None)

    # Backward compatibility: omitting selected_by_var must be byte-identical to before.
    shared_a = dc_adapter.selection_percentile_by_var(
        detailed, hot, n_trials=500, rng=np.random.default_rng(0))
    shared_b = dc_adapter.selection_percentile_by_var(
        detailed, hot, n_trials=500, rng=np.random.default_rng(0),
        selected_by_var=None)
    check("omitting selected_by_var scores EVERY variable on the shared selection "
          "(the pooled submit-time path, unchanged)",
          shared_a == shared_b and set(shared_a) == {"var_a", "var_b"}
          and all(p is not None for p in shared_a.values()))
    check("and that shared view genuinely differs from the per-variable one "
          "(so the new parameter is doing real work)",
          shared_a["var_b"] != per_var["var_b"])

    # --- low-k: 1 and 2 eligible points must COMPUTE, not error or vanish ----------
    print("\nlow-k eligible sets (recon measured these as noisy, not degenerate):")
    for k in (1, 2):
        low = dc_adapter.selection_percentile_by_var(
            detailed, [], n_trials=500, rng=np.random.default_rng(0),
            selected_by_var={"var_a": set(hot[:k])})
        print(f"    k={k} -> var_a={low['var_a']}")
        check(f"a {k}-point eligible set computes a real percentile in [0, 1] "
              f"(noisy is fine; it is not an error and not degenerate)",
              low["var_a"] is not None and 0.0 <= low["var_a"] <= 1.0)

    # ===================================================================== #
    # Wiring: the trigger computes the sets itself; readiness stays GLOBAL
    # ===================================================================== #
    print("\nevaluate_selection_progressive_trigger -- wiring and global readiness:")

    # 5 picks (readiness clears on the GLOBAL count) but they were made under var_a
    # only, while var_b is the variable on screen now.
    logs = [click("click_add_item", f"t{i}", x="var_a", at=100 + i) for i in range(6)]
    logs.append(click("click_add_item", "t6", x="var_b", at=200))
    # The CURRENT axes come from the last hover (get_current_axes), not from the last
    # click, so the record needs one to have var_b on screen now.
    logs.append({"interactionType": "mouseout_item", "interactionDuration": 10,
                 "interactionAt": 300, "data": {"id": "t6", "x": {"name": "var_b"}}})
    rec = {"dc_map_detailed": detailed, "beliefs": {}, "bias_logs": logs,
           "response_list": []}
    selected = dc_adapter.selected_ids(logs)
    result = llm_trigger.evaluate_selection_progressive_trigger(rec, selected)
    print(f"    n_selected={result['n_selected']} pbv={result['percentile_by_var']}")
    check("readiness is keyed on the GLOBAL unique-pick count (7 picks >= 5), not on "
          "any one variable's eligible set",
          result["ready"] is True and result["n_selected"] == 7)
    check("only the variable active NOW is scored (scoping is unchanged)",
          set(result["percentile_by_var"]) == {"var_b"})

    # The sharp case. var_b's ONE eligible pick is t6, a cold teen -- var_b's population
    # MAXIMUM -- so on its own evidence var_b is extreme. The other six picks were made
    # under var_a and are var_b's MINIMUM, so pooling them in buries it at the floor.
    # Same session, same map, opposite verdicts: this is what items 7-8 change.
    old_way = dc_adapter.selection_percentile_by_var(
        detailed, selected, variables={"var_b"}, rng=dc_adapter.live_rng())
    print(f"    var_b on its own eligible pick={result['percentile_by_var']['var_b']} "
          f"vs on the whole running selection={old_way['var_b']}")
    check("var_b, scored on ITS OWN single eligible pick, clears the threshold",
          result["percentile_by_var"]["var_b"] is not None
          and result["percentile_by_var"]["var_b"]
          >= llm_trigger.SELECTION_PERCENTILE_THRESHOLD)
    check("scored on the WHOLE running selection it would sit at the floor instead "
          "-- the six picks made under var_a are var_b's population minimum",
          old_way["var_b"] < 0.10)
    check("so the trigger is demonstrably using the per-variable eligible sets, "
          "not the shared selection",
          result["percentile_by_var"]["var_b"] != old_way["var_b"]
          and result["fired"] is True and result["target_var"] == "var_b")

    # ===================================================================== #
    # DEGENERATE-NULL GUARD (ported from the dwell trigger). A variable whose
    # re-pooled DC is CONSTANT has a null that collapses onto its real value, so it
    # scores a guaranteed 1.0 and would clear any threshold on every check.
    # ===================================================================== #
    print("\ndegenerate-null guard -- a flat variable must not be a free fire:")

    # Built from REAL elicited beliefs rather than hand-written consistencies, so this
    # exercises the way the degeneracy actually arises in the study: the participant
    # drew the diagnosed and non-diagnosed distributions IDENTICALLY for flat_var, so
    # dc_metric.vba returns log(1)=0 in every bin (C_v == 0 for every teen) and its js
    # weight is 0 -- which scoped_detailed_map's 0/0 guard turns into dc 0.0 for all.
    beliefs = {
        "flat_var": {
            "attribute": "flat_var",
            "binEdges": [0, 5, 10],
            "countsByGroup": {"diagnosed": {"counts": [10, 10], "confidence": 80},
                              "nonDiagnosed": {"counts": [10, 10], "confidence": 80}}},
        "real_var": {
            "attribute": "real_var",
            "binEdges": [0, 5, 10],
            "countsByGroup": {"diagnosed": {"counts": [20, 0], "confidence": 60},
                              "nonDiagnosed": {"counts": [0, 20], "confidence": 60}}},
    }
    # Both diagnosis labels appear in BOTH bins, so real_var's per-teen consistency
    # genuinely varies (belief-consistent teens vs belief-contradicting ones). Without
    # that mix -- label perfectly tracking bin -- even a strongly-held belief yields a
    # constant C_v and real_var would be degenerate too, for a quite different reason.
    teens = {}
    for i in range(12):
        low_bin = i < 6
        consistent = (i % 6) < 3
        teens[f"t{i}"] = {
            "flat_var": 2 if low_bin else 7,
            "real_var": 2 if low_bin else 7,
            dc_metric.LABEL_ATTR: ("Yes" if low_bin else "No") if consistent
                                  else ("No" if low_bin else "Yes"),
        }
    deg_map = dc_metric.dc_map_detailed(teens, beliefs)
    # The belief-CONSISTENT teens: real_var's population maximum.
    picks = ["t0", "t1", "t2", "t9", "t10"]

    scoped_flat = dc_adapter.scoped_detailed_map(deg_map, ["flat_var"])
    flat_pct = dc_metric.selection_bias_percentile(
        {tid: e["dc"] for tid, e in scoped_flat.items()}, picks,
        n_trials=500, rng=np.random.default_rng(0))
    print(f"    unguarded, the flat variable scores pct={flat_pct} (every draw ties)")
    check("identical group distributions really do produce a flat scope "
          "(w_v == 0 -> dc 0.0 for every teen)",
          {round(e["dc"], 12) for e in scoped_flat.values()} == {0.0})
    check("WITHOUT the guard it would score a guaranteed 1.0 -- clearing any threshold "
          "on every check, forever",
          flat_pct == 1.0)
    check("is_degenerate_scope recognises that scope",
          dc_adapter.is_degenerate_scope(scoped_flat) is True)
    check("and does NOT flag the real variable's scope",
          dc_adapter.is_degenerate_scope(
              dc_adapter.scoped_detailed_map(deg_map, ["real_var"])) is False)

    scored = dc_adapter.selection_percentile_by_var(
        deg_map, picks, n_trials=500, rng=np.random.default_rng(0))
    print(f"    through the scorer -> {scored}")
    check("the scorer OMITS the flat variable entirely (never scored, so never a "
          "candidate) while still scoring the real one",
          set(scored) == {"real_var"})
    check("degenerate_vars names it, so a log can tell this from a below-threshold miss",
          dc_adapter.degenerate_vars(deg_map) == {"flat_var": "degenerate_null"})
    check("degenerate_vars honours the same `variables` narrowing as the scorer",
          dc_adapter.degenerate_vars(deg_map, variables=["real_var"]) == {}
          and dc_adapter.degenerate_vars(
              deg_map, variables=["flat_var"]) == {"flat_var": "degenerate_null"})

    # --- and through the trigger: the flat variable must not win ------------------
    deg_logs = [click("click_add_item", tid, x="flat_var", y="real_var",
                      at=100 + n) for n, tid in enumerate(picks)]
    deg_logs.append({"interactionType": "mouseout_item", "interactionDuration": 10,
                     "interactionAt": 200,
                     "data": {"id": "t0", "x": {"name": "flat_var"},
                              "y": {"name": "real_var"}}})
    deg_rec = {"dc_map_detailed": deg_map, "beliefs": {}, "bias_logs": deg_logs,
               "response_list": []}
    deg_result = llm_trigger.evaluate_selection_progressive_trigger(
        deg_rec, dc_adapter.selected_ids(deg_logs))
    print(f"    through the trigger -> fired={deg_result['fired']} "
          f"target={deg_result['target_var']} pbv={deg_result['percentile_by_var']} "
          f"excluded={deg_result['excluded_vars']}")
    check("both variables are active, but the flat one is not scored at all",
          set(deg_result["percentile_by_var"]) == {"real_var"})
    check("the trigger reports it as excluded, with the degenerate_null marker "
          "(the same convention the dwell trace carries)",
          deg_result["excluded_vars"] == {"flat_var": "degenerate_null"})
    check("so the flat variable can never be the target -- despite being axis-tier "
          "with the HIGHER elicited confidence, which would otherwise win the tiebreak",
          deg_result["target_var"] != "flat_var")
    check("and the REAL variable, scored on its own eligible picks (all belief-"
          "consistent), still fires normally alongside the exclusion",
          deg_result["fired"] is True and deg_result["target_var"] == "real_var")

    print("\n" + "=" * 72)
    print(f"{'ALL CHECKS PASSED' if failures == 0 else str(failures) + ' CHECK(S) FAILED'}")
    print("=" * 72)
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if main() else 0)
