# helia_profiler.run_guard

Who vouches for the board a run flashes: the guard protocol a capture consults and the operator guard the command line uses.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

## helia_profiler.OperatorGuard

`class` · `python`

```python
OperatorGuard(board: str, jlink_serial: str) -> None
```

`dataclass`

Capture authority from an operator who names the board and the J-Link probe on it.

HPX cannot prove which physical board a probe is wired to: the core's
CPUID and the probe list name a SoC and a serial, not a board. With this
guard the operator vouches for the board and holds it for the whole
capture, as ``hpx profile`` assumes. :meth:`verify_target` refuses a
capture whose board or probe serial differs from the ones the operator
named, and :meth:`check` allows every step. A harness that can prove the
board and lock it passes its own
:class:`~helia_profiler.FixtureCaptureGuard` instead.

**API tier:** `experimental`

Source: `src/helia_profiler/run_guard.py:14`

### helia_profiler.OperatorGuard.board

`attribute` · `python`

```python
board: str
```

Source: `src/helia_profiler/run_guard.py:28`

### helia_profiler.OperatorGuard.jlink_serial

`attribute` · `python`

```python
jlink_serial: str
```

Source: `src/helia_profiler/run_guard.py:29`

### helia_profiler.OperatorGuard.check

`method` · `python`

```python
check(*, require_free: bool, remaining_s: float) -> None
```

Allow the step: the operator holds the board for the whole capture.

Source: `src/helia_profiler/run_guard.py:37`

### helia_profiler.OperatorGuard.verify_target

`method` · `python`

```python
verify_target(*, target: FixtureTarget, jlink_serial: str) -> None
```

Refuse a target board or probe serial other than the operator's.

**Raises**

| Name | Description |
| --- | --- |
| ConfigError | The board or the serial differs from the operator's. |

Source: `src/helia_profiler/run_guard.py:41`

## helia_profiler.FixtureTarget

`class` · `python`

```python
FixtureTarget(board: str, device: str, load_address: int) -> None
```

`dataclass`

The board a fixture image runs on, its J-Link device and the image's load address.

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_target.py:19`

### helia_profiler.FixtureTarget.board

`attribute` · `python`

```python
board: str
```

Source: `src/helia_profiler/fixture_target.py:23`

### helia_profiler.FixtureTarget.device

`attribute` · `python`

```python
device: str
```

Source: `src/helia_profiler/fixture_target.py:24`

### helia_profiler.FixtureTarget.load_address

`attribute` · `python`

```python
load_address: int
```

Source: `src/helia_profiler/fixture_target.py:25`

### helia_profiler.FixtureTarget.verify

`method` · `python`

```python
verify() -> None
```

Source: `src/helia_profiler/fixture_target.py:27`

## helia_profiler.FixtureCaptureGuard

`class` · `python`

```python
FixtureCaptureGuard()
```

Caller-owned authorization and remaining-window checks.

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_capture.py:46`

### helia_profiler.FixtureCaptureGuard.check

`method` · `python`

```python
check(*, require_free: bool, remaining_s: float) -> None
```

Source: `src/helia_profiler/fixture_capture.py:49`

### helia_profiler.FixtureCaptureGuard.verify_target

`method` · `python`

```python
verify_target(*, target: FixtureTarget, jlink_serial: str) -> None
```

Require caller-verified physical board/serial identity and exclusive ownership.

Core CPUID and probe enumeration cannot establish the physical board.
Reject if the caller has no current independent board/serial evidence.

Source: `src/helia_profiler/fixture_capture.py:51`
