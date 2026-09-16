// hpx compile-check stub — declarations only, tracks template usage (#187)
//
// nsx-ethos-u probe surface as _npu_pmu.j2 uses it.
#pragma once

#define NSX_ETHOS_U_PROBE_BEGIN "begin"
#define NSX_ETHOS_U_PROBE_END "end"

struct ethosu_driver;

typedef void (*nsx_ethos_u_probe_fn)(const char *phase, struct ethosu_driver *drv,
                                     void *user_arg);

static inline void nsx_ethos_u_set_probe(nsx_ethos_u_probe_fn fn) { (void)fn; }
