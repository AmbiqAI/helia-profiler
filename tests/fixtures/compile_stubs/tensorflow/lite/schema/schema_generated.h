// hpx compile-check stub — declarations only, tracks template usage (#187)
#pragma once

#include <stddef.h>
#include <stdint.h>

#define TFLITE_SCHEMA_VERSION 3

namespace tflite {

// Minimal flatbuffers-vector look-alike: size() + Get().
template <typename T>
struct HpxStubVector {
    unsigned int size() const { return 0U; }
    T Get(unsigned int index) const {
        (void)index;
        return T();
    }
};

enum BuiltinOperator : int32_t {
    BuiltinOperator_CUSTOM = 0,
    BuiltinOperator_CONV_2D,
    BuiltinOperator_SOFTMAX,
};

enum TensorType : int8_t {
    TensorType_FLOAT32 = 0,
    TensorType_INT8,
    TensorType_INT16,
    TensorType_INT32,
    TensorType_INT64,
    TensorType_UINT8,
    TensorType_BOOL,
    TensorType_COMPLEX64,
};

inline const char *EnumNameBuiltinOperator(BuiltinOperator op) {
    (void)op;
    return "";
}

inline const char *EnumNameTensorType(TensorType type) {
    (void)type;
    return "";
}

struct HpxStubString {
    const char *c_str() const { return ""; }
};

struct OperatorCode {
    const HpxStubString *custom_code() const { return nullptr; }
};

struct Tensor {
    TensorType type() const { return TensorType_FLOAT32; }
};

struct SubGraph {
    const HpxStubVector<const Tensor *> *tensors() const { return nullptr; }
};

struct Model {
    // unsigned long mirrors the arm-none-eabi uint32_t the rendered %lu
    // format strings were written against.
    unsigned long version() const { return TFLITE_SCHEMA_VERSION; }
    const HpxStubVector<const OperatorCode *> *operator_codes() const { return nullptr; }
    const HpxStubVector<const SubGraph *> *subgraphs() const { return nullptr; }
};

inline const Model *GetModel(const void *buf) {
    (void)buf;
    return nullptr;
}

}  // namespace tflite
