These frozen run records were generated with the pre-retirement configuration
contract at commit 9333ad913974e7294905340643dbb4661513cf4f. The model is
`b"TFL3" + bytes(64)`; the local module contains `nsx-module.yaml` with
`module: nsx-core\n`. Compiler probing was stubbed and the build environment
was empty. Before serializing, the frozen dataclass's model and module paths
were normalized to `/historical/model.tflite` and `/historical/module` using
that same commit's hashing implementation. Tests supply the equivalent model
and stand in for local directory availability; they retain the stored content
identity and compare the exact recorded intent hashes. Do not regenerate
these fixtures using the current configuration contract.

`baseline.json` is the compatibility baseline at that commit. The replay test
loads it in place of the packaged baseline, so later baseline moves do not
invalidate these records.
