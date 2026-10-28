# helia_profiler.run_guard

Who vouches for the board a run flashes and the instrument it powers or measures with: the guard protocols a capture consults, the operator guard the command line uses, and the rail and supply requests a power run selects.

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

Source: `src/helia_profiler/fixtures/target.py:19`

### helia_profiler.FixtureTarget.board

`attribute` · `python`

```python
board: str
```

Source: `src/helia_profiler/fixtures/target.py:23`

### helia_profiler.FixtureTarget.device

`attribute` · `python`

```python
device: str
```

Source: `src/helia_profiler/fixtures/target.py:24`

### helia_profiler.FixtureTarget.load_address

`attribute` · `python`

```python
load_address: int
```

Source: `src/helia_profiler/fixtures/target.py:25`

### helia_profiler.FixtureTarget.verify

`method` · `python`

```python
verify() -> None
```

Source: `src/helia_profiler/fixtures/target.py:27`

## helia_profiler.FixtureCaptureGuard

`class` · `python`

```python
FixtureCaptureGuard()
```

Caller-owned authorization and remaining-window checks.

**API tier:** `experimental`

Source: `src/helia_profiler/fixtures/capture.py:38`

### helia_profiler.FixtureCaptureGuard.check

`method` · `python`

```python
check(*, require_free: bool, remaining_s: float) -> None
```

Source: `src/helia_profiler/fixtures/capture.py:41`

### helia_profiler.FixtureCaptureGuard.verify_target

`method` · `python`

```python
verify_target(*, target: FixtureTarget, jlink_serial: str) -> None
```

Require caller-verified physical board/serial identity and exclusive ownership.

Core CPUID and probe enumeration cannot establish the physical board.
Reject if the caller has no current independent board/serial evidence.

Source: `src/helia_profiler/fixtures/capture.py:43`

## helia_profiler.FixtureBoardPowerRequest

`class` · `python`

```python
FixtureBoardPowerRequest(driver: str, serial: str, rail: str) -> None
```

`dataclass`

Explicit station-owned supply path; enabling it does not request energy measurement.

**API tier:** `experimental`

Source: `src/helia_profiler/fixtures/power.py:42`

### helia_profiler.FixtureBoardPowerRequest.driver

`attribute` · `python`

```python
driver: str
```

Source: `src/helia_profiler/fixtures/power.py:46`

### helia_profiler.FixtureBoardPowerRequest.serial

`attribute` · `python`

```python
serial: str
```

Source: `src/helia_profiler/fixtures/power.py:47`

### helia_profiler.FixtureBoardPowerRequest.rail

`attribute` · `python`

```python
rail: str
```

Source: `src/helia_profiler/fixtures/power.py:48`

## helia_profiler.FixtureRailPowerRequest

`class` · `python`

```python
FixtureRailPowerRequest(
    serial: str,
    rail: str,
    io_voltage: float,
    sync_gpio_pin: int,
    state_gpio_pin: int,
    go_gpio_pin: int,
    sync_input_index: int = 0,
    state_input_index: int = 1,
    go_output_index: int = 0,
    stats_rate_hz: int = 1000,
) -> None
```

`dataclass`

Station-selected instrument/rail and channels, separate from firmware wiring.

**API tier:** `experimental`

Source: `src/helia_profiler/fixtures/power.py:57`

### helia_profiler.FixtureRailPowerRequest.serial

`attribute` · `python`

```python
serial: str
```

Source: `src/helia_profiler/fixtures/power.py:61`

### helia_profiler.FixtureRailPowerRequest.rail

`attribute` · `python`

```python
rail: str
```

Source: `src/helia_profiler/fixtures/power.py:62`

### helia_profiler.FixtureRailPowerRequest.io_voltage

`attribute` · `python`

```python
io_voltage: float
```

Source: `src/helia_profiler/fixtures/power.py:63`

### helia_profiler.FixtureRailPowerRequest.sync_gpio_pin

`attribute` · `python`

```python
sync_gpio_pin: int
```

Source: `src/helia_profiler/fixtures/power.py:64`

### helia_profiler.FixtureRailPowerRequest.state_gpio_pin

`attribute` · `python`

```python
state_gpio_pin: int
```

Source: `src/helia_profiler/fixtures/power.py:65`

### helia_profiler.FixtureRailPowerRequest.go_gpio_pin

`attribute` · `python`

```python
go_gpio_pin: int
```

Source: `src/helia_profiler/fixtures/power.py:66`

### helia_profiler.FixtureRailPowerRequest.sync_input_index

`attribute` · `python`

```python
sync_input_index: int = 0
```

Source: `src/helia_profiler/fixtures/power.py:67`

### helia_profiler.FixtureRailPowerRequest.state_input_index

`attribute` · `python`

```python
state_input_index: int = 1
```

Source: `src/helia_profiler/fixtures/power.py:68`

### helia_profiler.FixtureRailPowerRequest.go_output_index

`attribute` · `python`

```python
go_output_index: int = 0
```

Source: `src/helia_profiler/fixtures/power.py:69`

### helia_profiler.FixtureRailPowerRequest.stats_rate_hz

`attribute` · `python`

```python
stats_rate_hz: int = 1000
```

Source: `src/helia_profiler/fixtures/power.py:70`

### helia_profiler.FixtureRailPowerRequest.firmware_wiring

`method` · `python`

```python
firmware_wiring() -> FixturePowerSync
```

Source: `src/helia_profiler/fixtures/power.py:93`

## helia_profiler.FixturePowerGuard

`class` · `python`

```python
FixturePowerGuard()
```

Station authority for the board and measured instrument/rail.

**API tier:** `experimental`

Source: `src/helia_profiler/fixtures/power.py:97`

### helia_profiler.FixturePowerGuard.check

`method` · `python`

```python
check(*, require_free: bool, remaining_s: float) -> None
```

Source: `src/helia_profiler/fixtures/power.py:100`

### helia_profiler.FixturePowerGuard.verify_power_device

`method` · `python`

```python
verify_power_device(*, serial: str, rail: str) -> None
```

Source: `src/helia_profiler/fixtures/power.py:102`
