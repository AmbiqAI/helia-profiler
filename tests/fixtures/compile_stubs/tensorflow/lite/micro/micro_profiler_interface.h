// hpx compile-check stub — declarations only, tracks template usage (#187)
#pragma once

#include <stdint.h>

namespace tflite {

class MicroProfilerInterface {
 public:
    virtual ~MicroProfilerInterface() {}
    virtual uint32_t BeginEvent(const char *tag) = 0;
    virtual void EndEvent(uint32_t event_handle) = 0;
};

}  // namespace tflite
