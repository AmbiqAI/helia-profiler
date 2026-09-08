{
  cpio,
  gzip,
  lib,
  makeWrapper,
  patchelf,
  fetchurl,
  rsync,
  stdenv,
  stdenvNoCC,
  systemd,
  xar,
}:

let
  version = "9.62";
  platform =
    {
      x86_64-linux = {
        filename = "JLink_Linux_V962_x86_64.tgz";
        hash = "md5-q7lsv8s+ODjroVaA+OZHCQ==";
      };
      aarch64-linux = {
        filename = "JLink_Linux_V962_arm64.tgz";
        hash = "md5-scb4wyxZjC1FocYPOdmkkw==";
      };
      aarch64-darwin = {
        filename = "JLink_MacOSX_V962_arm64.pkg";
        hash = "md5-G6/PQimbDn5F6aKuCIFgTw==";
      };
    }
    .${stdenv.hostPlatform.system}
      or (throw "SEGGER J-Link is unsupported on ${stdenv.hostPlatform.system}");
  downloadUrl = "https://www.segger.com/downloads/jlink/${platform.filename}";
  jlinkRoot = "$out/opt/SEGGER/JLink";
in
stdenvNoCC.mkDerivation {
  pname = "segger-jlink";
  inherit version;

  src = fetchurl {
    name = platform.filename;
    url = downloadUrl;
    inherit (platform) hash;
    curlOptsList = [
      "--data"
      "accept_license_agreement=accepted"
    ];
  };

  nativeBuildInputs = [
    makeWrapper
  ]
  ++ lib.optionals stdenv.hostPlatform.isLinux [ patchelf ]
  ++ lib.optionals stdenv.hostPlatform.isDarwin [
    cpio
    gzip
    rsync
    xar
  ];
  dontBuild = true;
  dontStrip = true;

  unpackPhase = lib.optionalString stdenv.hostPlatform.isDarwin ''
    runHook preUnpack
    xar -xf "$src"
    runHook postUnpack
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p "${jlinkRoot}" "$out/bin"
    ${
      if stdenv.hostPlatform.isDarwin then
        ''
          mkdir payload
          cd payload
          gzip -dc ../JLink.pkg/Payload | cpio -i
          installed_root="$(find Applications/SEGGER -maxdepth 1 -type d -name 'JLink_V*' -print -quit)"
          test -n "$installed_root"
          rsync -rtl "$installed_root/" "${jlinkRoot}/"
          cd ..

          makeWrapper "${jlinkRoot}/JLinkExe" "$out/bin/JLinkExe" \
            --set HPX_JLINK_DLL "${jlinkRoot}/libjlinkarm.dylib" \
            --prefix DYLD_LIBRARY_PATH : "${jlinkRoot}"
        ''
      else
        ''
          cp -a . "${jlinkRoot}/"

          patchelf \
            --set-interpreter "${stdenv.cc.bintools.dynamicLinker}" \
            --set-rpath "${lib.makeLibraryPath [ stdenv.cc.cc.lib ]}" \
            "${jlinkRoot}/JLinkExe"

          makeWrapper "${jlinkRoot}/JLinkExe" "$out/bin/JLinkExe" \
            --set HPX_JLINK_DLL "${jlinkRoot}/libjlinkarm.so" \
            --prefix LD_LIBRARY_PATH : "${jlinkRoot}:${
              lib.makeLibraryPath [
                stdenv.cc.cc.lib
                systemd
              ]
            }"
        ''
    }
    runHook postInstall
  '';

  meta = {
    description = "SEGGER J-Link Software and Documentation Pack";
    homepage = "https://www.segger.com/downloads/jlink/";
    license = lib.licenses.unfree;
    mainProgram = "JLinkExe";
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
      "aarch64-darwin"
    ];
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
}
