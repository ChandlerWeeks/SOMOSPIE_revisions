# Python/GDAL Preprocessing Workflow

These preprocessing scripts replace the legacy R preprocessing entry points with
Python scripts built directly on GDAL/OGR/OSR.

The scripts are executable, but the examples below use `python3` so they work
without relying on executable-bit or `PATH` setup.

## Environment

Run from the SOMOSPIE repository root or from `SOMOSPIE/code` depending on how
you are using the notebook workflow. The scripts require Python plus GDAL Python
bindings. The notebook curation path also uses the existing Pandas-based code.

With Pixi, sync the repository environment first:

```bash
pixi install
```

For a minimal environment, the dependency set is:

```bash
pixi add python gdal pandas
```

`gdal` pulls in NumPy in the conda-forge environment. Add other modeling
packages only when the modeling stage needs them.

## Recommended Order

The old R workflow generally did four things: create a region, prepare raster
layers, crop data into training/evaluation CSVs, then run model and analysis
scripts. The Python/GDAL workflow should be run in the same order.

### 1. Inspect Raster Metadata

Before reprojection or stacking, inspect the grid contract for each source
raster. This helps catch pixel-size, CRS, nodata, and band-order drift.

```bash
python3 SOMOSPIE/code/preprocessing/print_raster_info.py \
  /media/volume/aitechx_vol1/terrain_parameters/aspect.tif
```

Use `--wkt` when you need the full projection definition:

```bash
python3 SOMOSPIE/code/preprocessing/print_raster_info.py --wkt aspect.tif
```

### 2. Create Region Geometry

Generated regions are now OGR-readable vector files, normally GeoJSON. Use
`.geojson` instead of the old `.rds` shape outputs.

```bash
python3 SOMOSPIE/code/preprocessing/create_shape.py \
  BOX -95.8_-89.1_36.0_40.6 data/shapes/example_box.geojson
```

Supported region types are:

- `BOX`: second argument is `x1_x2_y1_y2` in WGS84 coordinates.
- `CEC`: reads local CEC ecoregion shapefiles under `data/`.
- `NEON`: reads `data/NEONDomains_0/NEON_Domains.shp`.
- `STATE`: reads a local state-boundary vector. Set `SOMOSPIE_STATE_VECTOR`
  when the source cannot be discovered automatically.

Example for state boundaries:

```bash
export SOMOSPIE_STATE_VECTOR=/path/to/state_boundaries.shp
python3 SOMOSPIE/code/preprocessing/create_shape.py \
  STATE Missouri data/shapes/Missouri.geojson
```

### 3. Prepare Terrain Rasters

If the source terrain rasters are finer than the target model grid, aggregate
first. The default aggregation mirrors the old mean-aggregation intent using
GDAL average resampling.

```bash
python3 SOMOSPIE/code/preprocessing/coarsify.py \
  data/topo_predictors/Aspect.sdat \
  data/topo_predictors/10Aspect.tif \
  10
```

Reproject each terrain raster to the SOMOSPIE WGS84 longitude/latitude target.
The default target matches the legacy R PROJ string. You can pass an explicit
SRS as the optional third argument.

```bash
python3 SOMOSPIE/code/preprocessing/reproject_raster.py \
  data/topo_predictors/10Aspect.tif \
  data/topo_predictors/10Aspect.tif

python3 SOMOSPIE/code/preprocessing/reproject_raster.py \
  input.tif output.tif EPSG:4326
```

### 4. Stack Covariate Rasters

Stack terrain/covariate rasters in the exact band order expected by modeling.
The script supports the old R-style syntax and newer direct-file syntax.

Legacy-compatible syntax:

```bash
python3 SOMOSPIE/code/preprocessing/make_raster_stack.py \
  data/topo_predictors \
  data/topo_predictors/stack.tif \
  10Aspect.tif 10Slope.tif 10Topographic_Wetness_Index.tif
```

Direct-file syntax:

```bash
python3 SOMOSPIE/code/preprocessing/make_raster_stack.py \
  --output data/topo_predictors/stack.tif \
  data/topo_predictors/10Aspect.tif \
  data/topo_predictors/10Slope.tif \
  data/topo_predictors/10Topographic_Wetness_Index.tif
```

Directory glob syntax:

```bash
python3 SOMOSPIE/code/preprocessing/make_raster_stack.py \
  --input-dir data/topo_predictors \
  --glob '10*.tif' \
  --output data/topo_predictors/stack.tif
```

By default, inputs must share width, height, geotransform, and CRS. Use
`--allow-mismatch` only when you explicitly want GDAL VRT behavior for mismatched
inputs.

Print stack band names when checking model feature order:

```bash
python3 SOMOSPIE/code/preprocessing/read_raster_layers_names.py \
  data/topo_predictors/stack.tif
```

### 5. Generate Monthly Soil-Moisture Raster

The old `extract_SM_monthly.R` wrote RDS. The Python version writes a multi-band
GeoTIFF where each band is a month number.

```bash
python3 SOMOSPIE/code/preprocessing/extract_SM_monthly.py \
  2019 data/ESA_CCI
```

Examples for a single month or range:

```bash
python3 SOMOSPIE/code/preprocessing/extract_SM_monthly.py \
  2019 data/ESA_CCI 6

python3 SOMOSPIE/code/preprocessing/extract_SM_monthly.py \
  2019 data/ESA_CCI 6 12
```

Default outputs are:

