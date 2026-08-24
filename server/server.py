"""Server for interfacing with the frontend.
"""
import os
from pathlib import Path

import pandas as pd
import socketio
from aiohttp import web
from aiohttp_index import IndexMiddleware

import bias
import bias_util
import firebase_logger
import dc_metric
import dc_adapter
import llm_intervention
import llm_trigger

# Set the path for the Google Cloud Logging logger
currdir = Path(__file__).parent.absolute()

# Debug toggle: when True, on_interaction streams the per-attribute JS
# confirmation-bias scores to stdout. Off by default; flip on when building/
# debugging the next layer.
JS_DEBUG = False

CLIENTS = {}  # entire data map of all client data
# pid -> last logged "not triggered" gate code. Keeps the LLM skip log to one
# line per gate change instead of one per interaction.
LLM_LAST_SKIP = {}
# The three LLM interface versions, as app_type values. Each condition shows only
# its own component(s), so the two features gate on membership rather than on a
# single "LLM" equality check.
LLM_REALTIME_TYPES = {"LLM", "LLM_BOTH"}
LLM_SUMMARY_TYPES = {"LLM_SUMMARY", "LLM_BOTH"}
CLIENT_PARTICIPANT_ID_SOCKET_ID_MAPPING = {}
CLIENT_SOCKET_ID_PARTICIPANT_MAPPING = {}
COMPUTE_BIAS_FOR_TYPES = [
    "mouseout_item",
    "mouseout_group",
    "click_group",
    "click_add_item",
    "click_remove_item",
]

SIO = socketio.AsyncServer(cors_allowed_origins='*')
APP = web.Application(middlewares=[IndexMiddleware()])
SIO.attach(APP)

async def handle_ui_files(request):
    # Extract the requested file name
    fname = request.match_info.get('fname', 'index.html')

    # Serve index.html for all routes that don't have a file extension
    if '.' not in fname:
        fname = 'index.html'

    # Define the public directory (similar to Flask's 'public' directory)
    public_dir = os.path.join(os.path.dirname(__file__), 'public')

    # Serve the file from the public directory
    file_path = os.path.join(public_dir, fname)

    try:
        return web.FileResponse(file_path)
    except FileNotFoundError:
        raise web.HTTPNotFound()

# Static file serving
APP.router.add_static('/static/', path=str(os.path.join(os.path.dirname(__file__), 'public')), name='static')

# Dynamic routing for all paths, similar to Flask's catch-all routes
APP.router.add_route('GET', '/{fname:.*}', handle_ui_files)

@SIO.event
async def connect(sid, environ):
    print(f"Connected: {sid}")
    attr_dist = {}
    for filename in bias.DATA_MAP:
        dataset = bias.DATA_MAP[filename]
        attr_dist[filename] = dataset["distribution"]
    await SIO.emit("attribute_distribution", attr_dist, room=sid)


@SIO.event
def disconnect(sid):
    if sid in CLIENT_SOCKET_ID_PARTICIPANT_MAPPING:
        pid = CLIENT_SOCKET_ID_PARTICIPANT_MAPPING[sid]
        if pid in CLIENTS:
            CLIENTS[pid]["disconnected_at"] = bias_util.get_current_time()
            print(f"Disconnected: Participant ID: {pid} | Socket ID: {sid}")


@SIO.event
async def on_session_end_page_level_logs(sid, payload):
    pid = payload["participantId"]
    if pid in CLIENTS and "data" in payload:
        dirname = f"output/{CLIENTS[pid]['app_type']}/{pid}"
        # parents=True: a condition whose output/<app_type>/ dir isn't committed
        # would otherwise raise here and skip the writes below.
        Path(dirname).mkdir(parents=True, exist_ok=True)
        filename = f"output/{CLIENTS[pid]['app_type']}/{pid}/session_end_page_logs_{pid}_{bias_util.get_current_time()}.tsv"
        df_to_save = pd.DataFrame(payload["data"])

        # persist to disk
        df_to_save.transpose().to_csv(filename, sep="\t")

        print(f"Saved session logs to file: {filename}")


