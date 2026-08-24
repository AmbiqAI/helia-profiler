// hpx compile-check stub — declarations only, tracks template usage (#187)
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace nsx {
namespace executorch {

enum class OperatorKind {
    kKernel = 0,
    kDelegate,
};

struct OperatorEvent {
    OperatorKind kind;
    const char *name;
    int32_t chain_index;
    uint32_t instruction_index;
};

struct ProfilingCallbacks {
    void *user_data;
    uint32_t (*begin_operator)(void *user_data, const OperatorEvent &event);
    void (*end_operator)(void *user_data, uint32_t handle);
};

struct Buffer {
    uint8_t *data;
    size_t size;
};

enum class Stage {
    kLoad = 0,
    kExecute,
};

struct RunResult {
    Stage stage;
    uint32_t executorch_error;
    uint32_t planned_bytes_required;
    uint64_t execution_cycles;
    bool ok() const { return executorch_error == 0U; }
};

static inline const char *stage_name(Stage stage) {
    (void)stage;
    return "";
}

static inline RunResult run_once_profiled(const unsigned char *program, unsigned int program_len,
                                          Buffer method_arena, Buffer planned_arena,
                                          Buffer temporary_arena, const Buffer *inputs,
                                          int num_inputs, Buffer *outputs, int num_outputs,
                                          const ProfilingCallbacks *callbacks) {
    (void)program;
    (void)program_len;
    (void)method_arena;
    (void)planned_arena;
    (void)temporary_arena;
    (void)inputs;
    (void)num_inputs;
    (void)outputs;
    (void)num_outputs;
    (void)callbacks;
    return RunResult{};
}

}  // namespace executorch
}  // namespace nsx
