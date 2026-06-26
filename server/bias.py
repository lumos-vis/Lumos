"""Handles bias computation logic.
"""
import ast
import csv
import json
import math
import numbers
import os
import random
import statistics

import scipy
import sys
from scipy.stats import chisquare
from scipy.stats import ks_2samp

import bias_util
from js_divergence import js_confirmation_score

NUM_QUANTILES = 4
MIN_LOG_NUM = 10
DATA_MAP = {
    "credit_risk.csv": {
        "attributes": [],
        "distribution": {},
        "numerical_attributes": [
            "Age",
            "Annual Income",
            "Employment Length",
            "Loan Amount",
            "Loan Interest Rate",
            "Credit History",
        ],
        "data": {},
    },
    "mental_health_data.csv": {
        "attributes": [],
        "distribution": {},
        "numerical_attributes": [
            "child_age_years",
            "screen_time_weekday",
            "hours_sleep_weeknight",
            "days_physical_activity_week",
        ],
        "data": {},
    },
    "cars.csv": {
        "attributes": [],
        "distribution": {},
        "numerical_attributes": [
            "Length",
            "Width",
            "Height",
            "Number of Forward Gears",
            "Torque",
            "Horsepower",
            "City mpg",
            "Highway mpg",
        ],
        "data": {},
    },
    "cars-w-year.csv": {
        "attributes": [],
        "distribution": {},
        "numerical_attributes": [
            "MPG",
            "Cylinders",
            "Displacement",
            "Horsepower",
            "Weight",
            "Acceleration",
            "Year",
        ],
        "data": {},
    },
    "movies-w-year.csv": {
        "attributes": [],
        "distribution": {},
        "numerical_attributes": [
            "Running Time",
            "Rotten Tomatoes Rating",
            "IMDB Rating",
            "Worldwide Gross",
            "Production Budget",
            "Release Year",
        ],
        "data": {},
    },
    "euro.csv": {
        "attributes": [],
        "distribution": {},
        "numerical_attributes": ["Age", "Salary", "Goals"],
        "data": {},
    },
    "housing.csv": {
        "attributes": [],
        "distribution": {},
        "numerical_attributes": [
            "Rooms",
            "Fireplaces",
            "Price",
            "Satisfaction",
            "Lot Area",
            "Year",
        ],
        "data": {},
    },
    "colleges.csv": {
        "attributes": [],
        "distribution": {},
        "numerical_attributes": [
            "Admission Rate",
            "ACT Median",
            "SAT Average",
            "Population",
            "Average Cost",
            "Expenditure",
            "Average Faculty Salary",
            "Median Debt",
            "Median Family Income",
            "Median Earnings",
        ],
        "data": {},
    },
}


def precompute_distributions():
    """Precompute the distributions of each attribute of the data."""
    print("**precomputing attribute distributions")
    for filename in DATA_MAP:
        read_data(filename)
        dataset = DATA_MAP[filename]
        for attr in dataset["attributes"]:
            if attr in dataset["numerical_attributes"]:
                # if it is numerical, make it a list of values, cast to num
                dataset["distribution"][attr] = [
                    bias_util.cast_to_num(dataset["data"][row_id][attr]) for row_id in dataset["data"]
                ]
                # Sort in ascending order
                dataset["distribution"][attr].sort()
            else:
                # if it is categorical, make it a dictionary with counts instead
                dataset["distribution"][attr] = {}
                for row_id in dataset["data"]:
                    # safe to cast to string
                    val = str(dataset["data"][row_id][attr])
                    if val not in dataset["distribution"][attr]:
                        dataset["distribution"][attr][val] = 0
                    dataset["distribution"][attr][val] += 1


def read_data(filename):
    """Read in the data file and save it."""
    dataset = DATA_MAP[filename]
    data = dataset["data"]
    with open(os.path.join("data", filename), encoding="utf-8") as csvfile:
        print(f"  reading data for {filename} ... ", end="", flush=True)
        reader = csv.DictReader(csvfile, delimiter=",", quotechar='"')
        dataset["attributes"] = reader.fieldnames
        for row in reader:
            data[row["id"]] = {}  # store data in data dict
            for attr in row:
                if attr in dataset["numerical_attributes"]:
                    data[row["id"]][attr] = bias_util.cast_to_num(row[attr])
                else:
                    data[row["id"]][attr] = str(row[attr])
        print(f"done")


