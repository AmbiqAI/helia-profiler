// hpx compile-check stub — declarations only, tracks template usage (#187)
#pragma once

#include "tensorflow/lite/micro/micro_allocator.h"

namespace tflite {

class MicroResourceVariables {
 public:
    static MicroResourceVariables *Create(MicroAllocator *allocator, int num_variables) {
        (void)allocator;
        (void)num_variables;
        return nullptr;
    }
};

}  // namespace tflite
