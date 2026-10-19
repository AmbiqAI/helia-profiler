# Compatibility between the selected heliaAOT release and the provider pins.
#
# The flake does not choose CORE (ns-cmsis-nn), the SDK, NSX modules, LiteRT
# Micro or the compilers from the heliaAOT selection: those come from the
# profiler's compatibility baseline and from nix/*.nix, and stay as they are.
# Selecting a newer heliaAOT therefore does not bring a newer ns-cmsis-nn. This
# file states which ns-cmsis-nn each heliaAOT release needs and stops
# evaluation when the baseline is older. A baseline commit missing from
# nsCmsisNnReleases does not block the environment: it is reported, and the
# compat-baseline check fails until the commit is mapped.
{
  lib,
  baseline,
  heliaAot,
}:
let
  # Minimum ns-cmsis-nn per heliaAOT release, newest rule first. A heliaAOT
  # release uses the first rule whose `from` it has reached.
  floors = [
    {
      from = "0.32.0";
      nsCmsisNn = "7.47.0";
    }
    {
      from = "0.31.0";
      nsCmsisNn = "7.46.0";
    }
    {
      from = "0.30.0";
      nsCmsisNn = "7.45.0";
    }
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
    "166a6fa94004b53512de718099aa35ed655a70cf" = "7.44.2";
    "3573bfbdb651092a14662daaa3557ce63452e294" = "7.45.0";
    "afe4e5de42f8d67de562ec2c853a8031872e2931" = "7.45.1";
    "4a937fe7988935e1cfc2d8a41859fdada042c17c" = "7.46.0";
    "9f793bd9343b60e794f915b285bd7b7c4a885abe" = "7.47.0";
  };

  baselineRef = baseline.projects.ns-cmsis-nn.ref;
  baselineVersion = nsCmsisNnReleases.${baselineRef} or null;
  unmappedMessage = "The compatibility baseline pins ns-cmsis-nn ${baselineRef}, which nix/compat.nix does not map to a release; add it to nsCmsisNnReleases.";

  rule = lib.findFirst (r: lib.versionAtLeast heliaAot.version r.from) (throw ''
    heliaAOT ${heliaAot.version} is older than every rule in nix/compat.nix.
  '') floors;
in
assert lib.assertMsg (baselineVersion == null || lib.versionAtLeast baselineVersion rule.nsCmsisNn)
  ''
    heliaAOT ${heliaAot.version} needs ns-cmsis-nn ${rule.nsCmsisNn} or newer, but the
    profiler's compatibility baseline pins ns-cmsis-nn ${baselineVersion} (${baselineRef}).
    Selecting heliaAOT does not move the baseline. Update
    src/helia_profiler/data/compatibility-baseline-v1.json (and requalify the
    runtimes that share it) before selecting this release.
  '';
{
  inherit baselineRef floors unmappedMessage;
  baselineMapped = baselineVersion != null;
  baselineVersion =
    if baselineVersion == null then
      builtins.trace "warning: ${unmappedMessage}" "unmapped"
    else
      baselineVersion;
  requiredNsCmsisNn = rule.nsCmsisNn;
}
