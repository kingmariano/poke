#include <stdio.h>
#include <stdint.h>
int main(void){
  uint32_t seeds[][2] = {{1u,1u},{0xdeadbeefu,0x12345678u},{0xffffffffu,0x1u},{12345u,67890u},{0x80000000u,0x7fffffffu}};
  for (int s=0;s<5;s++){
    uint32_t st0=seeds[s][0], st1=seeds[s][1];
    printf("state %u %u", st0, st1);
    for (int i=0;i<12;i++){
      st0 = 18273 * (st0 & 0xFFFF) + (st0 >> 16);
      st1 = 36969 * (st1 & 0xFFFF) + (st1 >> 16);
      uint32_t r = (st0 << 14) + (st1 & 0x3FFFF);
      printf(" %u", r);
    }
    printf("\n");
  }
  return 0;
}
