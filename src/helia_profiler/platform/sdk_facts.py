"""SDK compatibility facts pending a released catalog query."""

# WORKAROUND helia-platforms#168: topology does not establish HAL API availability.
_RSS_POWER_OFF_ROUTES = frozenset(
    {
        ("AM_PART_APOLLO330P", "apollo330P.h"),
        ("AM_PART_APOLLO510L", "apollo510L.h"),
    }
)


def rss_power_off_available(c_define: str, cmsis_header: str) -> bool:
    """Return true only for an SDK route known to expose the RSS power-off API."""
    return (c_define, cmsis_header) in _RSS_POWER_OFF_ROUTES
