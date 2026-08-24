// hpx compile-check stub — declarations only, tracks template usage (#187)
//
// Signatures mirror the real nsx-core includes-api/nsx_core.h; part gates
// resolve against the AM_PART_* define the harness passes on the compile
// line (production supplies it the same way, via the toolchain).
#pragma once

#include <stdint.h>

void nsx_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static inline void nsx_delay_us(uint32_t usec) { (void)usec; }

// Real signatures return void (fidelity fix, #187 round 2).
static inline void nsx_itm_printf_enable(void) {}
static inline void nsx_itm_printf_disable(void) {}
static inline void nsx_uart_printf_enable(void) {}
static inline void nsx_uart_printf_disable(void) {}

// --- API version negotiation (mirrors the real structs) ------------------
typedef struct {
    uint16_t major;
    uint16_t minor;
    uint16_t revision;
} nsx_semver_t;

typedef struct {
    uint32_t apiId;
    nsx_semver_t version;
} nsx_core_api_t;

#define NSX_STATUS_SUCCESS 0
#define NSX_STATUS_FAILURE -1
#define NSX_STATUS_INVALID_HANDLE 1
#define NSX_STATUS_INVALID_VERSION 2
#define NSX_STATUS_INVALID_CONFIG 3
#define NSX_STATUS_INIT_FAILED 4

static inline void nsx_core_fail_loop(void) {}

// Mirrors the real macro: the msg argument is EXPANDED (it feeds
// nsx_printf), so an invalid msg expression cannot hide behind the stub.
#define NSX_TRY(func, msg)                                                     \
    if (func) {                                                                \
        nsx_printf(msg);                                                       \
        nsx_core_fail_loop();                                                  \
    }

// --- Cache maintenance shims (see _cache_shims.j2) -----------------------
// Capability macros carry the real per-part values (nsx-core), so the
// #else arms of the rendered HPX_CACHE_* wrappers are exercised somewhere
// in the matrix rather than never.
#if defined(AM_PART_APOLLO3P)
#define NSX_CACHE_HAS_INVALIDATE_OBSERVED 0
#define NSX_CACHE_HAS_SYNC_SHARED 0
#elif defined(AM_PART_APOLLO4P)
#define NSX_CACHE_HAS_INVALIDATE_OBSERVED 0
#define NSX_CACHE_HAS_SYNC_SHARED 1
#else
#define NSX_CACHE_HAS_INVALIDATE_OBSERVED 1
#define NSX_CACHE_HAS_SYNC_SHARED 1
#endif

static inline uint32_t nsx_cache_flush(void) { return 0U; }
static inline uint32_t nsx_cache_publish_writes(void) { return 0U; }
static inline uint32_t nsx_cache_invalidate_observed_data(void) { return 0U; }
static inline uint32_t nsx_cache_sync_shared_data(void) { return 0U; }
