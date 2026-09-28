#include <stdio.h>
#include <stdlib.h>
int main(void) {
  unsigned seeds[] = {0u, 1u, 42u, 123456789u, 0xdeadbeefu, 1294200190u, 557933949u, 1395042082u, 0xffffffffu};
  int n = 9;
  for (int s = 0; s < n; s++) {
    srandom(seeds[s]);
    printf("random %u", seeds[s]);
    for (int i = 0; i < 12; i++) printf(" %ld", random());
    printf("\n");
  }
  return 0;
}
