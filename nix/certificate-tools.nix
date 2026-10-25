{ pkgs }:
let
  pipWheel = pkgs.fetchurl {
    url = "https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl";
    sha256 = "fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70";
  };
  # GHSA-6q48-6c7w-9gw2: virtualenv seeds independently of Python's ensurepip.
  virtualenv = pkgs.python3Packages.virtualenv.overridePythonAttrs (old: {
    postPatch = (old.postPatch or "") + ''
      cp ${pipWheel} src/virtualenv/seed/wheels/embed/pip-26.2.2-py3-none-any.whl
      rm src/virtualenv/seed/wheels/embed/pip-26.1.2-py3-none-any.whl
      substituteInPlace src/virtualenv/seed/wheels/embed/__init__.py \
        --replace-fail 'pip-26.1.2-py3-none-any.whl' 'pip-26.2.2-py3-none-any.whl' \
        --replace-fail '382ff9f685ee3bc25864f820aa50505825f10f5458ffff07e30a6d96e5715cab' 'fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70'
    '';
  });
in
{
  inherit virtualenv;
  python312 = pkgs.python312.overrideAttrs (old: {
    postPatch = (old.postPatch or "") + ''
      rm Lib/ensurepip/_bundled/pip-*.whl
      cp ${pipWheel} Lib/ensurepip/_bundled/pip-26.2.2-py3-none-any.whl
      substituteInPlace Lib/ensurepip/__init__.py \
        --replace-fail '_PIP_VERSION = "25.0.1"' '_PIP_VERSION = "26.2.2"'
    '';
  });
  preCommit =
    (pkgs.pre-commit.override {
      python3Packages = pkgs.python3Packages // {
        inherit virtualenv;
      };
    }).overridePythonAttrs
      (_: rec {
        version = "4.6.0";
        src = pkgs.fetchFromGitHub {
          owner = "pre-commit";
          repo = "pre-commit";
          tag = "v${version}";
          hash = "sha256-WfajnE1PktzNs0Tand51/qUWEULGZqSNH48Ivu67kA8=";
        };
      });
}
