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
// Apollo510-only in the real HAL (AP3/AP4 renders only ever name these in
// comments); part-gated so a non-AP5 render arm calling them fails the gate.
#if defined(AM_PART_APOLLO510)
static inline uint32_t am_hal_debug_enable(void) { return 0U; }
static inline uint32_t am_hal_debug_disable(void) { return 0U; }
#endif  // AM_PART_APOLLO510

// --- Burst mode (Apollo3 only; mirrors am_hal_burst.h) -------------------
#if defined(AM_PART_APOLLO3P)
typedef enum {
    AM_HAL_BURST_AVAIL,
    AM_HAL_BURST_NOTAVAIL,
} am_hal_burst_avail_e;

typedef enum {
    AM_HAL_BURST_MODE,
    AM_HAL_NORMAL_MODE,
} am_hal_burst_mode_e;

static inline uint32_t am_hal_burst_mode_initialize(am_hal_burst_avail_e *peBurstAvail) {
    (void)peBurstAvail;
    return 0U;
}

static inline uint32_t am_hal_burst_mode_enable(am_hal_burst_mode_e *peBurstStatus) {
    (void)peBurstStatus;
    return 0U;
}

static inline uint32_t am_hal_burst_mode_disable(am_hal_burst_mode_e *peBurstStatus) {
    (void)peBurstStatus;
    return 0U;
}
#endif  // AM_PART_APOLLO3P

// --- VCOMP (crypto/OTP/VCOMP shutdown block) -----------------------------
typedef struct {
    volatile uint32_t PWDKEY;
} HpxStubVcompType;

#define VCOMP ((HpxStubVcompType *)0x4000)
#define VCOMP_PWDKEY_PWDKEY_Pos 0U
#define VCOMP_PWDKEY_PWDKEY_Msk 0xFFFFFFFFU
#define VCOMP_PWDKEY_PWDKEY_Key 0x37U