@SIO.event
async def on_save_logs(sid, data):
    if sid in CLIENT_SOCKET_ID_PARTICIPANT_MAPPING:
        pid = CLIENT_SOCKET_ID_PARTICIPANT_MAPPING[sid]
        if pid in CLIENTS:
            dirname = f"output/{CLIENTS[pid]['app_type']}/{pid}"
            Path(dirname).mkdir(parents=True, exist_ok=True)
            ts = bias_util.get_current_time()

            filename = f"{dirname}/logs_{pid}_{ts}.tsv"
            df_to_save = pd.DataFrame(CLIENTS[pid]["response_list"])
            df_to_save.to_csv(filename, sep="\t")
            print(f"Saved logs to file: {filename}")
            firebase_logger.save_logs(pid, CLIENTS[pid]["response_list"])

            priors = CLIENTS[pid].get("priors", {})
            if priors:
                priors_filename = f"{dirname}/priors_{pid}_{ts}.tsv"
                pd.DataFrame(priors.values()).to_csv(priors_filename, sep="\t", index=False)
                print(f"Saved priors to file: {priors_filename}")
            firebase_logger.save_priors(pid, priors)

def ensure_client(sid, pid, app_mode, app_type, app_level, participant_id_source="random"):
    """Get-or-create the per-participant record, applying dataset/level resets.

    Shared by on_interaction and on_commit_priors so both entry points
    initialize the same record the same way (and so priors committed *before*
    any interaction still land in a well-formed record). Returns the record.
    """
    # Let these get updated everytime an event occurs, to handle the
    #   worst case scenario of random restart of the server.
    CLIENT_SOCKET_ID_PARTICIPANT_MAPPING[sid] = pid
    CLIENT_PARTICIPANT_ID_SOCKET_ID_MAPPING[pid] = sid

    if pid not in CLIENTS:
        # new participant => establish data mapping for them!
        CLIENTS[pid] = {}
        CLIENTS[pid]["id"] = sid
        CLIENTS[pid]["participant_id"] = pid
        CLIENTS[pid]["app_mode"] = app_mode
        CLIENTS[pid]["app_type"] = app_type
        CLIENTS[pid]["app_level"] = app_level
        CLIENTS[pid]["connected_at"] = bias_util.get_current_time()
        CLIENTS[pid]["participant_id_source"] = participant_id_source
        CLIENTS[pid]["bias_logs"] = []
        CLIENTS[pid]["response_list"] = []
        CLIENTS[pid]["priors"] = {}  # {attribute: PriorBelief}, last-write-wins

        # Split-flow support: this is the first time THIS server process has
        # ever seen this participant (fresh CLIENTS entry), but they may have
        # already completed /elicitation in an earlier, separate session --
        # possibly against a since-restarted server process, so nothing of
        # theirs survives in memory. Reload whatever priors Firestore has for
        # them and, if all variables are complete, compute dc_map right away
        # so a /main-only session still gets bias metrics/LLM intervention
        # from the start, not just combined-flow sessions where elicitation
        # just happened in-process.
        reloaded_priors = firebase_logger.load_priors(pid)
        if reloaded_priors:
            CLIENTS[pid]["priors"] = reloaded_priors
            maybe_compute_dc_map(pid, app_mode, CLIENTS[pid])

    if app_type != CLIENTS[pid]["app_type"]:
        # Overwrite a stale app_type from an earlier contact with this participant
        # (e.g. /elicitation, where there's no ?type= param so it's just the
        # AWARENESS default) once the real group shows up, typically on /main
        # load. Persist immediately rather than waiting for the next on_save_logs,
        # so Firestore doesn't keep showing the wrong group in the meantime.
        CLIENTS[pid]["app_type"] = app_type
        firebase_logger.save_meta(pid, CLIENTS[pid])

    if app_mode != CLIENTS[pid]["app_mode"] or app_level != CLIENTS[pid]["app_level"]:
        # datasets have been switched => reset the logs array!
        # OR
        # app_level (e.g. practice > live) is changed but same dataset is in use => reset the logs array!
        # Priors are per-dataset, so a dataset/level switch invalidates them too.
        CLIENTS[pid]["app_mode"] = app_mode
        CLIENTS[pid]["app_level"] = app_level
        CLIENTS[pid]["bias_logs"] = []
        CLIENTS[pid]["response_list"] = []
        CLIENTS[pid]["priors"] = {}

    return CLIENTS[pid]


