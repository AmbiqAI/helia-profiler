// hpx compile-check stub — declarations only, tracks template usage (#187)
//
// Shared TFLM/TfLite types pulled in by the per-path tensorflow/... stub
// headers, mirroring what the real headers provide transitively.
#pragma once

#include <stddef.h>
#include <stdint.h>

typedef enum {
    kTfLiteOk = 0,
    kTfLiteError = 1,
} TfLiteStatus;

typedef union {
    char *raw;
    void *data;
} TfLitePtrUnion;

typedef struct {
    size_t bytes;
    TfLitePtrUnion data;
} TfLiteTensor;

typedef struct {
    int builtin_code;
} TFLMRegistration;
