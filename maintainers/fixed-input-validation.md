# Fixed INT8 input validation

This option requires the `analysis` extra (`helia-profiler[analysis]`).

Set `model.validation_data` to an NPZ file containing only `input_0` and
`output_0`, both INT8 arrays matching the concrete input/output tensor shapes
in the original TFLite model. The optional path supports one input and one
output on TFLM or heliaAOT, without variable tensors, power measurement or a
busy-loop probe. A dynamic shape signature is permitted only with the existing
concrete tensor shape; firmware also checks the allocated byte sizes.

Firmware embeds only the input. Before clean timing, it performs an inference
and emits the actual output, model hash and input hash. The capture stage saves
`validation-capture.txt` and requires exact output equality. Missing, repeated,
malformed, out-of-frame or mismatched records fail capture. Exactly one complete
START/END capture frame is required. The expected output is never
embedded in firmware. One matching vector does not establish dataset accuracy.

For these runs both engines restore the fixed bytes inside every clean timing
iteration. The workload label is `golden_int8_refill_included_v1`: latency
includes input copy and invocation/status checking. This is a whole-stack
measurement with each selected kernel provider, not isolated runtime overhead.
The input array and validation firmware also contribute to linked footprint.
Existing raw-zero/default and power configurations keep their prior behavior.

A numerical admission capture should precede timing repeats; retain its raw
record and the exact source, model, golden, compiler, dependency and firmware
identities. Capture-health success without this option has no numerical meaning.
See issue #369 for the first KWS comparison and its target-qualification status.
