set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

case "$repo_root" in
  /nix/store/*)
    echo "Run verify-isolation from a helia-profiler source checkout." >&2
    exit 1
    ;;
esac

echo "Entering the flake with the caller's environment removed..."

# The single-quoted command is intentionally expanded only inside nix develop.
# shellcheck disable=SC2016
nix develop "path:$repo_root" \
  --ignore-environment \
  --keep HOME \
  --keep USER \
  --command bash -euo pipefail -c '
    forbidden_prefixes="/run/current-system/sw|/etc/profiles|/home/[^/]+/\\.nix-profile"
    required_commands=(
      hpx
      python
      cmake
      ninja
      arm-none-eabi-gcc
      JLinkExe
    )

    for command_name in "${required_commands[@]}"; do
      command_path="$(command -v "$command_name")"
      printf "%-24s %s\n" "$command_name" "$command_path"
      if [[ "$command_path" =~ $forbidden_prefixes ]]; then
        echo "ERROR: $command_name leaked from the host profile." >&2
        exit 1
      fi
      case "$command_path" in
        /nix/store/*) ;;
        *)
          echo "ERROR: $command_name is not provided by the Nix store." >&2
          exit 1
          ;;
      esac
    done

    python -c "import ai_edge_litert, helia_aot, helia_profiler, pyjoulescope_driver"
    hpx --help >/dev/null
    "$ATFE_ROOT/bin/clang" --version >/dev/null

    echo
    echo "Isolation check passed: runtime, AOT, native Python modules, build"
    echo "tools, both ARM toolchains, and J-Link all resolve from /nix/store."
  '
