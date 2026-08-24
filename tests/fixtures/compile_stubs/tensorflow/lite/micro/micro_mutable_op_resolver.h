// hpx compile-check stub — declarations only, tracks template usage (#187)
#pragma once

#include "hpx_stub_tflm_common.h"
#include "tensorflow/lite/schema/schema_generated.h"

namespace tflite {

class MicroOpResolver {
 public:
    virtual ~MicroOpResolver() {}
};

template <unsigned int tOpCount>
class MicroMutableOpResolver : public MicroOpResolver {
 public:
    MicroMutableOpResolver() {}

    const TFLMRegistration *FindOp(BuiltinOperator op) const {
        (void)op;
        return nullptr;
    }
    const TFLMRegistration *FindOp(const char *op_name) const {
        (void)op_name;
        return nullptr;
    }

    // Registration surface: only the ops the render matrix registers today.
    // A template that starts registering a new op fails the gate until it is
    // declared here — same discipline as every other stub symbol.
    TfLiteStatus AddConv2D() { return kTfLiteOk; }
    TfLiteStatus AddSoftmax() { return kTfLiteOk; }
};

}  // namespace tflite
