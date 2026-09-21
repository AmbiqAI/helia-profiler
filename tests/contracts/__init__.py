"""Architectural contract tests for the HPX modular-architecture refactor.

These tests pin current behaviour before code moves behind new abstraction
boundaries (registries, capability objects, probe/transport protocols), so a
future PR that changes observable behaviour fails loudly here.

They are deliberately fast, deterministic, and hardware-free — every external
tool (JLinkExe, pylink, pyserial, Joulescope, NSX) is mocked at its boundary.
"""
