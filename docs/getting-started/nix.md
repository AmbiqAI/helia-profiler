# Nix environment

The repository contains a pinned Nix flake for native development and runtime
environments on x86-64 Linux, ARM64 Linux, and Apple Silicon macOS. It is
independent of NixOS: a conventional Linux distribution or macOS installation
with Nix and flakes enabled can use it.

## What the flake provides

- Python 3.12 and heliaPROFILER from the current checkout
- heliaAOT and LiteRT
- neuralSPOT X
- pylink and the Joulescope native driver
- CMake and Ninja
- GNU Arm Embedded 15.2
- Arm Toolchain for Embedded 22.1.0 with its Newlib overlay
- SEGGER J-Link 9.62, downloaded automatically after explicit license acceptance
- test, lint, notebook, and documentation dependencies in the development shell

On Linux, USB access remains a one-time host configuration because Nix builds
cannot modify `/etc/udev/rules.d`. macOS needs no equivalent udev step.

## First-time setup

Review SEGGER's
[download terms](https://www.segger.com/downloads/jlink/)
and [software licensing](https://www.segger.com/purchase/licensing/). Then
explicitly accept them and let the helper download, verify, and add the pinned
J-Link 9.62 package for the current host to the local Nix store:

```bash
nix run .#prepare-jlink -- --accept-license
```

Passing `--accept-license` confirms that the user accepts SEGGER's terms and
will use the software only with original SEGGER products or authorized OEM
products. The helper selects the Linux x86-64, Linux ARM64, or Apple Silicon
macOS artifact automatically, submits SEGGER's required acceptance field,
downloads directly from SEGGER, and rejects any response whose checksum or
size does not match the tested 9.62 release. The archive and derived J-Link
package stay in the user's local Nix store and must not be uploaded to a public
binary cache.

On Linux, install the J-Link and Joulescope USB access rules:

```bash
nix run .#install-udev-rules -- --dry-run
nix run .#install-udev-rules
```

This is the only step that uses `sudo`. Reconnect the devices afterward.
macOS grants USB access through its native device services and does not use
udev, so skip these two commands there.

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
  nixos/nix:latest sh
```

Then, inside the container:

```bash
cd /src
nix run path:.#prepare-jlink -- --accept-license
nix flake check path:.
nix run path:.#verify-isolation
```

The container starts with a separate Nix store and no packages from the host
operating system. It validates the x86-64 Linux environment's software
containment, not physical USB access. Hardware testing should still be
performed directly on the host; Linux hosts need the udev rules described
above.
