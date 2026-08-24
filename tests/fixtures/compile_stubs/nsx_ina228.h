// hpx compile-check stub — declarations only, tracks template usage (#187)
//
// nsx-sensors INA228 driver surface as the rendered power binary uses it
// (the module is not in the local NSX cache, so shapes are taken from the
// call sites: context + i2c binding, config setters, raw accumulator reads).
#pragma once

#include <stdint.h>

#include "nsx_i2c.h"

// DIAG_ALRT bits the render masks for accumulator health.
#define INA228_ALERT_MATH_OVERFLOW (1U << 9)
#define INA228_ALERT_CHARGE_OVERFLOW (1U << 10)
#define INA228_ALERT_ENERGY_OVERFLOW (1U << 11)

typedef enum {
    INA228_TIME_540_us = 5,
} ina228_conversion_time_e;

typedef enum {
    INA228_COUNT_16 = 3,
} ina228_averaging_count_e;

typedef enum {
    INA228_MODE_CONT_BUS_SHUNT = 0xB,
} ina228_mode_e;

typedef struct {
    nsx_i2c_config_t *i2c;
    uint8_t address;
} ina228_context_t;

static inline uint32_t ina228_init(ina228_context_t *ctx, nsx_i2c_config_t *i2c,
                                   uint8_t address) {
    (void)ctx;
    (void)i2c;
    (void)address;
    return 0U;
}

static inline uint32_t ina228_reset(ina228_context_t *ctx) {
    (void)ctx;
    return 0U;
}

static inline uint32_t ina228_get_manufacturer_id(ina228_context_t *ctx, uint16_t *id) {
    (void)ctx;
    (void)id;
    return 0U;
}

static inline uint32_t ina228_get_device_id(ina228_context_t *ctx, uint16_t *id) {
    (void)ctx;
    (void)id;
    return 0U;
}

static inline uint32_t ina228_set_adc_range(ina228_context_t *ctx, uint8_t range) {
    (void)ctx;
    (void)range;
    return 0U;
}

static inline uint32_t ina228_set_shunt(ina228_context_t *ctx, float shunt_ohms,
                                        float max_current_a) {
    (void)ctx;
    (void)shunt_ohms;
    (void)max_current_a;
    return 0U;
}

static inline uint32_t ina228_set_current_conversion_time(ina228_context_t *ctx,
                                                          ina228_conversion_time_e time) {
    (void)ctx;
    (void)time;
    return 0U;
}

static inline uint32_t ina228_set_voltage_conversion_time(ina228_context_t *ctx,
                                                          ina228_conversion_time_e time) {
    (void)ctx;
    (void)time;
    return 0U;
}

static inline uint32_t ina228_set_averaging_count(ina228_context_t *ctx,
                                                  ina228_averaging_count_e count) {
    (void)ctx;
    (void)count;
    return 0U;
}

static inline uint32_t ina228_set_mode(ina228_context_t *ctx, ina228_mode_e mode) {
    (void)ctx;
    (void)mode;
    return 0U;
}

static inline uint32_t ina228_reset_accumulators(ina228_context_t *ctx) {
    (void)ctx;
    return 0U;
}

static inline uint32_t ina228_alert_functions(ina228_context_t *ctx, uint16_t *diag) {
    (void)ctx;
    (void)diag;
    return 0U;
}

static inline uint32_t ina228_read_energy_raw(ina228_context_t *ctx, uint64_t *energy) {
    (void)ctx;
    (void)energy;
    return 0U;
}

static inline uint32_t ina228_read_charge_raw(ina228_context_t *ctx, int64_t *charge) {
    (void)ctx;
    (void)charge;
    return 0U;
}

static inline uint32_t ina228_read_bus_voltage(ina228_context_t *ctx, float *bus_mv) {
    (void)ctx;
    (void)bus_mv;
    return 0U;
}
