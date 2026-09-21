# helia_profiler.vocab

The fixed vocabularies a configuration selects from: engines, toolchains, capture transports, memory placements and reset strategies.

Every name on this page is imported from `helia_profiler`.

**API tier:** `stable`

Generated from the `src/helia_profiler` tree `872d67ad4e9083b1eacd7d6d3859148437b96b59`.

## helia_profiler.EngineType

`class` · `python`

```python
EngineType()
```

Supported inference engine identifiers.

``StrEnum`` so values are interchangeable with raw strings — Jinja
templates and YAML configs can compare against the canonical hyphen
form (``"helia-aot"``) without manually unwrapping ``.value``.

**API tier:** `stable`

Source: `src/helia_profiler/engines/__init__.py:17`

### helia_profiler.EngineType.TFLM

`constant` · `python`

```python
TFLM = 'tflm'
```

Source: `src/helia_profiler/engines/__init__.py:25`

### helia_profiler.EngineType.HELIA_RT

`constant` · `python`

```python
HELIA_RT = 'helia-rt'
```

Source: `src/helia_profiler/engines/__init__.py:26`

### helia_profiler.EngineType.HELIA_AOT

`constant` · `python`

```python
HELIA_AOT = 'helia-aot'
```

Source: `src/helia_profiler/engines/__init__.py:27`

### helia_profiler.EngineType.EXECUTORCH

`constant` · `python`

```python
EXECUTORCH = 'executorch'
```

Source: `src/helia_profiler/engines/__init__.py:28`

### helia_profiler.EngineType.wire_name

`attribute` · `python`

```python
wire_name: str
```

Identifier the firmware emits on the wire (``HPX_ENGINE=``).

The wire protocol predates the hyphenated config spelling and uses
C-identifier-safe names, so ``helia-aot`` goes out as ``helia_aot``.
Owned here rather than derived inline at the render boundary so the
two spellings cannot drift apart.

Source: `src/helia_profiler/engines/__init__.py:31`

### helia_profiler.EngineType.short_slug

`attribute` · `python`

```python
short_slug: str
```

Compact identifier used in case IDs and report tables.

Source: `src/helia_profiler/engines/__init__.py:42`

## helia_profiler.Toolchain

`class` · `python`

```python
Toolchain()
```

Supported cross-compiler toolchains for the profiler firmware.

``GCC`` and ``ARM_NONE_EABI_GCC`` are aliases — both resolve to the
GNU Arm Embedded toolchain.  ``ARMCLANG`` is Arm Compiler 6 (Keil),
``ATFE`` is the Arm Toolchain for Embedded (LLVM).

**API tier:** `stable`

Source: `src/helia_profiler/vocab.py:18`

### helia_profiler.Toolchain.ARM_NONE_EABI_GCC

`constant` · `python`

```python
ARM_NONE_EABI_GCC = 'arm-none-eabi-gcc'
```

Source: `src/helia_profiler/vocab.py:26`

### helia_profiler.Toolchain.GCC

`constant` · `python`

```python
GCC = 'gcc'
```

Source: `src/helia_profiler/vocab.py:27`

### helia_profiler.Toolchain.ARMCLANG

`constant` · `python`

```python
ARMCLANG = 'armclang'
```

Source: `src/helia_profiler/vocab.py:28`

### helia_profiler.Toolchain.ATFE

`constant` · `python`

```python
ATFE = 'atfe'
```

Source: `src/helia_profiler/vocab.py:29`

## helia_profiler.Placement

`class` · `python`

```python
Placement()
```

Logical placement region for arenas / weights / model data.

The four logical regions abstract over the SoC physical layout —
e.g. ``Placement.TCM`` covers DTCM on AP5 and is unavailable on AP3.
Engine adapters that emit physical names (heliaAOT's ``DTCM``,
``ITCM``, …) normalise to this enum at the adapter boundary.

**API tier:** `stable`

Source: `src/helia_profiler/platform/placement.py:24`

### helia_profiler.Placement.TCM

`constant` · `python`

```python
TCM = 'tcm'
```

Source: `src/helia_profiler/platform/placement.py:33`

### helia_profiler.Placement.SRAM

`constant` · `python`

```python
SRAM = 'sram'
```

Source: `src/helia_profiler/platform/placement.py:34`

### helia_profiler.Placement.MRAM

`constant` · `python`

```python
MRAM = 'mram'
```

Source: `src/helia_profiler/platform/placement.py:35`

### helia_profiler.Placement.PSRAM

`constant` · `python`

```python
PSRAM = 'psram'
```

Source: `src/helia_profiler/platform/placement.py:36`

## helia_profiler.Transport

`class` · `python`

```python
Transport()
```

Host↔target transport for capture and heartbeat traffic.

**API tier:** `stable`

Source: `src/helia_profiler/vocab.py:32`

### helia_profiler.Transport.RTT

`constant` · `python`

```python
RTT = 'rtt'
```

Source: `src/helia_profiler/vocab.py:35`

### helia_profiler.Transport.USB_CDC

`constant` · `python`

```python
USB_CDC = 'usb_cdc'
```

Source: `src/helia_profiler/vocab.py:36`

### helia_profiler.Transport.SWO

`constant` · `python`

```python
SWO = 'swo'
```

Source: `src/helia_profiler/vocab.py:37`

### helia_profiler.Transport.UART

`constant` · `python`

```python
UART = 'uart'
```

Source: `src/helia_profiler/vocab.py:38`

## helia_profiler.ResetStrategy

`class` · `python`

```python
ResetStrategy()
```

User-selectable reset policy for target lifecycle preparation.

**API tier:** `stable`

Source: `src/helia_profiler/target/lifecycle.py:42`

### helia_profiler.ResetStrategy.AUTO

`constant` · `python`

```python
AUTO = 'auto'
```

Source: `src/helia_profiler/target/lifecycle.py:45`

### helia_profiler.ResetStrategy.POWER_CYCLE

`constant` · `python`

```python
POWER_CYCLE = 'power_cycle'
```

Source: `src/helia_profiler/target/lifecycle.py:46`

### helia_profiler.ResetStrategy.NONE

`constant` · `python`

```python
NONE = ResetAction.NONE.value
```

Source: `src/helia_profiler/target/lifecycle.py:47`

### helia_profiler.ResetStrategy.DEBUG_RESET

`constant` · `python`

```python
DEBUG_RESET = ResetAction.DEBUG_RESET.value
```

Source: `src/helia_profiler/target/lifecycle.py:48`

### helia_profiler.ResetStrategy.SWPOI_RESET

`constant` · `python`

```python
SWPOI_RESET = ResetAction.SWPOI_RESET.value
```

Source: `src/helia_profiler/target/lifecycle.py:49`

### helia_profiler.ResetStrategy.DEBUG_RESET_THEN_SWPOI

`constant` · `python`

```python
DEBUG_RESET_THEN_SWPOI = ResetAction.DEBUG_RESET_THEN_SWPOI.value
```

Source: `src/helia_profiler/target/lifecycle.py:50`
