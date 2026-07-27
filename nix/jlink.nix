{
  lib,
  makeWrapper,
  patchelf,
  requireFile,
  stdenv,
  stdenvNoCC,
  systemd,
}:

stdenvNoCC.mkDerivation rec {
  pname = "segger-jlink";
  version = "9.62";

  src = requireFile {
    name = "JLink_Linux_x86_64.tgz";
    url = "https://www.segger.com/downloads/jlink/JLink_Linux_V962_x86_64.tgz";
    # Published by SEGGER for the 2026-07-22 J-Link 9.62 release.
    hash = "md5-q7lsv8s+ODjroVaA+OZHCQ==";
    message = ''
      J-Link is proprietary software and SEGGER requires explicit license
      acceptance before download. Review the terms, then run:

        nix run .#prepare-jlink -- --accept-license
    '';
  };

  nativeBuildInputs = [
    makeWrapper
    patchelf
  ];
  dontBuild = true;
  dontStrip = true;

  installPhase = ''
    runHook preInstall
    mkdir -p "$out/opt/SEGGER/JLink" "$out/bin"
    cp -a . "$out/opt/SEGGER/JLink/"

    patchelf \
      --set-interpreter "${stdenv.cc.bintools.dynamicLinker}" \
      --set-rpath "${lib.makeLibraryPath [ stdenv.cc.cc.lib ]}" \
      "$out/opt/SEGGER/JLink/JLinkExe"

    makeWrapper "$out/opt/SEGGER/JLink/JLinkExe" "$out/bin/JLinkExe" \
      --set HPX_JLINK_DLL "$out/opt/SEGGER/JLink/libjlinkarm.so" \
      --prefix LD_LIBRARY_PATH : "$out/opt/SEGGER/JLink:${
        lib.makeLibraryPath [
          stdenv.cc.cc.lib
          systemd
        ]
      }"
    runHook postInstall
  '';

  meta = {
    description = "SEGGER J-Link Software and Documentation Pack";
    homepage = "https://www.segger.com/downloads/jlink/";
    license = lib.licenses.unfree;
    mainProgram = "JLinkExe";
    platforms = [ "x86_64-linux" ];
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
}
