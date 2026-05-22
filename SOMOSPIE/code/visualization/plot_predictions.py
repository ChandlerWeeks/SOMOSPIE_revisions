#!/usr/bin/env python3

"""Render SOMOSPIE point predictions as a GeoTIFF and PNG map."""

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
from osgeo import gdal, osr

matplotlib.use("Agg")
from matplotlib import pyplot as plt

gdal.UseExceptions()
osr.UseExceptions()

DEFAULT_NODATA = -9999.0
DEFAULT_CREATION_OPTIONS = ["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"]


def _default_output_dir(prediction_csv):
    """Place run-pipeline outputs beside predictions/ when possible."""
    prediction_csv = Path(prediction_csv)
    if prediction_csv.parent.name == "predictions":
        return prediction_csv.parent.parent / "visualization"
    return prediction_csv.parent


def _read_predictions(prediction_csv):
    """Read a SOMOSPIE prediction CSV with or without an x,y,sm header."""
    first_row = pd.read_csv(prediction_csv, header=None, nrows=1).iloc[0].astype(str).str.strip().str.lower().tolist()
    if first_row[:3] == ["x", "y", "sm"]:
        df = pd.read_csv(prediction_csv)
    else:
        df = pd.read_csv(prediction_csv, header=None, names=["x", "y", "sm"])

    missing = {"x", "y", "sm"} - set(df.columns)
    if missing:
        raise ValueError(f"Prediction CSV must contain x, y, and sm columns; missing {sorted(missing)}")

    df = df[["x", "y", "sm"]].copy()
    for column in ["x", "y", "sm"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["x", "y", "sm"])
    if df.empty:
        raise ValueError(f"No numeric prediction rows found in {prediction_csv}")
    return df


def predictions_to_grid(df):
    """Convert x/y/sm prediction points into a regular 2D grid."""
    xs = np.sort(df["x"].unique())
    ys_ascending = np.sort(df["y"].unique())
    ys = ys_ascending[::-1]
    if len(xs) < 2 or len(ys) < 2:
        raise ValueError("Prediction CSV must contain at least two unique x and y coordinates.")

    x_step = float(np.median(np.diff(xs)))
    y_step = float(np.median(np.diff(ys_ascending)))
    if x_step <= 0 or y_step <= 0:
        raise ValueError("Could not infer positive grid spacing from prediction coordinates.")

    x_codes = pd.Categorical(df["x"], categories=xs, ordered=True).codes
    y_codes = pd.Categorical(df["y"], categories=ys, ordered=True).codes
    if (x_codes < 0).any() or (y_codes < 0).any():
        raise ValueError("Could not map prediction coordinates onto a regular grid.")

    grid = np.full((len(ys), len(xs)), np.nan, dtype=np.float32)
    grid[y_codes, x_codes] = df["sm"].to_numpy(dtype=np.float32)
    transform = (float(xs[0] - x_step / 2), x_step, 0.0, float(ys[0] + y_step / 2), 0.0, -y_step)
    extent = [xs[0] - x_step / 2, xs[-1] + x_step / 2, ys[-1] - y_step / 2, ys[0] + y_step / 2]
    return grid, transform, extent


def write_geotiff(output_tif, grid, transform, epsg, nodata=DEFAULT_NODATA):
    """Write the prediction grid as a single-band GeoTIFF."""
    output_tif = Path(output_tif)
    output_tif.parent.mkdir(parents=True, exist_ok=True)
    if output_tif.exists():
        output_tif.unlink()

    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(
        str(output_tif),
        grid.shape[1],
        grid.shape[0],
        1,
        gdal.GDT_Float32,
        options=DEFAULT_CREATION_OPTIONS,
    )
    if dataset is None:
        raise RuntimeError(f"Could not create GeoTIFF: {output_tif}")

    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromEPSG(int(epsg))
    dataset.SetProjection(spatial_ref.ExportToWkt())
    dataset.SetGeoTransform(transform)

    band = dataset.GetRasterBand(1)
    band.SetDescription("soil_moisture")
    band.SetNoDataValue(float(nodata))
    band.WriteArray(np.where(np.isnan(grid), nodata, grid).astype(np.float32))
    dataset.FlushCache()
    dataset = None


def _color_scale(values, vmin=None, vmax=None):
    """Resolve a robust color scale from finite prediction values."""
    finite = np.asarray(values)[np.isfinite(values)]
    if finite.size == 0:
        raise ValueError("No finite prediction values to plot.")
    if vmin is None or vmax is None:
        stretch_min, stretch_max = np.percentile(finite, [2, 98])
        vmin = stretch_min if vmin is None else vmin
        vmax = stretch_max if vmax is None else vmax
    if vmin == vmax:
        delta = abs(vmin) * 0.01 or 1.0
        vmin -= delta
        vmax += delta
    return float(vmin), float(vmax)


def _plot_boundaries(ax, shape_path, epsg):
    """Overlay region boundaries on a matplotlib axis."""
    if shape_path is None:
        return
    boundary = gpd.read_file(shape_path)
    if boundary.crs is not None:
        boundary = boundary.to_crs(epsg=int(epsg))
    boundary.boundary.plot(ax=ax, color="black", linewidth=0.8)


