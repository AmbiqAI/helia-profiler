// hpx compile-check stub — declarations only, tracks template usage (#187)
//
// nsx-psram surface as the rendered firmware uses it.  The renders target a
// newer nsx-psram than the cached includes-api copy (clock_hz + get_info /
// write are not in NSX_PSRAM_V0_0_1), so fields beyond the cached header
// follow the call sites.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "nsx_core.h"

extern const nsx_core_api_t nsx_psram_current_version;

typedef struct {
    const nsx_core_api_t *api;
    bool enable;
    bool enable_xip;
    bool configure_mpu;
    uint32_t clock_hz;
    uint32_t base_address;
    uint32_t size_bytes;
} nsx_psram_config_t;

typedef struct {
    // uintptr_t rather than the target's uint32_t: the renders cast
    // base_address arithmetic to uint8_t* — pointer-width on the Cortex-M
    // target, but an int-to-pointer truncation warning on an LP64 host.
    // Same class of host-width adjustment as Model::version()'s
    // unsigned long in the TFLM stub.
    uintptr_t base_address;
    uint32_t size_bytes;
    uint32_t configured_clock_hz;
    uint32_t capabilities;
    int state;
    uint32_t last_init_status;
    bool xip_enabled;
    int timing_status;
    uint32_t rxdqs_delay;
} nsx_psram_info_t;

static inline uint32_t nsx_psram_default_config(nsx_psram_config_t *cfg) {
    (void)cfg;
    return 0U;
}

static inline uint32_t nsx_psram_init(nsx_psram_config_t *cfg) {
    (void)cfg;
    return 0U;
}

static inline uint32_t nsx_psram_get_info(nsx_psram_info_t *info) {
    (void)info;
    return 0U;
}

static inline uint32_t nsx_psram_write(uint32_t offset, const void *src, size_t len) {
    (void)offset;
    (void)src;
    (void)len;
    return 0U;
}
