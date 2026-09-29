Every field a YAML config file or a `Session` can set, generated from the
`ProfileConfig` models in `src/helia_profiler/config/` by
`tools/docs/extract_schema.py` at the source tree this site was built from.
Fields are grouped by their top-level key (`model`, `engine`, `target`,
`profiling`, `power`, `output`, `timeouts`, `build`), plus the top-level
scalars `frozen`, `work_dir`, `clean` and `verbose`, with the YAML path,
the CLI flag where one exists, the type and the default. Unknown keys are
rejected at load time, so a key that is not on this page is not a key.
[Configuration](../../guide/configuration/) covers how files, flags and
defaults combine.
