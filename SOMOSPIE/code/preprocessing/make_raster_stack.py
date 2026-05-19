#!/usr/bin/env python3

import argparse
import os
import tempfile
from pathlib import Path

from osgeo import gdal, osr

gdal.UseExceptions()
osr.UseExceptions()

DEFAULT_CREATION_OPTIONS = ["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"]


def _open_dataset(path):
    """Open a raster dataset for read-only stack inspection."""
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None:
        raise FileNotFoundError(f"Could not open raster: {path}")
    return dataset


def _same_spatial_ref(left_wkt, right_wkt):
    """Compare two CRS definitions for spatial equivalence."""
    if left_wkt == right_wkt:
        return True
    if not left_wkt or not right_wkt:
        return left_wkt == right_wkt

    left = osr.SpatialReference()
    right = osr.SpatialReference()
    left.ImportFromWkt(left_wkt)
    right.ImportFromWkt(right_wkt)
    return bool(left.IsSame(right))


def _same_transform(left, right, tolerance):
    """Compare two geotransforms within a numeric tolerance."""
    return all(abs(a - b) <= tolerance for a, b in zip(left, right))


def _default_band_name(path, dataset, band_index):
    """Choose a stable output band name from source metadata or filename."""
    band = dataset.GetRasterBand(band_index)
    description = band.GetDescription()
    if description:
        return description
    if dataset.RasterCount == 1:
        return Path(path).stem
    return f"{Path(path).stem}_{band_index}"


def _collect_band_info(input_files):
    """Collect source band names and nodata values for stack output metadata."""
    band_info = []
    for raster_path in input_files:
        dataset = _open_dataset(raster_path)
        try:
            for band_index in range(1, dataset.RasterCount + 1):
                band = dataset.GetRasterBand(band_index)
                band_info.append(
                    {
                        "name": _default_band_name(raster_path, dataset, band_index),
                        "nodata": band.GetNoDataValue(),
                    }
                )
        finally:
            dataset = None
    return band_info


def validate_stack_inputs(input_files, strict=True, transform_tolerance=1e-12):
    """Validate that stack inputs share dimensions, transform, and CRS."""
    if not input_files:
        raise ValueError("At least one input raster is required.")

    first_path = input_files[0]
    first = _open_dataset(first_path)
    try:
        expected_width = first.RasterXSize
        expected_height = first.RasterYSize
        expected_transform = first.GetGeoTransform()
        expected_projection = first.GetProjection()
    finally:
        first = None

    for raster_path in input_files:
        if not Path(raster_path).exists():
            raise FileNotFoundError(f"Input raster does not exist: {raster_path}")

        dataset = _open_dataset(raster_path)
        try:
            if dataset.RasterCount < 1:
                raise ValueError(f"Input raster has no bands: {raster_path}")

            if not strict:
                continue

            if dataset.RasterXSize != expected_width or dataset.RasterYSize != expected_height:
                raise ValueError(
                    "Input raster dimensions do not match the first raster: "
                    f"{raster_path} has {dataset.RasterXSize}x{dataset.RasterYSize}; "
                    f"expected {expected_width}x{expected_height}."
                )

            transform = dataset.GetGeoTransform()
            if not _same_transform(transform, expected_transform, transform_tolerance):
                raise ValueError(
                    "Input raster geotransform does not match the first raster: "
                    f"{raster_path} has {transform}; expected {expected_transform}."
                )

            projection = dataset.GetProjection()
            if not _same_spatial_ref(projection, expected_projection):
                raise ValueError(
                    "Input raster CRS does not match the first raster: "
                    f"{raster_path}."
                )
        finally:
            dataset = None


def _apply_band_metadata(dataset, band_info, band_names=None):
    """Apply band names and nodata values to a stack dataset."""
    names = band_names or [info["name"] for info in band_info]
    if len(names) != dataset.RasterCount:
        raise ValueError(
            f"Expected {dataset.RasterCount} band names, received {len(names)}."
        )

    for band_index, name in enumerate(names, start=1):
        band = dataset.GetRasterBand(band_index)
        if name:
            band.SetDescription(str(name))

        nodata = band_info[band_index - 1]["nodata"]
        if nodata is not None:
            band.SetNoDataValue(nodata)


def _remove_existing_output(output_path, overwrite):
    """Remove an existing output when overwrite semantics allow it."""
    if output_path.exists():
        if not overwrite:
            raise FileExistsError(f"Output already exists: {output_path}")
        output_path.unlink()


