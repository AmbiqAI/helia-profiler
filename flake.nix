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
  };

  outputs =
    {
      nixpkgs,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      ...
    }:
    let
      inherit (nixpkgs) lib;
      supportedSystems = [ "x86_64-linux" ];
      forAllSystems = lib.genAttrs supportedSystems;
      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
      pythonOverlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };
      editableOverlay = workspace.mkEditablePyprojectOverlay { root = "$REPO_ROOT"; };

      mkPkgs =
        system:
        import nixpkgs {
          inherit system;
          config.allowUnfreePredicate =
            pkg:
            builtins.elem (lib.getName pkg) [
              "JLink_Linux_x86_64.tgz"
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
            ]
          );

      mkComponents =
        system:
        let
          pkgs = mkPkgs system;
          pythonSet = mkPythonSet pkgs;
          runtimeEnv = pythonSet.mkVirtualEnv "helia-profiler-python-env" {
            "helia-profiler" = [
              "aot"
              "analysis"
            ];
          };
          atfe = pkgs.callPackage ./nix/atfe.nix { };
          jlink = pkgs.callPackage ./nix/jlink.nix { };
          nativeTools = [
            pkgs.cmake
            pkgs.gcc-arm-embedded
            pkgs.ninja
            atfe
            jlink
          ];
          hpx = pkgs.symlinkJoin {
            name = "helia-profiler-with-tools";
            paths = [ runtimeEnv ];
            nativeBuildInputs = [ pkgs.makeWrapper ];
            postBuild = ''
              wrapProgram "$out/bin/hpx" \
                --prefix PATH : "${lib.makeBinPath nativeTools}" \
                --set ATFE_ROOT "${atfe}" \
                --set HPX_JLINK_DLL "${jlink}/opt/SEGGER/JLink/libjlinkarm.so" \
                --prefix LD_LIBRARY_PATH : "${
                  lib.makeLibraryPath [
                    pkgs.stdenv.cc.cc.lib
                    pkgs.systemd
                  ]
                }"
            '';
            meta.mainProgram = "hpx";
          };
        in
        {
          inherit
            atfe
            hpx
            jlink
            nativeTools
            pkgs
            pythonSet
            runtimeEnv
            ;
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
          prepareJlink = c.pkgs.writeShellApplication {
            name = "hpx-prepare-jlink";
            runtimeInputs = [
              c.pkgs.coreutils
              c.pkgs.curl
              c.pkgs.nix
            ];
            text = builtins.readFile ./nix/scripts/prepare-jlink.sh;
          };
          installUdevRules = c.pkgs.writeShellApplication {
            name = "hpx-install-udev-rules";
            runtimeInputs = [
              c.pkgs.coreutils
              c.pkgs.systemd
            ];
            text = builtins.readFile ./nix/scripts/install-udev-rules.sh;
          };
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
          prepare-jlink = {
            type = "app";
            program = "${prepareJlink}/bin/hpx-prepare-jlink";
            meta.description = "Download, verify, and import licensed SEGGER J-Link 9.62";
          };
          install-udev-rules = {
            type = "app";
            program = "${installUdevRules}/bin/hpx-install-udev-rules";
            meta.description = "Install Linux USB access rules for J-Link and Joulescope";
          };
          verify-isolation = {
            type = "app";
            program = "${verifyIsolation}/bin/hpx-verify-isolation";
            meta.description = "Verify that the development environment does not use host profiles";
          };
        }
      );

      devShells = forAllSystems (
        system:
        let
          c = components.${system};
          editablePythonSet = c.pythonSet.overrideScope editableOverlay;
          devEnv = editablePythonSet.mkVirtualEnv "helia-profiler-dev-env" workspace.deps.all;
        in
        {
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
              LD_LIBRARY_PATH = lib.makeLibraryPath [
                c.pkgs.stdenv.cc.cc.lib
                c.pkgs.systemd
              ];
              UV_NO_SYNC = "1";
              UV_PYTHON = editablePythonSet.python.interpreter;
              UV_PYTHON_DOWNLOADS = "never";
            };
            shellHook = ''
              unset PYTHONPATH
              export REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
            '';
          };
        }
      );

      checks = forAllSystems (
        system:
        let
          c = components.${system};
        in
        {
          smoke = c.pkgs.runCommand "helia-profiler-nix-smoke" { } ''
            export PATH="${
              lib.makeBinPath (
                [
                  c.hpx
                  c.runtimeEnv
                ]
                ++ c.nativeTools
              )
            }"
            export ATFE_ROOT="${c.atfe}"
            export HPX_JLINK_DLL="${c.jlink}/opt/SEGGER/JLink/libjlinkarm.so"
            export LD_LIBRARY_PATH="${
              lib.makeLibraryPath [
                c.pkgs.stdenv.cc.cc.lib
                c.pkgs.systemd
              ]
            }"
            hpx --help >/dev/null
            python -c "import helia_aot, helia_profiler, pyjoulescope_driver"
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