@SIO.on('on_commit_priors')
async def on_commit_priors(sid, data):
    """Receive elicited prior beliefs and stash them per-participant."""
    print(f"[on_commit_priors] received from sid={sid}")
    try:
        app_mode = data["appMode"]
        app_type = data.get("appType")
        app_level = data.get("appLevel")
        pid = data["participantId"]
        pid_source = data.get("participantIdSource", "random")

        client = ensure_client(sid, pid, app_mode, app_type, app_level, pid_source)

        incoming = data.get("priors", [])
        if isinstance(incoming, dict):
            incoming = [incoming]
        for belief in incoming:
            condition = belief.get("condition", "default")
            key = f"{belief['attribute']}::{condition}"
            print(f"[on_commit_priors] attribute={belief.get('attribute')} condition={condition} key={key}")
            client["priors"][key] = belief

        print(f"[on_commit_priors] priors keys now: {sorted(client['priors'].keys())}")
        client["priors_committed_at"] = bias_util.get_current_time()
        firebase_logger.save_priors(pid, client["priors"])
        firebase_logger.save_meta(pid, client)

        maybe_compute_dc_map(pid, app_mode, client)
    except Exception as e:
        print(f"[on_commit_priors] ERROR: {e}", flush=True)
        raise


def maybe_compute_dc_map(pid, app_mode, client):
    """Compute the per-teen DC map ONCE all six elicited variables have both
    conditions, caching it on the client record for compute_metrics /
    on_task_submitted / llm_intervention to read live for the rest of this
    session.

    Shared by on_commit_priors (elicitation just happened, in this same
    session) and ensure_client (the /main-only split flow: elicitation
    happened in an earlier, separate session, so its priors -- reloaded from
    Firestore -- are only reaching this server process's memory for the
    first time now).
    """
    # Reshape stored priors via the adapter (guards incomplete vars + asserts
    # shared bins).
    ready, beliefs, report = dc_adapter.is_ready(client["priors"])
    print(f"[DC] {pid}: {len(report['complete'])}/{dc_adapter.EXPECTED_VARIABLE_COUNT} "
          f"variables complete {report['complete']}"
          + (f" | incomplete-so-far {report['skipped_incomplete']}" if report['skipped_incomplete'] else ""),
          flush=True)
    if not ready:
        return
    teens = bias.DATA_MAP.get(app_mode, {}).get("data", {})
    sample = next(iter(teens.values()), {})
    if teens and dc_metric.LABEL_ATTR in sample:
        dmap = dc_metric.dc_map(teens, beliefs)
        client["dc_map"] = dmap
        # The reshaped beliefs themselves, for llm_intervention: the belief
        # bins are what the recommended filter range is now drawn from.
        client["beliefs"] = beliefs
        # Detailed map (per-teen {dc, consistency, weights}) for the dwell
        # metrics; same teens/beliefs, computed once alongside dc_map.
        client["dc_map_detailed"] = dc_metric.dc_map_detailed(teens, beliefs)
        mean_dc = sum(dmap.values()) / len(dmap) if dmap else 0.0
        print(f"[DC] DC map computed for {pid}: {len(dmap)} teens, "
              f"mean DC {mean_dc:.4f} (vars: {report['complete']})", flush=True)
    else:
        print(f"[DC] {pid}: dataset '{app_mode}' has no '{dc_metric.LABEL_ATTR}' "
              f"label column; skipping DC map.", flush=True)


@SIO.on('on_participant_refreshed')
async def on_participant_refreshed(sid, data):
    """Log a page-refresh event. Only meaningful for Prolific participants --
    their participantId is stable across a refresh (derived from the
    PROLIFIC_PID URL param), whereas random IDs are regenerated on every
    page load and would just look like a new participant."""
    pid = data.get("participantId")
    pid_source = data.get("participantIdSource", "random")
    if not pid or pid_source != "prolific":
        return
    ts = bias_util.get_current_time()
    if pid in CLIENTS:
        CLIENTS[pid].setdefault("refresh_events", []).append(ts)
    print(f"[on_participant_refreshed] Participant ID: {pid} refreshed at {ts}")
    firebase_logger.save_refresh_event(pid, ts)


