#!/usr/bin/env bash
set -Eeuo pipefail

# Python/GDAL SOMOSPIE demo pipeline.
# Defaults: Alabama, June 2019, terrain inputs under
# /media/volume/aitechx_vol1/terrain_parameters/AL.
#
# Usage:
#   ./run_pipeline.sh [STATE] [YEAR] [MONTH]
#
# Examples:
#   ./run_pipeline.sh
#   ./run_pipeline.sh AL 2019 6
#   RUN_MODEL=1 MODEL=rf ./run_pipeline.sh AL 2019 6
#   RUN_MODEL=1 MODEL=rf_new ./run_pipeline.sh AL 2019 6
#   VISUALIZE=1 RUN_MODEL=0 MODEL=rf ./run_pipeline.sh AL 2019 6

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREPROC_DIR="$SCRIPT_DIR/code/preprocessing"
FETCH_DIR="$SCRIPT_DIR/data_readers/satellite"
MODEL_DIR="$SCRIPT_DIR/code/modeling"
VIS_DIR="$SCRIPT_DIR/code/visualization"

STATE="${1:-${STATE:-AL}}"
STATE="${STATE^^}"
YEAR="${2:-${YEAR:-2019}}"
MONTH="${3:-${MONTH:-6}}"
MONTH_PAD="$(printf '%02d' "$MONTH")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
DATA_ROOT="${SOMOSPIE_DATA_ROOT:-/media/volume/aitechx_vol1}"
TERRAIN_ROOT="${TERRAIN_ROOT:-$DATA_ROOT/terrain_parameters}"
TERRAIN_DIR="${TERRAIN_DIR:-$TERRAIN_ROOT/$STATE}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$DATA_ROOT/SOMOSPIE_demo}"
RUN_DIR="${RUN_DIR:-$OUTPUT_ROOT/${STATE}_${YEAR}_${MONTH_PAD}}"
ESA_DIR="$RUN_DIR/ESA_CCI"
SHAPE_DIR="$RUN_DIR/shapes"
COARSE_DIR="$RUN_DIR/terrain_coarse"
REPROJECTED_DIR="$RUN_DIR/terrain_wgs84"
STACK_DIR="$RUN_DIR/stacks"
TRAIN_DIR="$RUN_DIR/training"
EVAL_DIR="$RUN_DIR/evaluation"
PRED_DIR="$RUN_DIR/predictions"
PLOT_DIR="$RUN_DIR/visualization"
STATE_BOUNDARY_DIR="$RUN_DIR/state_boundaries"

STATE_BOUNDARY_URL="${STATE_BOUNDARY_URL:-https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_state_500k.zip}"
COARSIFY_FACTOR="${COARSIFY_FACTOR:-1}"
TERRAIN_GLOB="${TERRAIN_GLOB:-*.tif}"
FORCE="${FORCE:-0}"
ALLOW_MISMATCH="${ALLOW_MISMATCH:-0}"
RUN_MODEL="${RUN_MODEL:-0}"
VISUALIZE="${VISUALIZE:-$RUN_MODEL}"
MODEL="${MODEL:-rf}"
MODEL_SEED="${MODEL_SEED:-42}"
RF_MAXTREE="${RF_MAXTREE:-300}"
KNN_MAXK="${KNN_MAXK:-20}"
RF_NEW_CV="${RF_NEW_CV:-10}"
RF_NEW_INNER_CV="${RF_NEW_INNER_CV:-$RF_NEW_CV}"
RF_NEW_N_ITER="${RF_NEW_N_ITER:-10}"
RF_NEW_N_JOBS="${RF_NEW_N_JOBS:--1}"
RF_NEW_VALIDATION_MODE="${RF_NEW_VALIDATION_MODE:-nested}"
PREDICTION_CHUNK_SIZE="${PREDICTION_CHUNK_SIZE:-500000}"
MIN_COVARIATE_COVERAGE="${MIN_COVARIATE_COVERAGE:-0.8}"
STACK_FORMAT="${STACK_FORMAT:-VRT}"
PLOT_CMAP="${PLOT_CMAP:-RdBu}"
PLOT_DPI="${PLOT_DPI:-180}"
PLOT_RENDER_MODE="${PLOT_RENDER_MODE:-auto}"
PLOT_POINT_SIZE="${PLOT_POINT_SIZE:-0.2}"

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2
}

