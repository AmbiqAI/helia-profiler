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

    // Registration surface: one explicit method per firmware/op_resolver.py
    // _ALL_REGISTRATIONS entry (the full mode="all" plan renders every one of
    // these).  Deliberately NOT a catch-all: a renamed production entry must
    // fail the gate until this list is updated in the same PR (#187 stub
    // maintenance rule).
    TfLiteStatus AddAbs(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddAdd(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddAddN(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddAssignVariable(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddArgMax(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddArgMin(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddAveragePool2D(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddBatchMatMul(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddBatchToSpaceNd(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddBroadcastArgs(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddBroadcastTo(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddCast(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddCallOnce(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddCeil(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddConcatenation(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddConv2D(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddCumSum(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddDepthToSpace(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddDepthwiseConv2D(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddDequantize(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddDiv(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddElu(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddEmbeddingLookup(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddEqual(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddExp(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddExpandDims(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddFill(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddFloor(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddFloorDiv(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddFloorMod(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddFullyConnected(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddGather(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddGatherNd(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddGreater(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddGreaterEqual(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddHardSwish(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddL2Normalization(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddL2Pool2D(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddLeakyRelu(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddLess(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddLessEqual(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddLog(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddLogSoftmax(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddLogicalAnd(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddLogicalNot(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddLogicalOr(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddLogistic(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddMaxPool2D(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddMaximum(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddMean(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddMinimum(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddMirrorPad(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddMul(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddNeg(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddNotEqual(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddPack(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddPad(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddPadV2(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddPrelu(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddQuantize(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddReadVariable(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddReduceMax(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddRelu(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddRelu6(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddReshape(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddResizeBilinear(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddResizeNearestNeighbor(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddRound(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddRsqrt(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddSelectV2(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddShape(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddSlice(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddSoftmax(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddSpaceToBatchNd(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddSpaceToDepth(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddSplit(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddSplitV(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddSqrt(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddSquare(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddSquaredDifference(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddSqueeze(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddStridedSlice(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddSub(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddSum(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddSvdf(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddTanh(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddTranspose(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddTransposeConv(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddUnidirectionalSequenceLSTM(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddUnpack(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddVarHandle(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
    TfLiteStatus AddZerosLike(const TFLMRegistration *registration = nullptr) {
        (void)registration;
        return kTfLiteOk;
    }
};

}  // namespace tflite
