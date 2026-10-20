# helia_profiler.identity

Shared identity vocabulary: canonical engine and kernel names with today's spellings as aliases, and unknown facts that carry their reason.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

## helia_profiler.CanonicalEngine

`class` · `python`

```python
CanonicalEngine()
```

An inference engine, by its shared name.

**API tier:** `experimental`

Source: `src/helia_profiler/identity.py:24`

### helia_profiler.CanonicalEngine.LITERT

`constant` · `python`

```python
LITERT = 'litert'
```

Source: `src/helia_profiler/identity.py:27`

### helia_profiler.CanonicalEngine.HELIA_RT

`constant` · `python`

```python
HELIA_RT = 'helia-rt'
```

Source: `src/helia_profiler/identity.py:28`

### helia_profiler.CanonicalEngine.HELIA_AOT

`constant` · `python`

```python
HELIA_AOT = 'helia-aot'
```

Source: `src/helia_profiler/identity.py:29`

### helia_profiler.CanonicalEngine.EXECUTORCH

`constant` · `python`

```python
EXECUTORCH = 'executorch'
```

Source: `src/helia_profiler/identity.py:30`

## helia_profiler.CanonicalKernel

`class` · `python`

```python
CanonicalKernel()
```

A kernel library, by its shared name.

``CMSIS_NN`` is Arm's CMSIS-NN. ``HELIA_CORE`` is heliaCORE, the product
name of Ambiq's ``ns-cmsis-nn`` library.

**API tier:** `experimental`

Source: `src/helia_profiler/identity.py:33`

### helia_profiler.CanonicalKernel.REFERENCE

`constant` · `python`

```python
REFERENCE = 'reference'
```

Source: `src/helia_profiler/identity.py:40`

### helia_profiler.CanonicalKernel.CMSIS_NN

`constant` · `python`

```python
CMSIS_NN = 'cmsis-nn'
```

Source: `src/helia_profiler/identity.py:41`

### helia_profiler.CanonicalKernel.HELIA_CORE

`constant` · `python`

```python
HELIA_CORE = 'helia-core'
```

Source: `src/helia_profiler/identity.py:42`

## helia_profiler.Unknown

`class` · `python`

```python
Unknown(reason: str) -> None
```

`dataclass`

A fact that could not be determined, with the reason.

**API tier:** `experimental`

Source: `src/helia_profiler/identity.py:45`

### helia_profiler.Unknown.reason

`attribute` · `python`

```python
reason: str
```

Source: `src/helia_profiler/identity.py:49`

## helia_profiler.canonical_engine

`function` · `python`

```python
canonical_engine(name: str) -> CanonicalEngine
```

The shared name for an engine, from its canonical name or any alias.

**API tier:** `experimental`

Source: `src/helia_profiler/identity.py:84`

## helia_profiler.canonical_kernel

`function` · `python`

```python
canonical_kernel(name: str) -> CanonicalKernel
```

The shared name for a kernel library, from its canonical name or any alias.

**API tier:** `experimental`

Source: `src/helia_profiler/identity.py:89`
