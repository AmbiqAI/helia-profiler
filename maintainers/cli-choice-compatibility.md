# Public CLI choices

CLI choice annotations use public enums, preserving values, defaults, help,
errors and aliases; command adapters pass plain strings or `None`.
Registered power-driver names retain case and punctuation, including enum-reserved names.
The supported Typer range is `>=0.16`, using public enum annotations without an upper cap.
CLI contract tests cover the minimum and the locked release. Single-choice errors
may use singular wording depending on the Click implementation.
Configuration validation stays in its existing owner ([#459](https://github.com/AmbiqAI/helia-profiler/issues/459)).
