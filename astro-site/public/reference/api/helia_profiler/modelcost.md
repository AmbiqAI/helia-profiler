# helia_profiler.modelcost

The static cost model of a network, read from the model file before anything is built or run.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `d8fc7a994d8f2b900482cdc6119d92751a2b7315`.

## helia_profiler.ModelAnalysis

`class` · `python`

```python
ModelAnalysis(
    layers: list[LayerOps],
    total_macs: int,
    total_ops: int,
    num_parameters: int,
    engine: str = 'tflite',
) -> None
```

`dataclass`

Full model analysis result.

**API tier:** `experimental`

Source: `src/helia_profiler/modelcost/model_analysis.py:177`

### helia_profiler.ModelAnalysis.layers

`attribute` · `python`

```python
layers: list[LayerOps]
```

Source: `src/helia_profiler/modelcost/model_analysis.py:181`

### helia_profiler.ModelAnalysis.total_macs

`attribute` · `python`

```python
total_macs: int
```

Source: `src/helia_profiler/modelcost/model_analysis.py:182`

### helia_profiler.ModelAnalysis.total_ops

`attribute` · `python`

```python
total_ops: int
```

Source: `src/helia_profiler/modelcost/model_analysis.py:183`

### helia_profiler.ModelAnalysis.num_parameters

`attribute` · `python`

```python
num_parameters: int
```

Approximate parameter count (weights + biases).

Source: `src/helia_profiler/modelcost/model_analysis.py:184`

### helia_profiler.ModelAnalysis.engine

`attribute` · `python`

```python
engine: str = 'tflite'
```

Engine/interpreter that produced this analysis ('tflite', 'helia-rt', 'helia-aot').

Source: `src/helia_profiler/modelcost/model_analysis.py:186`

### helia_profiler.ModelAnalysis.ethos_u_op_count

`attribute` · `python`

```python
ethos_u_op_count: int
```

Number of Vela-generated ethos-u custom ops in the graph.

Source: `src/helia_profiler/modelcost/model_analysis.py:190`

### helia_profiler.ModelAnalysis.has_ethos_u_op

`attribute` · `python`

```python
has_ethos_u_op: bool
```

True when the model was compiled by Vela (contains ethos-u ops).

Source: `src/helia_profiler/modelcost/model_analysis.py:195`