def plot_png(output_png, grid, extent, shape_path=None, title=None, cmap="RdBu", vmin=None, vmax=None, dpi=180, epsg=4326):
    """Render a PNG map for quick inspection."""
    vmin, vmax = _color_scale(grid, vmin, vmax)

    output_png = Path(output_png)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 9), constrained_layout=True)
    image = ax.imshow(np.ma.masked_invalid(grid), extent=extent, origin="upper", cmap=cmap, vmin=vmin, vmax=vmax)
    _plot_boundaries(ax, shape_path, epsg)

    ax.set_title(title or "SOMOSPIE Soil Moisture Prediction")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_aspect("equal", adjustable="box")
    fig.colorbar(image, ax=ax, label="Soil moisture")
    fig.savefig(output_png, dpi=dpi)
    plt.close(fig)
    return float(vmin), float(vmax)


def plot_points_png(
    output_png,
    df,
    extent,
    shape_path=None,
    title=None,
    cmap="RdBu",
    vmin=None,
    vmax=None,
    dpi=180,
    epsg=4326,
    point_size=0.2,
):
    """Render sparse native-resolution predictions directly from points."""
    vmin, vmax = _color_scale(df["sm"].to_numpy(dtype=np.float32), vmin, vmax)

    output_png = Path(output_png)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 9), constrained_layout=True)
    image = ax.scatter(
        df["x"],
        df["y"],
        c=df["sm"],
        s=point_size,
        marker="s",
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        linewidths=0,
        edgecolors="none",
        rasterized=True,
    )
    _plot_boundaries(ax, shape_path, epsg)

    ax.set_title(title or "SOMOSPIE Soil Moisture Prediction")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_aspect("equal", adjustable="box")
    fig.colorbar(image, ax=ax, label="Soil moisture")
    fig.savefig(output_png, dpi=dpi)
    plt.close(fig)
    return float(vmin), float(vmax)


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Render SOMOSPIE x,y,sm predictions as a GeoTIFF and PNG.")
    parser.add_argument("prediction_csv", type=Path, help="Prediction CSV with x,y,sm columns or headerless x,y,sm rows.")
    parser.add_argument("--shape", type=Path, help="Optional region boundary vector to overlay on the PNG.")
    parser.add_argument("--output-dir", type=Path, help="Directory for default outputs.")
    parser.add_argument("--tif", type=Path, help="Output GeoTIFF path. Defaults to OUTPUT_DIR/<input-stem>.tif.")
    parser.add_argument("--png", type=Path, help="Output PNG path. Defaults to OUTPUT_DIR/<input-stem>.png.")
    parser.add_argument("--title", help="PNG title.")
    parser.add_argument("--epsg", type=int, default=4326, help="Output CRS EPSG code. Default: 4326.")
    parser.add_argument("--cmap", default="RdBu", help="Matplotlib colormap. Default: RdBu.")
    parser.add_argument("--vmin", type=float, help="Minimum color scale value. Defaults to 2nd percentile.")
    parser.add_argument("--vmax", type=float, help="Maximum color scale value. Defaults to 98th percentile.")
    parser.add_argument("--dpi", type=int, default=180, help="PNG resolution. Default: 180.")
    parser.add_argument(
        "--render-mode",
        choices=["auto", "raster", "points"],
        default="auto",
        help="PNG rendering mode. Default: auto.",
    )
    parser.add_argument(
        "--sparse-threshold",
        type=float,
        default=0.05,
        help="Use point rendering in auto mode below this finite-cell fraction. Default: 0.05.",
    )
    parser.add_argument("--point-size", type=float, default=0.2, help="Point marker area for sparse PNG rendering.")
    return parser.parse_args()


def main():
    """Run the command-line entry point."""
    args = parse_args()
    output_dir = args.output_dir or _default_output_dir(args.prediction_csv)
    output_tif = args.tif or output_dir / f"{args.prediction_csv.stem}.tif"
    output_png = args.png or output_dir / f"{args.prediction_csv.stem}.png"

    predictions = _read_predictions(args.prediction_csv)
    grid, transform, extent = predictions_to_grid(predictions)
    write_geotiff(output_tif, grid, transform, args.epsg)

    finite_count = int(np.isfinite(grid).sum())
    occupancy = finite_count / grid.size
    if args.render_mode == "points" or (args.render_mode == "auto" and occupancy < args.sparse_threshold):
        render_mode = "points"
        vmin, vmax = plot_points_png(
            output_png,
            predictions,
            extent,
            args.shape,
            args.title,
            args.cmap,
            args.vmin,
            args.vmax,
            args.dpi,
            args.epsg,
            args.point_size,
        )
    else:
        render_mode = "raster"
        vmin, vmax = plot_png(output_png, grid, extent, args.shape, args.title, args.cmap, args.vmin, args.vmax, args.dpi, args.epsg)

    print(f"Wrote GeoTIFF: {output_tif}")
    print(f"Wrote PNG: {output_png}")
    print(f"Grid: {grid.shape[1]} x {grid.shape[0]}; finite cells: {finite_count}; occupancy: {occupancy:.3%}")
    print(f"PNG render mode: {render_mode}")
    print(f"Color scale: {vmin:.6f} to {vmax:.6f}")


if __name__ == "__main__":
    main()
