# heliaAOT selection for the flake.
#
# The `helia-aot` flake input pins one heliaAOT source: a release's sdist from
# PyPI (public, the default) or a Git revision of AmbiqAI/helia-aot (needs access
# to that private repository). The selected version must have a runtime record
# under src/helia_profiler/data/runtimes/helia-aot/, and a Git revision must equal
# that record's `source.commit`; a Git checkout with uncommitted changes is
# refused. This is stricter than hpx's own range check and runs at evaluation,
# with the fix spelled out. A PyPI selection is identified by its version and the
# content hash flake.lock records.
#
# When the selected release is the one in uv.lock, the locked wheel is used and
# the environment is the same as the uv path. Any other recorded release is
# built from the pinned source against the locked dependency set. Evaluation
# stops if a dependency it declares is missing from uv.lock or the locked
# version is outside the release's version range; nothing is re-resolved.
{
  lib,
  pyproject-nix,
  src,
  runtimeRecords,
  uvLock,
}:
let
  # A PyPI sdist input has no revision; a Git input does. A Git input with
  # uncommitted changes has only dirtyRev.
  rev = src.rev or null;
  dirtyRev = src.dirtyRev or null;

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
        ${selected} has no helia-profiler runtime record. Recorded releases
        (version, commit):
        ${recorded}
        To support a new release, add src/helia_profiler/data/runtimes/helia-aot/<version>.json
        with source.commit set to the release commit, widen the `aot` extra in
        pyproject.toml, and re-lock (see the Nix environment guide).
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
assert lib.assertMsg (dirtyRev == null) ''
  The helia-aot input ${dirtyRev} has uncommitted changes. Select a committed
  release revision.
'';
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
  narHash = src.narHash or null;
  # How the selection was matched to its runtime record.
  verifiedBy = if rev != null then "git commit" else "version (PyPI sdist; see narHash)";
  inherit (record) version;

  # Python package-set overlay. A no-op when the locked wheel is selected.
  overlay =
    final: _prev:
    lib.optionalAttrs (!fromLockedWheel) (
      let
        inherit (pyproject-nix.lib)
          pep440
          pep508
          pep621
          pypa
          ;
        environ = pep508.mkEnviron final.python;
        dependencies = (pep621.filterDependenciesByEnviron environ [ ] project.dependencies).dependencies;
        # Runtime dependencies the locked package set does not satisfy.
        unmet = lib.concatMap (
          dep:
          let
            locked = final.${pypa.normalizePackageName dep.name}.version or null;
            outOfRange = lib.filter (
              cond: !pep440.comparators.${cond.op} (pep440.parseVersion locked) cond.version
            ) dep.conditions;
          in
          if locked == null then
            [ "${dep.name} (not in uv.lock)" ]
          else if outOfRange != [ ] then
            [ "${dep.name} ${locked} (outside the release's range)" ]
          else
            [ ]
        ) dependencies;
      in
      {
        helia-aot =
          if unmet != [ ] then
            throw ''
              ${selected} needs dependencies the locked package set does not
              provide: ${lib.concatStringsSep ", " unmet}.
              The flake does not re-resolve dependencies; update uv.lock with a
              release that supports this heliaAOT first.
            ''
          else
            final.stdenv.mkDerivation (
              pyproject-nix.build.lib.renderers.mkDerivation {
                inherit project environ;
              } { inherit (final) pyprojectHook resolveBuildSystem; }
            );
      }
    );
}
