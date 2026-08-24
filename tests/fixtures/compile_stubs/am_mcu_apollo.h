// hpx compile-check stub — declarations only, tracks template usage (#187)
//
// Umbrella HAL header: STIMER, debug-domain power control and the VCOMP
// comparator register the crypto/OTP shutdown block pokes.
#pragma once

#include <stdint.h>

#include "am_hal_pwrctrl.h"
#include "am_hal_timer.h"

// --- STIMER --------------------------------------------------------------
#define AM_HAL_STIMER_CFG_CLEAR (1U << 0)
#define AM_HAL_STIMER_CFG_RUN (1U << 1)
#define AM_HAL_STIMER_XTAL_32KHZ (1U << 2)

static inline uint32_t am_hal_stimer_config(uint32_t config) {
    (void)config;
    return 0U;
}

static inline uint32_t am_hal_stimer_counter_get(void) { return 0U; }

// --- Debug power domain --------------------------------------------------
static inline uint32_t am_hal_debug_enable(void) { return 0U; }
static inline uint32_t am_hal_debug_disable(void) { return 0U; }

// --- VCOMP (crypto/OTP/VCOMP shutdown block) -----------------------------
typedef struct {
    volatile uint32_t PWDKEY;
} HpxStubVcompType;

#define VCOMP ((HpxStubVcompType *)0x4000)
#define VCOMP_PWDKEY_PWDKEY_Pos 0U
#define VCOMP_PWDKEY_PWDKEY_Msk 0xFFFFFFFFU
#define VCOMP_PWDKEY_PWDKEY_Key 0x37U
