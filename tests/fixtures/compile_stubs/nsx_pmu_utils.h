// hpx compile-check stub — declarations only, tracks template usage (#187)
#pragma once

#include <stdint.h>

#include "nsx_core.h"

#define HPX_STUB_NSX_PMU_MAX_EVENTS 8

typedef enum {
    NSX_PMU_EVENT_COUNTER_SIZE_32 = 0,
} nsx_pmu_event_counter_size_e;

typedef enum {
    NSX_PMU_PRESET_ML_DEFAULT = 0,
} nsx_pmu_preset_e;

typedef struct {
    uint32_t eventId;
    bool enabled;
} nsx_pmu_event_t;

typedef struct {
    uint32_t counterValue;
} nsx_pmu_counter_t;

typedef struct nsx_pmu_api_t nsx_pmu_api_t;
extern const nsx_pmu_api_t nsx_pmu_V1_0_0;

typedef struct {
    const nsx_pmu_api_t *api;
    nsx_pmu_event_t events[HPX_STUB_NSX_PMU_MAX_EVENTS];
    nsx_pmu_counter_t counter[HPX_STUB_NSX_PMU_MAX_EVENTS];
} nsx_pmu_config_t;

static inline void nsx_pmu_reset_config(nsx_pmu_config_t *cfg) { (void)cfg; }

static inline void nsx_pmu_event_create(nsx_pmu_event_t *event, uint32_t event_id,
                                        nsx_pmu_event_counter_size_e size) {
    (void)event;
    (void)event_id;
    (void)size;
}

static inline uint32_t nsx_pmu_init(nsx_pmu_config_t *cfg) {
    (void)cfg;
    return 0U;
}

static inline uint32_t nsx_pmu_get_counters(nsx_pmu_config_t *cfg) {
    (void)cfg;
    return 0U;
}

static inline void nsx_pmu_reset_counters(void) {}

static inline void nsx_pmu_apply_preset(nsx_pmu_config_t *cfg, nsx_pmu_preset_e preset) {
    (void)cfg;
    (void)preset;
}
