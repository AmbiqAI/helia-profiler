"""The supported SDK descriptor that has no catalog hardware target."""

from .soc import (
    ClockDomain,
    ClockSpeed,
    CoreArch,
    MemoryLayout,
    PerfTier,
    PmuTier,
    SocDef,
    SocFamily,
)


def apollo5b_descriptor() -> SocDef:
    # This is not a physical-product identity: https://github.com/AmbiqAI/helia-platforms/issues/64#issuecomment-6064752168
    return SocDef(
        name="apollo5b",
        family=SocFamily.AP5,
        core=CoreArch.CORTEX_M55,
        pmu_tier=PmuTier.ARMV8M_PMU,
        has_mve=True,
        memory=MemoryLayout(mram_kb=4096, sram_kb=3072, dtcm_kb=512, itcm_kb=256, psram_kb=32768),
        clocks=(
            ClockDomain(
                "cpu",
                (ClockSpeed("lp", 96, PerfTier.LOW), ClockSpeed("hp", 250, PerfTier.HIGH)),
                default="lp",
            ),
        ),
        c_define="AM_PART_APOLLO5B",
        cmsis_header="apollo510.h",
        rtt_scan_ranges=((536870912, 524288),),
        jlink_device="AP510NFA-CBR",
        pmu_max_ops=4096,
    )