def compute_metrics(filename, logs, priors=None):
    """Compute all of the bias metrics.

    Args:
        filename: dataset key into DATA_MAP, e.g. "credit_risk.csv".
        logs: the participant's bias-relevant interaction logs.
        priors: optional {attribute: PriorBelief} elicited via balls-into-bins.
            When absent/empty, the JS confirmation metric returns {} (priors are
            usually committed before any interaction, and not every attribute
            will have one).

    Return results in a dictionary mapping metric name to result.
    """
    dataset = DATA_MAP[filename]

    # dump log data to JSON file for debugging
    # with open('logs.json', 'w') as fp:
    #     json.dump(logs, fp)

    # Calculate the metrics one by one
    dpc = data_point_coverage(logs, dataset["data"])
    dpd = data_point_distribution(logs, dataset["data"])
    ac = attribute_coverage(logs, dataset["data"], dataset["attributes"], dataset["distribution"])
    ad = attribute_distribution(
        logs,
        dataset["data"],
        dataset["attributes"],
        dataset["distribution"],
        dataset["numerical_attributes"],
    )
    js = js_divergence(
        logs,
        dataset["data"],
        priors,
        dataset["numerical_attributes"],
    )

    # Prepare the payload and round metrics to 4 decimal places
    metrics = {
        "data_point_coverage": dpc,
        "data_point_distribution": dpd,
        "attribute_coverage": ac,
        "attribute_distribution": ad,
        "js_divergence": js,
    }

    return metrics


def data_point_coverage(logs, active_data):
    """Compute the data point coverage metric.

    Returns a tuple of
        (1) the overall metric value, and
        (2) a dictionary mapping data point id to 1 (if covered) or 0 (if not)

    Metric value set to 0 if # of logs < min_log_num to avoid volatility in
        initial bias values when few logs are present.
    """
    visited = set()
    log_counter = 0
    logs = bias_util.filter_out_agg_logs(logs)

    # iterate through the logs to populate data point coverage
    for log in logs:
        if "data" in log and "id" in log["data"]:
            if isinstance(log["data"]["id"], list):
                log_counter += len(log["data"]["id"])
                visited.update(log["data"]["id"])
            else:
                log_counter += 1
                visited.add(log["data"]["id"])
    visited_list = sorted(list(visited))

    # calculate dpc metric
    expected = bias_util.get_markov_expected_value(len(active_data), log_counter)
    percent_unique = len(visited) / expected
    if len(logs) < MIN_LOG_NUM:
        dpc_metric = 0
    else:
        dpc_metric = float(f"{1.0 - min(1, percent_unique):.4f}")

    # record meta data for dpc calculation
    dpc_details = {}
    dpc_details["N(dataset_size)"] = len(active_data)
    dpc_details["total_num_logs"] = len(logs)
    dpc_details["k(num_dp_logs)"] = log_counter
    dpc_details["covered"] = len(visited)
    dpc_details["visited"] = visited_list
    dpc_details["expected_unique"] = expected
    dpc_details["percent_unique"] = percent_unique

    return dpc_metric, dpc_details


def data_point_distribution(logs, active_data):
    """Compute the data point distribution metric.

    Returns a tuple of
        (1) the overall metric value, and
        (2) a dictionary mapping data point id to number of cumulative
            interactions

    Metric value set to 0 if # of logs < min_log_num to avoid volatility in
        initial bias values when few logs are present.
    """
    dpd_details = {}

    # initialize count of data items seen
    dpd_details["counts"] = {}
    for item in active_data:
        dpd_details["counts"][item] = 0

    # iterate through the logs to count distribution
    log_counter = 0
    # Comment out the below line IFF we want to
    #   filter out all aggregate interactions for
    #   this computation
    # logs = bias_util.filter_out_agg_logs(logs)
    for log in logs:
        if "data" in log and "id" in log["data"]:
            if isinstance(log["data"]["id"], list):
                agg_size = len(log["data"]["id"])
                # print(log["data"]["id"])
                for _id in log["data"]["id"]:
                    # increment by fractional aggregate value
                    log_counter += 1.0 / agg_size
                    # Comment out the below line IFF Interaction with a
                    #   group / aggregation (e.g., bar, line, dot) should not
                    #   be considered as an interaction with individual points.
                    #   No need to increment this counter.
                    dpd_details["counts"][_id] += 1.0 / agg_size
            else:
                log_counter += 1
                dpd_details["counts"][log["data"]["id"]] += 1

    # construct an array of expected values and an array of observed values
    expected = 1.0 * log_counter / len(active_data)
    exp_arr = [expected for _ in range(len(active_data))]
    obs_arr = [dpd_details["counts"][item] for item in active_data]

    # compute chi square result and dpd metric
    chi_squared_result = chisquare(obs_arr, f_exp=exp_arr)
    if len(logs) < MIN_LOG_NUM:
        dpd_metric = 0
    else:
        dpd_metric = float(f"{1 - chi_squared_result[1]:.4f}")

    # record meta data for dpd calculation
    dpd_details["total_num_logs"] = len(logs)
    dpd_details["k(num_dp_logs)"] = log_counter
    dpd_details["expected_per_dp"] = expected
    dpd_details["degrees_of_freedom"] = len(active_data) - 1
    dpd_details["chi_squared"] = chi_squared_result[0]
    if str(chi_squared_result[1]) == "nan":
        dpd_details["p_value"] = None
    else:
        dpd_details["p_value"] = chi_squared_result[1]

    return dpd_metric, dpd_details


