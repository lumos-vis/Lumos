"""Synthetic validation harness for CONSTRAINT-AWARE filter activity (Shiyao's item 9).

Standalone -- no sockets, no live server path. Run it as:

    cd server && python3 test_filter_activity.py       # needs the venv (scipy via bias)

"Active filter" used to mean "switched on at some point": filter_changed added
unconditionally and only an explicit removal ever took an attribute back out. It now
means the filter is actually EXCLUDING ROWS -- a change back to the full range or the
full category set DEACTIVATES the attribute again.

That deactivate-on-unconstrain transition is the whole point and it is also brand new:
no pilot data exercises it, because until now no code path could produce it. So the
replays here are deliberately synthetic and walk the full activate -> deactivate ->
reactivate cycle rather than sampling a single state.

Covers:
  * llm_intervention._attribute_domain     -- reading a dataset's full domain out of
    bias.DATA_MAP's precomputed distribution (numeric ends / categorical key set).
  * llm_intervention._filter_is_constraining -- the per-type comparison, including the
    "cannot tell -> assume constraining" fallback that keeps every pre-existing
    minimal-record test meaning what it did before.
  * llm_intervention.get_current_filters   -- the replay, its three app_mode resolution
    paths, and llmTheme parity.
  * llm_trigger.filters_active_as_of       -- that the time-bounded view INHERITS all of
    the above through delegation, with no rules of its own.

Mirrors test_dc_metric.py's pattern (a nonlocal `check`, pure asserts + prints, exits
non-zero on failure). Touches none of the existing suites.
"""
import csv
import os

import bias
import llm_intervention
import llm_trigger

# A DATA_MAP entry in exactly the shape bias.precompute_distributions builds:
# numerical attributes -> a SORTED list of every value in the column; categorical
# attributes -> a {value: count} dict. Registered under a test-only key so the real
# datasets are never mutated. The ranges mirror the real study dataset (asserted
# against the CSV below), so the comparisons here are exercised on real-world values.
FAKE_MODE = "test_filter_activity.csv"

NUMERIC_DOMAINS = {
    "child_age_years": (13.0, 17.0),
    "screen_time_weekday": (0.0, 8.0),
    "hours_sleep_weeknight": (5.0, 11.0),
    "days_physical_activity_week": (0.0, 7.0),
}
CATEGORICAL_DOMAINS = {
    "child_sex": ["Female", "Male"],
    "difficulty_making_friends": [
        "A little difficulty", "A lot of difficulty", "No difficulty"],
}


def install_fake_dataset():
    """Register FAKE_MODE in bias.DATA_MAP with a precomputed-shaped distribution."""
    distribution = {}
    for attr, (lo, hi) in NUMERIC_DOMAINS.items():
        # Only the ends are ever read, but keep it a genuine sorted list of values.
        distribution[attr] = sorted({lo, (lo + hi) / 2.0, hi})
    for attr, cats in CATEGORICAL_DOMAINS.items():
        distribution[attr] = {c: 1 for c in cats}
    bias.DATA_MAP[FAKE_MODE] = {
        "attributes": list(distribution),
        "distribution": distribution,
        "numerical_attributes": list(NUMERIC_DOMAINS),
        "data": {},
    }


def flog(itype, attribute=None, value=None, at=None, app_mode=None,
         filter_type=None):
    """One response_list entry (a wrapped message) for a filter interaction.

    Mirrors the real frontend payload: filter_changed carries the live filterModel
    under data.value (NOT data.filterModel) plus a data.filterType naming what drove
    it; filter_added / filter_removed carry no model at all.
    """
    data = {}
    if attribute is not None:
        data["attribute"] = attribute
    if value is not None:
        data["value"] = value
    if filter_type is not None:
        data["filterType"] = filter_type
    message = {"interactionType": itype, "data": data}
    if at is not None:
        message["interactionAt"] = at
    if app_mode is not None:
        message["appMode"] = app_mode
    return {"input_data": message}


def record(entries, app_mode=FAKE_MODE):
    """A client record carrying its own app_mode, as the live CLIENTS[pid] does."""
    rec = {"response_list": entries}
    if app_mode is not None:
        rec["app_mode"] = app_mode
    return rec