@SIO.on('on_elicitation_submitted')
async def on_elicitation_submitted(sid, data):
    pid = data.get("participantId")
    code = data.get("verificationCode")
    elicitation_started_at = data.get("elicitationStartedAt")
    elicitation_ended_at = data.get("elicitationEndedAt")
    elicitation_duration_ms = data.get("elicitationDurationMs")
    elicitation_engagement = data.get("elicitationEngagement")
    if pid:
        submitted_at = bias_util.get_current_time()
        firebase_logger.save_elicitation_submission(
            pid, code, submitted_at,
            elicitation_started_at=elicitation_started_at,
            elicitation_ended_at=elicitation_ended_at,
            elicitation_duration_ms=elicitation_duration_ms,
            elicitation_engagement=elicitation_engagement,
        )
        print(f"Elicitation submitted for {pid}: {code}"
              f" | elicitation_ms={elicitation_duration_ms} elicitation_engagement={elicitation_engagement}")


@SIO.on('on_selected_subjects')
async def on_selected_subjects(sid, data):
    pid = data.get("participantId")
    subjects = data.get("selected_subjects", [])
    if pid:
        firebase_logger.save_selected_subjects(pid, subjects)
        print(f"Selected subjects for {pid}: {subjects}")


@SIO.on('on_task_submitted')
async def on_task_submitted(sid, data):
    pid = data.get("participantId")
    code = data.get("verificationCode")
    subjects = data.get("selected_subjects", [])
    elicitation_started_at = data.get("elicitationStartedAt")
    elicitation_ended_at = data.get("elicitationEndedAt")
    elicitation_duration_ms = data.get("elicitationDurationMs")
    elicitation_engagement = data.get("elicitationEngagement")
    main_task_started_at = data.get("mainTaskStartedAt")
    main_task_ended_at = data.get("mainTaskEndedAt")
    main_task_duration_ms = data.get("mainTaskDurationMs")
    main_engagement = data.get("mainEngagement")
    if pid:
        submitted_at = bias_util.get_current_time()
        firebase_logger.save_task_submission(
            pid, code, subjects, submitted_at,
            elicitation_started_at=elicitation_started_at,
            elicitation_ended_at=elicitation_ended_at,
            elicitation_duration_ms=elicitation_duration_ms,
            elicitation_engagement=elicitation_engagement,
            main_task_started_at=main_task_started_at,
            main_task_ended_at=main_task_ended_at,
            main_task_duration_ms=main_task_duration_ms,
            main_engagement=main_engagement,
        )
        print(f"Task submitted for {pid}: {code}"
              f" | elicitation_ms={elicitation_duration_ms} elicitation_engagement={elicitation_engagement}"
              f" main_task_ms={main_task_duration_ms} main_engagement={main_engagement}")

        # --- SelectionBias at submit: computed ONCE here from the CACHED maps +
        # the FINAL submitted selection (no DC recompute). Shiyao confirmed
        # selection_bias has no trigger policy -- it's a one-shot at submission.
        # The dependent measure is the PER-VARIABLE selection_bias_v (the
        # methodology moved to per-variable VC); the pooled percentile stays
        # alongside it so the measure carried through the study so far is not
        # lost mid-study. Both computations raise fail-loud inside dc_metric,
        # so guard at this handler boundary (same convention as on_interaction's
        # dwell_bias guard) -- a stray id must never break submission. Absent
        # dc_map (elicitation never completed) -> skip. ---
        client = CLIENTS.get(pid)
        dc_map_cached = client.get("dc_map") if client else None
        if dc_map_cached:
            try:
                pct = dc_adapter.compute_selection_percentile(
                    dc_map_cached, subjects, rng=dc_adapter.live_rng())
                detailed = client.get("dc_map_detailed")
                selection = (llm_intervention.selection_metrics(detailed, subjects)
                             if detailed else None)
                print(f"[SEL%] pid={pid} | "
                      f"selection_bias_percentile={pct['selection_bias_percentile']} "
                      f"(n_selected={len(subjects)})", flush=True)
                if selection:
                    print(f"[SEL-V] pid={pid} | " + "  ".join(
                        f"{v}={b:+.4f}" for v, b in selection["selection_bias_v"].items()),
                        flush=True)
                distinct_vals = sorted(set(round(v, 4) for v in dc_map_cached.values()))
                print(f"[DC-ARR] pid={pid} | n={len(dc_map_cached)} distinct={len(distinct_vals)} | "
                      f"values={'  '.join(f'{v:.4f}' for v in dc_map_cached.values())}", flush=True)
                # Persist the submission-time SelectionBias (per-variable and
                # pooled) and the DC-ARR data, which otherwise live only in the
                # prints above.
                firebase_logger.save_logs(pid, [{
                    "kind": "selection_percentile",
                    "participant_id": pid,
                    "created_at": bias_util.get_current_time(),
                    "selection_bias_percentile": pct["selection_bias_percentile"],
                    "selection_bias": selection["selection_bias"] if selection else None,
                    "selection_bias_v": selection["selection_bias_v"] if selection else None,
                    "n_selected": len(subjects),
                    "dc_map_n": len(dc_map_cached),
                    "dc_map_distinct": len(distinct_vals),
                    "dc_map_values": list(dc_map_cached.values()),
                }])
            except Exception as e:
                print(f"[SEL%] compute failed: {e}", flush=True)


