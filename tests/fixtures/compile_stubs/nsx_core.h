// hpx compile-check stub — declarations only, tracks template usage (#187)
#pragma once

#include <stdint.h>

#define NSX_STATUS_SUCCESS 0U

#define NSX_TRY(expr, msg)              \
    do {                                \
        if ((expr) != NSX_STATUS_SUCCESS) { \
        }                               \
    } while (0)

static inline void nsx_delay_us(uint32_t us) { (void)us; }

// Debug-transport printf enable/disable (ITM/SWO and UART paths).
static inline uint32_t nsx_itm_printf_enable(void) { return 0U; }
static inline uint32_t nsx_itm_printf_disable(void) { return 0U; }
static inline uint32_t nsx_uart_printf_enable(void) { return 0U; }
static inline uint32_t nsx_uart_printf_disable(void) { return 0U; }

void nsx_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

// Cache maintenance shims (see _cache_shims.j2).
#define NSX_CACHE_HAS_INVALIDATE_OBSERVED 1
#define NSX_CACHE_HAS_SYNC_SHARED 1

static inline uint32_t nsx_cache_flush(void) { return 0U; }
static inline uint32_t nsx_cache_publish_writes(void) { return 0U; }
static inline uint32_t nsx_cache_invalidate_observed_data(void) { return 0U; }
static inline uint32_t nsx_cache_sync_shared_data(void) { return 0U; }
