"""Host-environment probes: tools, toolchains, caches, and binary inspection.

Everything here asks questions about the machine hpx runs on — ``doctor``
(dependency checks), ``toolchains``/``toolchain_probe`` (compiler specs and
version/section probes), ``elf_inventory`` (ELF section/segment/symbol
inventories), ``memory_measurement`` (inventory x memory-map join), and
``cache_dirs`` (persistent cache roots).
"""
