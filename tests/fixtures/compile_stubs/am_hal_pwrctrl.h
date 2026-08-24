// hpx compile-check stub — declarations only, tracks template usage (#187)
#pragma once

#include <stdint.h>

typedef enum {
    AM_HAL_PWRCTRL_SRAM_NONE = 0,
    AM_HAL_PWRCTRL_SRAM_3M,
} am_hal_pwrctrl_sram_e;

// Field order matters: the rendered designated initializers must match the
// declaration order (C++ requires designators in order).
typedef struct {
    am_hal_pwrctrl_sram_e eSRAMCfg;
    am_hal_pwrctrl_sram_e eActiveWithMCU;
    am_hal_pwrctrl_sram_e eActiveWithGFX;
    am_hal_pwrctrl_sram_e eActiveWithDISP;
    am_hal_pwrctrl_sram_e eSRAMRetain;
} am_hal_pwrctrl_sram_memcfg_t;

typedef enum {
    AM_HAL_PWRCTRL_PERIPH_ADC = 0,
    AM_HAL_PWRCTRL_PERIPH_AUDADC,
    AM_HAL_PWRCTRL_PERIPH_AUDPB,
    AM_HAL_PWRCTRL_PERIPH_AUDREC,
    AM_HAL_PWRCTRL_PERIPH_CRYPTO,
    AM_HAL_PWRCTRL_PERIPH_DEBUG,
    AM_HAL_PWRCTRL_PERIPH_DISP,
    AM_HAL_PWRCTRL_PERIPH_DISPPHY,
    AM_HAL_PWRCTRL_PERIPH_GFX,
    AM_HAL_PWRCTRL_PERIPH_I2S0,
    AM_HAL_PWRCTRL_PERIPH_I2S1,
    AM_HAL_PWRCTRL_PERIPH_IOM0,
    AM_HAL_PWRCTRL_PERIPH_IOM1,
    AM_HAL_PWRCTRL_PERIPH_IOM2,
    AM_HAL_PWRCTRL_PERIPH_IOM3,
    AM_HAL_PWRCTRL_PERIPH_IOM4,
    AM_HAL_PWRCTRL_PERIPH_IOM5,
    AM_HAL_PWRCTRL_PERIPH_IOM6,
    AM_HAL_PWRCTRL_PERIPH_IOM7,
    AM_HAL_PWRCTRL_PERIPH_MSPI0,
    AM_HAL_PWRCTRL_PERIPH_MSPI1,
    AM_HAL_PWRCTRL_PERIPH_MSPI2,
    AM_HAL_PWRCTRL_PERIPH_OTP,
    AM_HAL_PWRCTRL_PERIPH_PDM0,
    AM_HAL_PWRCTRL_PERIPH_PDM1,
    AM_HAL_PWRCTRL_PERIPH_PDM2,
    AM_HAL_PWRCTRL_PERIPH_PDM3,
    AM_HAL_PWRCTRL_PERIPH_SDIO,
    AM_HAL_PWRCTRL_PERIPH_UART0,
    AM_HAL_PWRCTRL_PERIPH_UART1,
    AM_HAL_PWRCTRL_PERIPH_UART2,
    AM_HAL_PWRCTRL_PERIPH_UART3,
    AM_HAL_PWRCTRL_PERIPH_USB,
    AM_HAL_PWRCTRL_PERIPH_USBPHY,
} am_hal_pwrctrl_periph_e;

typedef enum {
    AM_HAL_PWRCTRL_CONTROL_CRYPTO_POWERDOWN = 0,
} am_hal_pwrctrl_control_e;

static inline uint32_t am_hal_pwrctrl_sram_config(am_hal_pwrctrl_sram_memcfg_t *cfg) {
    (void)cfg;
    return 0U;
}

static inline uint32_t am_hal_pwrctrl_periph_disable(am_hal_pwrctrl_periph_e periph) {
    (void)periph;
    return 0U;
}

static inline uint32_t am_hal_pwrctrl_periph_enable(am_hal_pwrctrl_periph_e periph) {
    (void)periph;
    return 0U;
}

static inline uint32_t am_hal_pwrctrl_control(am_hal_pwrctrl_control_e control, void *args) {
    (void)control;
    (void)args;
    return 0U;
}
