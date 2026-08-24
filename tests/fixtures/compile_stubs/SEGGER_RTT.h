// hpx compile-check stub — declarations only, tracks template usage (#187)
#pragma once

#include <stdint.h>

#define SEGGER_RTT_MODE_NO_BLOCK_TRIM (1U)

typedef struct {
    char acID[16];
} SEGGER_RTT_CB;

extern SEGGER_RTT_CB _SEGGER_RTT;

static inline void SEGGER_RTT_Init(void) {}

static inline int SEGGER_RTT_ConfigUpBuffer(unsigned buffer_index, const char *name, void *buffer,
                                            unsigned buffer_size, unsigned flags) {
    (void)buffer_index;
    (void)name;
    (void)buffer;
    (void)buffer_size;
    (void)flags;
    return 0;
}

static inline unsigned SEGGER_RTT_Write(unsigned buffer_index, const void *buffer,
                                        unsigned num_bytes) {
    (void)buffer_index;
    (void)buffer;
    (void)num_bytes;
    return 0U;
}

static inline unsigned SEGGER_RTT_Read(unsigned buffer_index, void *buffer, unsigned buffer_size) {
    (void)buffer_index;
    (void)buffer;
    (void)buffer_size;
    return 0U;
}

static inline unsigned SEGGER_RTT_GetAvailWriteSpace(unsigned buffer_index) {
    (void)buffer_index;
    return 0U;
}

static inline unsigned SEGGER_RTT_HasDataUp(unsigned buffer_index) {
    (void)buffer_index;
    return 0U;
}
