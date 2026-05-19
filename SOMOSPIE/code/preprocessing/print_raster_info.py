#!/usr/bin/env python3

import argparse
from pathlib import Path

from osgeo import gdal, osr

gdal.UseExceptions()


def _format_optional(value):
    """Render optional metadata values consistently for display."""
    if value is None:
        return "None"
    return value


def _corner_coordinates(transform, width, height):
    """Calculate raster corner and center coordinates from a geotransform."""
    origin_x, pixel_width, rotation_x, origin_y, rotation_y, pixel_height = transform

    def coordinate(col, row):
        """Calculate one image-space coordinate from column and row offsets."""
        x = origin_x + col * pixel_width + row * rotation_x
        y = origin_y + col * rotation_y + row * pixel_height
        return x, y

    return {
        "Upper Left": coordinate(0, 0),
        "Lower Left": coordinate(0, height),
        "Upper Right": coordinate(width, 0),
        "Lower Right": coordinate(width, height),
        "Center": coordinate(width / 2, height / 2),
    }


def _spatial_reference_summary(projection_wkt):
    """Summarize a projection WKT as authority and CRS name."""
    if not projection_wkt:
        return "None"

    srs = osr.SpatialReference()
    srs.ImportFromWkt(projection_wkt)

    authority = None
    authority_name = srs.GetAuthorityName(None)
    authority_code = srs.GetAuthorityCode(None)
    if authority_name and authority_code:
        authority = f"{authority_name}:{authority_code}"

    name = srs.GetName()
    if authority and name:
        return f"{authority} - {name}"
    if authority:
        return authority
    return name or "WKT present, authority unknown"


def read_raster_info(raster_path):
    """Open a raster dataset for metadata inspection."""
    raster = gdal.Open(str(raster_path), gdal.GA_ReadOnly)

    if raster is None:
        raise FileNotFoundError(f"Could not open raster file: {raster_path}")

    return raster


def print_raster_info(raster, raster_path=None, include_wkt=False):
    """Print migration-relevant raster metadata in a compact text form."""
    driver = raster.GetDriver()
    transform = raster.GetGeoTransform(can_return_null=True)
    projection = raster.GetProjection()
    metadata = raster.GetMetadata()
    image_metadata = raster.GetMetadata("IMAGE_STRUCTURE")

    if raster_path:
        print(f"File: {raster_path}")
    print(f"Driver: {driver.ShortName}/{driver.LongName}")
    print(f"Size: {raster.RasterXSize} x {raster.RasterYSize}")
    print(f"Bands: {raster.RasterCount}")
    print(f"CRS: {_spatial_reference_summary(projection)}")

    spatial_ref = raster.GetSpatialRef()
    if spatial_ref is not None:
        mapping = spatial_ref.GetDataAxisToSRSAxisMapping()
        if mapping:
            print(f"Data axis to CRS axis mapping: {','.join(str(axis) for axis in mapping)}")

    if transform:
        origin_x, pixel_width, rotation_x, origin_y, rotation_y, pixel_height = transform
        print(f"Origin: ({origin_x:.15f}, {origin_y:.15f})")
        print(f"Pixel Size: ({pixel_width:.15f}, {pixel_height:.15f})")
        print(f"Rotation: ({rotation_x:.15f}, {rotation_y:.15f})")
        print(f"GeoTransform: {transform}")
        print("Corner Coordinates:")
        for label, (x, y) in _corner_coordinates(transform, raster.RasterXSize, raster.RasterYSize).items():
            print(f"  {label:<11} ({x:.15f}, {y:.15f})")
    else:
        print("GeoTransform: None")

    if metadata:
        print("Metadata:")
        for key in sorted(metadata):
            print(f"  {key}={metadata[key]}")

    if image_metadata:
        print("Image Structure Metadata:")
        for key in sorted(image_metadata):
            print(f"  {key}={image_metadata[key]}")

    print("Bands:")
    for band_index in range(1, raster.RasterCount + 1):
        band = raster.GetRasterBand(band_index)
        block_x, block_y = band.GetBlockSize()
        print(
            f"  Band {band_index}: "
            f"Block={block_x}x{block_y} "
            f"Type={gdal.GetDataTypeName(band.DataType)} "
            f"ColorInterp={gdal.GetColorInterpretationName(band.GetColorInterpretation())} "
            f"NoData={_format_optional(band.GetNoDataValue())}"
        )
        description = band.GetDescription()
        if description:
            print(f"    Description={description}")
        scale = band.GetScale()
        offset = band.GetOffset()
        if scale is not None or offset is not None:
            print(f"    Scale={_format_optional(scale)} Offset={_format_optional(offset)}")

    if include_wkt and projection:
        print("Projection WKT:")
        print(projection)


def parse_args():
    """Parse command-line arguments for this script."""
    parser = argparse.ArgumentParser(
        description="Print important raster metadata for migration and parity checks."
    )
    parser.add_argument("raster", type=Path, help="Path to the raster file to inspect.")
    parser.add_argument("--wkt", action="store_true", help="Print the full projection WKT.")
    return parser.parse_args()


def main():
    """Run the command-line entry point."""
    args = parse_args()
    raster = read_raster_info(args.raster)
    try:
        print_raster_info(raster, args.raster, include_wkt=args.wkt)
    finally:
        raster = None


if __name__ == "__main__":
    main()
