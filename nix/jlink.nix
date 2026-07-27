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
  version = "9.60";

  src = requireFile {
    name = "JLink_Linux_x86_64.tgz";
    url = "https://www.segger.com/downloads/jlink/";
    hash = "sha256-qwjb34spXDgR4LBAhxYU0j8rZp19hAlGi67NkharypE=";
    message = ''
      J-Link is proprietary software and must be downloaded after accepting
      SEGGER's license. Download the Linux x86-64 TGZ, then run:

        nix run .#prepare-jlink -- /path/to/JLink_Linux_x86_64.tgz
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
