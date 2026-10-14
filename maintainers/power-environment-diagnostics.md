# Power environment diagnostics

The pure Python/device compatibility rule lives in `power/joulescope_compatibility.py`.
The selected-device guard applies it before native open; doctor's environment-check
registry uses it to report the same incompatibility alongside interpreter and driver metadata.
Doctor reads host metadata without contacting hardware; unobserved devices remain unknown.
Missing driver metadata does not conceal a known Python/device incompatibility.

The registry also compares the PATH launcher with the current environment's entry point.
The same filesystem entry is accepted, including aliases. Other launcher targets remain
unverified: a shim can forward correctly, so its location alone does not prove it stale.
The diagnostic shows the current environment's entry point without invoking the launcher.
