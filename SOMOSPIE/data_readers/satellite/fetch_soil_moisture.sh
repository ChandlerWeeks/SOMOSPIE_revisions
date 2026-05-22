#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: fetch_soil_moisture.sh [YEAR] [OUTPUT_DIR]

Downloads ESA CCI Soil Moisture daily COMBINED NetCDF files for one YEAR.
Defaults to 2019 when YEAR is omitted.

Environment overrides:
  ESA_CCI_VERSION      Product version without or with leading v. Default: 09.2
  ESA_CCI_PRODUCT      Product stream. Default: COMBINED
  ESA_CCI_BASE_URL     CEDA THREDDS base URL. Default: current CCI file server
  ESA_CCI_FIRST_DATE   First valid date for the product. Default: 1978-11-01
  ESA_CCI_LAST_DATE    Last valid date for the product. Default: 2024-12-31
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    usage
    exit 0
fi

if [ "$#" -gt 2 ]; then
    usage >&2
    exit 1
fi

year=${1:-2019}
sm_dir=${2:-data/ESA_CCI}

if ! [[ "$year" =~ ^[0-9]{4}$ ]]; then
    echo "YEAR must be a four-digit year, got: $year" >&2
    exit 1
fi

if ! command -v wget >/dev/null 2>&1; then
    echo "Required command not found: wget" >&2
    exit 1
fi
if ! command -v date >/dev/null 2>&1; then
    echo "Required command not found: date" >&2
    exit 1
fi

version=${ESA_CCI_VERSION:-09.2}
version=${version#v}
version_dir="v$version"
product=${ESA_CCI_PRODUCT:-COMBINED}
base_url=${ESA_CCI_BASE_URL:-https://data.cci.ceda.ac.uk/thredds/fileServer/esacci/soil_moisture/data/daily_files}
first_date=${ESA_CCI_FIRST_DATE:-1978-11-01}
last_date=${ESA_CCI_LAST_DATE:-2024-12-31}

start_date="$year-01-01"
end_date="$year-12-31"

if [[ "$start_date" < "$first_date" ]]; then
    start_date=$first_date
fi
if [[ "$end_date" > "$last_date" ]]; then
    end_date=$last_date
fi

if [[ "$start_date" > "$end_date" ]]; then
    echo "No ESA CCI Soil Moisture $product $version_dir files are available for $year." >&2
    echo "Configured date range is $first_date through $last_date." >&2
    exit 1
fi

year_dir="$sm_dir/$year"
mkdir -p "$year_dir"

download_count=0
skip_count=0
current_date=$start_date

while [[ "$current_date" < "$end_date" || "$current_date" == "$end_date" ]]; do
    ymd=${current_date//-/}
    file_name="ESACCI-SOILMOISTURE-L3S-SSMV-${product}-${ymd}000000-fv${version}.nc"
    url="$base_url/$product/$version_dir/$year/$file_name"
    output_path="$year_dir/$file_name"

    if [ -s "$output_path" ]; then
        skip_count=$((skip_count + 1))
    else
        echo "Downloading $file_name"
        wget --continue --tries=3 --timeout=60 --output-document="$output_path" "$url"
        download_count=$((download_count + 1))
    fi

    current_date=$(date -u -d "$current_date + 1 day" '+%Y-%m-%d')
done

cat > "$year_dir/downloaded.txt" <<EOF
Downloaded: $(date --utc '+%Y-%m-%dT%H:%M:%SZ')
Product: $product
Version: $version_dir
Date range: $start_date to $end_date
Downloaded files this run: $download_count
Existing files skipped: $skip_count
EOF

echo "ESA CCI Soil Moisture $product $version_dir ready under $year_dir"
