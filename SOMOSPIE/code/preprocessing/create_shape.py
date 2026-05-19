#!/usr/bin/env python3

import argparse
import os
from pathlib import Path

from osgeo import ogr, osr

ogr.UseExceptions()
osr.UseExceptions()

WGS84_PROJ4 = "+proj=longlat +ellps=WGS84 +datum=WGS84 +no_defs"


def _repo_roots():
    """Yield likely repository roots used to discover SOMOSPIE data folders."""
    script_dir = Path(__file__).resolve().parent
    code_dir = script_dir.parent
    somospie_dir = code_dir.parent
    yield Path.cwd()
    yield code_dir
    yield somospie_dir
    yield somospie_dir.parent


def _data_roots():
    """Yield existing SOMOSPIE data directories without duplicates."""
    seen = set()
    for root in _repo_roots():
        for candidate in (root / "data", root / "SOMOSPIE" / "data"):
            resolved = candidate.resolve()
            if resolved not in seen and candidate.exists():
                seen.add(resolved)
                yield candidate


def _candidate_paths(*relative_paths):
    """Yield existing paths below known SOMOSPIE data roots."""
    for data_root in _data_roots():
        for rel in relative_paths:
            candidate = data_root / rel
            if candidate.exists():
                yield candidate


def _spatial_ref_wgs84():
    """Build the legacy WGS84 spatial reference used by the R scripts."""
    srs = osr.SpatialReference()
    srs.ImportFromProj4(WGS84_PROJ4)
    return srs


def _open_vector(path):
    """Open an OGR vector dataset for read-only geometry access."""
    ds = ogr.Open(str(path), 0)
    if ds is None:
        raise FileNotFoundError(f"Could not open vector dataset: {path}")
    return ds


def _find_first_existing(paths):
    """Return the first existing path from a sequence of candidates."""
    for path in paths:
        if Path(path).exists():
            return Path(path)
    return None


def _find_state_source():
    """Locate a local state-boundary vector source for STATE regions."""
    env_path = os.environ.get("SOMOSPIE_STATE_VECTOR")
    if env_path and Path(env_path).exists():
        return Path(env_path)

    names = ("gadm", "GADM", "usa", "USA", "states", "States")
    for data_root in _data_roots():
        for path in data_root.rglob("*.shp"):
            lower = str(path).lower()
            if any(name.lower() in lower for name in names):
                return path
    return None


def _copy_matching_features(source_path, output_path, field_name, accepted_values):
    """Union matching vector features and write them as one region geometry."""
    source = _open_vector(source_path)
    try:
        layer = source.GetLayer(0)
        source_srs = layer.GetSpatialRef()
        union_geom = None
        matched = 0
        accepted = {str(value) for value in accepted_values}

        for feature in layer:
            value = feature.GetField(field_name)
            if value is None or str(value) not in accepted:
                continue
            geom = feature.GetGeometryRef()
            if geom is None:
                continue
            geom = geom.Clone()
            union_geom = geom if union_geom is None else union_geom.Union(geom)
            matched += 1

        if union_geom is None:
            raise ValueError(f"No features matched {field_name} in {source_path}: {sorted(accepted)}")

        _write_geometry(output_path, union_geom, source_srs or _spatial_ref_wgs84(), {field_name: ";".join(sorted(accepted))})
        return matched
    finally:
        source = None


