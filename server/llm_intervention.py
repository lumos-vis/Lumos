"""LLM intervention for the confirmation-bias study's "LLM" condition.

Ported from the reviewed proof of concept (bias_signal_assembler.py +
confirmation_bias_llm_generator.py). The SYSTEM_PROMPT, OUTPUT_SCHEMA, tone
constraints, theme construction and input shape are the experimental stimulus
and are reproduced verbatim -- do not reword them.

Two halves:
  1. Assembly -- turn the pre-computed signals (dwell_bias_v, diagnosis focus,
     and the bin x group candidate cells from dc_metric.bin_group_candidates)
     into the structured "LLM input" the prompt expects. Both the summary and the
     recommendation read the same grid, so they name ranges from the participant's
     OWN belief bins and always concern the same variable.
  2. Generation -- call Claude (claude-sonnet-5, low effort) with a
     schema-enforced JSON response.

Follows firebase_logger's "no-op when unconfigured" pattern: if
ANTHROPIC_API_KEY is absent the client init prints one message and disables the
feature. A missing key (or any failure) never raises and never affects the
CONTROL / AWARENESS / ADMIN conditions.
"""
import asyncio
import json
import os

from dotenv import load_dotenv

import bias_util
import dc_metric
import firebase_logger

# Load ANTHROPIC_API_KEY (and anything else) from server/.env if present. Safe
# no-op when the file is absent; never overrides an already-set environment.
load_dotenv()


# --------------------------------------------------------------------------- #
# 1. Assembly -- variables, ranges, phrasing (from the real dataset)
# --------------------------------------------------------------------------- #
TASK_CONTEXT = (
    "The participant is exploring a teen mental health dataset and choosing "
    "cases for a data journalism story."
)

READABLE_NAMES = {
    "screen_time_weekday": "screen time",
    "hours_sleep_weeknight": "sleep hours",
    "days_physical_activity_week": "physical activity",
    "difficulty_making_friends": "in-person social time",
    "child_age_years": "age",
    "child_sex": "gender",
}
# All SIX elicited belief variables (dc_adapter.EXPECTED_VARIABLE_COUNT). Every one
# of them has a filter row in the interface -- the mental health dataset opens every
# attribute except the id -- so any of them can carry an intervention. A variable
# must have a READABLE_NAMES entry to belong here, or the message would name the raw
# column.
SUPPORTED_VARIABLES = {
    "screen_time_weekday",
    "hours_sleep_weeknight",
    "days_physical_activity_week",
    "difficulty_making_friends",           # categorical
    "child_age_years",
    "child_sex",                           # categorical, only two categories
}
# Raised the moment the selection intervention fires and lowered when its message
# (or the news that none is coming) arrives. Generation takes ~10s, long enough for
# the participant to pick two or three more teens before seeing anything, so the
# frontend holds selection closed for exactly that window.
SELECTION_PENDING_EVENT = "llm_selection_pending"

# A belief bin can be narrow enough that one bin plus one diagnosis group leaves
# almost nothing on screen. Below this, the recommended range widens.
MIN_RECOMMENDED_TEENS = 5

DEFAULT_TONE_CONSTRAINTS = [
    "Do not mention confirmation bias",
    "Do not mention prior beliefs",
    "Do not say the participant is wrong",
    "Use concrete variable names",
    "Use an inviting and non-judgmental tone",
    "The summary should be grounded in the interaction patterns",
    "Never use an em dash; use a period or comma instead",
]


def _diagnosis_phrase(value):
    """'Yes' -> 'a depression/anxiety diagnosis', 'No' -> 'no depression/anxiety diagnosis'."""
    return "a depression/anxiety diagnosis" if value == "Yes" else "no depression/anxiety diagnosis"


def _group_for(diagnosis_focus):
    """'Yes'/'No' -> the cell grid's group key."""
    return "diagnosed" if diagnosis_focus == "Yes" else "not_diagnosed"


def _plain(value):
    """5.0 -> 5 and 4.8999999999999995 -> 4.9. Bin edges are built by repeated
    addition, so they carry float noise. Prose only; the filter keeps full precision."""
    value = round(float(value), 2)
    return int(value) if value.is_integer() else value


def select_candidate_cell(cells):
    """The one (bin x diagnosis group) cell worth recommending, or None.

    Among the cells the participant under-attended, prefer the ones that also
    contradict their belief (vc < 0) and take the strongest combination of both;
    with none contradicting, fall back to the lowest vc available.

    Two different "positive"s meet here and must not be conflated. The VARIABLE was
    chosen because its bias is positive -- the participant over-attended cases
    matching their belief. The CELL is chosen because its underexploration is
    positive -- they under-attended that bin. Opposite directions, different layers.
    """
    underexplored = [c for c in cells if c["underexploration"] > 0]
    if not underexplored:
        return None
    contradicting = [c for c in underexplored if c["vc"] < 0]
    if contradicting:
        return max(contradicting, key=lambda c: c["underexploration"] * abs(c["vc"]))
    return min(underexplored, key=lambda c: c["vc"])


def _variable_for_filter_ranges(candidates, filter_ranges):
    """Variable whose candidate theme has exactly these filter_ranges, or None.

    Matches by VALUE, not position: the model is told to copy filter_ranges through
    verbatim, so they are a reliable key back to the candidate that produced them.
    Value matching means a reordered (or filter_ranges-altered) model response can
    never mis-apply a range to a participant's click; a theme that matches nothing
    returns None and the caller drops its filter action.
    """
    for candidate in candidates:
        if candidate.get("filter_ranges") == filter_ranges:
            predicate = candidate.get("predicate", {})
            return next((k for k in predicate if k != "diagnosis"), None)
    return None


