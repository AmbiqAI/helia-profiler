/* Minimal GNU ld script reproducing the NSX region shape for the #133
   inventory fixtures: TCM with .stack + .bss + fill-to-end .heap, MRAM
   with code and the .data load image. Regenerate the captures with the
   commands in the sibling README. */
MEMORY
{
  MCU_ITCM (rx)  : ORIGIN = 0x00000000, LENGTH = 262144
  MCU_MRAM (rx)  : ORIGIN = 0x00410000, LENGTH = 4128768
  MCU_TCM  (rwx) : ORIGIN = 0x20000000, LENGTH = 507904
}
SECTIONS
{
  .text : { *(.text*) *(.rodata*) } > MCU_MRAM
  .stack (NOLOAD) : { . = ALIGN(8); *(.stack) } > MCU_TCM
  .data : { *(.data*) } > MCU_TCM AT > MCU_MRAM
  .bss (NOLOAD) : { *(.bss*) *(COMMON) } > MCU_TCM
  .heap (NOLOAD) : {
    . = ALIGN(8);
    __HeapBase = .;
    . = ORIGIN(MCU_TCM) + LENGTH(MCU_TCM);
    __HeapLimit = .;
  } > MCU_TCM
}
