// hpx compile-check stub — declarations only, tracks template usage (#187)
//
// Mirrors the real nsx-perf includes-api/nsx_pmu_utils.h shapes.
#pragma once

#include <stdint.h>

#include "nsx_core.h"

#define NSX_PMU_MAX_COUNTERS 8

extern const nsx_core_api_t nsx_pmu_V1_0_0;
extern const nsx_core_api_t nsx_pmu_current_version;

typedef enum {
    NSX_PMU_EVENT_COUNTER_SIZE_16 = 0,
    NSX_PMU_EVENT_COUNTER_SIZE_32 = 1,
} nsx_pmu_event_counter_size_e;

typedef struct nsx_pmu_event {
    bool enabled;
    uint32_t eventId;
    nsx_pmu_event_counter_size_e counterSize;
} nsx_pmu_event_t;

typedef struct nsx_pmu_counter {
    bool added;
    uint32_t mapIndex;
    uint32_t counterValue;
} nsx_pmu_counter_t;

typedef struct nsx_pmu_config {
    const nsx_core_api_t *api;
    nsx_pmu_event_t events[NSX_PMU_MAX_COUNTERS];
    nsx_pmu_counter_t counter[NSX_PMU_MAX_COUNTERS];
} nsx_pmu_config_t;

typedef enum {
    NSX_PMU_PRESET_BASIC_CPU = 0,
    NSX_PMU_PRESET_MEMORY = 1,
    NSX_PMU_PRESET_MVE = 2,
    NSX_PMU_PRESET_ML_DEFAULT = 3,
} nsx_pmu_preset_e;

static inline uint32_t nsx_pmu_init(nsx_pmu_config_t *cfg) {
    (void)cfg;
    return 0U;
}

static inline uint32_t nsx_pmu_get_counters(nsx_pmu_config_t *cfg) {
    (void)cfg;
    return 0U;
}

static inline void nsx_pmu_event_create(nsx_pmu_event_t *event, uint32_t eventId,
                                        nsx_pmu_event_counter_size_e counterSize) {
    (void)event;
    (void)eventId;
    (void)counterSize;
}

static inline uint32_t nsx_pmu_apply_preset(nsx_pmu_config_t *cfg, nsx_pmu_preset_e preset) {
    (void)cfg;
    (void)preset;
    return 0U;
}

static inline void nsx_pmu_reset_counters(void) {}

static inline void nsx_pmu_reset_config(nsx_pmu_config_t *cfg) { (void)cfg; }
