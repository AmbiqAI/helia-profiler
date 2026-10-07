// hpx compile-check stub — declarations only, tracks template usage (#187)
//
// Ethos-U core-driver PMU surface as _npu_pmu.j2 and the ethos_npu pass
// blocks use it. The event enum lists every counter the host catalogue
// (platform/counters.py NPU group) can plan into a pass.
#pragma once

#include <stdint.h>

struct ethosu_driver;

#define ETHOSU_PMU_CCNT_Msk (1UL << 31)

enum ethosu_pmu_event_type {
    ETHOSU_PMU_CYCLE,
    ETHOSU_PMU_NPU_ACTIVE,
    ETHOSU_PMU_NPU_IDLE,
    ETHOSU_PMU_MAC_ACTIVE,
    ETHOSU_PMU_WD_ACTIVE,
    ETHOSU_PMU_SRAM_RD_DATA_BEAT_RECEIVED,
    ETHOSU_PMU_SRAM_WR_DATA_BEAT_WRITTEN,
    ETHOSU_PMU_EXT_RD_DATA_BEAT_RECEIVED,
    ETHOSU_PMU_EXT_WR_DATA_BEAT_WRITTEN,
    ETHOSU_PMU_SRAM0_RD_DATA_BEAT_RECEIVED,
    ETHOSU_PMU_SRAM1_RD_DATA_BEAT_RECEIVED,
    ETHOSU_PMU_SRAM0_WR_DATA_BEAT_WRITTEN,
    ETHOSU_PMU_SRAM1_WR_DATA_BEAT_WRITTEN,
    ETHOSU_PMU_SRAM0_RD_TRANS_ACCEPTED,
    ETHOSU_PMU_SRAM1_RD_TRANS_ACCEPTED,
    ETHOSU_PMU_SRAM0_WR_TRANS_ACCEPTED,
    ETHOSU_PMU_SRAM1_WR_TRANS_ACCEPTED,
    ETHOSU_PMU_EXT0_RD_DATA_BEAT_RECEIVED,
    ETHOSU_PMU_EXT1_RD_DATA_BEAT_RECEIVED,
    ETHOSU_PMU_EXT0_RD_TRANS_ACCEPTED,
    ETHOSU_PMU_EXT1_RD_TRANS_ACCEPTED,
    ETHOSU_PMU_SRAM_RD_STALL_LIMIT,
    ETHOSU_PMU_SRAM_RD_TRAN_REQ_STALLED,
    ETHOSU_PMU_SRAM_WR_STALL_LIMIT,
    ETHOSU_PMU_SRAM_WR_TRAN_REQ_STALLED,
    ETHOSU_PMU_SRAM_WR_DATA_BEAT_STALLED,
    ETHOSU_PMU_EXT_RD_STALL_LIMIT,
    ETHOSU_PMU_EXT_RD_TRAN_REQ_STALLED,
    ETHOSU_PMU_MAC_STALLED_BY_IB,
    ETHOSU_PMU_AO_STALLED_BY_OB,
    ETHOSU_PMU_WD_STALLED,
};

static inline void ETHOSU_PMU_Enable(struct ethosu_driver *drv) { (void)drv; }

static inline void ETHOSU_PMU_Set_EVTYPER(struct ethosu_driver *drv, uint32_t num,
                                          enum ethosu_pmu_event_type type) {
    (void)drv;
    (void)num;
    (void)type;
}

static inline void ETHOSU_PMU_CYCCNT_Reset(struct ethosu_driver *drv) { (void)drv; }

static inline void ETHOSU_PMU_EVCNTR_ALL_Reset(struct ethosu_driver *drv) { (void)drv; }

static inline void ETHOSU_PMU_CNTR_Enable(struct ethosu_driver *drv, uint32_t mask) {
    (void)drv;
    (void)mask;
}

static inline void ETHOSU_PMU_CNTR_Disable(struct ethosu_driver *drv, uint32_t mask) {
    (void)drv;
    (void)mask;
}

static inline uint32_t ETHOSU_PMU_Get_EVCNTR(struct ethosu_driver *drv, uint32_t num) {
    (void)drv;
    (void)num;
    return 0U;
}

static inline uint32_t ETHOSU_PMU_Get_CNTR_OVS(struct ethosu_driver *drv) {
    (void)drv;
    return 0U;
}

static inline void ETHOSU_PMU_Set_CNTR_OVS(struct ethosu_driver *drv, uint32_t mask) {
    (void)drv;
    (void)mask;
}
