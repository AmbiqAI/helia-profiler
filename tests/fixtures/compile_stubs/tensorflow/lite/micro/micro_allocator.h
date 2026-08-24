// hpx compile-check stub — declarations only, tracks template usage (#187)
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace tflite {

class MicroAllocator {
 public:
    static MicroAllocator *Create(uint8_t *tensor_arena, size_t arena_size) {
        (void)tensor_arena;
        (void)arena_size;
        return nullptr;
    }
};

}  // namespace tflite
