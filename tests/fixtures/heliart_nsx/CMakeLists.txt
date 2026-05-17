# ---------------------------------------------------------------------------
# nsx-helia-rt — NSX module for heliaRT prebuilt static libraries
# ---------------------------------------------------------------------------
# This module wraps heliaRT prebuilt libraries as an NSX module so that any
# NSX application can link nsx::helia_rt and get the full TFLM runtime with
# Ambiq-optimized (ns-cmsis-nn) kernels.
#
# The prebuilt .a files are expected under lib/ relative to this file:
#   lib/libhelia-rt-<core>-<toolchain>-<variant>.a
#
# CMake options:
#   HELIA_RT_VARIANT   — debug | release | release-with-logs (default)
# ---------------------------------------------------------------------------

if(NOT DEFINED NSX_BOARD_FLAGS_TARGET)
    message(FATAL_ERROR
        "Board must define NSX_BOARD_FLAGS_TARGET before nsx-helia-rt is added.")
endif()

if(NOT DEFINED NSX_SOC_FAMILY)
    message(FATAL_ERROR
        "NSX_SOC_FAMILY must be defined before nsx-helia-rt is added.")
endif()

# --- Resolve Cortex-M core tag from SoC family ---
set(_HELIA_RT_CORE "")
if(NSX_SOC_FAMILY MATCHES "^apollo5|^apollo330|^apollo510|^atomiq")
    set(_HELIA_RT_CORE "cm55")
elseif(NSX_SOC_FAMILY MATCHES "^apollo4|^apollo3")
    set(_HELIA_RT_CORE "cm4")
else()
    message(FATAL_ERROR
        "nsx-helia-rt: unsupported NSX_SOC_FAMILY '${NSX_SOC_FAMILY}'")
endif()

# --- Resolve toolchain tag ---
set(_HELIA_RT_TOOLCHAIN "gcc")
if(CMAKE_C_COMPILER_ID STREQUAL "ARMClang" OR
   CMAKE_C_COMPILER MATCHES "armclang")
    set(_HELIA_RT_TOOLCHAIN "armclang")
endif()

# --- Build variant (user-configurable) ---
set(HELIA_RT_VARIANT "release-with-logs" CACHE STRING
    "heliaRT build variant: debug | release-with-logs | release")
set_property(CACHE HELIA_RT_VARIANT PROPERTY STRINGS
    "debug" "release-with-logs" "release")

# --- Locate the prebuilt static library ---
# v1.16.x naming: libhelia-rt-{core}-{tc}-{variant}.a
set(_HELIA_RT_LIB_NAME
    "libhelia-rt-${_HELIA_RT_CORE}-${_HELIA_RT_TOOLCHAIN}-${HELIA_RT_VARIANT}.a")
set(_HELIA_RT_LIB_PATH
    "${CMAKE_CURRENT_LIST_DIR}/lib/${_HELIA_RT_LIB_NAME}")

if(NOT EXISTS "${_HELIA_RT_LIB_PATH}")
    message(FATAL_ERROR
        "nsx-helia-rt: prebuilt library not found:\n"
        "  ${_HELIA_RT_LIB_PATH}\n"
        "Check HELIA_RT_VARIANT (${HELIA_RT_VARIANT}) and toolchain (${_HELIA_RT_TOOLCHAIN}).")
endif()

message(STATUS "nsx-helia-rt: using ${_HELIA_RT_LIB_NAME}")

# --- Import the prebuilt static library ---
add_library(nsx_helia_rt_prebuilt STATIC IMPORTED GLOBAL)
set_target_properties(nsx_helia_rt_prebuilt PROPERTIES
    IMPORTED_LOCATION "${_HELIA_RT_LIB_PATH}"
)

# --- Public-facing target ---
# The prebuilt .a contains the TFLM runtime but leaves MicroPrintf()
# undefined.  We compile the platform glue here so the linker resolves it.
add_library(nsx_helia_rt STATIC
    ${CMAKE_CURRENT_LIST_DIR}/tensorflow/lite/micro/micro_log.cc
    ${CMAKE_CURRENT_LIST_DIR}/tensorflow/lite/micro/cortex_m_generic/debug_log.cc
)
set_target_properties(nsx_helia_rt PROPERTIES EXPORT_NAME helia_rt)
add_library(nsx::helia_rt ALIAS nsx_helia_rt)

target_link_libraries(nsx_helia_rt
    PUBLIC
        ${NSX_BOARD_FLAGS_TARGET}
        nsx_helia_rt_prebuilt
    PRIVATE
        nsx_soc_hal
        nsx_core
)

target_include_directories(nsx_helia_rt
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_LIST_DIR}>
        $<BUILD_INTERFACE:${CMAKE_CURRENT_LIST_DIR}/third_party>
        $<BUILD_INTERFACE:${CMAKE_CURRENT_LIST_DIR}/third_party/flatbuffers/include>
        $<BUILD_INTERFACE:${CMAKE_CURRENT_LIST_DIR}/third_party/gemmlowp>
)

target_compile_definitions(nsx_helia_rt
    PUBLIC
        TF_LITE_STATIC_MEMORY
        NS_TFSTRUCTURE_RECENT
        NS_TFLM_NEW_MICRO_PROFILER
)