def attribute_coverage(logs, active_data, active_attrs, active_attr_distr):
    """Compute the attribute coverage metric for each attribute.

    Returns a tuple of
        (1) dictionary mapping attribute name to [0, 1] metric value, and
        (2) dictionary mapping attribute name to the quantized coverage of the
            attribute

    Metric value set to 0 if # of logs < min_log_num to avoid volatility in
        initial bias values when few logs are present.
    """
    ac_metric = {}
    ac_details = {}

    # calculate ac for EACH attribute
    for attr in active_attrs:
        ac_details[attr] = {}

        # get attribute distribution and quantization
        quantiles = bias_util.get_quantization(active_attr_distr[attr], NUM_QUANTILES)
        ac_details[attr]["quantiles"] = quantiles

        # calculate coverage
        ac_details[attr]["coverage"] = {}
        for q in quantiles:
            ac_details[attr]["coverage"][q] = 0  # initialize to 0

        # iterate through the logs to populate quantile coverage
        log_counter = 0
        for log in logs:
            if "data" in log and "id" in log["data"]:
                if isinstance(log["data"]["id"], list):  # aggregate interaction

                    # for actively visualized x- and y- attribute axes, we already have the list of attributes in the log
                    log_counter += 1
                    if log["data"]["x"]["name"] == attr:
                        val_list = log["data"]["x"]["value"]
                    elif log["data"]["y"]["name"] == attr:
                        val_list = log["data"]["y"]["value"]
                    else:  # need to create the list of values
                        val_list = []
                        for pid in log["data"]["id"]:
                            val_list.append(active_data[pid][attr])

                    if attr in DATA_MAP[log["appMode"]]["numerical_attributes"]:
                        # take the median value
                        val = statistics.median(val_list)
                    else:
                        try:
                            # use the most common categorical value
                            val = statistics.mode(val_list)
                        # thrown in the event of all equal values (and no unique mode)
                        except statistics.StatisticsError:
                            # just take the first element on the list then
                            val = val_list[0]
                    which_quantile = bias_util.which_quantile(quantiles, val)
                    ac_details[attr]["coverage"][which_quantile] = 1

                else:
                    log_counter += 1
                    pid = log["data"]["id"]
                    dp = active_data[pid]
                    which_quantile = bias_util.which_quantile(quantiles, dp[attr])
                    ac_details[attr]["coverage"][which_quantile] = 1

        # calculate ac metric
        covered = 0
        for q in quantiles:
            # count how many quantiles were covered and compare to expected
            if ac_details[attr]["coverage"][q] == 1:
                covered += 1
        expected = bias_util.get_markov_expected_value(len(quantiles), log_counter)
        if expected == 0:  # prevent divide by 0 error
            # if expected value is 0 (no relevant logs), then set metric value to 0
            percent_unique = 1
        else:
            percent_unique = covered / expected
        if len(logs) < MIN_LOG_NUM:
            ac_metric[attr] = 0
        else:
            ac_metric[attr] = float(f"{1.0 - min(1, percent_unique):.4f}")

        # record meta data for ac calculation
        ac_details[attr]["N(num_quantiles)"] = len(quantiles)
        ac_details[attr]["total_num_logs"] = len(logs)
        ac_details[attr]["k(num_dp_logs)"] = log_counter
        ac_details[attr]["expected_unique"] = expected
        ac_details[attr]["covered"] = covered
        ac_details[attr]["percent_unique"] = percent_unique

    return ac_metric, ac_details


