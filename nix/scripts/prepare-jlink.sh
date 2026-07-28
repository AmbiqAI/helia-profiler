set -euo pipefail

version="9.62"
: "${HPX_JLINK_DOWNLOAD_URL:?missing platform download URL}"
: "${HPX_JLINK_EXPECTED_MD5:?missing platform checksum}"
: "${HPX_JLINK_EXPECTED_SIZE:?missing platform archive size}"
: "${HPX_JLINK_STORE_NAME:?missing platform archive name}"

if [[ "${1:-}" != "--accept-license" || "$#" -ne 1 ]]; then
  cat >&2 <<'EOF'
Usage: nix run .#prepare-jlink -- --accept-license

Before using --accept-license, review:
  https://www.segger.com/downloads/jlink/
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
archive="$download_dir/$HPX_JLINK_STORE_NAME"

echo "Downloading SEGGER J-Link $version from:"
echo "  $HPX_JLINK_DOWNLOAD_URL"
curl \
  --data "accept_license_agreement=accepted" \
  --fail \
  --location \
  --output "$archive" \
  --retry 3 \
  --show-error \
  --silent \
  "$HPX_JLINK_DOWNLOAD_URL"

actual_md5="$(md5sum "$archive" | cut -d ' ' -f 1)"
actual_size="$(stat -c '%s' "$archive")"
if [[ "$actual_md5" != "$HPX_JLINK_EXPECTED_MD5" || "$actual_size" != "$HPX_JLINK_EXPECTED_SIZE" ]]; then
  cat >&2 <<EOF
Unexpected J-Link archive.
Expected J-Link $version MD5:  $HPX_JLINK_EXPECTED_MD5
Actual MD5:                    $actual_md5
Expected size:                 $HPX_JLINK_EXPECTED_SIZE bytes
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
    --name "$HPX_JLINK_STORE_NAME" \
    "$archive"
)"

echo "J-Link $version is available to Nix at:"
echo "  $store_path"
echo
echo "Next:"
if [[ "$(uname -s)" == "Linux" ]]; then
  echo "  nix run .#install-udev-rules"
fi
echo "  nix develop"
