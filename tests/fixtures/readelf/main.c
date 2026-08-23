/* Fixture source for the #133 inventory captures — same shape idea as the
   fromelf fixtures: real zero-init state, a stack array, initialized data
   (so a PT_LOAD with paddr != vaddr exists), and a fill-to-end heap from
   the linker script. */
__attribute__((section(".stack"))) static unsigned int g_stack[4096];
static unsigned int g_zero_init[62];
static unsigned int g_initialized[8] = {1, 2, 3, 4, 5, 6, 7, 8};
int main(void) {
    g_stack[0] = 1u;
    g_zero_init[1] = g_initialized[2];
    return (int)(g_stack[0] + g_zero_init[1]);
}
void _start(void) { main(); }
