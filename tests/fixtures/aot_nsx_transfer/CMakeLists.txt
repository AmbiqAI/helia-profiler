# The transfer-provider section heliaAOT emits in an nsx module's CMakeLists.txt
# (helia_aot/aot/templates/nsx/CMakeLists.txt.j2), rendered for module kws_model,
# prefix kws.
# ── Transfer provider for the module's dma units ──────────────────────────
# A second target on the nsx-psram module's asynchronous read, built only by
# an application that links it to bind the emitted provider (kws_nsx_transfer.h).
if(NOT TARGET nsx::psram)
    message(FATAL_ERROR
        "kws_model: nsx::psram is missing. This module's transfer provider "
        "(nsx::kws_model_nsx_transfer) is built on the nsx-psram module, API 1.1.0 or newer, "
        "which must support the board ${NSX_AMBIQ_BOARD_NAME}; add nsx-psram to the project "
        "before this module, or convert with --module.transfer-provider none and bind a "
        "provider of your own.")
endif()
add_library(kws_model_nsx_transfer STATIC EXCLUDE_FROM_ALL ${CMAKE_CURRENT_LIST_DIR}/src/kws_nsx_transfer.c)
target_link_libraries(kws_model_nsx_transfer PUBLIC kws_model nsx::psram)
set_target_properties(kws_model_nsx_transfer PROPERTIES EXPORT_NAME kws_model_nsx_transfer)
add_library(nsx::kws_model_nsx_transfer ALIAS kws_model_nsx_transfer)