def _diagnosis_filter_for_filter_ranges(candidates, filter_ranges):
    """The diagnosis half of the matching candidate, as a ready-to-apply
    {variable, filter_ranges} pair, or None.

    Every candidate theme is a CONJUNCTION -- a range on the top variable AND a
    diagnosis status -- but the reviewed OUTPUT_SCHEMA carries only filter_ranges.
    Emitting the range alone leaves the participant looking at both diagnosis
    groups, including the one they were already over-attending to, which is the
    opposite of the contrast the theme names.
    """
    for candidate in candidates:
        if candidate.get("filter_ranges") == filter_ranges:
            status = candidate.get("predicate", {}).get("diagnosis")
            if status is None:
                return None
            # predicate stores the label column's own value lowercased.
            return {"variable": dc_metric.LABEL_ATTR, "filter_ranges": [status.capitalize()]}
    return None


def get_current_axes(client_record):
    """The attributes currently on the x/y axes -> {"x": name|None, "y": name|None}.

    Scans bias_logs in reverse for the most recent point/group hover, whose message
    embeds the live axis attribute names as data.x.name / data.y.name (the same
    fields bias.py reads). Dedicated CHANGE_AXIS_ATTRIBUTE events are NOT stored in
    bias_logs, so the last hover is the reliable source; on the realtime path a
    dwell trigger always implies recent hovers, so this stays current. Returns
    {"x": None, "y": None} when no qualifying hover has been logged yet.
    """
    for entry in reversed(client_record.get("bias_logs", [])):
        if entry.get("interactionType") in ("mouseout_item", "mouseout_group"):
            data = entry.get("data", {})
            x = data.get("x") if isinstance(data.get("x"), dict) else {}
            y = data.get("y") if isinstance(data.get("y"), dict) else {}
            return {"x": x.get("name"), "y": y.get("name")}
    return {"x": None, "y": None}


def _attribute_domain(app_mode, attribute):
    """The attribute's FULL (unfiltered) domain in this dataset, or None if unknown.

    -> ("numeric", (min, max)) | ("categorical", frozenset(values)) | None

    Read from bias.DATA_MAP's precomputed distribution -- the same structure the
    server already builds at startup (bias.precompute_distributions), so this needs no
    new logging and no new source of truth: for a numerical attribute the distribution
    is a SORTED list of every value in the column, whose ends are exactly the min/max
    the frontend slider was configured with; for a categorical one it is a
    {value: count} dict whose keys are exactly the frontend's `types` domain. Both
    sides derive theirs from the same CSV, so they agree by construction.

    None means "cannot tell" -- an unknown app_mode, a dataset whose distributions were
    never precomputed (every unit test that builds a minimal record), or an attribute
    that is not in this dataset. Callers must treat None as "no opinion" rather than as
    "unconstrained"; see _filter_is_constraining.

    bias is imported at CALL time, not module scope: it pulls in scipy and reads CSVs,
    and llm_intervention is imported by tests that never touch a real dataset. Same
    call-time-import convention dc_metric.dwell_percentile_ready uses.
    """
    try:
        import bias
        dataset = bias.DATA_MAP.get(app_mode)
        if not dataset:
            return None
        distribution = dataset.get("distribution", {}).get(attribute)
        if not distribution:
            return None
        if attribute in dataset.get("numerical_attributes", []):
            return "numeric", (distribution[0], distribution[-1])
        return "categorical", frozenset(str(v) for v in distribution)
    except Exception:
        return None


def _filter_is_constraining(app_mode, attribute, value):
    """Does this filterModel actually NARROW the attribute, or is it just switched on?

    Shiyao's item 9: a filter the participant has merely touched is not evidence they
    are reasoning about that variable -- only one that actually excludes rows is. The
    comparison is against the attribute's full domain (_attribute_domain):

      numeric (Q/T):     value is the slider's [lo, hi]. Constraining iff that pair
                         differs from [min, max]. Ends are compared after
                         bias_util.cast_to_num because the slider can emit strings, and
                         are normalised with min()/max() so a reversed pair is not
                         mistaken for a constraint.
      categorical (N/O): value is a plain list of the selected category strings (the
                         multiselect binds straight to `types`, with no {id, text}
                         wrapping). Constraining iff that SET differs from the full
                         category set -- compared as sets, so ordering never matters.

    FALLS BACK TO TRUE (constraining) whenever the question cannot be answered: no
    value on the event, a value of an unexpected shape, or an unknown domain. That
    keeps this strictly a refinement -- an unprovable case behaves exactly as the old
    unconditional "a change means it is on" rule did, so no signal is silently dropped
    and every record built without dataset context keeps its previous meaning.
    """
    domain = _attribute_domain(app_mode, attribute)
    if domain is None or not isinstance(value, (list, tuple)):
        return True
    kind, full = domain
    if kind == "numeric":
        if len(value) != 2:
            return True
        try:
            ends = [bias_util.cast_to_num(v) for v in value]
        except TypeError:
            return True
        if any(not isinstance(v, (int, float)) for v in ends):
            return True
        return [min(ends), max(ends)] != [full[0], full[1]]
    return {str(v) for v in value} != full


