{
  description = "Portable heliaPROFILER development and runtime environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
    };
    # The heliaAOT source the environment uses: the PyPI sdist of 0.25.0, the
    # release in uv.lock. Its version must have a runtime record in
    # src/helia_profiler/data/runtimes/helia-aot/. Select another recorded
    # release, or a Git revision of the private AmbiqAI/helia-aot repository, with
    # --override-input (see the Nix setup guide).
    helia-aot = {
      url = "https://files.pythonhosted.org/packages/57/45/cddc80a6eeb59ea11d6dd57057eb97bd7f5e106cbf84e06c1615e683b510/helia_aot-0.25.0.tar.gz";
      flake = false;
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      helia-aot,
      ...
    }:
    let
      inherit (nixpkgs) lib;
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
      forAllSystems = lib.genAttrs supportedSystems;
      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
      pythonOverlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };
      editableOverlay = workspace.mkEditablePyprojectOverlay { root = "$REPO_ROOT"; };

      heliaAot = import ./nix/helia-aot.nix {
        inherit lib pyproject-nix;
        src = helia-aot;
        runtimeRecords = ./src/helia_profiler/data/runtimes/helia-aot;
        uvLock = ./uv.lock;
      };
      baseline = lib.importJSON ./src/helia_profiler/data/compatibility-baseline-v1.json;
      compat = import ./nix/compat.nix { inherit lib baseline heliaAot; };

      mkPkgs =
        system:
        import nixpkgs {
          inherit system;
          config.allowUnfreePredicate =
            pkg:
            builtins.elem (lib.getName pkg) [
              "segger-jlink"
            ];
        };

      mkPythonSet =
        pkgs:
        (pkgs.callPackage pyproject-nix.build.packages {
          python = pkgs.python312;
        }).overrideScope
          (
            lib.composeManyExtensions [
              pyproject-build-systems.overlays.wheel
              pythonOverlay
              (import ./nix/python-overrides.nix { inherit pkgs; })
              # Evaluating the compatibility rules first stops an incompatible
              # heliaAOT selection before anything is built.
              (builtins.seq compat.requiredNsCmsisNn heliaAot.overlay)
            ]
          );

      mkComponents =
        system:
        let
          pkgs = mkPkgs system;
          isLinux = pkgs.stdenv.hostPlatform.isLinux;
          pythonSet = mkPythonSet pkgs;
          runtimeEnv = pythonSet.mkVirtualEnv "helia-profiler-python-env" {
            "helia-profiler" = [
              "aot"
              "analysis"
            ];
          };
          atfe = pkgs.callPackage ./nix/atfe.nix { };
          jlink = pkgs.callPackage ./nix/jlink.nix { };
          jlinkLibrary =
            "${jlink}/opt/SEGGER/JLink/" + (if isLinux then "libjlinkarm.so" else "libjlinkarm.dylib");
          runtimeLibraries = lib.optionals isLinux [
            pkgs.stdenv.cc.cc.lib
            pkgs.systemd
          ];
          nativeTools = [
            pkgs.cmake
            pkgs.gcc-arm-embedded
            pkgs.ninja
            atfe
            jlink
          ];
          identities = builtins.toJSON {
            flake = {
              rev = self.rev or self.dirtyRev or "unknown";
              nixpkgs = nixpkgs.rev;
            };
            helia-aot = {
              inherit (heliaAot) version lockedVersion;
              inherit (heliaAot) commit;
              input-rev = heliaAot.rev;
              built-from = if heliaAot.fromLockedWheel then "uv.lock wheel" else "helia-aot flake input source";
            };
            compatibility = {
              inherit (baseline) baseline_id;
              neuralspotx = baseline.neuralspotx.version;
              ns-cmsis-nn = {
                ref = compat.baselineRef;
                version = compat.baselineVersion;
                required = compat.requiredNsCmsisNn;
              };
              projects = lib.mapAttrs (_: project: project.ref) baseline.projects;
            };
            toolchains = {
              arm-gnu = pkgs.gcc-arm-embedded.version;
              atfe = atfe.version;
              jlink = jlink.version;
            };
          };
          identitiesApp = pkgs.writeShellApplication {
            name = "hpx-nix-identities";
            runtimeInputs = [
              runtimeEnv
              pkgs.uv
            ]
            ++ nativeTools;
            text = ''
              export ATFE_ROOT="${atfe}"
              export HPX_NIX_IDENTITIES=${lib.escapeShellArg identities}
              exec python ${./nix/scripts/identities.py}
            '';
          };
          hpx = pkgs.symlinkJoin {
            name = "helia-profiler-with-tools";
            paths = [ runtimeEnv ];
            nativeBuildInputs = [ pkgs.makeWrapper ];
            postBuild = ''
              wrapProgram "$out/bin/hpx" \
                --prefix PATH : "${lib.makeBinPath nativeTools}" \
                --set ATFE_ROOT "${atfe}" \
                --set HPX_JLINK_DLL "${jlinkLibrary}" ${lib.optionalString isLinux ''
                  --prefix LD_LIBRARY_PATH : "${lib.makeLibraryPath runtimeLibraries}"
                ''}
            '';
            meta.mainProgram = "hpx";
          };
        in
        {
          inherit
            atfe
            hpx
            identitiesApp
            jlink
            jlinkLibrary
            nativeTools
            pkgs
            pythonSet
            runtimeLibraries
            runtimeEnv
            ;
          inherit isLinux;
        };

      components = forAllSystems mkComponents;
    in
    {
      formatter = forAllSystems (system: components.${system}.pkgs.nixfmt);

      packages = forAllSystems (
        system:
        let
          c = components.${system};
        in
        {
          default = c.hpx;
          inherit (c) atfe hpx jlink;
          python-environment = c.runtimeEnv;
        }
      );

      apps = forAllSystems (
        system:
        let
          c = components.${system};
          installUdevRules = lib.optionalAttrs c.isLinux (
            c.pkgs.writeShellApplication {
              name = "hpx-install-udev-rules";
              runtimeInputs = [
                c.pkgs.coreutils
                c.pkgs.systemd
              ];
              text = builtins.readFile ./nix/scripts/install-udev-rules.sh;
            }
          );
          verifyIsolation = c.pkgs.writeShellApplication {
            name = "hpx-verify-isolation";
            runtimeInputs = [
              c.pkgs.bash
              c.pkgs.coreutils
              c.pkgs.git
              c.pkgs.nix
            ];
            text = builtins.readFile ./nix/scripts/verify-isolation.sh;
          };
        in
        {
          default = {
            type = "app";
            program = "${c.hpx}/bin/hpx";
            meta.description = "Run the packaged heliaPROFILER CLI";
          };
          identities = {
            type = "app";
            program = "${c.identitiesApp}/bin/hpx-nix-identities";
            meta.description = "Print the resolved heliaAOT, provider, toolchain and flake identities as JSON";
          };
          verify-isolation = {
            type = "app";
            program = "${verifyIsolation}/bin/hpx-verify-isolation";
            meta.description = "Verify that the development environment does not use host profiles";
          };
        }
        // lib.optionalAttrs c.isLinux {
          install-udev-rules = {
            type = "app";
            program = "${installUdevRules}/bin/hpx-install-udev-rules";
            meta.description = "Install Linux USB access rules for J-Link and Joulescope";
          };
        }
      );

      devShells = forAllSystems (
        system:
        let
          c = components.${system};
          editablePythonSet = c.pythonSet.overrideScope editableOverlay;
          devEnv = editablePythonSet.mkVirtualEnv "helia-profiler-dev-env" workspace.deps.all;
          contributorPreCommit = c.pkgs.pre-commit.overridePythonAttrs (_: rec {
            version = "4.6.0";
            src = c.pkgs.fetchFromGitHub {
              owner = "pre-commit";
              repo = "pre-commit";
              tag = "v${version}";
              hash = "sha256-WfajnE1PktzNs0Tand51/qUWEULGZqSNH48Ivu67kA8=";
            };
          });
        in
        rec {
          default = c.pkgs.mkShell {
            packages = [
              devEnv
              c.pkgs.git
              c.pkgs.git-lfs
              c.pkgs.uv
            ]
            ++ c.nativeTools;
            env = {
              ATFE_ROOT = "${c.atfe}";
              HPX_JLINK_DLL = c.jlinkLibrary;
              UV_NO_SYNC = "1";
              UV_PYTHON = editablePythonSet.python.interpreter;
              UV_PYTHON_DOWNLOADS = "never";
            };
            LD_LIBRARY_PATH = lib.optionalString c.isLinux (lib.makeLibraryPath c.runtimeLibraries);
            shellHook = ''
              unset PYTHONPATH
              # mkShell points CC and CXX at the host compiler. hpx never uses
              # it, and fixture builds reject both variables because they accept
              # pinned toolchain inputs only.
              unset CC CXX
              export REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
            '';
          };
          contrib = default.overrideAttrs (previous: {
            nativeBuildInputs = previous.nativeBuildInputs ++ [ contributorPreCommit ];
          });
        }
      );

      checks = forAllSystems (
        system:
        let
          c = components.${system};
          # The compatibility rule table, checked against fixed cases: whether
          # nix/compat.nix accepts a heliaAOT version against a baseline
          # ns-cmsis-nn commit.
          accepts =
            version: ref:
            (builtins.tryEval
              (import ./nix/compat.nix {
                inherit lib;
                heliaAot = { inherit version; };
                baseline.projects.ns-cmsis-nn.ref = ref;
              }).requiredNsCmsisNn
            ).success;
          v7_39_2 = "5f3fed9f21a57390cc7f00f77a37db8f5f110cb8";
          v7_44_1 = "063fdd35fc398e44edf3f32f8f95b3d101803874";
          compatCases = {
            "0.25.0 on v7.39.2" = accepts "0.25.0" v7_39_2;
            "0.26.0 on v7.39.2" = accepts "0.26.0" v7_39_2;
            "0.27.0 rejected on v7.39.2" = !accepts "0.27.0" v7_39_2;
            "0.27.0 on v7.44.1" = accepts "0.27.0" v7_44_1;
            "0.28.0 rejected on v7.39.2" = !accepts "0.28.0" v7_39_2;
            "0.28.0 rejected on v7.44.1" = !accepts "0.28.0" v7_44_1;
            "1.0.0 rejected on v7.44.1" = !accepts "1.0.0" v7_44_1;
            "unmapped baseline commit rejected" = !accepts "0.26.0" "0000000000000000000000000000000000000000";
            "release older than every rule rejected" = !accepts "0.22.0" v7_39_2;
          };
          failedCompatCases = lib.attrNames (lib.filterAttrs (_: ok: !ok) compatCases);
        in
        {
          compat-rules =
            assert lib.assertMsg (
              failedCompatCases == [ ]
            ) "nix/compat.nix cases failed: ${lib.concatStringsSep ", " failedCompatCases}";
            c.pkgs.runCommand "helia-profiler-nix-compat-rules" { } ''
              ${c.pkgs.coreutils}/bin/touch "$out"
            '';
          smoke = c.pkgs.runCommand "helia-profiler-nix-smoke" { } ''
            export PATH="${
              lib.makeBinPath (
                [
                  c.hpx
                  c.identitiesApp
                  c.runtimeEnv
                ]
                ++ c.nativeTools
              )
            }"
            export ATFE_ROOT="${c.atfe}"
            export HPX_JLINK_DLL="${c.jlinkLibrary}"
            ${lib.optionalString c.isLinux ''
              export LD_LIBRARY_PATH="${lib.makeLibraryPath c.runtimeLibraries}"
            ''}
            hpx --help >/dev/null
            python -c "import helia_aot, helia_profiler, pyjoulescope_driver"
            python -c 'import importlib.metadata as m, sys; v = m.version("helia-aot"); sys.exit(0 if v == "${heliaAot.version}" else "helia-aot " + v + " installed, ${heliaAot.version} selected")'
            hpx-nix-identities > /dev/null
            command -v arm-none-eabi-gcc >/dev/null
            command -v cmake >/dev/null
            command -v ninja >/dev/null
            command -v JLinkExe >/dev/null
            "$ATFE_ROOT/bin/clang" --version >/dev/null
            "${c.pkgs.coreutils}/bin/touch" "$out"
          '';
        }
      );
    };
}
