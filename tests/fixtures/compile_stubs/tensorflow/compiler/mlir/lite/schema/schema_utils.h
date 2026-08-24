// hpx compile-check stub — declarations only, tracks template usage (#187)
#pragma once

#include "tensorflow/lite/schema/schema_generated.h"

namespace tflite {

inline BuiltinOperator GetBuiltinCode(const OperatorCode *op_code) {
    (void)op_code;
    return BuiltinOperator_CUSTOM;
}

}  // namespace tflite
