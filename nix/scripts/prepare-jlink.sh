set -euo pipefail

version="9.62"
download_url="https://www.segger.com/downloads/jlink/JLink_Linux_V962_x86_64.tgz"
expected_md5="abb96cbfcb3e3838eba15680f8e64709"
expected_size="67213850"
store_name="JLink_Linux_x86_64.tgz"

if [[ "${1:-}" != "--accept-license" || "$#" -ne 1 ]]; then
  cat >&2 <<'EOF'
Usage: nix run .#prepare-jlink -- --accept-license

Before using --accept-license, review:
  https://www.segger.com/downloads/jlink/JLink_Linux_V962_x86_64.tgz
  https://www.segger.com/purchase/licensing/

Passing --accept-license confirms that you accept SEGGER's terms and will use
the software only with original SEGGER products or authorized OEM products.
The helper then downloads the pinned J-Link 9.62 archive directly from SEGGER,
verifies it, and adds it to the local Nix store.
EOF
  exit 2
fi

download_dir="$(mktemp -d)"
trap 'rm -rf "$download_dir"' EXIT
archive="$download_dir/$store_name"

echo "Downloading SEGGER J-Link $version from:"
echo "  $download_url"
curl \
  --data "accept_license_agreement=accepted" \
  --fail \
  --location \
  --output "$archive" \
  --retry 3 \
  --show-error \
  --silent \
  "$download_url"

actual_md5="$(md5sum "$archive" | cut -d ' ' -f 1)"
actual_size="$(stat -c '%s' "$archive")"
if [[ "$actual_md5" != "$expected_md5" || "$actual_size" != "$expected_size" ]]; then
  cat >&2 <<EOF
Unexpected J-Link archive.
Expected J-Link $version MD5:  $expected_md5
Actual MD5:                    $actual_md5
Expected size:                 $expected_size bytes
Actual size:                   $actual_size bytes

SEGGER may have changed the download. Update nix/jlink.nix deliberately after
testing the new artifact rather than accepting it implicitly.
EOF
  exit 1
fi

store_path="$(
  nix store add \
    --hash-algo md5 \
    --mode flat \
    --name "$store_name" \
    "$archive"
)"

echo "J-Link $version is available to Nix at:"
echo "  $store_path"
echo
echo "Next:"
echo "  nix run .#install-udev-rules"
echo "  nix develop"
