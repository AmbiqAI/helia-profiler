# readelf inventory fixtures (#133 Phase 1)

Unedited captures from the Arm GNU toolchain on an ELF built from the
committed `main.c` + `linker.ld` (the NSX region shape: fill-to-end `.heap`,
16 KB `.stack`, `.data` load image in MRAM, app MRAM origin 0x00410000).

Regenerate:

```sh
arm-none-eabi-gcc -mcpu=cortex-m55 -nostdlib -T linker.ld main.c -o fw.elf
arm-none-eabi-readelf -S -W fw.elf > sections.txt
arm-none-eabi-readelf -l -W fw.elf > segments.txt
llvm-readelf -S -W fw.elf > sections_atfe.txt
llvm-readelf -l -W fw.elf > segments_atfe.txt
rm fw.elf
```

`sections_atfe.txt` / `segments_atfe.txt` are llvm-readelf (ATfE 22.1.0) on
the SAME ELF — ATfE's toolchain spec resolves its `readelf` to llvm-readelf,
and D5 wants each toolchain's real output shape pinned. The data rows are
byte-identical to GNU readelf's; only the column-header wording ("Address"
vs "Addr") and the flag-key legend differ, neither of which the parser
reads. Captures from GNU readelf 2.45.1 (Arm GNU Toolchain 15.2.Rel1) and
llvm-readelf 22.1.0.