@SIO.on('on_llm_dismissed')
async def on_llm_dismissed(sid, data=None):
    """The realtime panel closed, by timeout or by hand.

    Only the client knows when that happened, so both the system-wide display pause and
    the per-variable recheck window can only be lifted from here. The two are separate
    mechanisms that happen to share this one trigger: the pause is what stops ANY new
    intervention while a panel is up, the watermark is what stops the SAME variable
    re-firing off dwell the participant spent reading it.
    """
    pid = (data or {}).get("participantId")
    client_record = CLIENTS.get(pid)
    if client_record:
        llm_trigger.release_display_pause(client_record)
        llm_trigger.reset_dwell_watermark(client_record)


async def _fire_dwell_intervention(pid, client_record, dwell_metrics, teens, target_var):
    """Run a fired dwell intervention, and reopen the display pause if it never landed.

    A thin wrapper around llm_intervention.generate_and_emit purely so its "did this
    actually reach the participant" answer is not thrown away. start_background_task
    discards a coroutine's return value, so the delivered flag had nowhere to go before.

    Why it matters now: the system-wide gate is no longer a duration that expires on its
    own -- it stays closed until something clears it. Generation can fail, time out, or
    find no live socket, in which case NO panel is ever displayed and NO dismiss will
    ever arrive. Without this release the participant would be silently muted for the
    rest of the session. This is the failure mode the old 30s wall clock papered over.

    Only the pause is released, never the per-variable watermark: reset_dwell_watermark
    rebases the fired variable's recheck window against dwell spent READING the panel,
    and there was no panel to read.
    """
    delivered = await llm_intervention.generate_and_emit(
        SIO, CLIENT_PARTICIPANT_ID_SOCKET_ID_MAPPING, pid,
        client_record, dwell_metrics, teens, target_var)
    if not delivered:
        llm_trigger.release_display_pause(client_record)
        print(f"[LLM] {pid}: intervention never reached the participant; "
              f"display pause released", flush=True)
    return delivered