def attribute_distribution(logs, active_data, active_attrs, active_attr_distr, active_numerical_attrs):
    """Compute the attribute distribution metric for each attribute.

    Returns a tuple of
        (1) dictionary mapping attribute name to [0, 1] metric value, and
        (2) dictionary mapping attribute name to the quantized distribution of
            the attribute

    Metric value set to 0 if # of logs < min_log_num to avoid volatility in
        initial bias values when few logs are present.
    """
    ad_metric = {}
    ad_details = {}

    # calculate ad for EACH attribute
    for attr in active_attrs:
        a_distr = active_attr_distr[attr]
        ad_details[attr] = {}
        ad_details[attr]["baseline_distr"] = a_distr
        ad_details[attr]["total_num_logs"] = len(logs)

        try:
            # numerical attribute -- k-s test
            active_numerical_attrs.index(attr)
            baseline_weights = [1.0] * len(a_distr)
            user_distr = []
            user_weights = []
            log_counter = 0
            for log in logs:
                if "data" in log and "id" in log["data"]:

                    if isinstance(log["data"]["id"], list):  # aggregate interaction
                        agg_size = len(log["data"]["id"])

                        for pid in log["data"]["id"]:
                            val = active_data[pid][attr]
                            log_counter += 1.0 / agg_size  # increment by fractional value
                            user_distr.append(val)
                            user_weights.append(1.0 / agg_size)

                    else:
                        log_counter += 1
                        pid = log["data"]["id"]
                        dp = active_data[pid]
                        user_distr.append(dp[attr])
                        user_weights.append(1.0)

            # calculate ad metric
            ks_stat = bias_util.ks_w2(a_distr, user_distr, baseline_weights, user_weights)
            if len(logs) < MIN_LOG_NUM:
                ad_metric[attr] = 0
            else:
                ad_metric[attr] = float(f"{ks_stat:.4f}")

            # record meta data for ad calculation
            ad_details[attr]["interaction_distr"] = user_distr
            ad_details[attr]["baseline_weights"] = baseline_weights
            ad_details[attr]["user_distr_weights"] = user_weights
            ad_details[attr]["ks_stat"] = ks_stat
            ad_details[attr]["p_value"] = "TODO"  # TODO

        except ValueError:
            # categorical attribute -- chi-square test
            user_distr = {}
            user_distr_flat = []
            log_counter = 0
            for log in logs:
                if "data" in log and "id" in log["data"]:
                    if isinstance(log["data"]["id"], list):  # aggregate interaction
                        agg_size = len(log["data"]["id"])

                        for pid in log["data"]["id"]:
                            val = active_data[pid][attr]
                            log_counter += 1.0 / agg_size  # increment by fractional value
                            if val in user_distr:
                                # increment by fractional value
                                user_distr[val] += 1.0 / agg_size
                            else:
                                # increment by fractional value
                                user_distr[val] = 1.0 / agg_size

                    else:
                        log_counter += 1
                        pid = log["data"]["id"]
                        dp = active_data[pid]
                        user_distr_flat.append(dp[attr])
                        if dp[attr] in user_distr:
                            user_distr[dp[attr]] += 1
                        else:
                            user_distr[dp[attr]] = 1

            # compute expected values arrays
            exp_arr, obs_arr = [], []
            for key in a_distr:
                exp_arr.append(1.0 * a_distr[key] / len(active_data) * log_counter)
                try:
                    obs_arr.append(user_distr[key])
                except KeyError:
                    obs_arr.append(0)

            # compute chi square result and ad metric
            chi_squared_result = chisquare(obs_arr, f_exp=exp_arr)
            if len(logs) < MIN_LOG_NUM:
                ad_metric[attr] = 0
            else:
                ad_metric[attr] = float(f"{1 - chi_squared_result[1]:.4f}")

            # record meta data for ad calculation
            try:
                user_distr_flat = sorted([float(i) for i in user_distr_flat])
            except Exception as e:
                user_distr_flat = sorted([str(i) for i in user_distr_flat])
            ad_details[attr]["interaction_distr"] = user_distr_flat
            ad_details[attr]["interaction_distr_dict"] = user_distr
            ad_details[attr]["k(num_dp_logs)"] = log_counter
            ad_details[attr]["chi_squared"] = chi_squared_result[0]
            if str(chi_squared_result[1]) == "nan":
                ad_details[attr]["p_value"] = None
            else:
                ad_details[attr]["p_value"] = chi_squared_result[1]

    return ad_metric, ad_details


