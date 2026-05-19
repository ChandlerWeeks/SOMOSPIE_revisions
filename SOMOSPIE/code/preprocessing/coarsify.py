#!/usr/bin/env python3

import argparse
import math
import os
import tempfile
from pathlib import Path

from osgeo import gdal

gdal.UseExceptions()

DEFAULT_CREATION_OPTIONS = ["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"]


def _temporary_output_path(output_path):
    """Create a temporary output path beside the final raster target."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{output_path.stem}.", suffix=output_path.suffix, dir=output_path.parent)
    os.close(fd)
    os.unlink(temp_name)
    return Path(temp_name)


def coarsify(input_file, output_file, aggregate_factor, creation_options=None, resample_alg="average"):
    """Aggregate a raster by an integer factor using GDAL resampling."""
    aggregate_factor = int(aggregate_factor)
    if aggregate_factor < 1:
        raise ValueError("aggregate_factor must be >= 1")

    source = gdal.Open(str(input_file), gdal.GA_ReadOnly)
    if source is None:
        raise FileNotFoundError(f"Could not open raster: {input_file}")

    creation_options = creation_options if creation_options is not None else DEFAULT_CREATION_OPTIONS
    output_path = Path(output_file)
    output_is_input = output_path.resolve() == Path(input_file).resolve()
    target_path = _temporary_output_path(output_path) if output_is_input else output_path

    width = max(1, math.ceil(source.RasterXSize / aggregate_factor))
    height = max(1, math.ceil(source.RasterYSize / aggregate_factor))

    nodata_values = []
    for band_index in range(1, source.RasterCount + 1):
        nodata = source.GetRasterBand(band_index).GetNoDataValue()
        if nodata is not None:
            nodata_values.append(nodata)

    options_kwargs = {
        "format": "GTiff",
        "width": width,
        "height": height,
        "resampleAlg": resample_alg,
        "creationOptions": creation_options,
    }
    if nodata_values:
        options_kwargs["srcNodata"] = nodata_values[0] if len(set(nodata_values)) == 1 else nodata_values
        options_kwargs["dstNodata"] = nodata_values[0]

    warped = gdal.Warp(str(target_path), source, options=gdal.WarpOptions(**options_kwargs))
    if warped is None:
        raise RuntimeError("GDAL failed to aggregate raster.")
    warped.FlushCache()
    warped = None
    source = None

    if output_is_input:
        os.replace(target_path, output_path)


def parse_args():
    """Parse command-line arguments for this script."""
    parser = argparse.ArgumentParser(description="Aggregate a raster to coarser resolution.")
    parser.add_argument("input_file")
    parser.add_argument("output_file")
    parser.add_argument("aggregate_factor", type=int)
    parser.add_argument("--resample-alg", default="average")
    parser.add_argument("--co", action="append", default=[])
    parser.add_argument("--no-default-co", action="store_true")
    return parser.parse_args()


def main():
    """Run the command-line entry point."""
    args = parse_args()
    creation_options = [] if args.no_default_co else list(DEFAULT_CREATION_OPTIONS)
    creation_options.extend(args.co)
    coarsify(args.input_file, args.output_file, args.aggregate_factor, creation_options, args.resample_alg)


if __name__ == "__main__":
    main()