def get_current_filters(client_record, app_mode=None):
    """The attributes with a CONSTRAINING filter currently applied -> set of names.

    Filter events are NOT in bias_logs (they are not in COMPUTE_BIAS_FOR_TYPES), but
    every interaction, filter events included, is appended to response_list, so the
    active-filter set is reconstructed by replaying that list in order -- the same
    add/remove replay dc_adapter.selected_ids uses for the selection.

    response_list wraps each message under "input_data" (unlike bias_logs, which
    stores the raw data), so the interactionType and attribute are read from there.

    Replay rules:
      filter_added        -> add the attribute
      filter_removed      -> discard it
      all_filters_removed -> clear the whole set
      filter_changed      -> add it if the new filterModel CONSTRAINS the attribute,
                             otherwise DISCARD it (see below)

    "Active" used to mean "switched on at some point": filter_changed unconditionally
    added, and nothing but an explicit removal ever took an attribute back out. It now
    means the filter is actually EXCLUDING ROWS (Shiyao's item 9) -- a change back to
    the full range or the full category set deactivates the attribute again. This is
    the first rule that can deactivate on a value change, which matters because in the
    live study filter_changed is the ONLY filter event the UI can emit: the study
    dataset enables every attribute's filter row at load without a message, which
    leaves the add button disabled, and the remove button is hidden under the CONTROL
    layout every study condition runs. The other three rules are kept because they are
    structurally correct and reachable from other datasets/layouts -- they are simply
    not what the study exercises.

    llmTheme-sourced changes (data.filterType == "llmTheme", emitted when a participant
    applies a recommended theme) are deliberately NOT special-cased: an applied theme
    really does constrain the data, so it counts exactly like a hand-set filter.

    app_mode selects the dataset whose ranges the comparison is against. Resolved in
    order: the explicit argument (how filters_active_as_of passes it, since it hands in
    a synthetic record holding only a response_list slice), then the client record's own
    "app_mode", then the individual message's "appMode" -- every logged interaction
    carries one, so a record assembled from raw messages still resolves. Unresolvable
    (or a dataset with no precomputed distributions, as in the unit tests) leaves every
    filter_changed treated as constraining, i.e. exactly the old behaviour.

    Empty or absent response_list -> empty set (matches how the dwell tests build
    minimal records without this key).
    """
    record_mode = app_mode if app_mode is not None else client_record.get("app_mode")
    active = set()
    for entry in client_record.get("response_list", []):
        message = entry.get("input_data", {})
        itype = message.get("interactionType")
        if itype == "all_filters_removed":
            active.clear()
            continue
        data = message.get("data", {})
        attribute = data.get("attribute")
        if attribute is None:
            continue
        if itype == "filter_added":
            active.add(attribute)
        elif itype == "filter_removed":
            active.discard(attribute)
        elif itype == "filter_changed":
            mode = record_mode if record_mode is not None else message.get("appMode")
            if _filter_is_constraining(mode, attribute, data.get("value")):
                active.add(attribute)
            else:
                active.discard(attribute)
    return active


def get_currently_active_variables(client_record):
    """The variables the participant is actively working with -> set of names.

    The union of the x/y axis attributes (get_current_axes, Nones dropped) and the
    attributes with an active filter (get_current_filters). This is the single source
    the dwell trigger scopes and cools down on, so a variable counts as "active"
    whether it is on an axis, filtered, or both. Both source functions are left
    unmodified; this only composes them.
    """
    axes = get_current_axes(client_record)
    active = {v for v in (axes.get("x"), axes.get("y")) if v is not None}
    active |= get_current_filters(client_record)
    return active


def top_variable(dwell_bias_v, axes=None, force_variable=None):
    """The single variable that drives the intervention, or None.

    force_variable (HARD override, highest precedence): when given, it is returned
    verbatim, bypassing the supported/positive filter, the ranking, AND the axis
    preference below. Blind by design -- no validation -- because the only caller
    (generate_selection_and_emit) passes a target_var that is guaranteed supported
    and positive by construction. This is how the selection path pins the message to
    the variable its per-variable percentile fired on, instead of the soft, top-3
    axis steer. axes and force_variable are mutually exclusive per caller: the
    selection path passes force_variable (axes=None); every other path leaves
    force_variable None and uses axes exactly as before.

    Default (force_variable None; axes is None, or neither axis qualifies): the most
    positively-biased supported variable. Positive only -- a negative score is
    attention to belief-INCONSISTENT teens, the opposite of the bias we flag. Both
    halves of the intervention are about this one variable -- the summary names the
    range they over-attended, the recommendation the range they skipped -- so the two
    can never disagree about the subject.

    Axis preference (axes = {"x": name|None, "y": name|None}, realtime path only):
    if an attribute the participant currently has on an axis is among the top 3 of
    the ranking AND still positively biased, prefer it over the overall argmax, so
    the intervention targets what they are actively looking at. Ties between two
    qualifying axis attributes go to the higher weighted score.
    """
    if force_variable is not None:
        return force_variable
    supported = [kv for kv in dwell_bias_v.items()
                 if kv[0] in SUPPORTED_VARIABLES and kv[1] > 0]
    if not supported:
        return None
    ranked = sorted(supported, key=lambda kv: kv[1], reverse=True)

    if axes:
        top3 = [name for name, _ in ranked[:3]]
        score_of = dict(ranked)
        # deduped, None-tolerant list of the attributes currently on the axes
        axis_vars = list(dict.fromkeys(
            a for a in (axes.get("x"), axes.get("y")) if a is not None))
        # top-3 membership already implies supported and positively biased
        eligible = [(a, score_of[a]) for a in axis_vars if a in top3]
        if eligible:
            return max(eligible, key=lambda av: av[1])[0]

    return ranked[0][0]


def over_attended_cells(cells, group, n=3):
    """The n cells of one group that contributed MOST to the detected bias, by how
    far the participant attended them beyond their share of the dataset times how
    strongly they match the belief. The mirror of select_candidate_cell over the
    same grid, ranked the same way -- attention alone would put a barely-consistent
    range the participant happened to linger on above a strongly consistent one.

    Restricted to the focus group so the characteristics named cannot describe a
    different group from the one current_focus.diagnosis_status names.

    Belief-CONSISTENT only (vc > 0). Over-attention on its own is not the bias the
    summary is about: a range the participant lingered on that contradicts their
    beliefs is the opposite of confirmation, and naming it would have the message
    accuse them of favouring their expectations while citing evidence against it.
    """
    over = [c for c in cells
            if c["underexploration"] < 0 and c["vc"] > 0 and c["group"] == group]
    return sorted(over, key=lambda c: c["underexploration"] * abs(c["vc"]))[:n]


