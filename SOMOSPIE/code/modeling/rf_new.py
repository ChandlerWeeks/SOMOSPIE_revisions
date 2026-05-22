#!/usr/bin/env python3

"""Random forest model with all-point cross-validation reporting."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import r2_score, root_mean_squared_error
from sklearn.model_selection import KFold, RandomizedSearchCV, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def get_parser():
    """Parse Random Forest model arguments."""
    parser = argparse.ArgumentParser(description="Random forest with out-of-fold CV metrics and final full-data refit.")
    parser.add_argument("-t", "--trainingdata", required=True, help="Training CSV with x,y,z and covariates.")
    parser.add_argument("-e", "--evaluationdata", required=True, help="Evaluation CSV with x,y and covariates.")
    parser.add_argument("-o", "--outputdata", required=True, help="Output prediction CSV.")
    parser.add_argument("-l", "--log", help="Optional log file path; accepted for compatibility.")
    parser.add_argument("-maxtree", "--maxtree", type=int, default=300, help="Maximum number of trees to search. Default: 300.")
    parser.add_argument("-seed", "--seed", type=int, default=42, help="Random seed. Default: 42.")
    parser.add_argument("--cv", type=int, default=10, help="Outer CV fold count. Default: 10.")
    parser.add_argument("--inner-cv", type=int, help="Inner CV fold count for nested CV. Default: same as --cv.")
    parser.add_argument("--n-iter", type=int, default=10, help="Randomized search candidate count. Default: 10.")
    parser.add_argument("--n-jobs", type=int, default=-1, help="Parallel jobs for CV/search/final RF. Default: -1.")
    parser.add_argument(
        "--validation-mode",
        choices=["nested", "oof"],
        default="nested",
        help=(
            "nested runs hyperparameter search inside each outer fold for less biased CV metrics; "
            "oof searches once, then computes out-of-fold predictions with the selected parameters. Default: nested."
        ),
    )
    parser.add_argument(
        "--prediction-chunk-size",
        type=int,
        default=500000,
        help="Rows per chunk when predicting the evaluation CSV. Default: 500000.",
    )
    parser.add_argument("--metrics-output", help="Metrics CSV path. Defaults beside --outputdata.")
    parser.add_argument("--cv-output", help="Out-of-fold prediction CSV path. Defaults beside --outputdata.")
    parser.add_argument("--params-output", help="Best-parameter JSON path. Defaults beside --outputdata.")
    return parser


def _default_sidecar(output_data, suffix):
    """Build a sidecar output path next to the prediction CSV."""
    output_path = Path(output_data)
    return output_path.with_name(f"{output_path.stem}{suffix}")


def _read_training(path):
    """Read and validate model training data."""
    training = pd.read_csv(path)
    if training.shape[1] < 4:
        raise ValueError(f"Training data must contain x, y, z, and at least one covariate: {path}")

    columns = list(training.columns)
    if "z" not in columns:
        columns[2] = "z"
        training.columns = columns

    required = ["x", "y", "z"]
    missing = [column for column in required if column not in training.columns]
    if missing:
        raise ValueError(f"Training data missing required columns: {missing}")

    training = training.apply(pd.to_numeric, errors="coerce").dropna()
    if training.empty:
        raise ValueError(f"No complete numeric training rows found in {path}")
    if len(training) < 3:
        raise ValueError(
            f"Only {len(training)} complete training rows found in {path}. "
            "This is too few for cross-validation; inspect missing covariates or lower/drop sparse covariates upstream."
        )
    return training


def _feature_columns(training):
    """Return model feature columns, preserving the existing SOMOSPIE x/y behavior."""
    return [column for column in training.columns if column != "z"]


def _fold_count(requested, n_rows, label):
    """Clamp fold count to a valid range."""
    requested = int(requested)
    if requested < 2:
        raise ValueError(f"{label} must be at least 2.")
    if n_rows < 2:
        raise ValueError("At least two training rows are required for cross-validation.")
    return min(requested, n_rows)


def _tree_candidates(maxtree):
    """Return unique n_estimators candidates."""
    maxtree = int(maxtree)
    if maxtree < 1:
        raise ValueError("--maxtree must be positive.")
    start = min(300, maxtree)
    return sorted({int(value) for value in np.linspace(start=start, stop=maxtree, num=20)})


def _param_distributions(maxtree):
    """Build the random forest search space."""
    return {
        "rf__n_estimators": _tree_candidates(maxtree),
        "rf__max_features": ["sqrt"],
        "rf__max_depth": [20, 50, 70, None],
        "rf__bootstrap": [True],
    }


def _pipeline(seed, rf_n_jobs, params=None):
    """Build a preprocessing/model pipeline."""
    rf = RandomForestRegressor(random_state=seed, n_jobs=rf_n_jobs)
    pipe = Pipeline([("scale", StandardScaler()), ("rf", rf)])
    if params:
        pipe.set_params(**params)
    return pipe


def _make_cv(n_splits, seed):
    """Build a reproducible KFold splitter."""
    return KFold(n_splits=n_splits, shuffle=True, random_state=seed)


def _search(X, y, args, cv, rf_n_jobs=1):
    """Run randomized hyperparameter search."""
    param_distributions = _param_distributions(args.maxtree)
    max_candidates = np.prod([len(values) for values in param_distributions.values()])
    n_iter = min(int(args.n_iter), int(max_candidates))
    search = RandomizedSearchCV(
        estimator=_pipeline(args.seed, rf_n_jobs),
        param_distributions=param_distributions,
        n_iter=n_iter,
        scoring="neg_root_mean_squared_error",
        cv=cv,
        random_state=args.seed,
        n_jobs=args.n_jobs,
        verbose=1,
        refit=True,
    )
    search.fit(X, y)
    return search


def _nested_cv_predictions(X, y, args, outer_cv, inner_splits):
    """Generate out-of-fold predictions using nested CV."""
    predictions = np.empty(len(y), dtype=float)
    best_params_by_fold = []

    for fold_index, (train_index, test_index) in enumerate(outer_cv.split(X, y), start=1):
        X_train, y_train = X.iloc[train_index], y.iloc[train_index]
        X_test = X.iloc[test_index]
        if len(train_index) < 2:
            raise ValueError(
                f"Outer fold {fold_index} has only {len(train_index)} training row(s). "
                "Use more complete training rows or switch RF_NEW_VALIDATION_MODE=oof for very small test runs."
            )
        inner_cv = _make_cv(_fold_count(inner_splits, len(train_index), "inner CV folds"), args.seed + fold_index)
        print(f"Nested CV fold {fold_index}/{outer_cv.get_n_splits()}: tuning on {len(train_index)} rows")
        search = _search(X_train, y_train, args, inner_cv, rf_n_jobs=1)
        predictions[test_index] = search.predict(X_test)
        best_params_by_fold.append(search.best_params_)

    return predictions, best_params_by_fold


def _oof_predictions_with_best_params(X, y, args, outer_cv, best_params):
    """Generate out-of-fold predictions with a fixed selected parameter set."""
    estimator = _pipeline(args.seed, rf_n_jobs=1, params=best_params)
    return cross_val_predict(estimator, X, y, cv=outer_cv, n_jobs=args.n_jobs)


def _write_cv_outputs(training, predictions, metrics, cv_output, metrics_output):
    """Write out-of-fold predictions and metrics."""
    cv_df = pd.DataFrame(
        {
            "x": training["x"].round(decimals=9),
            "y": training["y"].round(decimals=9),
            "observed": training["z"],
            "predicted": predictions,
        }
    )
    cv_df["residual"] = cv_df["observed"] - cv_df["predicted"]
    cv_df.to_csv(cv_output, index=False)
    pd.DataFrame([metrics]).to_csv(metrics_output, index=False)


def _write_params(params_output, final_search, nested_fold_params=None):
    """Write selected parameters to JSON."""
    payload = {
        "best_score_neg_rmse": float(final_search.best_score_),
        "best_params": final_search.best_params_,
    }
    if nested_fold_params is not None:
        payload["nested_fold_best_params"] = nested_fold_params
    Path(params_output).write_text(json.dumps(payload, indent=2) + "\n")


def _predict_evaluation_chunks(evaluation_path, output_path, estimator, feature_columns, chunk_size):
    """Predict the high-resolution evaluation CSV without loading it all at once."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    total_rows = 0
    for chunk_index, chunk in enumerate(pd.read_csv(evaluation_path, chunksize=chunk_size), start=1):
        missing = [column for column in feature_columns if column not in chunk.columns]
        if missing:
            raise ValueError(f"Evaluation data missing feature columns: {missing}")

        features = chunk[feature_columns].apply(pd.to_numeric, errors="coerce")
        valid = features.notna().all(axis=1)
        chunk = chunk.loc[valid].copy()
        features = features.loc[valid]
        if chunk.empty:
            continue

        predictions = estimator.predict(features)
        out_df = pd.DataFrame(
            {
                "x": pd.to_numeric(chunk["x"], errors="coerce").round(decimals=9),
                "y": pd.to_numeric(chunk["y"], errors="coerce").round(decimals=9),
                "sm": predictions,
            }
        )
        out_df.to_csv(output_path, mode="a", index=False, header=False)
        total_rows += len(out_df)
        print(f"Predicted chunk {chunk_index}: {len(out_df)} rows; total {total_rows}")

    if total_rows == 0:
        raise ValueError(f"No valid evaluation rows found in {evaluation_path}")