die() {
    log "ERROR: $*" >&2
    exit 1
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

state_name() {
    case "$1" in
        AL) echo "Alabama" ;;
        AK) echo "Alaska" ;;
        AZ) echo "Arizona" ;;
        AR) echo "Arkansas" ;;
        CA) echo "California" ;;
        CO) echo "Colorado" ;;
        CT) echo "Connecticut" ;;
        DE) echo "Delaware" ;;
        DC) echo "District of Columbia" ;;
        FL) echo "Florida" ;;
        GA) echo "Georgia" ;;
        HI) echo "Hawaii" ;;
        ID) echo "Idaho" ;;
        IL) echo "Illinois" ;;
        IN) echo "Indiana" ;;
        IA) echo "Iowa" ;;
        KS) echo "Kansas" ;;
        KY) echo "Kentucky" ;;
        LA) echo "Louisiana" ;;
        ME) echo "Maine" ;;
        MD) echo "Maryland" ;;
        MA) echo "Massachusetts" ;;
        MI) echo "Michigan" ;;
        MN) echo "Minnesota" ;;
        MS) echo "Mississippi" ;;
        MO) echo "Missouri" ;;
        MT) echo "Montana" ;;
        NE) echo "Nebraska" ;;
        NV) echo "Nevada" ;;
        NH) echo "New Hampshire" ;;
        NJ) echo "New Jersey" ;;
        NM) echo "New Mexico" ;;
        NY) echo "New York" ;;
        NC) echo "North Carolina" ;;
        ND) echo "North Dakota" ;;
        OH) echo "Ohio" ;;
        OK) echo "Oklahoma" ;;
        OR) echo "Oregon" ;;
        PA) echo "Pennsylvania" ;;
        RI) echo "Rhode Island" ;;
        SC) echo "South Carolina" ;;
        SD) echo "South Dakota" ;;
        TN) echo "Tennessee" ;;
        TX) echo "Texas" ;;
        UT) echo "Utah" ;;
        VT) echo "Vermont" ;;
        VA) echo "Virginia" ;;
        WA) echo "Washington" ;;
        WV) echo "West Virginia" ;;
        WI) echo "Wisconsin" ;;
        WY) echo "Wyoming" ;;
        *) return 1 ;;
    esac
}

download_file() {
    local url="$1"
    local output="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -L --fail --output "$output" "$url"
    elif command -v wget >/dev/null 2>&1; then
        wget --continue --tries=3 --timeout=60 --output-document="$output" "$url"
    else
        die "Either curl or wget is required for downloading $url"
    fi
}

ensure_state_boundaries() {
    if [[ -n "${SOMOSPIE_STATE_VECTOR:-}" && -f "${SOMOSPIE_STATE_VECTOR}" ]]; then
        printf '%s\n' "${SOMOSPIE_STATE_VECTOR}"
        return 0
    fi

    mkdir -p "$STATE_BOUNDARY_DIR"
    local existing=""
    existing="$(find "$STATE_BOUNDARY_DIR" -type f -name 'cb_*_us_state_500k.shp' | sort | tail -n 1 || true)"
    if [[ -n "$existing" ]]; then
        printf '%s\n' "$existing"
        return 0
    fi

    local zip_path="$STATE_BOUNDARY_DIR/$(basename "$STATE_BOUNDARY_URL")"
    if [[ "$FORCE" == "1" || ! -f "$zip_path" ]]; then
        log "Downloading Census state boundaries: $STATE_BOUNDARY_URL"
        download_file "$STATE_BOUNDARY_URL" "$zip_path"
    fi

    unzip -o "$zip_path" -d "$STATE_BOUNDARY_DIR" >/dev/null
    existing="$(find "$STATE_BOUNDARY_DIR" -type f -name 'cb_*_us_state_500k.shp' | sort | tail -n 1 || true)"
    [[ -n "$existing" ]] || die "No state shapefile found after extracting $zip_path"
    printf '%s\n' "$existing"
}

