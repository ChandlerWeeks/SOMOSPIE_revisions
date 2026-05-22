#!/usr/bin/env python3

import argparse
import csv
import math
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
from osgeo import gdal, ogr, osr

gdal.UseExceptions()
ogr.UseExceptions()
osr.UseExceptions()

WGS84_PROJ4 = "+proj=longlat +ellps=WGS84 +datum=WGS84 +no_defs"
BUFFER_PROJ4 = "+proj=aeqd +lat_0=52 +lon_0=-97.5 +x_0=8264722.17686 +y_0=4867518.35323 +datum=WGS84 +units=m +no_defs +ellps=WGS84 +towgs84=0,0,0"
DEFAULT_NODATA = -9999.0
DEFAULT_CREATION_OPTIONS = ["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"]
LEGACY_TOPO_NAMES = [
    "DEM", "HILL", "SLP", "ASP", "CSC", "LC", "CI", "CD", "FA", "TWI", "LSF", "CNB", "VDC", "VD", "RSP"
]
RASTER_EXTENSIONS = {".tif", ".tiff", ".sdat", ".img", ".vrt", ".nc"}
VECTOR_EXTENSIONS = {".geojson", ".json", ".gpkg", ".shp", ".rds"}
CSV_EXTENSIONS = {".csv", ".txt"}


def _wgs84():
    """Build the default WGS84 spatial reference for point CSV coordinates."""
    srs = osr.SpatialReference()
    srs.ImportFromProj4(WGS84_PROJ4)
    return srs


def _buffer_srs():
    """Build the projected CRS used for meter-based region buffering."""
    srs = osr.SpatialReference()
    srs.ImportFromProj4(BUFFER_PROJ4)
    return srs


def _open_vector(path):
    """Open an OGR vector dataset for read-only geometry access."""
    ds = ogr.Open(str(path), 0)
    if ds is None:
        raise FileNotFoundError(f"Could not open shape/vector file: {path}")
    return ds


def _union_layer_geometry(layer):
    """Union every geometry in an OGR layer into one geometry."""
    union = None
    for feature in layer:
        geom = feature.GetGeometryRef()
        if geom is None:
            continue
        geom = geom.Clone()
        union = geom if union is None else union.Union(geom)
    if union is None:
        raise ValueError("Shape file contains no geometry.")
    return union


def _load_shape_geometry(shape_path, buffer_meters=0, target_srs=None):
    """Load, optionally buffer, and optionally reproject a region geometry."""
    ds = _open_vector(shape_path)
    try:
        layer = ds.GetLayer(0)
        source_srs = layer.GetSpatialRef() or _wgs84()
        geom = _union_layer_geometry(layer)
    finally:
        ds = None

    if buffer_meters and int(buffer_meters) > 0:
        planar = _buffer_srs()
        to_planar = osr.CoordinateTransformation(source_srs, planar)
        from_planar = osr.CoordinateTransformation(planar, source_srs)
        geom.Transform(to_planar)
        geom = geom.Buffer(float(buffer_meters))
        geom.Transform(from_planar)

    if target_srs is not None and not source_srs.IsSame(target_srs):
        geom.Transform(osr.CoordinateTransformation(source_srs, target_srs))
        source_srs = target_srs

    return geom, source_srs


def _write_temp_shape(shape_path, buffer_meters=0, target_srs=None):
    """Write a temporary GeoJSON cutline for GDAL Warp."""
    geom, srs = _load_shape_geometry(shape_path, buffer_meters, target_srs)
    fd, temp_name = tempfile.mkstemp(prefix="somospie_cutline_", suffix=".geojson")
    os.close(fd)
    os.unlink(temp_name)

    driver = ogr.GetDriverByName("GeoJSON")
    ds = driver.CreateDataSource(temp_name)
    layer = ds.CreateLayer("cutline", srs=srs, geom_type=geom.GetGeometryType())
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(geom)
    layer.CreateFeature(feature)
    feature = None
    ds = None
    return Path(temp_name)


def _raster_band_names(dataset, provided_names):
    """Resolve CSV column names for raster bands after crop."""
    if provided_names:
        if len(provided_names) != dataset.RasterCount:
            raise ValueError(f"Received {len(provided_names)} layer names for {dataset.RasterCount} bands.")
        return provided_names
    if dataset.RasterCount == len(LEGACY_TOPO_NAMES):
        return LEGACY_TOPO_NAMES
    names = []
    for band_index in range(1, dataset.RasterCount + 1):
        band = dataset.GetRasterBand(band_index)
        names.append(band.GetDescription() or f"band_{band_index}")
    return names


