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
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
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
              "JLink_Linux_V962_x86_64.tgz"
              "JLink_Linux_V962_arm64.tgz"
              "JLink_MacOSX_V962_arm64.pkg"
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
          prepareJlink = c.pkgs.writeShellApplication {
            name = "hpx-prepare-jlink";
            runtimeInputs = [
              c.pkgs.coreutils
              c.pkgs.curl
              c.pkgs.nix
            ];
            text = ''
              export HPX_JLINK_DOWNLOAD_URL="${c.jlink.download.downloadUrl}"
              export HPX_JLINK_EXPECTED_MD5="${c.jlink.download.md5}"
              export HPX_JLINK_EXPECTED_SIZE="${c.jlink.download.size}"
              export HPX_JLINK_STORE_NAME="${c.jlink.download.filename}"
              ${builtins.readFile ./nix/scripts/prepare-jlink.sh}
            '';
          };
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
          prepare-jlink = {
            type = "app";
            program = "${prepareJlink}/bin/hpx-prepare-jlink";
            meta.description = "Download, verify, and import licensed SEGGER J-Link 9.62";
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
              HPX_JLINK_DLL = c.jlinkLibrary;
              UV_NO_SYNC = "1";
              UV_PYTHON = editablePythonSet.python.interpreter;
              UV_PYTHON_DOWNLOADS = "never";
            };
            LD_LIBRARY_PATH = lib.optionalString c.isLinux (lib.makeLibraryPath c.runtimeLibraries);
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
            export HPX_JLINK_DLL="${c.jlinkLibrary}"
            ${lib.optionalString c.isLinux ''
              export LD_LIBRARY_PATH="${lib.makeLibraryPath c.runtimeLibraries}"
            ''}
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