def _temporary_output_path(output_path):
    """Create a temporary output path beside the final raster target."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}.", suffix=output_path.suffix, dir=output_path.parent
    )
    os.close(fd)
    os.unlink(temp_name)
    return Path(temp_name)


def make_raster_stack(
    input_files,
    output_file,
    creation_options=None,
    strict=True,
    overwrite=True,
    band_names=None,
    output_format=None,
    output_type=None,
):
    """Create a multi-band raster stack from ordered input rasters."""
    input_files = [Path(path) for path in input_files]
    output_path = Path(output_file)
    output_format = output_format or ("VRT" if output_path.suffix.lower() == ".vrt" else "GTiff")
    creation_options = creation_options if creation_options is not None else DEFAULT_CREATION_OPTIONS

    validate_stack_inputs(input_files, strict=strict)
    band_info = _collect_band_info(input_files)

    resolved_output = output_path.resolve()
    resolved_inputs = {path.resolve() for path in input_files if path.exists()}
    output_is_input = resolved_output in resolved_inputs

    if output_format.upper() == "VRT" and output_is_input:
        raise ValueError("Cannot safely update a VRT stack in place when it is also an input.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    vrt_path = f"/vsimem/somospie_stack_{os.getpid()}.vrt"
    vrt_options = gdal.BuildVRTOptions(separate=True)
    vrt = gdal.BuildVRT(vrt_path, [str(path) for path in input_files], options=vrt_options)
    if vrt is None:
        raise RuntimeError("GDAL failed to build the raster stack VRT.")

    try:
        _apply_band_metadata(vrt, band_info, band_names=band_names)

        if output_format.upper() == "VRT":
            _remove_existing_output(output_path, overwrite=overwrite)
            file_vrt = gdal.BuildVRT(
                str(output_path), [str(path) for path in input_files], options=vrt_options
            )
            if file_vrt is None:
                raise RuntimeError("GDAL failed to write the output VRT.")
            _apply_band_metadata(file_vrt, band_info, band_names=band_names)
            file_vrt.FlushCache()
            file_vrt = None
            return len(band_info)

        translate_target = _temporary_output_path(output_path) if output_is_input else output_path
        if not output_is_input:
            _remove_existing_output(output_path, overwrite=overwrite)

        translate_kwargs = {
            "format": output_format,
            "creationOptions": creation_options,
        }
        if output_type:
            output_type_code = gdal.GetDataTypeByName(output_type)
            if output_type_code == gdal.GDT_Unknown:
                raise ValueError(f"Unknown GDAL output data type: {output_type}")
            translate_kwargs["outputType"] = output_type_code

        translated = gdal.Translate(
            str(translate_target), vrt, options=gdal.TranslateOptions(**translate_kwargs)
        )
        if translated is None:
            raise RuntimeError("GDAL failed to write the output raster stack.")
        _apply_band_metadata(translated, band_info, band_names=band_names)
        translated.FlushCache()
        translated = None

        if output_is_input:
            os.replace(translate_target, output_path)

        return len(band_info)
    finally:
        vrt = None
        gdal.Unlink(vrt_path)


def _resolve_inputs(args, parser):
    """Resolve legacy and modern CLI arguments into input and output paths."""
    if args.output:
        output_file = Path(args.output)
        input_files = [Path(path) for path in args.inputs]

        if args.glob:
            if not args.input_dir:
                parser.error("--glob requires --input-dir")
            input_files.extend(sorted(Path(args.input_dir).glob(args.glob)))

        if args.input_dir:
            input_dir = Path(args.input_dir)
            input_files = [path if path.is_absolute() else input_dir / path for path in input_files]

        if not input_files:
            parser.error("No input rasters were provided.")
        return input_files, output_file

    if args.input_dir or args.glob:
        parser.error("--input-dir and --glob require --output")

    if len(args.inputs) < 3:
        parser.error(
            "Expected either legacy syntax: INPUT_DIR OUTPUT_FILE FILE [FILE ...], "
            "or modern syntax: --output OUTPUT FILE [FILE ...]."
        )

    input_dir = Path(args.inputs[0])
    output_file = Path(args.inputs[1])
    input_files = [input_dir / path for path in args.inputs[2:]]
    return input_files, output_file


def parse_args():
    """Parse command-line arguments for this script."""
    parser = argparse.ArgumentParser(
        description="Create a multi-band raster stack from ordered input rasters."
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help=(
            "Legacy mode: INPUT_DIR OUTPUT_FILE FILE [FILE ...]. "
            "Modern mode with --output: input raster files."
        ),
    )
    parser.add_argument("-o", "--output", help="Output raster stack path.")
    parser.add_argument("-d", "--input-dir", help="Directory for relative input files.")
    parser.add_argument("--glob", help="Glob pattern to stack files from --input-dir, sorted by name.")
    parser.add_argument(
        "--allow-mismatch",
        action="store_true",
        help="Allow inputs with different grids/CRS and let GDAL build the VRT anyway.",
    )
    parser.add_argument(
        "--format",
        default=None,
        help="GDAL output format. Defaults to VRT for .vrt outputs, otherwise GTiff.",
    )
    parser.add_argument(
        "--output-type",
        help="Optional GDAL output data type, e.g. Float32, Int16, Byte.",
    )
    parser.add_argument(
        "--co",
        action="append",
        default=[],
        help="GDAL creation option. Can be repeated, e.g. --co COMPRESS=LZW.",
    )
    parser.add_argument(
        "--no-default-co",
        action="store_true",
        help="Do not apply default GeoTIFF creation options.",
    )
    parser.add_argument(
        "--band-name",
        action="append",
        default=None,
        help="Output band name. Repeat once per output band.",
    )
    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Fail if the output exists. By default outputs are overwritten, matching the R script.",
    )
    return parser.parse_args(), parser


def main():
    """Run the command-line entry point."""
    args, parser = parse_args()
    input_files, output_file = _resolve_inputs(args, parser)

    creation_options = [] if args.no_default_co else list(DEFAULT_CREATION_OPTIONS)
    creation_options.extend(args.co)

    band_count = make_raster_stack(
        input_files=input_files,
        output_file=output_file,
        creation_options=creation_options,
        strict=not args.allow_mismatch,
        overwrite=not args.no_overwrite,
        band_names=args.band_name,
        output_format=args.format,
        output_type=args.output_type,
    )

    print(f"Stacked {band_count} band(s) from {len(input_files)} file(s) into {output_file}")


if __name__ == "__main__":
    main()