run_python_prepare_training() {
    "$PYTHON_BIN" - "$1" "$2" "$3" "$MONTH" "$MIN_COVARIATE_COVERAGE" <<'PY'
import csv
import math
import sys

train_raw, train_out, covariate_out, month, min_covariate_coverage = sys.argv[1:6]
month = int(month)
min_covariate_coverage = float(min_covariate_coverage)

def is_valid_number(value):
    if value is None:
        return False
    text = str(value).strip()
    if text == "" or text.upper() in {"NA", "NAN", "NULL", "NONE"}:
        return False
    try:
        return math.isfinite(float(text))
    except ValueError:
        return False

def read_csv(path):
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames or [], list(reader)

def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

train_fields, train_rows = read_csv(train_raw)
target_candidates = [str(month), f"{month:02d}", f"X{month}", "band_1"]
target_col = next((name for name in target_candidates if name in train_fields), None)
if target_col is None:
    raise SystemExit(f"Could not find month target column in {train_raw}; fields={train_fields}")

candidate_topo_cols = [name for name in train_fields if name not in {"x", "y", target_col}]
topo_cols = []
dropped_topo_cols = []
train_row_count = len(train_rows)
for name in candidate_topo_cols:
    valid_count = sum(1 for row in train_rows if is_valid_number(row.get(name)))
    coverage = valid_count / train_row_count if train_row_count else 0.0
    if coverage >= min_covariate_coverage:
        topo_cols.append(name)
    else:
        dropped_topo_cols.append((name, valid_count, train_row_count, coverage))

if dropped_topo_cols:
    print("Dropped low-coverage covariates:")
    for name, valid_count, row_count, coverage in dropped_topo_cols:
        print(f"  {name}: {valid_count}/{row_count} valid ({coverage:.1%})")
if not topo_cols:
    raise SystemExit("No covariates met MIN_COVARIATE_COVERAGE; lower the threshold or inspect terrain sampling.")

train_keep = []
required = ["x", "y", target_col] + topo_cols
for row in train_rows:
    if all(is_valid_number(row.get(name)) for name in required):
        cleaned = {"x": row["x"], "y": row["y"], "z": row[target_col]}
        cleaned.update({name: row[name] for name in topo_cols})
        train_keep.append(cleaned)

if not train_keep:
    raise SystemExit("No valid training rows after dropping NA/non-finite values.")

with open(covariate_out, "w", newline="") as handle:
    for name in topo_cols:
        handle.write(f"{name}\n")

train_out_fields = ["x", "y", "z"] + topo_cols
write_csv(train_out, train_out_fields, train_keep)
print(f"Model-ready training rows: {len(train_keep)}")
print(f"Covariate count: {len(topo_cols)}")
print(f"Covariate minimum coverage: {min_covariate_coverage:.1%}")
PY
}
main() {
    require_cmd "$PYTHON_BIN"
    require_cmd find
    require_cmd sort
    require_cmd unzip

    [[ "$YEAR" =~ ^[0-9]{4}$ ]] || die "YEAR must be four digits, got: $YEAR"
    [[ "$MONTH" =~ ^[0-9]+$ ]] || die "MONTH must be numeric, got: $MONTH"
    (( MONTH >= 1 && MONTH <= 12 )) || die "MONTH must be 1 through 12, got: $MONTH"
    [[ -d "$TERRAIN_DIR" ]] || die "Terrain input directory not found: $TERRAIN_DIR"

    local state_full_name=""
    state_full_name="$(state_name "$STATE")" || die "Unsupported state code: $STATE"

    mkdir -p "$ESA_DIR" "$SHAPE_DIR" "$COARSE_DIR" "$REPROJECTED_DIR" "$STACK_DIR" "$TRAIN_DIR" "$EVAL_DIR" "$PRED_DIR" "$PLOT_DIR"

    log "Running SOMOSPIE Python/GDAL demo pipeline for $state_full_name ($STATE), $YEAR-$MONTH_PAD"
    log "Terrain input: $TERRAIN_DIR"
    log "Run directory: $RUN_DIR"

    local state_vector=""
    state_vector="$(ensure_state_boundaries)"
    export SOMOSPIE_STATE_VECTOR="$state_vector"

    local shape_path="$SHAPE_DIR/${STATE}.geojson"
    if [[ "$FORCE" == "1" || ! -f "$shape_path" ]]; then
        log "Creating state shape: $shape_path"
        "$PYTHON_BIN" "$PREPROC_DIR/create_shape.py" STATE "$state_full_name" "$shape_path"
    fi

    log "Fetching ESA CCI Soil Moisture daily files for $YEAR"
    "$FETCH_DIR/fetch_soil_moisture.sh" "$YEAR" "$ESA_DIR"

    local sm_month_tif="$ESA_DIR/${YEAR}_${MONTH_PAD}_ESA_monthly.tif"
    if [[ "$FORCE" == "1" || ! -f "$sm_month_tif" ]]; then
        log "Computing monthly soil moisture raster: $sm_month_tif"
        "$PYTHON_BIN" "$PREPROC_DIR/extract_SM_monthly.py" "$YEAR" "$ESA_DIR" "$MONTH" "$MONTH" --output "$sm_month_tif"
    fi

    mapfile -t terrain_sources < <(find "$TERRAIN_DIR" -maxdepth 1 -type f -name "$TERRAIN_GLOB" | sort)
    (( ${#terrain_sources[@]} > 0 )) || die "No terrain rasters matched $TERRAIN_DIR/$TERRAIN_GLOB"

    local -a reprojected_files=()
    local -a band_names=()
    local src name coarse_path reproject_source reproj_path
    for src in "${terrain_sources[@]}"; do
        name="$(basename "${src%.*}")"
        band_names+=("$name")

        if (( COARSIFY_FACTOR > 1 )); then
            coarse_path="$COARSE_DIR/${name}_coarse.tif"
            if [[ "$FORCE" == "1" || ! -f "$coarse_path" ]]; then
                log "Coarsifying $name by factor $COARSIFY_FACTOR"
                "$PYTHON_BIN" "$PREPROC_DIR/coarsify.py" "$src" "$coarse_path" "$COARSIFY_FACTOR"
            fi
            reproject_source="$coarse_path"
        else
            reproject_source="$src"
        fi

        reproj_path="$REPROJECTED_DIR/${name}.tif"
        if [[ "$FORCE" == "1" || ! -f "$reproj_path" ]]; then
            log "Reprojecting $name to EPSG:4326"
            "$PYTHON_BIN" "$PREPROC_DIR/reproject_raster.py" "$reproject_source" "$reproj_path" EPSG:4326
        fi
        reprojected_files+=("$reproj_path")
    done

    local stack_ext="tif"
    if [[ "${STACK_FORMAT^^}" == "VRT" ]]; then
        stack_ext="vrt"
    fi
    local stack_tif="$STACK_DIR/${STATE}_terrain_stack.$stack_ext"
    if [[ "$FORCE" == "1" || ! -f "$stack_tif" ]]; then
        log "Building terrain stack: $stack_tif"
        local -a stack_cmd=("$PYTHON_BIN" "$PREPROC_DIR/make_raster_stack.py" --output "$stack_tif" --format "$STACK_FORMAT")
        if [[ "${STACK_FORMAT^^}" != "VRT" ]]; then
            stack_cmd+=(--output-type Float32)
        fi
        if [[ "$ALLOW_MISMATCH" == "1" ]]; then
            stack_cmd+=(--allow-mismatch)
        fi
        for name in "${band_names[@]}"; do
            stack_cmd+=(--band-name "$name")
        done
        stack_cmd+=("${reprojected_files[@]}")
        "${stack_cmd[@]}"
    fi

    local sm_points_csv="$TRAIN_DIR/${STATE}_${YEAR}_${MONTH_PAD}_soil_moisture_points.csv"
    local train_raw_csv="$TRAIN_DIR/${STATE}_${YEAR}_${MONTH_PAD}_train_with_covariates_raw.csv"
    local train_csv="$TRAIN_DIR/${STATE}_${YEAR}_${MONTH_PAD}_train.csv"
    local covariate_file="$TRAIN_DIR/${STATE}_${YEAR}_${MONTH_PAD}_covariates.txt"
    local eval_csv="$EVAL_DIR/${STATE}_${YEAR}_${MONTH_PAD}_eval.csv"
    local model_stack_vrt="$STACK_DIR/${STATE}_model_covariate_stack.vrt"

    if [[ "$FORCE" == "1" || ! -f "$sm_points_csv" ]]; then
        log "Cropping June soil moisture raster to $STATE points"
        "$PYTHON_BIN" "$PREPROC_DIR/crop_to_shape.py" "$sm_month_tif" "$shape_path" "$sm_points_csv" 0 "$MONTH"
    fi

    if [[ "$FORCE" == "1" || ! -f "$train_raw_csv" ]]; then
        log "Sampling terrain covariates at soil moisture points"
        "$PYTHON_BIN" "$PREPROC_DIR/add_topos.py" "$sm_points_csv" "$stack_tif" "$train_raw_csv" "${band_names[@]}"
    fi

    log "Writing model-ready training CSV"
    run_python_prepare_training "$train_raw_csv" "$train_csv" "$covariate_file"

    mapfile -t model_band_names < "$covariate_file"
    (( ${#model_band_names[@]} > 0 )) || die "No model covariates were written to $covariate_file"

    local -a model_reprojected_files=()
    local covariate found idx
    for covariate in "${model_band_names[@]}"; do
        found=0
        for idx in "${!band_names[@]}"; do
            if [[ "${band_names[$idx]}" == "$covariate" ]]; then
                model_reprojected_files+=("${reprojected_files[$idx]}")
                found=1
                break
            fi
        done
        (( found == 1 )) || die "Selected covariate not found among reprojected rasters: $covariate"
    done

    if [[ "$FORCE" == "1" || ! -f "$model_stack_vrt" || "$covariate_file" -nt "$model_stack_vrt" ]]; then
        log "Building model covariate stack: $model_stack_vrt"
        local -a model_stack_cmd=("$PYTHON_BIN" "$PREPROC_DIR/make_raster_stack.py" --output "$model_stack_vrt" --format VRT)
        if [[ "$ALLOW_MISMATCH" == "1" ]]; then
            model_stack_cmd+=(--allow-mismatch)
        fi
        for name in "${model_band_names[@]}"; do
            model_stack_cmd+=(--band-name "$name")
        done
        model_stack_cmd+=("${model_reprojected_files[@]}")
        "${model_stack_cmd[@]}"
    fi

    if [[ "$FORCE" == "1" || ! -f "$eval_csv" || "$model_stack_vrt" -nt "$eval_csv" ]]; then
        log "Building evaluation grid from selected model covariates"
        "$PYTHON_BIN" "$PREPROC_DIR/crop_to_shape.py" --drop-any-nodata "$model_stack_vrt" "$shape_path" "$eval_csv" 0 "${model_band_names[@]}"
    fi

    local pred_csv="$PRED_DIR/${STATE}_${YEAR}_${MONTH_PAD}_${MODEL}_predictions.csv"
    if [[ "$RUN_MODEL" == "1" ]]; then
        local model_script="$MODEL_DIR/${MODEL}.py"
        [[ -f "$model_script" ]] || die "Unknown model script: $model_script"
        local -a model_args=()
        case "$MODEL" in
            rf) model_args=(-maxtree "$RF_MAXTREE" -seed "$MODEL_SEED") ;;
            rf_new)
                model_args=(
                    -maxtree "$RF_MAXTREE"
                    -seed "$MODEL_SEED"
                    --cv "$RF_NEW_CV"
                    --inner-cv "$RF_NEW_INNER_CV"
                    --n-iter "$RF_NEW_N_ITER"
                    --n-jobs "$RF_NEW_N_JOBS"
                    --validation-mode "$RF_NEW_VALIDATION_MODE"
                    --prediction-chunk-size "$PREDICTION_CHUNK_SIZE"
                )
                ;;
            knn) model_args=(-k "$KNN_MAXK" -seed "$MODEL_SEED") ;;
            *) die "Unsupported MODEL=$MODEL. Use rf, rf_new, or knn." ;;
        esac
        log "Running model $MODEL -> $pred_csv"
        "$PYTHON_BIN" "$model_script" -t "$train_csv" -e "$eval_csv" -o "$pred_csv" "${model_args[@]}"
    else
        log "Model step skipped. Set RUN_MODEL=1 to run rf.py or knn.py."
    fi

    if [[ "$VISUALIZE" == "1" ]]; then
        local plot_script="$VIS_DIR/plot_predictions.py"
        [[ -f "$plot_script" ]] || die "Visualization script not found: $plot_script"
        [[ -f "$pred_csv" ]] || die "Prediction CSV not found for visualization: $pred_csv"
        log "Rendering prediction map from $pred_csv"
        local -a plot_args=(
            "$plot_script" "$pred_csv"
            --shape "$shape_path"
            --output-dir "$PLOT_DIR"
            --title "SOMOSPIE ${MODEL} soil moisture, ${state_full_name}, ${YEAR}-${MONTH_PAD}"
            --cmap "$PLOT_CMAP"
            --dpi "$PLOT_DPI"
            --render-mode "$PLOT_RENDER_MODE"
            --point-size "$PLOT_POINT_SIZE"
        )
        "$PYTHON_BIN" "${plot_args[@]}"
    else
        log "Visualization step skipped. Set VISUALIZE=1 to render prediction GeoTIFF/PNG."
    fi

    log "Done. Outputs:"
    log "  Shape: $shape_path"
    log "  Soil moisture raster: $sm_month_tif"
    log "  Terrain stack: $stack_tif"
    log "  Model covariate stack: $model_stack_vrt"
    log "  Training CSV: $train_csv"
    log "  Evaluation CSV: $eval_csv"
    if [[ "$VISUALIZE" == "1" ]]; then
        log "  Prediction CSV: $pred_csv"
        log "  Prediction GeoTIFF: $PLOT_DIR/$(basename "${pred_csv%.csv}.tif")"
        log "  Prediction PNG: $PLOT_DIR/$(basename "${pred_csv%.csv}.png")"
    fi
}

main "$@"
