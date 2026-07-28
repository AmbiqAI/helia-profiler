{
  fetchurl,
  lib,
  patchelf,
  stdenv,
  stdenvNoCC,
  gnutar,
  undmg,
  xz,
  zlib,
}:

let
  version = "22.1.0";
  platform =
    {
      x86_64-linux = {
        archive = "ATfE-${version}-Linux-x86_64.tar.xz";
        hash = "sha256-4unmN7ugl7puS65pgog/5wX/1+jDp9yHaWSDXvHHpyQ=";
      };
      aarch64-linux = {
        archive = "ATfE-${version}-Linux-AArch64.tar.xz";
        hash = "sha256-ynPnWWO7kMS8ZtZ7GHWgXhIKik/r89sK1fCTMPv/2hc=";
      };
      aarch64-darwin = {
        archive = "ATfE-${version}-Darwin-universal.dmg";
        hash = "sha256-WZWDFkizG39ckGSp3iDn/GKQE90f+iQroDfGRcROTeU=";
      };
    }
    .${stdenv.hostPlatform.system} or (throw "ATfE is unsupported on ${stdenv.hostPlatform.system}");
in
stdenvNoCC.mkDerivation {
  pname = "atfe";
  inherit version;

  src = fetchurl {
    url = "https://github.com/arm/arm-toolchain/releases/download/release-${version}-ATfE/${platform.archive}";
    inherit (platform) hash;
  };

  newlibOverlay = fetchurl {
    url = "https://github.com/arm/arm-toolchain/releases/download/release-${version}-ATfE/ATfE-newlib-overlay-${version}.tar.xz";
    hash = "sha256-BbYVScn6fVGvG5HxW6nFnyrufyaf1wkwqU2zG7CQEz4=";
  };

  nativeBuildInputs = [
    gnutar
    xz
  ]
  ++ lib.optionals stdenv.hostPlatform.isLinux [ patchelf ]
  ++ lib.optionals stdenv.hostPlatform.isDarwin [ undmg ];
  dontBuild = true;
  dontPatchELF = true;

  unpackPhase = lib.optionalString stdenv.hostPlatform.isDarwin ''
    runHook preUnpack
    undmg "$src"
    runHook postUnpack
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p "$out"
    ${
      if stdenv.hostPlatform.isDarwin then
        ''
          toolchain_root="$(find . -type d -name 'ATfE-${version}-Darwin-universal' -print -quit)"
          test -n "$toolchain_root"
          cp -a "$toolchain_root/." "$out/"
        ''
      else
        ''
          cp -a . "$out/"
        ''
    }
    tar -xJf "$newlibOverlay" -C "$out"
    runHook postInstall
  '';

  postFixup = lib.optionalString stdenv.hostPlatform.isLinux ''
    for binary in "$out"/bin/*; do
      if [[ -f "$binary" ]] && patchelf --print-interpreter "$binary" >/dev/null 2>&1; then
        patchelf --set-interpreter "${stdenv.cc.bintools.dynamicLinker}" "$binary"
        patchelf --set-rpath "${
          lib.makeLibraryPath [
            stdenv.cc.cc.lib
            zlib
          ]
        }" "$binary"
      fi
    done
  '';

  meta = {
    description = "Arm Toolchain for Embedded";
    homepage = "https://developer.arm.com/Tools%20and%20Software/Arm%20Toolchain%20for%20Embedded";
    license = lib.licenses.asl20;
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
      "aarch64-darwin"
    ];
  };
}