def _cell_centers(transform, width, row):
    """Return x/y center coordinates for every pixel in one raster row."""
    origin_x, pixel_width, rotation_x, origin_y, rotation_y, pixel_height = transform
    cols = range(width)
    xs = [origin_x + (col + 0.5) * pixel_width + (row + 0.5) * rotation_x for col in cols]
    ys = [origin_y + (col + 0.5) * rotation_y + (row + 0.5) * pixel_height for col in cols]
    return xs, ys


def _format_value(value, nodata):
    """Format raster values for CSV output while preserving nodata as NA."""
    if nodata is not None and value == nodata:
        return "NA"
    if isinstance(value, float) and math.isnan(value):
        return "NA"
    return value


def _block_coordinates(transform, row_start, row_indices, col_indices):
    """Return cell-center coordinates for flattened block row/column indices."""
    origin_x, pixel_width, rotation_x, origin_y, rotation_y, pixel_height = transform
    absolute_rows = row_start + row_indices
    xs = origin_x + (col_indices + 0.5) * pixel_width + (absolute_rows + 0.5) * rotation_x
    ys = origin_y + (col_indices + 0.5) * rotation_y + (absolute_rows + 0.5) * pixel_height
    return xs, ys


def _invalid_mask(array, nodata):
    """Return cells that should be treated as missing."""
    invalid = ~np.isfinite(array)
    if nodata is not None:
        invalid |= array == nodata
    return invalid


def _raster_to_csv_legacy(dataset, output_csv, layer_names=None):
    """Write a raster CSV with NA placeholders for partially missing rows."""
    transform = dataset.GetGeoTransform()
    names = _raster_band_names(dataset, layer_names or [])
    bands = [dataset.GetRasterBand(i) for i in range(1, dataset.RasterCount + 1)]
    nodata = [band.GetNoDataValue() for band in bands]

    with open(output_csv, "w", newline="") as dst:
        writer = csv.writer(dst)
        writer.writerow(["x", "y"] + names)
        for row in range(dataset.RasterYSize):
            xs, ys = _cell_centers(transform, dataset.RasterXSize, row)
            arrays = [band.ReadAsArray(0, row, dataset.RasterXSize, 1)[0] for band in bands]
            for col in range(dataset.RasterXSize):
                raw_values = [array[col].item() if hasattr(array[col], "item") else array[col] for array in arrays]
                formatted = [_format_value(value, nd) for value, nd in zip(raw_values, nodata)]
                if all(value == "NA" for value in formatted):
                    continue
                writer.writerow([xs[col], ys[col]] + formatted)


def raster_to_csv(dataset, output_csv, layer_names=None, drop_any_nodata=False, block_rows=64):
    """Write a raster dataset to a SOMOSPIE x/y/value CSV table."""
    if not drop_any_nodata:
        _raster_to_csv_legacy(dataset, output_csv, layer_names=layer_names)
        return

    transform = dataset.GetGeoTransform()
    names = _raster_band_names(dataset, layer_names or [])
    bands = [dataset.GetRasterBand(i) for i in range(1, dataset.RasterCount + 1)]
    nodata = [band.GetNoDataValue() for band in bands]
    width = dataset.RasterXSize
    height = dataset.RasterYSize
    block_rows = max(1, int(block_rows))
    total_rows = 0

    with open(output_csv, "w", newline="") as dst:
        dst.write(",".join(["x", "y"] + names) + "\n")
        for row_start in range(0, height, block_rows):
            rows = min(block_rows, height - row_start)
            arrays = []
            invalid_masks = []
            for band, band_nodata in zip(bands, nodata):
                array = band.ReadAsArray(0, row_start, width, rows).astype(np.float64, copy=False)
                arrays.append(array)
                invalid_masks.append(_invalid_mask(array, band_nodata))

            valid = ~np.logical_or.reduce(invalid_masks)
            if valid.any():
                row_indices, col_indices = np.nonzero(valid)
                xs, ys = _block_coordinates(transform, row_start, row_indices, col_indices)
                values = np.stack(arrays, axis=-1)[row_indices, col_indices, :]
                output = np.column_stack((xs, ys, values))
                np.savetxt(dst, output, delimiter=",", fmt="%.12g")
                total_rows += output.shape[0]

            row_end = row_start + rows
            if row_end == height or row_end % (block_rows * 5) == 0:
                print(
                    f"CSV export progress: raster rows {row_end}/{height}; output rows {total_rows}",
                    file=sys.stderr,
                    flush=True,
                )


def _output_is_raster(output_file):
    """Return whether the output extension should be written as raster data."""
    return Path(output_file).suffix.lower() in {".tif", ".tiff", ".vrt", ".img"}


