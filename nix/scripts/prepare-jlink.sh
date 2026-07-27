set -euo pipefail

archive="${1:-}"
expected_hash="ab08dbdf8b295c3811e0b040871614d23f2b669d7d8409468baecd9216abca91"
store_name="JLink_Linux_x86_64.tgz"

if [[ -z "$archive" ]]; then
  cat >&2 <<'EOF'
Usage: nix run .#prepare-jlink -- /path/to/JLink_Linux_x86_64.tgz

Download the Linux x86-64 TGZ from:
  https://www.segger.com/downloads/jlink/

You must review and accept SEGGER's license on the download page. This helper
only verifies the pinned J-Link 9.60 archive and adds it to your local Nix store.
EOF
  exit 2
fi

if [[ ! -f "$archive" ]]; then
  echo "J-Link archive does not exist: $archive" >&2
  exit 1
fi

actual_hash="$(sha256sum "$archive" | cut -d ' ' -f 1)"
if [[ "$actual_hash" != "$expected_hash" ]]; then
  cat >&2 <<EOF
Unexpected J-Link archive hash.
Expected J-Link 9.60: $expected_hash
Actual:               $actual_hash

The SEGGER download may have moved to a newer release. Update nix/jlink.nix
deliberately after testing that release rather than accepting it implicitly.
EOF
  exit 1
fi

store_path="$(
  nix store add \
    --mode flat \
    --name "$store_name" \
    "$archive"
)"

echo "J-Link 9.60 is available to Nix at:"
echo "  $store_path"
echo
echo "Next:"
echo "  nix run .#install-udev-rules"
echo "  nix develop"
