"""Shared Ethos-U NPU ("ethos_u" backend) NSX module identity.

Shared by the heliaRT and heliaAOT engine adapters: the ``nsx-npu`` registry
module vendors the Ethos-U core driver and the ``nsx_npu_init()`` bring-up
helper that both engines' ``ethos_u`` backend depends on. Lives here (rather
than inside either engine's package) per issue #7's layering rule: one engine
package must not import another's internals.
"""

from __future__ import annotations

NSX_NPU_MODULE = "nsx-npu"
NSX_NPU_PROJECT = "nsx-ambiq-sdk"
