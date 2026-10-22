/* Local-only static fixture: no network, filesystem writes or producer code. */
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "health") == 0) return 0;
    for (;;) pause();
}
