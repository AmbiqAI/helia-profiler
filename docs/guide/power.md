# Power Measurement

!!! note "Under construction"
    This page will cover Joulescope integration for power profiling.

heliaPROFILER can capture current/voltage traces alongside PMU data using a
Joulescope instrument.

## Setup

```bash
pip install 'helia-profiler[power]'
```

## Usage

```bash
hpx profile model.tflite --power --power-duration 30
```
