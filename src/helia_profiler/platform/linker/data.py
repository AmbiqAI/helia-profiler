"""Read the committed generated SDK layouts without resolving a build environment."""

from __future__ import annotations

import hashlib
import json
from functools import cache
from importlib.resources import files
from types import MappingProxyType
from typing import Any

from ..memory_map import LinkedRegionWindow, LinkFamily
from ..placement import MemoryRegion
from ..soc import MemoryRange
from .model import LinkerFile, LinkerInput, LinkerRegion


@cache
def _table() -> dict[str, Any]:
    data = json.loads(
        files("helia_profiler").joinpath("data/linker-windows-v1.json").read_text(encoding="utf-8")
    )
    if (data.get("schema"), data.get("schema_version")) != ("hpx.linker-windows", 1):
        raise ValueError("Unsupported generated linker layout data")
    return data


@cache
def characterized_input(
    soc_name: str, link_family: LinkFamily, linker_profile: str = "default"
) -> LinkerInput | None:
    """The expected input for a characterized profile; no actual-build claim is made."""
    if linker_profile != "default":
        return None
    table = _table()
    entry = table["socs"].get(soc_name)
    if entry is None:
        return None
    family = entry["families"][link_family.value]
    definition = {"sdk_commit": table["sdk_commit"], "inputs": entry["inputs"], "family": family}
    digest = hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    script = LinkerFile(family["script"], family["sha256"])
    return LinkerInput(
        sdk_commit=table["sdk_commit"],
        soc_name=soc_name,
        link_family=link_family,
        linker_profile=linker_profile,
        script=script,
        files=tuple(LinkerFile(path, sha) for path, sha in sorted(entry["inputs"].items()))
        + (script,),
        definition_sha256=digest,
        admission_source="unavailable",
        regions=tuple(
            LinkerRegion(
                region=MemoryRegion(region["region"]),
                window=MemoryRange(*region["window"]),
                app_window=MemoryRange(*region["app_window"]),
                allowed=None,
                window_provenance=region["window_provenance"],
                fixed_stack_bytes=region["fixed_stack_bytes"],
            )
            for region in family["regions"]
        ),
    )


def characterized_windows(soc_name: str) -> tuple[LinkedRegionWindow, ...]:
    """Existing public window representation, populated solely from generated inputs."""
    families = {family: characterized_input(soc_name, family) for family in LinkFamily}
    if any(value is None for value in families.values()):
        return ()
    by_family = {
        family: {region.region: region for region in value.regions}
        for family, value in families.items()
        if value is not None
    }
    first = by_family[LinkFamily.GNU]
    if any(set(regions) != set(first) for regions in by_family.values()):
        raise ValueError(f"{soc_name}: generated link families disagree on logical regions")
    result = []
    order = (MemoryRegion.MRAM, MemoryRegion.ITCM, MemoryRegion.DTCM, MemoryRegion.SRAM)
    for name, primary in sorted(first.items(), key=lambda item: order.index(item[0])):
        if any(regions[name].window != primary.window for regions in by_family.values()):
            raise ValueError(f"{soc_name}/{name}: generated classification apertures differ")
        result.append(
            LinkedRegionWindow(
                name,
                primary.window,
                MappingProxyType(
                    {family: regions[name].app_window for family, regions in by_family.items()}
                ),
                window_provenance=primary.window_provenance,
            )
        )
    return tuple(result)
