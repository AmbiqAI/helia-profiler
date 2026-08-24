// hpx compile-check stub — declarations only, tracks template usage (#187)
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "hpx_stub_tflm_common.h"
#include "tensorflow/lite/micro/micro_allocator.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/micro/micro_profiler_interface.h"
#include "tensorflow/lite/micro/micro_resource_variable.h"
#include "tensorflow/lite/schema/schema_generated.h"

namespace tflite {

class MicroInterpreter {
 public:
    MicroInterpreter(const Model *model, const MicroOpResolver &op_resolver,
                     uint8_t *tensor_arena, size_t tensor_arena_size,
                     MicroResourceVariables *resource_variables = nullptr,
                     MicroProfilerInterface *profiler = nullptr) {
        (void)model;
        (void)op_resolver;
        (void)tensor_arena;
        (void)tensor_arena_size;
        (void)resource_variables;
        (void)profiler;
    }

    MicroInterpreter(const Model *model, const MicroOpResolver &op_resolver,
                     MicroAllocator *allocator,
                     MicroResourceVariables *resource_variables = nullptr,
                     MicroProfilerInterface *profiler = nullptr) {
        (void)model;
        (void)op_resolver;
        (void)allocator;
        (void)resource_variables;
        (void)profiler;
    }

    TfLiteStatus AllocateTensors() { return kTfLiteOk; }
    TfLiteStatus Invoke() { return kTfLiteOk; }

    TfLiteTensor *input(int index) {
        (void)index;
        return nullptr;
    }
    TfLiteTensor *output(int index) {
        (void)index;
        return nullptr;
    }

    size_t arena_used_bytes() const { return 0U; }
    size_t inputs_size() const { return 0U; }
    size_t outputs_size() const { return 0U; }
};

}  // namespace tflite
