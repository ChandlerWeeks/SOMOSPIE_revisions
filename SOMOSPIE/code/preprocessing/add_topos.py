#!/usr/bin/env python3

import argparse
import csv
from pathlib import Path

from osgeo import gdal

gdal.UseExceptions()


def _open_raster(path):
    """Open a raster dataset for read-only covariate sampling."""
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None:
        raise FileNotFoundError(f"Could not open covariate raster: {path}")
    return dataset


def _band_names(dataset, provided_names):
    """Return output column names for covariate raster bands."""
    if provided_names:
        if len(provided_names) != dataset.RasterCount:
            raise ValueError(
                f"Received {len(provided_names)} layer names for {dataset.RasterCount} raster bands."
            )
        return provided_names

    names = []
    for band_index in range(1, dataset.RasterCount + 1):
        band = dataset.GetRasterBand(band_index)
        names.append(band.GetDescription() or f"band_{band_index}")
    return names


def _sample_bands(dataset, x, y):
    """Sample all raster bands at a single x/y coordinate."""
    transform = dataset.GetGeoTransform()
    inverse = gdal.InvGeoTransform(transform)
    if inverse is None:
        raise ValueError("Raster geotransform is not invertible.")

    pixel, line = gdal.ApplyGeoTransform(inverse, x, y)
    col = int(pixel)
    row = int(line)

    if col < 0 or row < 0 or col >= dataset.RasterXSize or row >= dataset.RasterYSize:
        return [None] * dataset.RasterCount

    values = []
    for band_index in range(1, dataset.RasterCount + 1):
        band = dataset.GetRasterBand(band_index)
        array = band.ReadAsArray(col, row, 1, 1)
        if array is None:
            values.append(None)
            continue
        value = float(array[0, 0])
        nodata = band.GetNoDataValue()
        if nodata is not None and value == nodata:
            values.append(None)
        else:
            values.append(value)
    return values


def add_topos(input_csv, covariate_raster, output_csv, layer_names=None):
    """Append raster covariate values to a point CSV with x/y columns."""
    dataset = _open_raster(covariate_raster)
    try:
        names = _band_names(dataset, layer_names or [])
        with open(input_csv, newline="") as src, open(output_csv, "w", newline="") as dst:
            reader = csv.DictReader(src)
            if reader.fieldnames is None:
                raise ValueError(f"Input CSV has no header: {input_csv}")
            if "x" not in reader.fieldnames or "y" not in reader.fieldnames:
                raise ValueError("Input CSV must contain 'x' and 'y' columns.")

            writer = csv.DictWriter(dst, fieldnames=list(reader.fieldnames) + names)
            writer.writeheader()
            for row in reader:
                values = _sample_bands(dataset, float(row["x"]), float(row["y"]))
                for name, value in zip(names, values):
                    row[name] = "NA" if value is None else value
                writer.writerow(row)
    finally:
        dataset = None


def parse_args():
    """Parse command-line arguments for this script."""
    parser = argparse.ArgumentParser(
        description="Append raster covariate values to a CSV containing x/y point columns."
    )
    parser.add_argument("input_csv")
    parser.add_argument("covariate_raster")
    parser.add_argument("output_csv")
    parser.add_argument("layer_names", nargs="*")
    return parser.parse_args()


def main():
    """Run the command-line entry point."""
    args = parse_args()
    add_topos(args.input_csv, args.covariate_raster, args.output_csv, args.layer_names)


if __name__ == "__main__":
    main()