def main():
    """Run model training, cross-validation reporting, and high-resolution prediction."""
    parser = get_parser()
    args = parser.parse_args()

    output_data = Path(args.outputdata)
    metrics_output = Path(args.metrics_output) if args.metrics_output else _default_sidecar(output_data, "_metrics.csv")
    cv_output = Path(args.cv_output) if args.cv_output else _default_sidecar(output_data, "_cv_predictions.csv")
    params_output = Path(args.params_output) if args.params_output else _default_sidecar(output_data, "_best_params.json")

    print(f"Reading training data from {args.trainingdata}")
    training = _read_training(args.trainingdata)
    feature_columns = _feature_columns(training)
    X = training[feature_columns]
    y = training["z"]

    outer_splits = _fold_count(args.cv, len(training), "CV folds")
    inner_splits = args.inner_cv if args.inner_cv is not None else outer_splits
    outer_cv = _make_cv(outer_splits, args.seed)

    if args.validation_mode == "nested":
        print(f"Running nested {outer_splits}-fold CV with inner {inner_splits}-fold hyperparameter search")
        cv_predictions, nested_fold_params = _nested_cv_predictions(X, y, args, outer_cv, inner_splits)
    else:
        print(f"Running full-data hyperparameter search before {outer_splits}-fold out-of-fold prediction")
        search = _search(X, y, args, outer_cv, rf_n_jobs=1)
        cv_predictions = _oof_predictions_with_best_params(X, y, args, outer_cv, search.best_params_)
        nested_fold_params = None

    rmse = root_mean_squared_error(y, cv_predictions)
    r2 = r2_score(y, cv_predictions)
    metrics = {
        "model": "rf_new",
        "validation_mode": args.validation_mode,
        "rows": len(training),
        "cv_folds": outer_splits,
        "inner_cv_folds": inner_splits if args.validation_mode == "nested" else "",
        "rmse": rmse,
        "r2": r2,
    }
    print(f"Cross-validated RMSE: {rmse}")
    print(f"Cross-validated R2: {r2}")
    _write_cv_outputs(training, cv_predictions, metrics, cv_output, metrics_output)
    print(f"Wrote CV predictions to {cv_output}")
    print(f"Wrote metrics to {metrics_output}")

    print("Fitting final hyperparameter search on all training rows")
    final_search = _search(X, y, args, outer_cv, rf_n_jobs=1)
    best_params = final_search.best_params_
    final_model = _pipeline(args.seed, rf_n_jobs=args.n_jobs, params=best_params)
    final_model.fit(X, y)
    _write_params(params_output, final_search, nested_fold_params)
    print(f"Wrote best parameters to {params_output}")
    print(f"Best full-data search RMSE: {-final_search.best_score_}")
    print(f"Best full-data search params: {best_params}")

    print(f"Reading evaluation data from {args.evaluationdata}")
    print(f"Writing predictions to {output_data}")
    _predict_evaluation_chunks(args.evaluationdata, output_data, final_model, feature_columns, args.prediction_chunk_size)


if __name__ == "__main__":
    main()