def _write_geometry(output_path, geometry, spatial_ref, fields=None):
    """Write a single OGR geometry to a GeoJSON or GeoPackage output."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    driver_name = "GeoJSON" if output_path.suffix.lower() in {".geojson", ".json", ".rds"} else "GPKG"
    driver = ogr.GetDriverByName(driver_name)
    ds = driver.CreateDataSource(str(output_path))
    if ds is None:
        raise RuntimeError(f"Could not create vector output: {output_path}")

    try:
        layer_name = output_path.stem or "region"
        geom_type = geometry.GetGeometryType()
        layer = ds.CreateLayer(layer_name, srs=spatial_ref, geom_type=geom_type)
        fields = fields or {}
        for key in fields:
            layer.CreateField(ogr.FieldDefn(str(key), ogr.OFTString))

        feature = ogr.Feature(layer.GetLayerDefn())
        for key, value in fields.items():
            feature.SetField(str(key), str(value))
        feature.SetGeometry(geometry)
        layer.CreateFeature(feature)
        feature = None
    finally:
        ds = None


def create_box(region, output_path):
    """Create a rectangular WGS84 region geometry from x1_x2_y1_y2 bounds."""
    values = region.split("_")
    if len(values) != 4:
        raise ValueError("BOX region must be x1_x2_y1_y2")
    x1, x2, y1, y2 = map(float, values)
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for x, y in [(x1, y1), (x1, y2), (x2, y2), (x2, y1), (x1, y1)]:
        ring.AddPoint(x, y)
    polygon = ogr.Geometry(ogr.wkbPolygon)
    polygon.AddGeometry(ring)
    _write_geometry(output_path, polygon, _spatial_ref_wgs84(), {"type": "BOX", "region": region})


def create_cec(region, output_path):
    """Create a CEC ecoregion geometry from local CEC shapefiles."""
    sources = {
        0: ("NA_Terrestrial_Ecoregions_Level_I_Shapefile/data/NA_Terrestrial_Ecoregions_v2_level1.shp", "LEVEL1"),
        1: ("NA_Terrestrial_Ecoregions_Level_II_Shapefile/data/NA_Terrestrial_Ecoregions_v2_level2.shp", "LEVEL2"),
        2: ("NA_Terrestrial_Ecoregions_v2_Level_III_Shapefile/data/NA_Terrestrial_Ecoregions_v2_level3.shp", "LEVEL3"),
    }
    level = region.count(".")
    if level not in sources:
        raise ValueError("CEC region must be a level 1, 2, or 3 code.")
    rel_path, field_name = sources[level]
    source_path = _find_first_existing(_candidate_paths(rel_path))
    if source_path is None:
        raise FileNotFoundError(f"Could not find CEC source shapefile under data/: {rel_path}")
    _copy_matching_features(source_path, output_path, field_name, [region])


def create_neon(region, output_path):
    """Create a NEON domain geometry from the local NEON domain shapefile."""
    source_path = _find_first_existing(_candidate_paths("NEONDomains_0/NEON_Domains.shp"))
    if source_path is None:
        raise FileNotFoundError("Could not find NEONDomains_0/NEON_Domains.shp under data/.")

    source = _open_vector(source_path)
    try:
        layer = source.GetLayer(0)
        fields = [layer.GetLayerDefn().GetFieldDefn(i).GetName() for i in range(layer.GetLayerDefn().GetFieldCount())]
    finally:
        source = None

    if "DomainID" in fields:
        try:
            _copy_matching_features(source_path, output_path, "DomainID", [region])
            return
        except ValueError:
            pass
    _copy_matching_features(source_path, output_path, "DomainName", [region])


def create_state(region, output_path):
    """Create a state or CONUS geometry from a local state-boundary dataset."""
    source_path = _find_state_source()
    if source_path is None:
        raise FileNotFoundError(
            "Could not find a state boundary shapefile. Set SOMOSPIE_STATE_VECTOR to a local state boundary vector dataset."
        )

    source = _open_vector(source_path)
    try:
        layer = source.GetLayer(0)
        defn = layer.GetLayerDefn()
        fields = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
    finally:
        source = None

    field_name = next((name for name in ("NAME_1", "NAME", "STATE_NAME", "STATE") if name in fields), None)
    if field_name is None:
        raise ValueError(f"Could not identify state name field in {source_path}; fields are {fields}")

    if region == "CONUS":
        source = _open_vector(source_path)
        try:
            layer = source.GetLayer(0)
            values = []
            for feature in layer:
                value = feature.GetField(field_name)
                if value and str(value) not in {"Alaska", "Hawaii"}:
                    values.append(str(value))
        finally:
            source = None
        _copy_matching_features(source_path, output_path, field_name, values)
    else:
        _copy_matching_features(source_path, output_path, field_name, [region])


def create_shape(region_type, region, output_path):
    """Dispatch region creation for STATE, BOX, CEC, and NEON inputs."""
    region_type = region_type.upper()
    if region_type == "BOX":
        create_box(region, output_path)
    elif region_type == "CEC":
        create_cec(region, output_path)
    elif region_type == "NEON":
        create_neon(region, output_path)
    elif region_type == "STATE":
        create_state(region, output_path)
    else:
        raise ValueError("Region type must be one of STATE, BOX, CEC, NEON.")


def parse_args():
    """Parse command-line arguments for this script."""
    parser = argparse.ArgumentParser(description="Create a region geometry for SOMOSPIE preprocessing.")
    parser.add_argument("region_type", choices=["STATE", "BOX", "CEC", "NEON"])
    parser.add_argument("region")
    parser.add_argument("output_path")
    return parser.parse_args()


def main():
    """Run the command-line entry point."""
    args = parse_args()
    create_shape(args.region_type, args.region, args.output_path)


if __name__ == "__main__":
    main()