def _bin_teens(cell, n_teens):
    """Teens of this cell's group sitting in its bin. dataset_share already is that
    count over the whole dataset, so bin_group_candidates has done the bucketing --
    including its clamping of out-of-range values, which re-counting would miss."""
    return round(cell["dataset_share"] * n_teens)


def widened_span(cell, cells, n_teens):
    """The contiguous run of cells to filter on: the chosen one, grown into adjacent
    bins of the SAME group until they hold MIN_RECOMMENDED_TEENS between them.

    Only the range moves, never the group, so the recommendation stays about the
    same thing -- and the count is inside the group for the same reason, since the
    filter is a conjunction and the other group is never on screen.

    Runs AFTER selection: a nearly empty cell may well be the most belief-
    inconsistent one, so dropping it earlier would defeat the ranking. Grows toward
    whichever neighbour adds more teens, and returns the full width if even that
    falls short of the minimum.
    """
    row = sorted((c for c in cells if c["group"] == cell["group"]),
                 key=lambda c: c["bin_index"])
    lo = hi = next(i for i, c in enumerate(row) if c["bin_index"] == cell["bin_index"])
    count = _bin_teens(row[lo], n_teens)
    while count < MIN_RECOMMENDED_TEENS and (lo > 0 or hi < len(row) - 1):
        below = _bin_teens(row[lo - 1], n_teens) if lo > 0 else -1
        above = _bin_teens(row[hi + 1], n_teens) if hi < len(row) - 1 else -1
        if below >= above:
            lo -= 1
            count += below
        else:
            hi += 1
            count += above
    return row[lo:hi + 1]


def filter_ranges_for(span, values):
    """A span of bins as something applyThemeFilter can drop into a filterModel.

    Categorical bins are their own labels and the multiselect model is a list of
    them. A numeric span is the slider's [lo, hi] with the top edge clamped:
    bin_group_candidates reports bin EDGES and the slider includes both ends, so
    handing the edges over as-is also filters in the next bin's first value. On
    days_physical_activity_week that turned a 39-teen bin into a 57-teen filter.

    Always clamp, even when the last bin's label is already closed ("]") --
    that bracket only means the bin's hi edge is meant to include the true max,
    not that hi IS the true max: the one-bin-per-integer path (small integer
    ranges) sets the top bin's hi to max_value + 1 as pure bin arithmetic, so
    days_physical_activity_week (real max 7) was rendering as "7 to 8" until
    this clamped it back down.
    """
    if not isinstance(span[0]["bin_range"], list):
        return [c["bin_range"] for c in span]
    lo = span[0]["bin_range"][0]
    hi = span[-1]["bin_range"][1]
    closed = span[-1]["bin_label"].endswith("]")
    inside = [v for v in values if lo <= v <= hi] if closed else [v for v in values if lo <= v < hi]
    hi = max(inside) if inside else hi
    return [lo, hi]


def _range_phrase(variable, span, values):
    """One span as prose: 'screen time in the 4 to 6 range', or the category labels."""
    name = READABLE_NAMES.get(variable, variable)
    ranges = filter_ranges_for(span, values)
    if not isinstance(span[0]["bin_range"], list):
        return f"{name} of " + " or ".join(f'"{label}"' for label in ranges)
    lo, hi = (_plain(v) for v in ranges)
    return f"{name} of {lo}" if lo == hi else f"{name} in the {lo} to {hi} range"


def build_awareness_input(session, variable):
    """The exact 'Structured Input for the LLM - Awareness' shape from the slides.

    Narrowed to the one variable the recommendation is about, so current_focus
    describes the same thing the participant is being pointed away from.
    """
    diagnosis_focus = session["diagnosis_focus"]  # "Yes" or "No"
    group = _group_for(diagnosis_focus)
    values = session.get("bin_values", [])
    contributors = [
        {"variable": READABLE_NAMES.get(variable, variable),
         "range": _range_phrase(variable, [cell], values)}
        for cell in over_attended_cells(session.get("bin_cells", []), group)
    ]
    diagnosis_status = ("diagnosed with depression/anxiety" if diagnosis_focus == "Yes"
                        else "not diagnosed with depression/anxiety")
    return {
        "task_context": TASK_CONTEXT,
        "current_focus": {
            "diagnosis_status": diagnosis_status,
            "main_characteristics": [c["range"] for c in contributors],
        },
        "evidence_for_focus": {
            # Grounds the awareness_summary, so it must name the signal that
            # really fired. Defaulted only for realtime callers and the fixtures
            # that predate the key.
            "trigger_signal": session.get(
                "trigger_signal",
                "point-level dwell bias exceeded participant-specific threshold"),
            "top_variable_contributors": contributors,
        },
        "tone_constraints": session.get("tone_constraints", DEFAULT_TONE_CONSTRAINTS),
    }


def build_candidate_themes(session, variable):
    """The 'Mitigation' candidate_themes: one theme from the one cell
    select_candidate_cell picks out of session["bin_cells"], or [] if none qualifies.

    The cell fixes BOTH halves of the filter -- its bins give the range, its group
    gives the diagnosis status -- so the theme stays the conjunction the frontend
    already applies.
    """
    cells = session.get("bin_cells", [])
    cell = select_candidate_cell(cells)
    if cell is None:
        return []
    diagnosis = "Yes" if cell["group"] == "diagnosed" else "No"
    values = session.get("bin_values", [])
    span = widened_span(cell, cells, session.get("n_teens", 0))
    return [{
        "raw_theme": f"teens with {_range_phrase(variable, span, values)} who have "
                     f"{_diagnosis_phrase(diagnosis)}",
        "predicate": {variable: [c["bin_label"] for c in span],
                      "diagnosis": diagnosis.lower()},
        "filter_ranges": filter_ranges_for(span, values),
    }]


