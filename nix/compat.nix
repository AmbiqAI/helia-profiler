# Compatibility between the selected heliaAOT release and the provider pins.
#
# The flake does not choose CORE (ns-cmsis-nn), the SDK, NSX modules, LiteRT
# Micro or the compilers from the heliaAOT selection: those come from the
# profiler's compatibility baseline and from nix/*.nix, and stay as they are.
# Selecting a newer heliaAOT therefore does not bring a newer ns-cmsis-nn. This
# file states which ns-cmsis-nn each heliaAOT release needs and stops
# evaluation when the baseline is older.
{
  lib,
  baseline,
  heliaAot,
}:
let
  # Minimum ns-cmsis-nn per heliaAOT release, newest rule first. A heliaAOT
  # release uses the first rule whose `from` it has reached. Releases 0.23-0.26
  # were qualified against the v7.39.2 baseline. 0.27 routes default kernels
  # that need up to 7.43.0. 0.28 and later need 7.44.2 (heliaAOT v1 floor).
  floors = [
    {
      from = "0.28.0";
      nsCmsisNn = "7.44.2";
    }
    {
      from = "0.27.0";
      nsCmsisNn = "7.43.0";
    }
    {
      from = "0.23.0";
      nsCmsisNn = "7.39.2";
    }
  ];

  # Release versions of the ns-cmsis-nn commits a baseline can pin. The
  # baseline records only the commit; add the tag here when the baseline moves.
  nsCmsisNnReleases = {
    "5f3fed9f21a57390cc7f00f77a37db8f5f110cb8" = "7.39.2";
    "714e058a7f654d6d24106bbb47ec6363058557bf" = "7.44.0";
    "063fdd35fc398e44edf3f32f8f95b3d101803874" = "7.44.1";
  };

  baselineRef = baseline.projects.ns-cmsis-nn.ref;
  baselineVersion =
    nsCmsisNnReleases.${baselineRef} or (throw ''
      The compatibility baseline pins ns-cmsis-nn ${baselineRef}, which nix/compat.nix
      does not map to a release. Add it to nsCmsisNnReleases.
    '');

  rule = lib.findFirst (r: lib.versionAtLeast heliaAot.version r.from) (throw ''
    heliaAOT ${heliaAot.version} is older than every rule in nix/compat.nix.
  '') floors;
in
assert lib.assertMsg (lib.versionAtLeast baselineVersion rule.nsCmsisNn) ''
  heliaAOT ${heliaAot.version} needs ns-cmsis-nn ${rule.nsCmsisNn} or newer, but the
  profiler's compatibility baseline pins ns-cmsis-nn ${baselineVersion} (${baselineRef}).
  Selecting heliaAOT does not move the baseline. Update
  src/helia_profiler/data/compatibility-baseline-v1.json (and requalify the
  runtimes that share it) before selecting this release.
'';
{
  inherit baselineRef baselineVersion floors;
  requiredNsCmsisNn = rule.nsCmsisNn;
}
