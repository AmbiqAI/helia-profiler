// hpx compile-check stub — declarations only, tracks template usage (#187)
#pragma once

#include <stdint.h>

// Real value from nsx-usb includes-api/nsx_usb.h.
#define NSX_USB_MIN_CDC_RX_BUFSIZE 1024

// Field order matters for the rendered designated initializer.
typedef struct {
    uint8_t *tx_buffer;
    uint32_t tx_buffer_len;
    uint8_t *rx_buffer;
    uint32_t rx_buffer_len;
} nsx_usb_config_t;

static inline uint32_t nsx_usb_init(nsx_usb_config_t *cfg) {
    (void)cfg;
    return 0U;
}

static inline bool nsx_usb_connected(nsx_usb_config_t *cfg) {
    (void)cfg;
    return true;
}

static inline uint32_t nsx_usb_send(nsx_usb_config_t *cfg, const void *buf, uint32_t len,
                                    uint32_t *sent) {
    (void)cfg;
    (void)buf;
    (void)len;
    (void)sent;
    return 0U;
}