def assemble_llm_input(session):
    """The LLM input, or None when there is nothing to recommend: no
    positively-biased variable to name, or no candidate cell to point them at."""
    variable = top_variable(session["dwell_bias_v"], session.get("axes"),
                            session.get("force_variable"))
    if variable is None:
        return None
    candidate_themes = build_candidate_themes(session, variable)
    if not candidate_themes:
        return None
    return {
        "phase": session.get("phase", "realtime"),
        **build_awareness_input(session, variable),
        "candidate_themes": candidate_themes,
        "previous_interventions": session.get("previous_interventions", []),
    }


# --------------------------------------------------------------------------- #
# 2. Generation -- Claude call (schema-enforced JSON)
# --------------------------------------------------------------------------- #
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "low"      # latency dial; this fires while the participant is mid-task
MAX_TOKENS = 500

# The SERVER owns the deadline, not the browser. The summary blocks a
# participant's submit button behind a client-side timeout, and if the browser
# gave up first we would emit (and record) an intervention nobody saw. Keep this
# comfortably under LLM_SUMMARY_TIMEOUT_MS in main-activity/component.ts.
#
# Sized from measurement, not taste: over 6 runs latency was min 5.5s, median
# 6.0s, max 14.1s. A tighter bound drops a real share of interventions.
GENERATION_TIMEOUT_SECONDS = 20.0
# Per-attempt ceiling, deliberately GENEROUS. A tight value here is worse than
# none: the SDK retries its own timeouts, so an 8s setting turned a single slow
# call into 14.1s of retries. The wait_for above is the real deadline; this only
# stops a hung socket pinning an executor thread, which wait_for cannot cancel.
API_TIMEOUT_SECONDS = 20.0

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "awareness_summary": {"type": "string"},
        "transition": {"type": "string"},
        "recommended_themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "filter_ranges": {"type": "array", "items": {}},
                },
                "required": ["title", "description", "filter_ranges"],
                "additionalProperties": False,   # required by the API; omitting it 400s
            },
        },
    },
    "required": ["awareness_summary", "transition", "recommended_themes"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You generate the "LLM condition" intervention for a study on confirmation bias in \
visual data exploration. A participant is exploring a teen mental health dataset to pick cases for a \
data journalism story. The system has already detected, upstream, that their exploration is trending \
toward one pattern, and has already worked out which variables and which contrastive themes are worth \
surfacing -- you are not deciding any of that, only writing the natural-language output.

You will receive a JSON payload with:
- "current_focus": the diagnosis direction and 0-3 characteristics the participant has been focused on.
  "main_characteristics" can be empty: then name the diagnosis direction alone and invent no range.
- "evidence_for_focus": why this was triggered, grounding your summary so it is not invented.
- "candidate_themes": 1-2 pre-computed contrastive themes, each with a "raw_theme" description, a
  "predicate", and already-resolved "filter_ranges" (numeric range or category list) -- pass these
  filter_ranges through unchanged, do not recompute or alter them.
- "tone_constraints": a list of rules for this specific request. Follow every one of them exactly.
- "previous_interventions" (optional): earlier summaries/themes already shown this session. Vary your
  wording and theme framing from these; do not repeat the same phrasing.
- "phase": "realtime" (a live nudge), "final_check" (a pre-submission reflection), or
  "balanced_check" (a pre-submission reflection with nothing to correct).

Produce exactly this JSON shape:
{
  "awareness_summary": "<1-2 sentences naming the pattern, grounded in current_focus / evidence_for_focus>",
  "transition": "<a short bridging sentence into the recommendation>",
  "recommended_themes": [
    {"title": "<short, concrete theme title>", "description": "<1-2 inviting sentences using the theme's raw_theme>", "filter_ranges": <copied unchanged from the matching candidate_theme>}
  ]
}

If phase is "final_check", let the transition frame this as worth a quick look before submitting rather \
than a live nudge (e.g. "Before you submit, ..."). Produce one recommended_theme per candidate_theme \
given, in the same order, each one's filter_ranges copied through exactly as given.

If phase is "balanced_check", the participant's final selection is NOT skewed, so there is nothing to \
correct and the candidate_themes are context only -- do not recommend them. Use awareness_summary to \
recap what they explored, grounded the same way, and say their selection looks relatively balanced for \
building a story. Let the transition offer a last look as optional rather than as a fix (e.g. "You can \
take another look, or submit as they are."). Return "recommended_themes": [].
"""


def build_user_prompt(llm_input):
    return (
        "Here is the assembled input for this intervention:\n\n"
        f"{json.dumps(llm_input, indent=2)}\n\n"
        "Return the structured output now."
    )


# Lazy singleton client, mirroring firebase_logger._get_db: initialise on first
# use, disable the feature (print once, return None) if the key is missing or
# the SDK fails to construct. Never raises to the caller.
_client = None
_client_initialized = False


def _get_client():
    global _client, _client_initialized
    if _client_initialized:
        return _client
    _client_initialized = True

    try:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("[llm_intervention] No ANTHROPIC_API_KEY found - LLM intervention disabled.")
            return None

        _client = anthropic.Anthropic(api_key=api_key, timeout=API_TIMEOUT_SECONDS)
        print("[llm_intervention] Anthropic client initialised.")
        return _client

    except Exception as e:
        print(f"[llm_intervention] Init failed, LLM intervention disabled: {e}")
        return None


def generate_with_usage(llm_input, model=DEFAULT_MODEL, effort=DEFAULT_EFFORT):
    """Generate one intervention, returning (result, usage).

    result: the parsed dict, or None on any failure (missing key, API error,
    unparseable response). usage: {"input_tokens", "output_tokens"} or None.
    Never raises.

    This is the single call path. Both the live server log and the offline CLI
    read their token counts from here, so the numbers always come from the same
    place. generate() wraps this for callers that only want the result.
    """
    client = _get_client()
    if client is None:
        return None, None

    try:
        import anthropic
    except Exception:
        return None, None

    user_prompt = build_user_prompt(llm_input)

    def _call(include_effort):
        output_config = {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}
        if include_effort:
            output_config["effort"] = effort
        return client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            output_config=output_config,
            messages=[{"role": "user", "content": user_prompt}],
        )

    try:
        try:
            message = _call(True)
        except anthropic.BadRequestError as e:
            # Some models reject the effort parameter; retry once without it,
            # re-raise anything else. (claude-sonnet-5 supports effort, so this
            # is a safety net, not the expected path.)
            if "effort" in str(e).lower():
                print("[llm_intervention] 'effort' rejected; retrying without it.", flush=True)
                message = _call(False)
            else:
                raise
        # output_config.format guarantees the first text block is valid JSON.
        text = next((b.text for b in message.content if b.type == "text"), None)
        if not text:
            return None, None
        raw_usage = getattr(message, "usage", None)
        usage = None
        if raw_usage is not None:
            usage = {
                "input_tokens": getattr(raw_usage, "input_tokens", None),
                "output_tokens": getattr(raw_usage, "output_tokens", None),
            }
        return json.loads(text), usage
    except Exception as e:
        print(f"[llm_intervention] generation failed: {e}", flush=True)
        return None, None


def generate(llm_input, model=DEFAULT_MODEL, effort=DEFAULT_EFFORT):
    """Generate one intervention. Returns the parsed dict, or None on any failure
    (missing key, API error, unparseable response). Never raises.

    Thin wrapper over generate_with_usage for callers that don't need tokens.
    """
    result, _usage = generate_with_usage(llm_input, model=model, effort=effort)
    return result


def selection_metrics(detailed_map, selected_ids):
    """Selection-weighted counterpart of dc_adapter.compute_dwell_metrics.

    dwell_bias / dwell_bias_v take a generic {teen_id: weight} map, so weighting
    every selected teen equally gives the selection-side metrics with no change to
    dc_metric: the scalar reduces to dc_metric.selection_bias (mean DC over the
    selection minus the all-teen mean, the number already logged as
    dc_bias.selection_bias on qualifying interactions once priors are committed),
    and the per-variable version is the breakdown that compute_phase_metrics does
    not expose but the prompt assembly needs.

    weighted=True for the same reason compute_dwell_metrics passes it: the scores
    only read as per-variable contributions to the scalar if they are on its scale.
    The two paths set it separately -- this one does not go through dc_adapter.

    Lives here rather than in dc_adapter because that module is out of scope for
    this feature.

    FAIL-LOUD, inherited: a selected id absent from detailed_map raises KeyError.
    The caller guards at the handler boundary, as on_interaction does for dwell.
    """
    weights = {teen_id: 1.0 for teen_id in selected_ids}
    return {
        "selection_bias": dc_metric.dwell_bias(detailed_map, weights),
        "selection_bias_v": dc_metric.dwell_bias_v(detailed_map, weights, weighted=True),
        "n_selected": len(weights),
    }


def live_room(sio, sid_by_pid, pid):
    """The participant's CURRENTLY CONNECTED room, or None.

    Two hazards this closes, both invisible without it:

    1. sid_by_pid is never pruned (server.disconnect only timestamps), so after a
       reconnect it holds the OLD sid. socket.io discards an emit to a dead room
       silently -- no error, no return value -- so the caller would report success
       for a message nobody received.
    2. room=None is NOT "nobody" in socket.io: base_manager.connect enters every
       client into room None, so it is the ALL-CLIENTS room. Emitting there would
       broadcast one participant's personalised intervention to every other
       participant in the study.
    """
    sid = sid_by_pid.get(pid)
    if sid is None or not sio.manager.is_connected(sid, "/"):
        return None
    return sid


async def generate_and_emit(sio, sid_by_pid, pid, client_record, dwell_metrics, teens,
                            target_var):
    """Background task: assemble -> generate -> emit the realtime intervention.

    Fired via SIO.start_background_task from on_interaction AFTER the interaction
    response has already gone out, so the ~4-6s Claude call is off the critical
    path.

    Takes the pid -> sid map rather than a sid: a reconnect during those seconds
    gives the participant a NEW sid, and emitting to the old room silently
    delivers nothing. live_room resolves it and rejects a stale one.

    target_var is the variable the dwell trigger's priority hierarchy fired on. It is
    passed as force_variable, a HARD override pinning the whole intervention -- cell grid
    and message text alike -- to that variable, exactly as generate_selection_and_emit
    does with its own winner. This REPLACES top_variable's independent ranking on this
    path: the trigger now scores each variable separately and already knows which one it
    fired on, so letting the assembly re-rank dwell_bias_v could name a DIFFERENT
    variable than the one that actually crossed the threshold. axes is therefore None
    here too -- force_variable fully replaces that soft top-3 steer. target_var is
    expected non-None (the caller only runs this on a fire).

    Emits an "llm_intervention" event carrying the generated object with each
    theme's variable / diagnosis_filter attached (see _generate_and_emit) -- the
    shape the frontend panel already consumes.

    Returns whether it DELIVERED, same as the selection sibling. The realtime caller used
    to discard this; it no longer can. The system-wide gate is a display-linked pause
    that only reopens when a panel goes away, so a generation that failed, timed out, or
    found no live socket has to say so -- otherwise no panel exists, no dismiss ever
    arrives, and the participant is muted for the rest of the session (server.py
    _fire_dwell_intervention).
    """
    dwell = dc_metric.dwell_by_teen(client_record.get("bias_logs", []))
    return await _generate_and_emit(
        sio, sid_by_pid, pid, client_record, teens,
        weights=dwell,
        attention={"dwell": dwell},
        bias_v=dwell_metrics.get("dwell_bias_v", {}),
        phase="realtime",
        trigger_signal="point-level dwell bias exceeded participant-specific threshold",
        event="llm_intervention",
        # Hard-pin the message to the fired variable (not the soft axis steer).
        axes=None,
        force_variable=target_var)


async def generate_selection_and_emit(sio, sid_by_pid, pid, client_record,
                                      selection, teens, selected_ids, target_var):
    """Background task: a NON-blocking, per-selection nudge on the running selection.

    The selection-weighted sibling of the REALTIME dwell nudge (generate_and_emit):
    it reuses the shared core, differing only in the arguments below. Teens are
    weighted equally by having been selected, the per-variable scores come from
    the selection, and the phase and event are the REALTIME ones ("realtime" /
    "llm_intervention"), so this delivers a live nudge and
    never blocks a submit. There is no fallback MESSAGE, but the pending state must
    still end: the frontend has selection closed until it hears back, so a generation
    that produced nothing has to say so.

    target_var is the variable the progressive trigger fired on (argmax of the
    per-variable selection percentiles). It is passed as force_variable, a HARD
    override that pins the whole intervention -- cell grid and message text alike --
    to that variable, unlike the realtime dwell path's soft top-3 axis preference.
    axes is None here: selection has no real axes, and force_variable fully replaces
    that lever. target_var is expected non-None (the caller only runs this on a fire).
    """
    generated = await _generate_and_emit(
        sio, sid_by_pid, pid, client_record, teens,
        weights={teen_id: 1.0 for teen_id in selected_ids},
        attention={"selected_ids": selected_ids},
        bias_v=selection.get("selection_bias_v", {}),
        phase="realtime",
        trigger_signal=("selection-level bias on the participant's running selection "
                        "exceeded participant-specific threshold for one variable"),
        event="llm_intervention",
        # Hard-pin the message to the fired variable (not the soft axis steer).
        axes=None,
        force_variable=target_var)
    if not generated:
        room = live_room(sio, sid_by_pid, pid)
        if room is not None:
            await sio.emit(SELECTION_PENDING_EVENT, {"active": False}, room=room)
        print(f"[LLM] {pid}: selection intervention produced nothing; "
              f"selection released", flush=True)
    return generated


async def _generate_and_emit(sio, sid_by_pid, pid, client_record, teens,
                             weights, attention, bias_v, phase, trigger_signal, event,
                             axes=None, force_variable=None):
    """Shared assemble -> generate -> emit -> persist core. Returns whether it DELIVERED.

    False covers "generation failed", "timed out", and "generated but the
    participant had no live socket" -- the caller has to treat all three the same
    way, because in all three the participant never saw anything.

    Everything downstream of the weights is phase-agnostic: _majority_diagnosis
    takes a {teen_id: weight} map, so the realtime and summary paths differ only in
    the arguments above.

    attention is the one kwarg bin_group_candidates takes for the same split:
    {"dwell": ...} realtime, {"selected_ids": ...} at submit. It stays separate from
    weights because that function counts a selection rather than weighting it.

    Wrapped end-to-end in try/except: a broken LLM call must never break an
    interaction response or lose data. Appends a compact record to
    client_record["llm_interventions"], which is fed back as previous_interventions
    so later generations (including a summary following realtime nudges) vary their
    wording, and persists the full input/output via firebase_logger.
    """
    delivered = False   # set before the try: the except clauses read it
    try:
        diagnosis_focus = _majority_diagnosis(teens, weights)

        # One variable drives the whole intervention, so it is resolved once here
        # and its cell grid is what assembly reads.
        variable = top_variable(bias_v, axes, force_variable)
        bin_cells, bin_values = [], []
        if variable is not None:
            bin_cells = dc_metric.bin_group_candidates(
                variable, client_record["beliefs"], teens, **attention)
            numeric = isinstance(bin_cells[0]["bin_range"], list)
            bin_values = [bias_util.cast_to_num(t[variable]) if numeric else t[variable]
                          for t in teens.values() if variable in t]

        session = {
            "phase": phase,
            # Carries selection-weighted scores on the selection path. The key
            # keeps its dwell name so the llm_samples fixtures stay valid.
            "dwell_bias_v": bias_v,
            # Axis attributes for top_variable's realtime axis-preference (None on
            # the summary path). Threaded through so assemble_llm_input's own
            # top_variable call resolves the SAME variable as the cell grid above.
            "axes": axes,
            # Hard variable override (selection path). MUST be threaded here too, not
            # just into the :807 call above: assemble_llm_input resolves the message
            # variable from session, so omitting it would let the cell grid (forced)
            # and the message text (argmax) silently disagree.
            "force_variable": force_variable,
            "bin_cells": bin_cells,
            "bin_values": bin_values,
            "n_teens": len(teens),
            "diagnosis_focus": diagnosis_focus,
            "trigger_signal": trigger_signal,
            "previous_interventions": client_record.get("llm_interventions", []),
        }
        llm_input = assemble_llm_input(session)
        if llm_input is None:
            # Realtime stays silent; the summary wrapper turns this False into a
            # skip, so the submit button is still released.
            print(f"[LLM] {pid}: nothing to surface "
                  f"(variable={variable}, cells={len(bin_cells)})", flush=True)
            return False

        # --- log what actually goes into the model -------------------------
        _top = [c.get("variable") for c in
                llm_input.get("evidence_for_focus", {}).get("top_variable_contributors", [])]
        _themes = " | ".join(
            f"{t.get('raw_theme')} -> {t.get('filter_ranges')}"
            for t in llm_input.get("candidate_themes", []))
        print(f"[LLM] {pid}: input focus={diagnosis_focus} top={_top} "
              f"phase={llm_input.get('phase')} "
              f"prev={len(llm_input.get('previous_interventions', []))}", flush=True)
        print(f"[LLM] {pid}: themes {_themes}", flush=True)

        started = _now()
        # run_in_executor, not a direct call: the Anthropic SDK client is
        # SYNCHRONOUS, and start_background_task returns an asyncio.Task on this
        # same loop -- so calling it inline froze the whole server (every
        # participant's interactions, not just this one) for the length of the
        # HTTP request. wait_for then bounds the total, including the
        # BadRequestError retry, which a per-call SDK timeout would not.
        loop = asyncio.get_event_loop()
        result, usage = await asyncio.wait_for(
            loop.run_in_executor(None, generate_with_usage, llm_input),
            timeout=GENERATION_TIMEOUT_SECONDS)
        elapsed = (_now() - started) / 1000.0
        if result is None:
            # Disabled (no API key) or generation failed; both already logged a
            # line of their own. Say so here so the trigger has a visible end.
            print(f"[LLM] {pid}: no output ({elapsed:.1f}s)", flush=True)
            return False


        # Attach both halves of each recommended theme's filter: the variable its
        # range applies to, and the diagnosis status it contrasts against. The
        # reviewed OUTPUT_SCHEMA deliberately carries only title/description/
        # filter_ranges, so both would otherwise be lost. We recover them by
        # matching the theme's filter_ranges back to the candidate that produced
        # it -- by VALUE, never by position (see below). Enriches the emitted
        # event only; schema/prompt untouched.
        candidates = llm_input.get("candidate_themes", [])
        for theme in result.get("recommended_themes", []):
            variable = _variable_for_filter_ranges(candidates, theme.get("filter_ranges"))
            if variable is None:
                # No exact-value match: the model reordered or (against the
                # prompt) altered the filter_ranges. Drop this theme's filter
                # action rather than guess -- applying a wrong range to a real
                # click would silently corrupt the stimulus. The frontend
                # already no-ops a theme with no "variable".
                print(f"[LLM] {pid}: theme filter_ranges "
                      f"{theme.get('filter_ranges')} matched no candidate; "
                      f"dropping its filter action.", flush=True)
                continue
            theme["variable"] = variable
            theme["diagnosis_filter"] = _diagnosis_filter_for_filter_ranges(
                candidates, theme.get("filter_ranges"))

        # Feed back next time so repeat interventions vary their wording.
        client_record.setdefault("llm_interventions", []).append({
            "awareness_summary": result.get("awareness_summary"),
            "recommended_theme_titles": [t.get("title") for t in result.get("recommended_themes", [])],
        })

        # --- log what the model produced -----------------------------------
        _titles = [t.get("title") for t in result.get("recommended_themes", [])]
        _tokens = (f"in={usage.get('input_tokens')} out={usage.get('output_tokens')}"
                   if usage else "n/a")
        print(f"[LLM] {pid}: output summary={result.get('awareness_summary')!r}", flush=True)
        print(f"[LLM] {pid}: output transition={result.get('transition')!r}", flush=True)
        print(f"[LLM] {pid}: output themes={_titles} "
              f"({elapsed:.1f}s, tokens {_tokens})", flush=True)

        room = live_room(sio, sid_by_pid, pid)
        if room is not None:
            await sio.emit(event, result, room=room)
            delivered = True
            # ANALYSIS ONLY -- read by no gate. llm_trigger's display pause opens at the
            # FIRE DECISION (llm_panel_open_since), which is the right thing for the
            # gate but means that timestamp includes the generation. This one marks when
            # the panel actually went out, so the two together give both the generation
            # latency and, against the dismiss, the real display duration. Stamped on
            # the shared core, so a selection panel records it too; nothing branches on
            # it either way.
            client_record["llm_panel_shown_at"] = _now()
        else:
            print(f"[LLM] {pid}: {event} not delivered (no live socket)", flush=True)

        # Persist the generated text so it is recoverable for analysis. This is
        # research data, not just UI. Reuse the existing per-participant logs path.
        firebase_logger.save_logs(pid, [{
            # The event name doubles as the record kind, so realtime nudges and
            # pre-submission summaries stay separable in the analysis data.
            "kind": event,
            "participant_id": pid,
            "created_at": _now(),
            # Generated is not seen. Without this an undelivered intervention
            # would count as treatment in the analysis.
            "delivered": delivered,
            "llm_input": llm_input,
            "output": result,
        }])
        return delivered
    except asyncio.TimeoutError:
        print(f"[LLM] {pid}: {event} timed out after "
              f"{GENERATION_TIMEOUT_SECONDS}s", flush=True)
        return False
    except Exception as e:
        # Return what actually reached the participant, not False. A failure
        # AFTER the emit (persistence, logging) must not make the caller send a
        # second "nothing to show" for a summary they are already looking at.
        print(f"[LLM] {event} failed: {e}", flush=True)
        return delivered


def _majority_diagnosis(teens, weights):
    """WEIGHTED majority diagnosis status among the teens carrying weight.

    Weighted by whatever drove this generation, consistent with the rest of the
    pipeline (dwell_bias, dwell_bias_v).

    On the REALTIME path the weights are dwell times, and that matters: a plain
    head count would let diagnosis_focus contradict the dwell signal that fired
    the trigger (e.g. long dwells on a few diagnosed teens plus brief glances at
    many non-diagnosed ones), which would invert current_focus and flip both
    candidate_themes -- recommending the participant's own fixation back to them.

    On the SUMMARY path every weight is 1.0, so this does reduce to a head count
    over the selection. That is intended, not an oversight: a teen is either in
    the final selection or not, so each one counts once.

    Returns "Yes" when diagnosed teens hold MORE than half the total weight, else
    "No" (ties -> "No", matching the dataset's own majority). Falls back to "No"
    when no weighted teen carries the label column.
    """
    diagnosed_weight = 0.0
    total_weight = 0.0
    for teen_id, weight in weights.items():
        teen = teens.get(teen_id)
        if teen is None or dc_metric.LABEL_ATTR not in teen:
            continue
        total_weight += weight
        if str(teen[dc_metric.LABEL_ATTR]) == dc_metric.DIAGNOSED_VALUE:
            diagnosed_weight += weight
    if total_weight == 0:
        return "No"
    return "Yes" if diagnosed_weight > total_weight / 2 else "No"


def _now():
    """Millisecond timestamp, matching the rest of the server's logging."""
    return bias_util.get_current_time()
