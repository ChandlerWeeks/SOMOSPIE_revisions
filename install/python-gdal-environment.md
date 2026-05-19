# Python/GDAL Environment

SOMOSPIE's migration environment starts as an empty Pixi workspace at the
repository root:

- `pixi.toml` declares the human-edited environment.
- `pixi.lock` records the exact resolved packages and should be committed.
- `.pixi/` contains the local environment and should not be committed.

Initialize or sync the empty environment:

```bash
pixi install
```

Add packages as the migration needs them. Prefer conda-forge for geospatial
packages so native libraries such as GDAL, PROJ, and GEOS resolve together:

```bash
pixi add python=3.11
pixi add gdal rasterio geopandas pyogrio shapely pyproj
pixi add pandas numpy scikit-learn
pixi add jupyterlab ipykernel
```

Use PyPI dependencies only when a package is unavailable from conda-forge:

```bash
pixi add --pypi some-package
```
