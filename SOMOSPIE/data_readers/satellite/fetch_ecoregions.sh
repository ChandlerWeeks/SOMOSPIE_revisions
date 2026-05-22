#!/usr/bin/env bash
set -euo pipefail

# If "data" is a subfolder of the working directory, move inside it before
# fetching the shapefiles. Existing workflow calls expect outputs under data/.
if [ -d "data" ]; then
    cd data
fi

CEC_LEVEL1_URL=${CEC_LEVEL1_URL:-https://www.cec.org/files/atlas_layers/1_terrestrial_ecosystems/1_06_1_terr_ecoregions_i/terr_ecoregions_v2_level_i_shapefile.zip}
CEC_LEVEL2_URL=${CEC_LEVEL2_URL:-https://www.cec.org/files/atlas_layers/1_terrestrial_ecosystems/1_06_2_terr_ecoregions_ii/terr_ecoregions_v2_level_ii_shapefile.zip}
CEC_LEVEL3_URL=${CEC_LEVEL3_URL:-https://www.cec.org/files/atlas_layers/1_terrestrial_ecosystems/1_06_3_terr_ecoregions_iii/terr_ecoregions_v2_level_iii_shapefile.zip}
NEON_URL=${NEON_URL:-https://www.neonscience.org/sites/default/files/NEONDomains_2024.zip}

require_command() {
    local command_name=$1
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "Required command not found: $command_name" >&2
        exit 1
    fi
}

require_command wget
require_command unzip
require_command find
require_command mktemp

cleanup_tmpdir() {
    local tmpdir=$1
    if [ -n "$tmpdir" ] && [ -d "$tmpdir" ]; then
        rm -rf "$tmpdir"
    fi
}

download_zip() {
    local url=$1
    local zip_path=$2

    echo "Downloading $url"
    case "$url" in
        file://*)
            cp "${url#file://}" "$zip_path"
            ;;
        *)
            wget --continue --tries=3 --timeout=60 --output-document="$zip_path" "$url"
            ;;
    esac
}

copy_shapefile_family() {
    local source_shp=$1
    local destination_dir=$2
    local basename_no_ext
    local source_dir

    source_dir=$(dirname "$source_shp")
    basename_no_ext=$(basename "$source_shp" .shp)

    mkdir -p "$destination_dir"
    cp "$source_dir/$basename_no_ext".* "$destination_dir/"
}

fetch_cec_level() {
    local level_name=$1
    local url=$2
    local target_dir=$3
    local expected_shp=$4
    local path_file=$5
    local canonical_path="$target_dir/data/$expected_shp"
    local tmpdir=""
    local source_shp=""

    if [ -f "$canonical_path" ]; then
        echo "Found existing $level_name shapefile: $canonical_path"
        printf '%s\n' "$canonical_path" > "$path_file"
        return
    fi

    tmpdir=$(mktemp -d)
    trap 'cleanup_tmpdir "$tmpdir"' RETURN

    download_zip "$url" "$tmpdir/source.zip"
    unzip -o "$tmpdir/source.zip" -d "$tmpdir/extracted" >/dev/null

    source_shp=$(find "$tmpdir/extracted" -type f -name "$expected_shp" | sort | head -n 1)
    if [ -z "$source_shp" ]; then
        echo "Could not find $expected_shp inside $url" >&2
        exit 1
    fi

    copy_shapefile_family "$source_shp" "$target_dir/data"
    find "$tmpdir/extracted" -maxdepth 2 -type f \( -name '*Cite*' -o -name '*Terms*' \) -exec cp {} "$target_dir/" \; || true

    date --utc '+%Y-%m-%dT%H:%M:%SZ' > "$target_dir/downloaded.txt"
    printf '%s\n' "$canonical_path" > "$path_file"
    echo "Prepared $level_name shapefile: $canonical_path"

    cleanup_tmpdir "$tmpdir"
    trap - RETURN
}

fetch_neon_domains() {
    local target_dir=NEONDomains_0
    local expected_shp=NEON_Domains.shp
    local canonical_path="$target_dir/$expected_shp"
    local tmpdir=""
    local source_shp=""

    if [ -f "$canonical_path" ]; then
        echo "Found existing NEON domain shapefile: $canonical_path"
        printf '%s\n' "$canonical_path" > path_NEON.txt
        return
    fi

    tmpdir=$(mktemp -d)
    trap 'cleanup_tmpdir "$tmpdir"' RETURN

    download_zip "$NEON_URL" "$tmpdir/source.zip"
    unzip -o "$tmpdir/source.zip" -d "$tmpdir/extracted" >/dev/null

    source_shp=$(find "$tmpdir/extracted" -type f -name "$expected_shp" | sort | head -n 1)
    if [ -z "$source_shp" ]; then
        echo "Could not find $expected_shp inside $NEON_URL" >&2
        exit 1
    fi

    copy_shapefile_family "$source_shp" "$target_dir"
    date --utc '+%Y-%m-%dT%H:%M:%SZ' > "$target_dir/downloaded.txt"
    printf '%s\n' "$canonical_path" > path_NEON.txt
    echo "Prepared NEON domain shapefile: $canonical_path"

    cleanup_tmpdir "$tmpdir"
    trap - RETURN
}

fetch_cec_level \
    "CEC Level I" \
    "$CEC_LEVEL1_URL" \
    "NA_Terrestrial_Ecoregions_Level_I_Shapefile" \
    "NA_Terrestrial_Ecoregions_v2_level1.shp" \
    "path_CEC_level1.txt"

fetch_cec_level \
    "CEC Level II" \
    "$CEC_LEVEL2_URL" \
    "NA_Terrestrial_Ecoregions_Level_II_Shapefile" \
    "NA_Terrestrial_Ecoregions_v2_level2.shp" \
    "path_CEC_level2.txt"

fetch_cec_level \
    "CEC Level III" \
    "$CEC_LEVEL3_URL" \
    "NA_Terrestrial_Ecoregions_v2_Level_III_Shapefile" \
    "NA_Terrestrial_Ecoregions_v2_level3.shp" \
    "path_CEC_level3.txt"

# Preserve the legacy single path file behavior. Historically this ended up
# pointing at the Level III file because it was fetched last.
cp path_CEC_level3.txt path_CEC.txt

fetch_neon_domains
