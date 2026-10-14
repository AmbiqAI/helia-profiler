# Public CLI choices

CLI choice annotations use public enums, preserving values, defaults, help,
errors and aliases; command adapters pass plain strings or `None`.
Registered power-driver names retain case and punctuation, including enum-reserved names.
The supported Typer range remains `>=0.16,<0.28`; minimum-version evidence is static.
Configuration validation stays in its existing owner ([#459](https://github.com/AmbiqAI/helia-profiler/issues/459)).
