// hpx compile-check stub — declarations only, tracks template usage (#187)
//
// Mirrors the real nsx-gpio includes-api/nsx_gpio.h shapes.
#pragma once

#include <stdint.h>

#include "nsx_core.h"

extern const nsx_core_api_t nsx_gpio_current_version;

typedef enum {
    NSX_GPIO_MODE_DISABLED = 0,
    NSX_GPIO_MODE_INPUT = 1,
    NSX_GPIO_MODE_OUTPUT = 2,
    NSX_GPIO_MODE_OUTPUT_WITH_READ = 3,
} nsx_gpio_mode_t;

typedef enum {
    NSX_GPIO_LEVEL_LOW = 0,
    NSX_GPIO_LEVEL_HIGH = 1,
} nsx_gpio_level_t;

typedef enum {
    NSX_GPIO_TRIGGER_NONE = 0,
    NSX_GPIO_TRIGGER_RISING = 1,
    NSX_GPIO_TRIGGER_FALLING = 2,
    NSX_GPIO_TRIGGER_BOTH = 3,
} nsx_gpio_trigger_t;

typedef void (*nsx_gpio_irq_cb_t)(uint32_t pin, void *ctx);

typedef struct {
    const nsx_core_api_t *api;
    uint32_t pin;
    nsx_gpio_mode_t mode;
    nsx_gpio_level_t initial_level;
    nsx_gpio_trigger_t trigger;
    nsx_gpio_irq_cb_t irq_cb;
    void *irq_ctx;
} nsx_gpio_config_t;

static inline uint32_t nsx_gpio_init(const nsx_gpio_config_t *cfg) {
    (void)cfg;
    return 0U;
}

static inline uint32_t nsx_gpio_write(uint32_t pin, nsx_gpio_level_t level) {
    (void)pin;
    (void)level;
    return 0U;
}

static inline uint32_t nsx_gpio_read(uint32_t pin, nsx_gpio_level_t *level) {
    (void)pin;
    (void)level;
    return 0U;
}
