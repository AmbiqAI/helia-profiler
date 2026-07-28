{ pkgs }:

final: prev: {
  neuralspotx = prev.neuralspotx.overrideAttrs (old: {
    postInstall = (old.postInstall or "") + ''
      project_config="$(find "$out" -path '*/site-packages/neuralspotx/project_config.py' -print -quit)"
      test -n "$project_config"
      site_packages="$(dirname "$(dirname "$project_config")")"
      patch -d "$site_packages" -p1 < ${./patches/neuralspotx-writable-packaged-trees.patch}
    '';
  });

  ai-edge-litert = prev.ai-edge-litert.overrideAttrs (old: {
    nativeBuildInputs =
      (old.nativeBuildInputs or [ ])
      ++ pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux [ pkgs.autoPatchelfHook ];
    buildInputs =
      (old.buildInputs or [ ])
      ++ pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux [ pkgs.stdenv.cc.cc.lib ];
    # LiteRT ships optional Qualcomm and Intel dispatch plugins whose vendor
    # SDKs are not needed by heliaAOT. Keep the core LiteRT libraries patched,
    # but do not require those unrelated proprietary runtimes.
    autoPatchelfIgnoreMissingDeps = [
      "libQnnHtp.so"
      "libQnnIr.so"
      "libQnnSaver.so"
      "libQnnSystem.so"
      "libopenvino.so.2630"
      "libopenvino_tensorflow_lite_frontend.so.2630"
    ];
  });

  pyjoulescope-driver = prev.pyjoulescope-driver.overrideAttrs (old: {
    nativeBuildInputs =
      (old.nativeBuildInputs or [ ])
      ++ pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux [ pkgs.autoPatchelfHook ];
    buildInputs =
      (old.buildInputs or [ ])
      ++ pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux [
        pkgs.libusb1
        pkgs.systemd
      ];
  });
}