- `data/ESA_CCI/2019_ESA_monthly.tif` for all months.
- `data/ESA_CCI/2019_06_ESA_monthly.tif` for one month.
- `data/ESA_CCI/2019_06-12_ESA_monthly.tif` for a month range.

### 6. Crop Training Data To Region

Crop the monthly soil-moisture raster to a region and write SOMOSPIE CSV points.
The first columns are `x,y`; following columns are raster bands.

```bash
python3 SOMOSPIE/code/preprocessing/crop_to_shape.py \
  data/ESA_CCI/2019_ESA_monthly.tif \
  data/shapes/Missouri.geojson \
  out/Missouri/train_raw.csv
```

A fourth argument applies a meter buffer around the region, matching the old
R-script buffer concept:

```bash
python3 SOMOSPIE/code/preprocessing/crop_to_shape.py \
  data/ESA_CCI/2019_ESA_monthly.tif \
  data/shapes/Missouri.geojson \
  out/Missouri/train_raw_buffered.csv \
  10000
```

### 7. Add Covariates To Training Points

Sample the stacked covariate raster at each training point.

```bash
python3 SOMOSPIE/code/preprocessing/add_topos.py \
  out/Missouri/train_raw.csv \
  data/topo_predictors/stack.tif \
  out/Missouri/train_with_covariates.csv \
  Aspect Slope Topographic_Wetness_Index
```

If band names already exist in the stack, the trailing names are optional.

### 8. Build Evaluation Points

There are two common ways to create evaluation data.

Crop a covariate stack directly to CSV points:

```bash
python3 SOMOSPIE/code/preprocessing/crop_to_shape.py \
  data/topo_predictors/stack.tif \
  data/shapes/Missouri.geojson \
  out/Missouri/eval_with_covariates.csv \
  0 \
  Aspect Slope Topographic_Wetness_Index
```

Or crop an existing evaluation CSV to the region:

```bash
python3 SOMOSPIE/code/preprocessing/crop_to_shape.py \
  data/eval_points.csv \
  data/shapes/Missouri.geojson \
  out/Missouri/eval_points_cropped.csv
```

### 9. Run Existing Postprocessing And Modeling

The notebook curation code still performs Pandas cleanup, NA handling, optional
PCA, model training, and prediction orchestration. After the steps above, the
existing workflow can consume the generated train/eval CSVs.

For direct model script use, keep the expected SOMOSPIE column convention:

- Training CSV: `x,y,<soil moisture target>,<covariates...>`.
- Evaluation CSV: `x,y,<covariates...>`.
- Prediction CSV: `x,y,sm` without a header for current model scripts.

### 10. Validate Predictions

Compare observed soil-moisture points against prediction CSV grid values:

```bash
python3 SOMOSPIE/code/analysis/obs_vs_pred.py \
  out/Missouri/original_observed.csv \
  out/Missouri/predictions/rf_predictions.csv \
  out/Missouri/r2.csv \
  out/Missouri/rmse.csv
```

## Legacy R-To-Python Mapping

| Legacy R script | Python/GDAL replacement |
| --- | --- |
| `add_topos.R` | `add_topos.py` |
| `coarsify.R` | `coarsify.py` |
| `create_shape.R` | `create_shape.py` |
| `crop_to_shape.R` | `crop_to_shape.py` |
| `extract_SM_monthly.R` | `extract_SM_monthly.py` |
| `make_raster_stack.R` | `make_raster_stack.py` |
| `read_raster_layers_names.R` | `read_raster_layers_names.py` |
| `reproject_raster.R` | `reproject_raster.py` |
| `analysis/obs_vs_pred.R` | `analysis/obs_vs_pred.py` |

## Notes For Batch Scripts

The old `run.sh` pattern can be translated by replacing each R preprocessing
call with the corresponding Python command above. Recommended batch order:

1. Resolve Python environment and check GDAL availability.
2. Create or locate region GeoJSON files.
3. Coarsify/reproject terrain rasters when needed.
4. Stack terrain rasters in fixed feature order.
5. Extract or locate monthly soil-moisture GeoTIFFs.
6. Crop training data to region CSVs.
7. Add covariates to training data.
8. Create evaluation covariate CSVs.
9. Run model scripts.
10. Run `obs_vs_pred.py` for validation metrics.

Keep logs for every command and preserve `print_raster_info.py` output for key
intermediate rasters. Those metadata snapshots are useful when proving parity
with legacy R outputs.

## Workflow Order Quick Reference

Run preprocessing in this order when building a new SOMOSPIE dataset:

1. Inspect source rasters with `print_raster_info.py`.
2. Create or locate region boundaries with `create_shape.py`.
3. Coarsen terrain rasters when needed with `coarsify.py`.
4. Reproject terrain rasters with `reproject_raster.py`.
5. Stack terrain/covariate rasters with `make_raster_stack.py`.
6. Verify stack band order with `read_raster_layers_names.py`.
7. Generate monthly soil-moisture GeoTIFFs with `extract_SM_monthly.py`, or locate existing monthly soil-moisture rasters.
8. Crop soil-moisture data to region training CSVs with `crop_to_shape.py`.
9. Add covariates to training CSVs with `add_topos.py`.
10. Build evaluation covariate CSVs with `crop_to_shape.py` on the covariate stack, or crop an existing evaluation CSV with `crop_to_shape.py`.
11. Continue to SOMOSPIE postprocessing, modeling, and analysis.
12. Validate predictions with `analysis/obs_vs_pred.py`.

For repeatable runs, keep the `print_raster_info.py` output for every source,
intermediate, and final raster used in the run.
