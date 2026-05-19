#!/usr/bin/env python3

import argparse
import csv
import math
from collections import defaultdict


def _to_float(value):
    """Convert a CSV value to float, treating empty and NA values as NaN."""
    try:
        if value in ("", "NA", "NaN", "nan", None):
            return math.nan
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _read_numeric_rows(path):
    """Read x/y/value triples from a headered or headerless CSV file."""
    with open(path, newline="") as src:
        rows = list(csv.reader(src))
    if not rows:
        return []

    def row_is_numeric(row):
        """Return whether a CSV row starts with numeric x/y/value fields."""
        return len(row) >= 3 and all(not math.isnan(_to_float(value)) for value in row[:3])

    if row_is_numeric(rows[0]):
        data_rows = rows
    else:
        data_rows = rows[1:]

    values = []
    for row in data_rows:
        if len(row) < 3:
            continue
        x, y, z = (_to_float(row[0]), _to_float(row[1]), _to_float(row[2]))
        if math.isnan(x) or math.isnan(y) or math.isnan(z):
            continue
        values.append((x, y, z))
    return values


def _infer_grid(predictions):
    """Infer a regular prediction grid from x/y/value CSV rows."""
    xs = sorted({x for x, _y, _z in predictions})
    ys = sorted({y for _x, y, _z in predictions}, reverse=True)
    if len(xs) < 1 or len(ys) < 1:
        raise ValueError("Prediction CSV does not contain enough points to infer a grid.")

    x_index = {x: i for i, x in enumerate(xs)}
    y_index = {y: i for i, y in enumerate(ys)}
    grid = [[math.nan for _ in xs] for _ in ys]
    for x, y, z in predictions:
        grid[y_index[y]][x_index[x]] = z

    x_res = min((xs[i + 1] - xs[i] for i in range(len(xs) - 1)), default=1.0)
    y_step = min((ys[i] - ys[i + 1] for i in range(len(ys) - 1)), default=1.0)
    return xs, ys, x_res, y_step, grid


def _sample_grid(x, y, xs, ys, x_res, y_step, grid):
    """Sample an inferred prediction grid at a point coordinate."""
    origin_x = xs[0] - x_res / 2
    origin_y = ys[0] + y_step / 2
    col = int((x - origin_x) / x_res)
    row = int((origin_y - y) / y_step)
    if row < 0 or col < 0 or row >= len(grid) or col >= len(xs):
        return math.nan
    return grid[row][col]


def _r2_and_rmse(pairs):
    """Compute squared correlation and RMSE for observed/predicted pairs."""
    n = len(pairs)
    if n == 0:
        return math.nan, math.nan
    obs = [old for old, _new in pairs]
    pred = [new for _old, new in pairs]
    mean_obs = sum(obs) / n
    mean_pred = sum(pred) / n
    cov = sum((o - mean_obs) * (p - mean_pred) for o, p in pairs)
    var_obs = sum((o - mean_obs) ** 2 for o in obs)
    var_pred = sum((p - mean_pred) ** 2 for p in pred)
    if var_obs == 0 or var_pred == 0:
        r2 = math.nan
    else:
        r2 = (cov / math.sqrt(var_obs * var_pred)) ** 2
    rmse = math.sqrt(sum((p - o) ** 2 for o, p in pairs) / n)
    return r2, rmse


def obs_vs_pred(obs_csv, pred_csv, r2_out, rmse_out):
    """Compare observed points to gridded prediction CSV values."""
    observations = _read_numeric_rows(obs_csv)
    predictions = _read_numeric_rows(pred_csv)
    xs, ys, x_res, y_step, grid = _infer_grid(predictions)

    pairs = []
    for x, y, observed in observations:
        predicted = _sample_grid(x, y, xs, ys, x_res, y_step, grid)
        if not math.isnan(predicted):
            pairs.append((observed, predicted))

    r2, rmse = _r2_and_rmse(pairs)
    with open(r2_out, "a") as dst:
        dst.write(f"{r2},{pred_csv}\n")
    with open(rmse_out, "a") as dst:
        dst.write(f"{rmse},{pred_csv}\n")


def parse_args():
    """Parse command-line arguments for this script."""
    parser = argparse.ArgumentParser(description="Compute R2 and RMSE by sampling prediction grid CSV at observed coordinates.")
    parser.add_argument("obs_csv")
    parser.add_argument("pred_csv")
    parser.add_argument("r2_out")
    parser.add_argument("rmse_out")
    return parser.parse_args()


def main():
    """Run the command-line entry point."""
    args = parse_args()
    obs_vs_pred(args.obs_csv, args.pred_csv, args.r2_out, args.rmse_out)


if __name__ == "__main__":
    main()