@SIO.event
async def on_interaction(sid, data):
    app_mode = data["appMode"]  # The dataset that is being used, e.g. cars.csv
    app_type = data["appType"]  # CONTROL / AWARENESS / ADMIN
    app_level = data["appLevel"]  # live / practice
    pid = data["participantId"]
    pid_source = data.get("participantIdSource", "random")
    interaction_type = data["interactionType"] # Interaction type - eg. hover, click

    ensure_client(sid, pid, app_mode, app_type, app_level, pid_source)

    # record response to interaction
    response = {}
    response["sid"] = sid
    response["participant_id"] = pid
    response["app_mode"] = app_mode
    response["app_type"] = app_type
    response["app_level"] = app_level
    response["processed_at"] = bias_util.get_current_time()
    response["interaction_type"] = interaction_type
    response["input_data"] = data

    # check whether to compute bias metrics or not
    if interaction_type in COMPUTE_BIAS_FOR_TYPES:
        CLIENTS[pid]["bias_logs"].append(data)
        metrics = bias.compute_metrics(
            app_mode,
            CLIENTS[pid]["bias_logs"],
            dc_adapter.flatten_priors_for_bias(CLIENTS[pid]["priors"]),
        )

        # --- DC phase metrics: read the CACHED dc_map (never recompute here) ---
        # Present only once elicitation is complete; absent/None otherwise (not
        # an error). Ids come from the same bias_logs the JS metric consumes.
        dc_map_cached = CLIENTS[pid].get("dc_map")
        if dc_map_cached:
            dc_bias = dc_adapter.compute_phase_metrics(dc_map_cached, CLIENTS[pid]["bias_logs"])
            metrics["dc_bias"] = dc_bias
            print(f"[DC] pid={pid} | n_logs={len(CLIENTS[pid]['bias_logs'])} | "
                  f"real_time={dc_bias['real_time_bias']:+.4f} "
                  f"overall={dc_bias['overall_interaction_bias']:+.4f} "
                  f"selection={dc_bias['selection_bias']:+.4f} "
                  f"(interacted={dc_bias['n_interacted']}, selected={dc_bias['n_selected']})",
                  flush=True)
        else:
            metrics["dc_bias"] = None

        # --- Dwell-weighted bias: read the CACHED detailed map (parallel to
        # dc_bias, a SEPARATE top-level key). dwell_bias/dwell_bias_v raise
        # fail-loud on an unknown id; on_interaction has no outer try/except, so
        # an unguarded raise would drop the whole interaction response (lost data).
        # Guard ONLY here at the handler boundary -- the strict raise stays inside
        # dc_metric. On failure: log + None, same shape as the map-absent case.
        detailed = CLIENTS[pid].get("dc_map_detailed")
        if detailed:
            try:
                metrics["dwell_bias"] = dc_adapter.compute_dwell_metrics(
                    detailed, CLIENTS[pid]["bias_logs"])
            except Exception as e:
                print(f"[DWELL] compute failed: {e}", flush=True)
                metrics["dwell_bias"] = None
        else:
            metrics["dwell_bias"] = None

        response["output_data"] = metrics

        # === JS DEBUG (toggle via JS_DEBUG flag near top of file) === start
        # Read-only stream of the JS confirmation-bias scores already in
        # output_data, one block per qualifying interaction. Off by default;
        # flip JS_DEBUG to re-enable when building the next layer.
        if JS_DEBUG:
            _js_metric = metrics["js_divergence"][0]  # {attr: score} (drop details)
            _n = len(CLIENTS[pid]["bias_logs"])
            if _js_metric:
                _scores = "  ".join(f"{a}={s}" for a, s in _js_metric.items())
            else:
                _scores = "(other attrs: no prior)"
            print(f"[JS DEBUG] pid={pid} | n={_n} | {_scores}", flush=True)
        # === JS DEBUG === end
    else:
        response["output_data"] = None

    # save response
    CLIENTS[pid]["response_list"].append(response)

    await SIO.emit("log", response)  # send this to all
    await SIO.emit("interaction_response", response, room=sid)
    firebase_logger.save_logs(pid, [response])

    # --- LLM intervention (realtime versions only) ----------------------------
    # Runs AFTER the interaction response has already been emitted above, so the
    # ~4-6s Claude call is off the critical path. Fired as a background task that
    # emits a SEPARATE "llm_intervention" event on completion; never inlined into
    # output_data. Guarded: a broken LLM path must never break an interaction.
    _out = response.get("output_data")
    if app_type in LLM_REALTIME_TYPES and _out and _out.get("dwell_bias"):
        try:
            client_record = CLIENTS[pid]
            teens = bias.DATA_MAP.get(app_mode, {}).get("data", {})
            _dwell = _out["dwell_bias"]
            # One wall-clock instant for this whole evaluation: fed to the system-wide
            # display-pause gate inside evaluate_trigger AND (on fire) stamped as
            # llm_panel_open_since, so the pause opens at exactly the moment we decided
            # -- never two slightly different get_current_time() reads for what is meant
            # to be the same instant.
            now_ms = bias_util.get_current_time()
            fired, reason, trace = llm_trigger.evaluate_trigger(client_record, _dwell, now_ms)
            # Persist the FULL trigger trace on every evaluation. Deliberately not
            # throttled like the reason-change console print below -- a complete
            # trace is what trigger-policy analysis needs.
            firebase_logger.save_logs(pid, [{
                "kind": "dwell_trigger",
                "participant_id": pid,
                "created_at": now_ms,
                "fired": fired,
                "reason": reason,
                # Per-variable shape, matching the selection_trigger record below: the
                # winner plus the FULL breakdown, since dwell is no longer one pooled
                # score. excluded_vars marks variables dropped before null-sampling
                # (degenerate) so analysis can tell that from a below-threshold miss.
                "target_var": trace["target_var"],
                "target_percentile": trace["target_percentile"],
                "percentile_by_var": trace["percentile_by_var"],
                "excluded_vars": trace["excluded_vars"],
                "n_dwelled": trace["n_dwelled"],
                "total_dwell_seconds": trace["total_dwell_seconds"],
            }])
            if fired:
                # Close the system-wide gate HERE, at the decision, not when the panel
                # is emitted ~5s later: those 5s are still hovers, still evaluations,
                # and an emit-time pause would let a second intervention start
                # underneath the one being written. It reopens on dismiss, or below if
                # this generation never reaches the participant.
                llm_trigger.open_display_pause(client_record, now_ms)
                LLM_LAST_SKIP.pop(pid, None)
                print(f"[LLM] {pid}: triggered on {trace['target_var']} "
                      f"(pct={trace['target_percentile']}, "
                      f"n_dwelled={_dwell.get('n_dwelled')})", flush=True)
                SIO.start_background_task(
                    _fire_dwell_intervention, pid, client_record, _dwell, teens,
                    trace["target_var"])
            else:
                # Log only when the blocking gate CHANGES, so exploring shows
                # why nothing fired without a line on every interaction.
                code = reason.split(" ")[0]
                if LLM_LAST_SKIP.get(pid) != code:
                    LLM_LAST_SKIP[pid] = code
                    print(f"[LLM] {pid}: not triggered ({reason})", flush=True)
        except Exception as e:
            print(f"[LLM] trigger failed: {e}", flush=True)

    # --- LLM selection intervention (selection conditions) --------------------
    # Evaluated on every added pick rather than once at submit, so the feedback
    # arrives while there are still picks left to change. Fires the SAME panel the
    # realtime nudge uses; what is new here is the holding dialogue the frontend
    # raises on the pending event below, which keeps the participant from picking
    # two or three more teens during the ~10s generation.
    if (app_type in LLM_SUMMARY_TYPES
            and interaction_type == "click_add_item"
            and CLIENTS[pid].get("dc_map_detailed")):
        try:
            client_record = CLIENTS[pid]
            selected = dc_adapter.selected_ids(client_record["bias_logs"])
            selection = llm_intervention.selection_metrics(
                client_record["dc_map_detailed"], selected)
            result = llm_trigger.evaluate_selection_trigger(client_record, selected)
            fired = bool(result.get("fired"))
            firebase_logger.save_logs(pid, [{
                "kind": "selection_trigger",
                "participant_id": pid,
                "created_at": bias_util.get_current_time(),
                "fired": fired,
                "reason": result["reason"],
                "target_var": result.get("target_var"),
                "target_percentile": result.get("target_percentile"),
                "percentile_by_var": result.get("percentile_by_var"),
                # Variables dropped before null-sampling (flat, signal-free scope),
                # so analysis can tell them from a genuine below-threshold miss --
                # the same field the dwell_trigger record carries.
                "excluded_vars": result.get("excluded_vars"),
                "selection_bias": selection["selection_bias"],
                "selection_bias_v": selection["selection_bias_v"],
                "n_selected": selection["n_selected"],
            }])
            if fired:
                print(f"[LLM] {pid}: selection triggered on {result['target_var']} "
                      f"(pct={result['target_percentile']}, "
                      f"n_selected={selection['n_selected']})", flush=True)
                # Sent before the background task starts, so selection is closed
                # for the whole generation rather than from whenever it finishes.
                await SIO.emit(llm_intervention.SELECTION_PENDING_EVENT,
                               {"active": True}, room=sid)
                SIO.start_background_task(
                    llm_intervention.generate_selection_and_emit,
                    SIO, CLIENT_PARTICIPANT_ID_SOCKET_ID_MAPPING, pid,
                    client_record, selection,
                    bias.DATA_MAP.get(app_mode, {}).get("data", {}), selected,
                    result["target_var"])
            else:
                print(f"[LLM] {pid}: selection not triggered ({result['reason']})",
                      flush=True)
        except Exception as e:
            # Same handler-boundary guard as the dwell path: selection_metrics is
            # fail-loud, and a broken intervention must never break a pick.
            print(f"[LLM] selection trigger failed: {e}", flush=True)


if __name__ == "__main__":
    bias.precompute_distributions()
    port = int(os.environ.get("PORT", 3000))
    web.run_app(APP, port=port)
