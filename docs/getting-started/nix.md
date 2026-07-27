# Nix environment

The repository contains a pinned Nix flake for an x86-64 Linux development and
runtime environment. It is independent of NixOS: any conventional Linux
distribution with Nix and flakes enabled can use it.

## What the flake provides

- Python 3.12 and heliaPROFILER from the current checkout
- heliaAOT and LiteRT
- neuralSPOT X
- pylink and the Joulescope native driver
- CMake and Ninja
- GNU Arm Embedded 15.2
- Arm Toolchain for Embedded 22.1.0 with its Newlib overlay
- SEGGER J-Link 9.60, after explicit license acceptance
- test, lint, notebook, and documentation dependencies in the development shell

USB access remains a one-time host configuration because Nix builds cannot
modify `/etc/udev/rules.d`.

## First-time setup

Download the J-Link 9.60 Linux x86-64 TGZ from the
[SEGGER download page](https://www.segger.com/downloads/jlink/). Review and
accept SEGGER's license there, then add the pinned archive to the local Nix
store:

```bash
nix run .#prepare-jlink -- ~/Downloads/JLink_Linux_x86_64.tgz
```

The helper rejects any archive whose SHA-256 does not match the tested 9.60
release. The archive and derived J-Link package stay in the user's local Nix
store and must not be uploaded to a public binary cache.

Install the J-Link and Joulescope USB access rules:

```bash
nix run .#install-udev-rules -- --dry-run
nix run .#install-udev-rules
```

This is the only step that uses `sudo`. Reconnect the devices afterward.

Enter the editable development environment:

```bash
nix develop
hpx doctor
```

Run `hpx`, `pytest`, `ruff`, and the other Python entry points directly.
`uv2nix` has already constructed the environment from `uv.lock`, so `uv run`
must not be used inside the shell.

For a packaged invocation that does not expose a development shell:

```bash
nix run . -- doctor
nix run . -- profile model.tflite --engine helia-aot
```

## Prove that dependencies do not leak from the host

The fast checks are:

```bash
nix flake check
nix run .#verify-isolation
```

The isolation command starts `nix develop` with the caller's environment
removed. It fails if `hpx`, Python, CMake, Ninja, GNU Arm Embedded, or J-Link
resolves from `/run/current-system/sw`, `/etc/profiles`, or a user profile. It
also imports heliaAOT, LiteRT, heliaPROFILER, and the Joulescope driver and
executes the packaged ATfE compiler.

This detects accidental dependencies on a NixOS configuration, but it can still
reuse artifacts already present in the machine's Nix store. For a true
from-empty-store check, use a disposable container:

```bash
docker run --rm -it \
  -v "$PWD:/src:ro" \
  -v "$HOME/Downloads/JLink_Linux_x86_64.tgz:/tmp/JLink_Linux_x86_64.tgz:ro" \
  nixos/nix:latest sh
```

Then, inside the container:

```bash
nix store add --mode flat \
  --name JLink_Linux_x86_64.tgz \
  /tmp/JLink_Linux_x86_64.tgz
cd /src
nix flake check path:.
nix run path:.#verify-isolation
```

The container starts with a separate Nix store and no packages from the host
operating system. It validates software containment, not physical USB access.
Hardware testing should still be performed on the host after installing the
udev rules.
