#!/usr/bin/env python3

import argparse
import calendar
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

gdal.UseExceptions()
osr.UseExceptions()

DEFAULT_NODATA = -9999.0
WGS84_PROJ4 = "+proj=longlat +ellps=WGS84 +datum=WGS84 +no_defs"
DEFAULT_CREATION_OPTIONS = ["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"]


def _wgs84_projection():
    """Return the default WGS84 projection for ESA CCI lon/lat grids."""
    srs = osr.SpatialReference()
    srs.ImportFromProj4(WGS84_PROJ4)
    return srs.ExportToWkt()


def _open_sm_dataset(path):
    """Open the ESA CCI soil-moisture variable from a NetCDF file."""
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is not None and dataset.RasterCount >= 1:
        return dataset

    nc_path = f'NETCDF:"{path}":sm'
    dataset = gdal.Open(nc_path, gdal.GA_ReadOnly)
    if dataset is not None:
        return dataset

    container = gdal.Open(str(path), gdal.GA_ReadOnly)
    if container is not None:
        subdatasets = container.GetSubDatasets()
        for name, _description in subdatasets:
            if name.endswith(":sm") or ":sm" in name:
                dataset = gdal.Open(name, gdal.GA_ReadOnly)
                if dataset is not None:
                    return dataset

    raise FileNotFoundError(f"Could not open soil moisture variable 'sm' in {path}")


def _month_files(year, directory, month):
    """Return daily NetCDF files matching one year and month."""
    pattern = f"*{year}{month:02d}*.nc"
    return sorted(Path(directory, str(year)).glob(pattern))


def _read_array(path):
    """Read one daily soil-moisture array and its grid metadata."""
    ds = _open_sm_dataset(path)
    try:
        band = ds.GetRasterBand(1)
        array = band.ReadAsArray().astype(np.float64)
        nodata = band.GetNoDataValue()
        if nodata is not None:
            array[array == nodata] = np.nan
        projection = ds.GetProjection() or _wgs84_projection()
        return array, ds.GetGeoTransform(), projection, ds.RasterXSize, ds.RasterYSize
    finally:
        ds = None


def _monthly_mean(files):
    """Compute a nan-aware monthly mean from daily soil-moisture files."""
    arrays = []
    metadata = None
    for path in files:
        array, transform, projection, width, height = _read_array(path)
        if metadata is None:
            metadata = transform, projection, width, height
        elif metadata != (transform, projection, width, height):
            raise ValueError(f"Input NetCDF grid does not match previous files: {path}")
        arrays.append(array)

    if not arrays:
        return None, metadata

    stack = np.stack(arrays, axis=0)
    valid = np.isfinite(stack)
    count = valid.sum(axis=0)
    total = np.where(valid, stack, 0.0).sum(axis=0)
    mean = np.full(stack.shape[1:], np.nan, dtype=np.float64)
    np.divide(total, count, out=mean, where=count > 0)
    return mean, metadata


def _output_path(year, directory, start_month, end_month):
    """Build the default monthly soil-moisture output path."""
    directory = Path(directory)
    if start_month == 1 and end_month == 12:
        return directory / f"{year}_ESA_monthly.tif"
    if start_month == end_month:
        return directory / f"{year}_{start_month:02d}_ESA_monthly.tif"
    return directory / f"{year}_{start_month:02d}-{end_month:02d}_ESA_monthly.tif"


def extract_sm_monthly(year, directory="~", start_month=1, end_month=12, output_file=None):
    """Compute monthly mean soil-moisture bands and write a GeoTIFF."""
    directory = Path(directory).expanduser()
    year = int(year)
    start_month = int(start_month)
    end_month = int(end_month)

    if start_month < 1 or end_month > 12 or start_month > end_month:
        raise ValueError("Month range must satisfy 1 <= start_month <= end_month <= 12")

    output_file = Path(output_file) if output_file else _output_path(year, directory, start_month, end_month)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    monthly_arrays = []
    metadata = None
    for month in range(start_month, end_month + 1):
        files = _month_files(year, directory, month)
        if not files:
            raise FileNotFoundError(f"No NetCDF files found for {year}-{month:02d} under {directory / str(year)}")
        mean, month_metadata = _monthly_mean(files)
        if metadata is None:
            metadata = month_metadata
        elif metadata != month_metadata:
            raise ValueError(f"Monthly grid metadata changed for {year}-{month:02d}")
        monthly_arrays.append((month, mean))

    transform, projection, width, height = metadata
    driver = gdal.GetDriverByName("GTiff")
    if output_file.exists():
        output_file.unlink()
    output = driver.Create(str(output_file), width, height, len(monthly_arrays), gdal.GDT_Float32, options=DEFAULT_CREATION_OPTIONS)
    if output is None:
        raise RuntimeError(f"Could not create output raster: {output_file}")
    output.SetGeoTransform(transform)
    if projection:
        output.SetProjection(projection)

    for band_index, (month, array) in enumerate(monthly_arrays, start=1):
        band = output.GetRasterBand(band_index)
        band.SetDescription(str(month))
        band.SetNoDataValue(DEFAULT_NODATA)
        out_array = np.where(np.isnan(array), DEFAULT_NODATA, array).astype(np.float32)
        band.WriteArray(out_array)
    output.FlushCache()
    output = None
    print(f"Monthly means written to {output_file}")


def parse_args():
    """Parse command-line arguments for this script."""
    parser = argparse.ArgumentParser(description="Compute monthly mean ESA CCI soil-moisture rasters from daily NetCDF files.")
    parser.add_argument("year", type=int)
    parser.add_argument("directory", nargs="?", default="~")
    parser.add_argument("start_month", nargs="?", default=None, type=int)
    parser.add_argument("end_month", nargs="?", default=None, type=int)
    parser.add_argument("--output")
    return parser.parse_args()


def main():
    """Run the command-line entry point."""
    args = parse_args()
    if args.start_month is None:
        start_month = 1
        end_month = 12
    else:
        start_month = args.start_month
        end_month = args.end_month if args.end_month is not None else args.start_month
    extract_sm_monthly(args.year, args.directory, start_month, end_month, args.output)


if __name__ == "__main__":
    main()