def crop_raster(input_file, shape_path, output_file, buffer_meters=0, layer_names=None, drop_any_nodata=False):
    """Crop and mask a raster by region, writing raster or CSV output."""
    source = gdal.Open(str(input_file), gdal.GA_ReadOnly)
    if source is None:
        raise FileNotFoundError(f"Could not open raster input: {input_file}")

    cutline = _write_temp_shape(shape_path, buffer_meters)
    temp_output = None
    try:
        if _output_is_raster(output_file):
            target = Path(output_file)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target.unlink()
            warp_target = str(target)
            warp_format = "GTiff" if target.suffix.lower() != ".vrt" else "VRT"
        else:
            target = Path(output_file)
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_output = target.with_suffix(target.suffix + ".tmp")
            if temp_output.exists():
                temp_output.unlink()
            warp_target = ""
            warp_format = "MEM"

        warp_kwargs = {
            "format": warp_format,
            "cutlineDSName": str(cutline),
            "cropToCutline": True,
            "dstNodata": DEFAULT_NODATA if warp_format != "MEM" else -3.4028234663852886e38,
        }
        if warp_format != "MEM":
            warp_kwargs["creationOptions"] = DEFAULT_CREATION_OPTIONS

        options = gdal.WarpOptions(**warp_kwargs)
        cropped = gdal.Warp(warp_target, source, options=options)
        if cropped is None:
            raise RuntimeError("GDAL failed to crop/mask raster.")

        if not _output_is_raster(output_file):
            raster_to_csv(cropped, temp_output, layer_names=layer_names, drop_any_nodata=drop_any_nodata)
            os.replace(temp_output, output_file)
        cropped = None
    finally:
        source = None
        if temp_output and Path(temp_output).exists():
            Path(temp_output).unlink()
        if cutline.exists():
            cutline.unlink()


def _is_numeric_row(row):
    """Return whether a CSV row begins with numeric x/y coordinates."""
    if len(row) < 2:
        return False
    try:
        float(row[0]); float(row[1])
        return True
    except ValueError:
        return False


def _read_csv_points(input_file):
    """Read point CSV rows and infer a header for headerless inputs."""
    with open(input_file, newline="") as src:
        rows = list(csv.reader(src))
    if not rows:
        return [], []
    if _is_numeric_row(rows[0]):
        width = max(len(row) for row in rows)
        header = ["x", "y"] + [f"V{i}" for i in range(3, width + 1)]
        return header, rows
    return rows[0], rows[1:]


def crop_points(input_file, shape_path, output_file, buffer_meters=0):
    """Filter point CSV rows to those inside or touching the region geometry."""
    header, rows = _read_csv_points(input_file)
    if not header:
        Path(output_file).write_text("")
        return

    x_index, y_index = 0, 1
    if "x" in header and "y" in header:
        x_index, y_index = header.index("x"), header.index("y")

    geom, _ = _load_shape_geometry(shape_path, buffer_meters, _wgs84())

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp") if output_path.resolve() == Path(input_file).resolve() else output_path

    with open(temp_path, "w", newline="") as dst:
        writer = csv.writer(dst)
        writer.writerow(header)
        for row in rows:
            if len(row) <= max(x_index, y_index):
                continue
            point = ogr.Geometry(ogr.wkbPoint)
            point.AddPoint(float(row[x_index]), float(row[y_index]))
            if geom.Contains(point) or geom.Touches(point):
                writer.writerow(row)

    if temp_path != output_path:
        os.replace(temp_path, output_path)


def crop_to_shape(input_file, shape_path, output_file, buffer_meters=0, layer_names=None, drop_any_nodata=False):
    """Crop raster or point CSV input to a supplied region shape."""
    ext = Path(input_file).suffix.lower()
    if ext in CSV_EXTENSIONS:
        crop_points(input_file, shape_path, output_file, buffer_meters)
    elif ext in RASTER_EXTENSIONS or gdal.Open(str(input_file), gdal.GA_ReadOnly) is not None:
        crop_raster(input_file, shape_path, output_file, buffer_meters, layer_names, drop_any_nodata)
    else:
        raise ValueError(f"Unsupported input file type: {input_file}")


def parse_args():
    """Parse command-line arguments for this script."""
    parser = argparse.ArgumentParser(description="Crop/mask raster data or point CSV data to a region shape.")
    parser.add_argument("input_file")
    parser.add_argument("shape_path")
    parser.add_argument("output_file")
    parser.add_argument("buffer", nargs="?", default=0, type=int)
    parser.add_argument("--drop-any-nodata", action="store_true", help="For raster-to-CSV output, write only rows where every raster band is valid.")
    parser.add_argument("layer_names", nargs="*")
    return parser.parse_args()


def main():
    """Run the command-line entry point."""
    args = parse_args()
    crop_to_shape(args.input_file, args.shape_path, args.output_file, args.buffer, args.layer_names, args.drop_any_nodata)


if __name__ == "__main__":
    main()
