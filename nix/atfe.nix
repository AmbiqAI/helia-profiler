{
  fetchurl,
  lib,
  patchelf,
  stdenv,
  stdenvNoCC,
  gnutar,
  xz,
  zlib,
}:

stdenvNoCC.mkDerivation rec {
  pname = "atfe";
  version = "22.1.0";

  src = fetchurl {
    url = "https://github.com/arm/arm-toolchain/releases/download/release-${version}-ATfE/ATfE-${version}-Linux-x86_64.tar.xz";
    hash = "sha256-4unmN7ugl7puS65pgog/5wX/1+jDp9yHaWSDXvHHpyQ=";
  };

  newlibOverlay = fetchurl {
    url = "https://github.com/arm/arm-toolchain/releases/download/release-${version}-ATfE/ATfE-newlib-overlay-${version}.tar.xz";
    hash = "sha256-BbYVScn6fVGvG5HxW6nFnyrufyaf1wkwqU2zG7CQEz4=";
  };

  nativeBuildInputs = [
    gnutar
    patchelf
    xz
  ];
  dontBuild = true;
  dontPatchELF = true;

  installPhase = ''
    runHook preInstall
    mkdir -p "$out"
    cp -a . "$out/"
    tar -xJf "$newlibOverlay" -C "$out"
    runHook postInstall
  '';

  postFixup = ''
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
    platforms = [ "x86_64-linux" ];
  };
}
