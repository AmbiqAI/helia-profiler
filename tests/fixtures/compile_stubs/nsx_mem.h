// hpx compile-check stub — declarations only, tracks template usage (#187)
//
// Placement macros expand to nothing on the host: the syntax gate cares that
// the declarations they decorate parse, not where the linker puts them.
#pragma once

#define NSX_MEM_FAST
#define NSX_MEM_FAST_BSS
#define NSX_MEM_SRAM
#define NSX_MEM_SRAM_BSS