def main():
    failures = 0

    def check(label, cond):
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    install_fake_dataset()

    # ===================================================================== #
    # The fixture must match the REAL study dataset, or every comparison below is
    # exercised on invented numbers. Read the CSV and assert it does.
    # ===================================================================== #
    print("fixture fidelity -- the fake domains match the real study dataset:")
    csv_path = os.path.join("data", "mental_health_data.csv")
    if os.path.exists(csv_path):
        with open(csv_path, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        for attr, (lo, hi) in NUMERIC_DOMAINS.items():
            vals = [float(r[attr]) for r in rows]
            check(f"real {attr} range is [{lo:g}, {hi:g}]",
                  min(vals) == lo and max(vals) == hi)
        for attr, cats in CATEGORICAL_DOMAINS.items():
            check(f"real {attr} domain is {cats}",
                  sorted({r[attr] for r in rows}) == sorted(cats))
    else:
        check(f"{csv_path} present to verify the fixture against", False)

    # ===================================================================== #
    # _attribute_domain: reading the full domain out of the precomputed distribution
    # ===================================================================== #
    print("\n_attribute_domain -- full domain from bias.DATA_MAP:")
    check("numeric attribute -> ('numeric', (min, max)) off the sorted distribution",
          llm_intervention._attribute_domain(FAKE_MODE, "screen_time_weekday")
          == ("numeric", (0.0, 8.0)))
    kind, full = llm_intervention._attribute_domain(FAKE_MODE, "child_sex")
    check("categorical attribute -> ('categorical', frozenset(categories))",
          kind == "categorical" and full == frozenset({"Female", "Male"}))
    check("unknown app_mode -> None (no opinion)",
          llm_intervention._attribute_domain("nope.csv", "child_sex") is None)
    check("unknown attribute in a known dataset -> None",
          llm_intervention._attribute_domain(FAKE_MODE, "not_a_column") is None)
    check("app_mode None -> None (the unit-test / no-dataset case)",
          llm_intervention._attribute_domain(None, "child_sex") is None)

    # ===================================================================== #
    # _filter_is_constraining: the per-type comparison
    # ===================================================================== #
    print("\n_filter_is_constraining -- does the model actually narrow the attribute:")
    constraining = llm_intervention._filter_is_constraining

    # --- numeric: exact-boundary equality is NOT a constraint --------------------
    check("numeric [min, max] exactly -> NOT constraining (the boundary case)",
          constraining(FAKE_MODE, "screen_time_weekday", [0, 8]) is False)
    check("numeric floats equal to the ends -> NOT constraining",
          constraining(FAKE_MODE, "screen_time_weekday", [0.0, 8.0]) is False)
    check("numeric as STRINGS from the slider -> cast, still NOT constraining",
          constraining(FAKE_MODE, "screen_time_weekday", ["0", "8"]) is False)
    check("raising the low end by one -> constraining",
          constraining(FAKE_MODE, "screen_time_weekday", [1, 8]) is True)
    check("lowering the high end by one -> constraining",
          constraining(FAKE_MODE, "screen_time_weekday", [0, 7]) is True)
    check("a reversed pair spanning the full range is normalised, NOT constraining",
          constraining(FAKE_MODE, "screen_time_weekday", [8, 0]) is False)
    check("a non-numeric end (unparseable) -> falls back to constraining",
          constraining(FAKE_MODE, "screen_time_weekday", ["", "8"]) is True)
    check("a one-element numeric model -> falls back to constraining",
          constraining(FAKE_MODE, "screen_time_weekday", [3]) is True)
    check("a different attribute's own ends are used, not a shared range "
          "(sleep is [5, 11], so [0, 8] IS a constraint there)",
          constraining(FAKE_MODE, "hours_sleep_weeknight", [5, 11]) is False
          and constraining(FAKE_MODE, "hours_sleep_weeknight", [0, 8]) is True)

    # --- categorical: SET equality, order-independent -----------------------------
    all_friends = ["A little difficulty", "A lot of difficulty", "No difficulty"]
    check("categorical full domain -> NOT constraining",
          constraining(FAKE_MODE, "difficulty_making_friends", all_friends) is False)
    check("categorical full domain REORDERED -> still NOT constraining "
          "(compared as sets, so ordering never matters)",
          constraining(FAKE_MODE, "difficulty_making_friends",
                       list(reversed(all_friends))) is False)
    check("dropping one category -> constraining",
          constraining(FAKE_MODE, "difficulty_making_friends",
                       all_friends[:2]) is True)
    check("empty selection (everything deselected) -> constraining",
          constraining(FAKE_MODE, "difficulty_making_friends", []) is True)
    check("two-category attribute: both selected NOT constraining, one IS",
          constraining(FAKE_MODE, "child_sex", ["Male", "Female"]) is False
          and constraining(FAKE_MODE, "child_sex", ["Female"]) is True)

    # --- the "cannot tell" fallback preserves the OLD behaviour --------------------
    check("no value on the event -> constraining (cannot prove otherwise)",
          constraining(FAKE_MODE, "screen_time_weekday", None) is True)
    check("unknown dataset -> constraining (the pre-item-9 default)",
          constraining(None, "screen_time_weekday", [0, 8]) is True)
    check("value of an unexpected shape (scalar, not a list) -> constraining",
          constraining(FAKE_MODE, "screen_time_weekday", 4) is True)

    # ===================================================================== #
    # get_current_filters: the replay, and the transition that was impossible before
    # ===================================================================== #
    print("\nget_current_filters -- activate / deactivate / reactivate:")
    FULL_SCREEN = [0, 8]
    NARROW_SCREEN = [5, 8]
    NARROWER_SCREEN = [6, 8]

    check("a constraining filter_changed ACTIVATES the attribute",
          llm_intervention.get_current_filters(
              record([flog("filter_changed", "screen_time_weekday", NARROW_SCREEN)]))
          == {"screen_time_weekday"})

    check("a filter_changed back to the FULL range DEACTIVATES it "
          "(previously impossible -- nothing but an explicit removal ever did)",
          llm_intervention.get_current_filters(
              record([flog("filter_changed", "screen_time_weekday", NARROW_SCREEN),
                      flog("filter_changed", "screen_time_weekday", FULL_SCREEN)]))
          == set())

    check("full activate -> deactivate -> REACTIVATE cycle ends active",
          llm_intervention.get_current_filters(
              record([flog("filter_changed", "screen_time_weekday", NARROW_SCREEN),
                      flog("filter_changed", "screen_time_weekday", FULL_SCREEN),
                      flog("filter_changed", "screen_time_weekday", NARROWER_SCREEN)]))
          == {"screen_time_weekday"})

    check("categorical activate -> deactivate cycle, order-independent on the way back",
          llm_intervention.get_current_filters(
              record([flog("filter_changed", "child_sex", ["Female"]),
                      flog("filter_changed", "child_sex", ["Male", "Female"])]))
          == set())

    check("one attribute deactivating leaves ANOTHER's constraint alone",
          llm_intervention.get_current_filters(
              record([flog("filter_changed", "screen_time_weekday", NARROW_SCREEN),
                      flog("filter_changed", "child_sex", ["Female"]),
                      flog("filter_changed", "screen_time_weekday", FULL_SCREEN)]))
          == {"child_sex"})

    # --- llmTheme parity ----------------------------------------------------------
    print("\nllmTheme-sourced changes behave identically to participant-driven ones:")
    theme_active = llm_intervention.get_current_filters(
        record([flog("filter_changed", "screen_time_weekday", NARROW_SCREEN,
                     filter_type="llmTheme")]))
    hand_active = llm_intervention.get_current_filters(
        record([flog("filter_changed", "screen_time_weekday", NARROW_SCREEN,
                     filter_type="sliderChange")]))
    check("an applied theme's constraining change activates, exactly like a hand-set one",
          theme_active == hand_active == {"screen_time_weekday"})
    theme_full = llm_intervention.get_current_filters(
        record([flog("filter_changed", "screen_time_weekday", NARROW_SCREEN,
                     filter_type="llmTheme"),
                flog("filter_changed", "screen_time_weekday", FULL_SCREEN,
                     filter_type="llmTheme")]))
    check("and an llmTheme change back to full range deactivates, same as by hand",
          theme_full == set())

    # --- the other three verbs keep their existing handling ------------------------
    print("\nfilter_added / filter_removed / all_filters_removed unchanged:")
    check("filter_added still activates unconditionally (it carries no model to judge)",
          llm_intervention.get_current_filters(
              record([flog("filter_added", "screen_time_weekday")]))
          == {"screen_time_weekday"})
    check("filter_removed still discards",
          llm_intervention.get_current_filters(
              record([flog("filter_changed", "screen_time_weekday", NARROW_SCREEN),
                      flog("filter_removed", "screen_time_weekday")]))
          == set())
    check("all_filters_removed still clears everything",
          llm_intervention.get_current_filters(
              record([flog("filter_changed", "screen_time_weekday", NARROW_SCREEN),
                      flog("filter_changed", "child_sex", ["Female"]),
                      flog("all_filters_removed")]))
          == set())
    check("an unconstraining change AFTER filter_added deactivates it -- the value "
          "rule wins over the earlier toggle",
          llm_intervention.get_current_filters(
              record([flog("filter_added", "screen_time_weekday"),
                      flog("filter_changed", "screen_time_weekday", FULL_SCREEN)]))
          == set())

    # --- app_mode resolution: explicit > record > per-message ---------------------
    print("\napp_mode resolution (explicit arg > record's app_mode > message appMode):")
    entries = [flog("filter_changed", "screen_time_weekday", FULL_SCREEN)]
    check("explicit app_mode argument resolves the domain",
          llm_intervention.get_current_filters(
              {"response_list": entries}, app_mode=FAKE_MODE) == set())
    check("the record's own app_mode resolves it",
          llm_intervention.get_current_filters(record(entries)) == set())
    check("failing both, the message's own appMode resolves it",
          llm_intervention.get_current_filters(
              {"response_list": [flog("filter_changed", "screen_time_weekday",
                                      FULL_SCREEN, app_mode=FAKE_MODE)]}) == set())
    check("no mode anywhere -> unresolvable -> old behaviour (stays active)",
          llm_intervention.get_current_filters({"response_list": entries})
          == {"screen_time_weekday"})

    # ===================================================================== #
    # filters_active_as_of INHERITS all of it -- it owns no rules of its own
    # ===================================================================== #
    print("\nfilters_active_as_of -- inherits item 9 through delegation:")
    timeline = [flog("filter_changed", "screen_time_weekday", NARROW_SCREEN, at=100),
                flog("filter_changed", "screen_time_weekday", FULL_SCREEN, at=300)]
    check("as of 200ms (after the narrowing, before the widening) -> ACTIVE",
          llm_trigger.filters_active_as_of(timeline, 200, FAKE_MODE)
          == {"screen_time_weekday"})
    check("as of 400ms (after the widening back to full) -> INACTIVE",
          llm_trigger.filters_active_as_of(timeline, 400, FAKE_MODE) == set())
    check("unbounded (None) == replaying to the end -> INACTIVE",
          llm_trigger.filters_active_as_of(timeline, None, FAKE_MODE) == set())
    check("without an app_mode the as-of view falls back to the old behaviour too "
          "(the synthetic record it builds carries no mode of its own)",
          llm_trigger.filters_active_as_of(timeline, 400) == {"screen_time_weekday"})

    # The as-of view must agree with a direct replay of the same prefix -- that
    # equivalence is the reason it delegates instead of duplicating the rules.
    for bound in (50, 100, 200, 300, 400):
        prefix = [e for e in timeline
                  if e["input_data"]["interactionAt"] <= bound]
        check(f"as-of {bound}ms == get_current_filters over the same prefix",
              llm_trigger.filters_active_as_of(timeline, bound, FAKE_MODE)
              == llm_intervention.get_current_filters(
                  record(prefix)))

    print("\n" + "=" * 72)
    print(f"{'ALL CHECKS PASSED' if failures == 0 else str(failures) + ' CHECK(S) FAILED'}")
    print("=" * 72)
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if main() else 0)
