// hpx compile-check stub — declarations only, tracks template usage (#187)
//
// Shared CMSIS-core surface pulled in by every per-part device header stub
// (apollo3p.h / apollo4p.h / apollo510.h).  Only the registers, intrinsics
// and PMU API the rendered firmware TUs actually touch are declared here.
#pragma once

#include <stdint.h>

// --- Core debug / DWT registers -----------------------------------------
typedef struct {
    volatile uint32_t DEMCR;
} HpxStubCoreDebugType;

typedef struct {
    volatile uint32_t CTRL;
    volatile uint32_t CYCCNT;
} HpxStubDwtType;

#define CoreDebug ((HpxStubCoreDebugType *)0x1000)
#define DWT ((HpxStubDwtType *)0x2000)
#define CoreDebug_DEMCR_TRCENA_Msk (1UL << 24)
#define DWT_CTRL_CYCCNTENA_Msk (1UL << 0)

// CMSIS global; the rendered main() assigns the resolved operating point.
extern uint32_t SystemCoreClock;

// CMSIS field-insertion helper (used by the VCOMP power-down block).
#define _VAL2FLD(field, value) (((uint32_t)(value) << field##_Pos) & field##_Msk)

// --- NVIC (USB-CDC timer pause/resume) -----------------------------------
typedef enum {
    TIMER3_IRQn = 3,
} IRQn_Type;

static inline void NVIC_EnableIRQ(IRQn_Type irq) { (void)irq; }
static inline void NVIC_DisableIRQ(IRQn_Type irq) { (void)irq; }

// --- Core intrinsics -----------------------------------------------------
typedef struct { volatile uint32_t VTOR; } HpxStubScbType;
#define SCB ((HpxStubScbType *)0x3000)
static inline uint32_t __get_PRIMASK(void) { return 0U; }
static inline void __disable_irq(void) {}
static inline void __set_PRIMASK(uint32_t value) { (void)value; }
static inline uint32_t __get_MSPLIM(void) { return 0U; }
static inline uint32_t __get_MSP(void) { return 0U; }
static inline uint32_t __get_CONTROL(void) { return 0U; }
static inline uint32_t __get_IPSR(void) { return 0U; }
static inline void __NOP(void) {}
static inline void __WFI(void) {}
static inline void __DMB(void) {}
static inline void __DSB(void) {}
static inline void __ISB(void) {}

// --- D-cache maintenance (M55 parts only; core_cm4.h declares none) -----
#if defined(HPX_STUB_HAS_DCACHE_API)
static inline void SCB_InvalidateDCache_by_Addr(volatile void *addr, int32_t dsize) {
    (void)addr;
    (void)dsize;
}
static inline void SCB_CleanDCache_by_Addr(volatile void *addr, int32_t dsize) {
    (void)addr;
    (void)dsize;
}
static inline void SCB_CleanInvalidateDCache(void) {}
static inline void SCB_InvalidateDCache(void) {}
#endif

// --- Armv8-M PMU (M55 parts only; guarded by the per-part header) --------
#if defined(HPX_STUB_HAS_ARMV8M_PMU)
static inline uint32_t ARM_PMU_Get_CNTR_OVS(void) { return 0U; }
static inline void ARM_PMU_Set_CNTR_OVS(uint32_t mask) { (void)mask; }
static inline void ARM_PMU_Enable(void) {}
static inline void ARM_PMU_Disable(void) {}
static inline void ARM_PMU_Set_EVTYPER(uint32_t num, uint32_t type) {
    (void)num;
    (void)type;
}
static inline void ARM_PMU_CNTR_Enable(uint32_t mask) { (void)mask; }
static inline void ARM_PMU_CNTR_Disable(uint32_t mask) { (void)mask; }
static inline void ARM_PMU_EVCNTR_ALL_Reset(void) {}
static inline void ARM_PMU_CYCCNT_Reset(void) {}
static inline uint32_t ARM_PMU_Get_EVCNTR(uint32_t num) {
    (void)num;
    return 0U;
}
static inline uint32_t ARM_PMU_Get_CCNTR(void) { return 0U; }
#endif  // HPX_STUB_HAS_ARMV8M_PMU
