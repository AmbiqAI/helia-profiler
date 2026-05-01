"""Pytest harness for heliaPROFILER hardware validation.

Marker: ``@pytest.mark.hardware`` (excluded from the default unit test
run — see ``[tool.pytest.ini_options].addopts`` in ``pyproject.toml``).

Invoke via ``hpx validate`` (preferred) or directly::

    pytest -m hardware tests/validation/ \\
        --mlperf-models kws,ic --mlperf-engines rt,aot --mlperf-power off
"""
