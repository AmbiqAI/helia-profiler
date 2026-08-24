// hpx compile-check stub — declarations only, tracks template usage (#187)
//
// Mirrors the real nsx-i2c includes-api/nsx_i2c.h shapes (the am_hal IOM
// handle/config tail of the real struct is untouched by the renders and is
// stood in by an opaque pointer-sized field).
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "nsx_core.h"

extern const nsx_core_api_t nsx_i2c_current_version;

typedef enum {
    NSX_I2C_STATUS_SUCCESS = 0,
    NSX_I2C_STATUS_ERROR = 1,
} nsx_i2c_status_e;

typedef struct {
    const nsx_core_api_t *api;
    int8_t iom;
    void *iomHandle;
} nsx_i2c_config_t;

static inline uint32_t nsx_i2c_interface_init(nsx_i2c_config_t *cfg, uint32_t speed) {
    (void)cfg;
    (void)speed;
    return 0U;
}

static inline uint32_t nsx_i2c_write_read(nsx_i2c_config_t *cfg, uint16_t addr,
                                          const void *writeBuf, size_t numWrite, void *readBuf,
                                          size_t numRead) {
    (void)cfg;
    (void)addr;
    (void)writeBuf;
    (void)numWrite;
    (void)readBuf;
    (void)numRead;
    return 0U;
}
