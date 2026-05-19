#!/usr/bin/env python3

import argparse
from osgeo import gdal

gdal.UseExceptions()


def print_layers(raster_path):
    """Print raster band count and band names."""
    ds = gdal.Open(str(raster_path), gdal.GA_ReadOnly)
    if ds is None:
        raise FileNotFoundError(f"Could not open raster: {raster_path}")
    print(ds.RasterCount)
    for band_index in range(1, ds.RasterCount + 1):
        band = ds.GetRasterBand(band_index)
        print(f"{band_index},{band.GetDescription() or f'band_{band_index}'}")
    ds = None


def main():
    """Run the command-line entry point."""
    parser = argparse.ArgumentParser(description="Print raster band count and band names.")
    parser.add_argument("raster")
    args = parser.parse_args()
    print_layers(args.raster)


if __name__ == "__main__":
    main()
