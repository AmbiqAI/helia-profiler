// hpx compile-check stub — declarations only, tracks template usage (#187)
//
// nsx-npu surface as _npu_init.j2 uses it.
#pragma once

#include <stdbool.h>
#include <stdint.h>

struct ethosu_driver;

typedef enum {
    NSX_NPU_PERF_ULTRA_LOW_POWER,
    NSX_NPU_PERF_HIGH_PERFORMANCE
} nsx_npu_perf_mode_e;

typedef struct {
    nsx_npu_perf_mode_e perf_mode;
    bool skip_perf_mode;
    bool tolerate_power_ack;
} nsx_npu_config_t;

static inline uint32_t nsx_npu_init(nsx_npu_config_t *cfg) {
    (void)cfg;
    return 0U;
}
