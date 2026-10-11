# heliaAOT selection for the flake.
#
# The `helia-aot` flake input pins one heliaAOT source: a release's sdist from
# PyPI (public, the default) or a Git revision of AmbiqAI/helia-aot (needs access
# to that private repository). The selected version must be one the profiler
# supports: it needs a runtime record under src/helia_profiler/data/runtimes/helia-aot/,
# and a Git revision must equal that record's `source.commit`. The profiler's
# own version gate rejects unrecorded versions at compile time; this check
# rejects them at evaluation instead, with the fix spelled out.
#
# When the selected release is the one in uv.lock, the locked wheel is used and
# the environment is the same as the uv path. Any other recorded release is
# built from the pinned source against the locked dependency set, so a release
# that needs different dependencies fails to build instead of resolving silently.
{
  lib,
  pyproject-nix,
  src,
  runtimeRecords,
  uvLock,
}:
let
  # A PyPI sdist input has no revision; a Git input does.
  rev = src.rev or null;

  recordFiles = lib.filter (lib.hasSuffix ".json") (lib.attrNames (builtins.readDir runtimeRecords));
  records = map (file: lib.importJSON (runtimeRecords + "/${file}")) recordFiles;
  recorded = lib.concatMapStringsSep "\n" (r: "  ${r.version}  ${r.source.commit}") (
    lib.sort (a: b: lib.versionOlder a.version b.version) records
  );

  sourceVersion = (lib.importTOML (src + "/pyproject.toml")).project.version;
  selected =
    if rev == null then "heliaAOT ${sourceVersion}" else "heliaAOT ${sourceVersion} at ${rev}";

  matches = lib.filter (r: r.version == sourceVersion) records;
  record =
    if matches == [ ] then
      throw ''
        ${selected} has no helia-profiler runtime record, so the profiler would
        refuse it. Recorded releases (version, commit):
        ${recorded}
        To support a new release, add src/helia_profiler/data/runtimes/helia-aot/<version>.json
        with source.commit set to the release commit, widen the `aot` extra in
        pyproject.toml, and re-lock (see the Nix guide).
      ''
    else
      lib.head matches;

  lockedPackages = (lib.importTOML uvLock).package;
  lockedVersion =
    (lib.findFirst (p: p.name == "helia-aot") (throw "uv.lock has no helia-aot entry") lockedPackages)
    .version;

  fromLockedWheel = record.version == lockedVersion;

  project = pyproject-nix.lib.project.loadPyproject { projectRoot = src; };
in
assert lib.assertMsg (rev == null || rev == record.source.commit) ''
  ${selected} is not the ${record.version} release recorded by the profiler
  (commit ${record.source.commit}). Select the recorded commit, or add a runtime
  record for this revision first.
'';
{
  inherit
    fromLockedWheel
    lockedVersion
    record
    rev
    ;
  commit = record.source.commit;
  inherit (record) version;

  # Python package-set overlay. A no-op when the locked wheel is selected.
  overlay =
    final: _prev:
    lib.optionalAttrs (!fromLockedWheel) {
      helia-aot = final.stdenv.mkDerivation (
        pyproject-nix.build.lib.renderers.mkDerivation {
          inherit project;
          environ = pyproject-nix.lib.pep508.mkEnviron final.python;
        } { inherit (final) pyprojectHook resolveBuildSystem; }
      );
    };
}
