// hpx compile-check stub — declarations only, tracks template usage (#187)
#pragma once

#include <stdint.h>

typedef enum {
    NSX_PERF_LOW = 0,
    NSX_PERF_HIGH,
} nsx_perf_mode_e;

typedef enum {
    NSX_DEBUG_NONE = 0,
    NSX_DEBUG_ITM,
    NSX_DEBUG_UART,
} nsx_debug_transport_e;

typedef struct {
    nsx_debug_transport_e transport;
} nsx_debug_config_t;

typedef struct {
    nsx_perf_mode_e perf_mode;
    bool enable_cache;
    bool enable_sram;
    nsx_debug_config_t debug;
    bool skip_bsp_init;
    bool spot_mgr_profile;
} nsx_system_config_t;

static inline uint32_t nsx_system_init(nsx_system_config_t *cfg) {
    (void)cfg;
    return 0U;
}