def _bucket_numerical(bin_edges, val):
    """Return the index of the bin (defined by bin_edges) that val falls in.

    bin_edges has len == num_bins + 1. Values outside the elicited range are
    clamped into the nearest edge bin rather than dropped, so out-of-range
    interactions still contribute to the interaction distribution.
    """
    num_bins = len(bin_edges) - 1
    if val <= bin_edges[0]:
        return 0
    if val >= bin_edges[-1]:
        return num_bins - 1
    for i in range(num_bins):
        # left-closed, right-open except the final bin which is closed above
        if bin_edges[i] <= val < bin_edges[i + 1]:
            return i
    return num_bins - 1  # numeric safety net


def js_divergence(logs, active_data, priors, numerical_attrs):
    """Compute the JS confirmation-bias metric for each attribute with a prior.

    Sibling of attribute_distribution, but it compares the user's interactions
    against their *elicited prior* (not the underlying data). For each attribute
    that has a prior it buckets every interacted point's value -- resolved by id
    via active_data[id][attr], ignoring the payload's on-screen x/y -- into the
    SAME bins the prior was elicited over, then calls the standalone
    js_confirmation_score().

    Bucketing:
        numerical   -> assign cast_to_num(value) into prior["binEdges"],
                       clamping out-of-range values into the nearest edge bin.
        categorical -> match str(value) against prior["categories"]; categories
                       that were never interacted with stay at zero, and
                       interacted values not in the prior's categories are
                       dropped (the prior defines the bin set on both sides).
    Aggregate interactions contribute each member id with weight 1/len(ids).

    Returns a tuple of
        (1) dictionary mapping attribute name to [0, 1] score (small JS distance
            => high confirmation bias => high score), and
        (2) dictionary mapping attribute name to per-attribute detail.

    Returns ({}, {}) when no priors are present (the usual case before the user
    has committed any beliefs, mirroring the defensive posture elsewhere).
    """
    js_metric = {}
    js_details = {}

    if not priors:
        return js_metric, js_details

    for attr, belief in priors.items():
        is_categorical = "categories" in belief and belief["categories"] is not None
        categories = belief.get("categories") or []
        bin_edges = belief.get("binEdges") or []
        prior_counts = belief.get("counts") or []

        # number of bins is defined by the prior
        num_bins = len(categories) if is_categorical else max(len(bin_edges) - 1, 0)
        if num_bins == 0 or len(prior_counts) != num_bins:
            # malformed prior -- skip rather than emit a misaligned score
            continue

        # category label -> bin index, for categorical matching
        cat_index = {str(c): i for i, c in enumerate(categories)} if is_categorical else {}

        interaction_counts = [0.0] * num_bins
        log_counter = 0.0
        for log in logs:
            if "data" not in log or "id" not in log["data"]:
                continue
            ids = log["data"]["id"]
            if isinstance(ids, list):  # aggregate interaction
                if not ids:
                    continue
                weight = 1.0 / len(ids)
                id_list = ids
            else:
                weight = 1.0
                id_list = [ids]

            for pid in id_list:
                if pid not in active_data or attr not in active_data[pid]:
                    continue
                raw = active_data[pid][attr]
                if is_categorical:
                    idx = cat_index.get(str(raw))
                    if idx is None:
                        # interacted value isn't a bin the prior defined -> drop
                        continue
                else:
                    idx = _bucket_numerical(bin_edges, bias_util.cast_to_num(raw))
                interaction_counts[idx] += weight
                log_counter += weight

        score = js_confirmation_score(prior_counts, interaction_counts)
        js_metric[attr] = float(f"{score:.4f}")

        js_details[attr] = {
            "is_categorical": is_categorical,
            "bins": categories if is_categorical else bin_edges,
            "prior_counts": prior_counts,
            "interaction_counts": interaction_counts,
            "k(num_dp_logs)": log_counter,
        }

    return js_metric, js_details
