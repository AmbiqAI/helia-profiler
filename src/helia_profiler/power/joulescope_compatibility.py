"""Host compatibility policy for Joulescope devices."""


def joulescope_python_hint(devices: tuple[str, ...], python: tuple[int, int]) -> str | None:
    """Return remediation for an unsupported device/Python combination."""
    if "js320" in devices and python < (3, 12):
        return (
            "Run JS320 with Python >= 3.12 in the matching HPX environment. "
            "JS110 and JS220 remain supported. See helia-profiler#228."
        )
    return None
