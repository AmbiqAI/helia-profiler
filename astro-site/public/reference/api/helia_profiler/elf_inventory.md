# helia_profiler.elf_inventory

The measured memory inventory of a linked image, read with the toolchain's own tools: every section with its address and size, the load segments, and how many rows could not be parsed.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

## helia_profiler.ElfSection

`class` · `python`

```python
ElfSection(
    name: str,
    address: int,
    size: int,
    nobits: bool,
    allocated: bool,
    index: int = -1,
    linker_reserved: bool = False,
) -> None
```

`dataclass`

One section of the linked image, address included.

``index`` is the ELF section-header index — the section's IDENTITY.
Section NAMES are not unique: armlink emits one section per execution
region per content class all named after the region (a real NSX AP510
scatter link yields two ``MCU_TCM`` sections, PROGBITS + NOBITS), and
NSX's own gcc scripts declare ``.text`` twice. Never key a collection
of these on ``name`` alone — bytes vanish (#176).

``linker_reserved`` marks the NOBITS+allocated regions the linker
manufactures rather than the program needing them (today: fill-to-end
heaps, armlink's ``ARM_LIB_HEAP`` — the #24/#131 rule, same predicate
as the ``BinarySections.reserved`` split). ``.stack``/``ARM_LIB_STACK``
are deliberately NOT reserved: the stack is live memory the firmware
needs — but note it usually sits OUTSIDE the per-family app window
(``LinkedRegionWindow.app_window``), which is how the free-space math
stays consistent.

**API tier:** `experimental`

Source: `src/helia_profiler/hostenv/elf_inventory.py:68`

### helia_profiler.ElfSection.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/hostenv/elf_inventory.py:88`

### helia_profiler.ElfSection.address

`attribute` · `python`

```python
address: int
```

Source: `src/helia_profiler/hostenv/elf_inventory.py:89`

### helia_profiler.ElfSection.size

`attribute` · `python`

```python
size: int
```

Source: `src/helia_profiler/hostenv/elf_inventory.py:90`

### helia_profiler.ElfSection.nobits

`attribute` · `python`

```python
nobits: bool
```

Source: `src/helia_profiler/hostenv/elf_inventory.py:91`

### helia_profiler.ElfSection.allocated

`attribute` · `python`

```python
allocated: bool
```

Source: `src/helia_profiler/hostenv/elf_inventory.py:92`

### helia_profiler.ElfSection.index

`attribute` · `python`

```python
index: int = -1
```

Source: `src/helia_profiler/hostenv/elf_inventory.py:93`

### helia_profiler.ElfSection.linker_reserved

`attribute` · `python`

```python
linker_reserved: bool = False
```

Source: `src/helia_profiler/hostenv/elf_inventory.py:94`

## helia_profiler.LoadSegment

`class` · `python`

```python
LoadSegment(virtual_address: int, physical_address: int, file_size: int, memory_size: int) -> None
```

`dataclass`

One PT_LOAD program header: where bytes load (physical) vs run
(virtual) — the fact that makes initialized data's MRAM load image
accountable (#133 D3).

Toolchain shape caveat (#176, measured on real links against the NSX
AP510 scripts): GNU ld emits one PT_LOAD per load
region (``.data`` gets vaddr in DTCM, paddr in MRAM — per-section
recovery works). armlink emits a SINGLE PT_LOAD whose vaddr==paddr is
the image base and whose ``memory_size`` SUMS discontiguous execution
regions — ``paddr + memory_size`` is a phantom span, and mapping
sections into segments by vaddr containment understates armlink MRAM
~400x. Region-level load-image accounting that works on BOTH families:
sum ``file_size`` grouped by ``classify_address(physical_address)``,
never walk sections into segments. Per-symbol load-image attribution
on armlink is NOT recoverable from these primitives.

**API tier:** `experimental`

Source: `src/helia_profiler/hostenv/elf_inventory.py:97`

### helia_profiler.LoadSegment.virtual_address

`attribute` · `python`

```python
virtual_address: int
```

Source: `src/helia_profiler/hostenv/elf_inventory.py:115`

### helia_profiler.LoadSegment.physical_address

`attribute` · `python`

```python
physical_address: int
```

Source: `src/helia_profiler/hostenv/elf_inventory.py:116`

### helia_profiler.LoadSegment.file_size

`attribute` · `python`

```python
file_size: int
```

Source: `src/helia_profiler/hostenv/elf_inventory.py:117`

### helia_profiler.LoadSegment.memory_size

`attribute` · `python`

```python
memory_size: int
```

Source: `src/helia_profiler/hostenv/elf_inventory.py:118`

## helia_profiler.SectionInventory

`class` · `python`

```python
SectionInventory(
    sections: tuple[ElfSection, ...],
    segments: tuple[LoadSegment, ...] = (),
    unparsed_rows: int = 0,
) -> None
```

`dataclass`

The measured memory inventory of one linked binary.

``unparsed_rows`` counts SECTION rows/blocks that LOOKED like
inventory entries but failed to parse. When nonzero the section
inventory is PARTIAL — occupancy computed from it is understated, and
a consumer must treat the measured view as unavailable rather
than publish a silently-low number (#131's discipline, structural
instead of a debug log). Segment parse failures are NOT counted:
segments refine the inventory and their absence degrades
independently (empty tuple), on both tool paths.

**API tier:** `experimental`

Source: `src/helia_profiler/hostenv/elf_inventory.py:121`

### helia_profiler.SectionInventory.sections

`attribute` · `python`

```python
sections: tuple[ElfSection, ...]
```

Source: `src/helia_profiler/hostenv/elf_inventory.py:134`

### helia_profiler.SectionInventory.segments

`attribute` · `python`

```python
segments: tuple[LoadSegment, ...] = ()
```

Source: `src/helia_profiler/hostenv/elf_inventory.py:135`

### helia_profiler.SectionInventory.unparsed_rows

`attribute` · `python`

```python
unparsed_rows: int = 0
```

Source: `src/helia_profiler/hostenv/elf_inventory.py:136`

## helia_profiler.section_inventory

`function` · `python`

```python
section_inventory(binary_path: Path, toolchain: str, *, timeout_s: int = 30) -> SectionInventory | None
```

The measured section/segment inventory of *binary_path*, or None.

Dispatches like :func:`binary_sections`: readelf for the size-probed
toolchains, ``fromelf --text -v`` for armclang. Degrades to ``None`` on
any tool or parse failure — the measured memory view simply does not
exist for that run, exactly like an absent ``reserved`` split.

**API tier:** `experimental`

Source: `src/helia_profiler/hostenv/elf_inventory.py:335`
