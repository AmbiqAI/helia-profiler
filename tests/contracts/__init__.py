"""Architectural contract tests for the HPX modular-architecture refactor (WP0).

These tests PIN current behaviour before code moves behind new abstraction
boundaries (registries, capability objects, probe/transport protocols).  They
are the refactor's safety net: they encode the implicit invariants of the
transport-hardening baseline (commit a599105) so a future PR that changes the
observable behaviour fails loudly here.

They are deliberately fast, deterministic, and hardware-free — every external
tool (JLinkExe, pylink, pyserial, Joulescope, NSX) is mocked at its boundary.
"""
