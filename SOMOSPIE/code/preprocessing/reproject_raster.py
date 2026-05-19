#!/usr/bin/env python3

import argparse
import os
import tempfile
from pathlib import Path

from osgeo import gdal

gdal.UseExceptions()

DEFAULT_TARGET_SRS = "+proj=longlat +ellps=WGS84 +datum=WGS84 +no_defs"
DEFAULT_CREATION_OPTIONS = ["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"]


def _temporary_output_path(output_path):
    """Create a temporary output path beside the final raster target."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{output_path.stem}.", suffix=output_path.suffix, dir=output_path.parent)
    os.close(fd)
    os.unlink(temp_name)
    return Path(temp_name)


def reproject_raster(input_file, output_file, target_srs=DEFAULT_TARGET_SRS, creation_options=None, resample_alg=None, dst_nodata=None):
    """Reproject a raster with GDAL Warp using legacy WGS84 defaults."""
    source = gdal.Open(str(input_file), gdal.GA_ReadOnly)
    if source is None:
        raise FileNotFoundError(f"Could not open raster: {input_file}")

    creation_options = creation_options if creation_options is not None else DEFAULT_CREATION_OPTIONS
    output_path = Path(output_file)
    output_is_input = output_path.resolve() == Path(input_file).resolve()
    target_path = _temporary_output_path(output_path) if output_is_input else output_path

    kwargs = {
        "dstSRS": target_srs,
        "format": "GTiff",
        "creationOptions": creation_options,
        "multithread": True,
        "warpOptions": ["NUM_THREADS=ALL_CPUS"],
    }
    if resample_alg:
        kwargs["resampleAlg"] = resample_alg
    if dst_nodata is not None:
        kwargs["dstNodata"] = dst_nodata

    warped = gdal.Warp(str(target_path), source, options=gdal.WarpOptions(**kwargs))
    if warped is None:
        raise RuntimeError("GDAL failed to reproject raster.")
    warped.FlushCache()
    warped = None
    source = None

    if output_is_input:
        os.replace(target_path, output_path)


def parse_args():
    """Parse command-line arguments for this script."""
    parser = argparse.ArgumentParser(description="Reproject a raster with GDAL Warp.")
    parser.add_argument("input_file")
    parser.add_argument("output_file")
    parser.add_argument("target_srs", nargs="?", default=DEFAULT_TARGET_SRS, help="Target SRS, e.g. EPSG:4326 or a PROJ/WKT string.")
    parser.add_argument("--resample-alg", default=None)
    parser.add_argument("--dst-nodata", type=float, default=None)
    parser.add_argument("--co", action="append", default=[])
    parser.add_argument("--no-default-co", action="store_true")
    return parser.parse_args()


def main():
    """Run the command-line entry point."""
    args = parse_args()
    creation_options = [] if args.no_default_co else list(DEFAULT_CREATION_OPTIONS)
    creation_options.extend(args.co)
    reproject_raster(args.input_file, args.output_file, args.target_srs, creation_options, args.resample_alg, args.dst_nodata)


if __name__ == "__main__":
    main()
