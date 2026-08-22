/* Reproduction of the #24 shape: a few hundred bytes of real zero-init
 * state, against a linker heap reservation that runs to the end of the
 * region. */
static volatile unsigned char g_state[248];
static volatile unsigned int g_counter = 42; /* RW data */
int main(void) {
    g_state[0] = (unsigned char)g_counter;
    for (;;) {}
    return 0;
}
