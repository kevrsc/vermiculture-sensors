#include <stdbool.h>

// Arduino frees the BLE controller memory at boot unless this returns true.
bool bleInUse(void) {
  return true;
}
