# readelf inventory fixtures (#133 Phase 1)

Unedited captures from the Arm GNU toolchain on an ELF built from the
committed `main.c` + `linker.ld` (the NSX region shape: fill-to-end `.heap`,
16 KB `.stack`, `.data` load image in MRAM, app MRAM origin 0x00410000).

Regenerate:

```sh
arm-none-eabi-gcc -mcpu=cortex-m55 -nostdlib -T linker.ld main.c -o fw.elf
arm-none-eabi-readelf -S -W fw.elf > sections.txt
arm-none-eabi-readelf -l -W fw.elf > segments.txt
rm fw.elf
```
