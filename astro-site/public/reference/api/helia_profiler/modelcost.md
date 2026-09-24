# helia_profiler.modelcost

The static cost model of a network, read from the model file before anything is built or run.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `872d67ad4e9083b1eacd7d6d3859148437b96b59`.

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

Source: `src/helia_profiler/modelcost/model_analysis.py:165`

### helia_profiler.ModelAnalysis.layers

`attribute` · `python`

```python
layers: list[LayerOps]
```

Source: `src/helia_profiler/modelcost/model_analysis.py:169`

### helia_profiler.ModelAnalysis.total_macs

`attribute` · `python`

```python
total_macs: int
```

Source: `src/helia_profiler/modelcost/model_analysis.py:170`

### helia_profiler.ModelAnalysis.total_ops

`attribute` · `python`

```python
total_ops: int
```

Source: `src/helia_profiler/modelcost/model_analysis.py:171`

### helia_profiler.ModelAnalysis.num_parameters

`attribute` · `python`

```python
num_parameters: int
```

Approximate parameter count (weights + biases).

Source: `src/helia_profiler/modelcost/model_analysis.py:172`

### helia_profiler.ModelAnalysis.engine

`attribute` · `python`

```python
engine: str = 'tflite'
```

Engine/interpreter that produced this analysis ('tflite', 'helia-rt', 'helia-aot').

Source: `src/helia_profiler/modelcost/model_analysis.py:174`

### helia_profiler.ModelAnalysis.ethos_u_op_count

`attribute` · `python`

```python
ethos_u_op_count: int
```

Number of Vela-generated ethos-u custom ops in the graph.

Source: `src/helia_profiler/modelcost/model_analysis.py:178`

### helia_profiler.ModelAnalysis.has_ethos_u_op

`attribute` · `python`

```python
has_ethos_u_op: bool
```

True when the model was compiled by Vela (contains ethos-u ops).

Source: `src/helia_profiler/modelcost/model_analysis.py:183`
