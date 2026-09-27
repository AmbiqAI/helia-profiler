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
    int8_t *int8;
    char *raw;
    void *data;
} TfLitePtrUnion;

enum TfLiteType { kTfLiteInt8 = 9 };
struct TfLiteIntArray { int size; int data[4]; };
struct TfLiteQuantizationParams { float scale; int32_t zero_point; };

typedef struct {
    TfLiteType type;
    TfLiteIntArray *dims;
    TfLiteQuantizationParams params;
    size_t bytes;
    TfLitePtrUnion data;
} TfLiteTensor;

typedef struct {
    int builtin_code;
} TFLMRegistration;
