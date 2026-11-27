# Cards with 4 OSes, only when no volume key is held: L1 = GPIO3_B1 = byte 1 bit 1 of the
# copied EXT_PORT register, active low and already pulled up (GPIO3B_P above): byte 1 is
# 0xfd while it is held (0x7d if B7 reads low, as for Vol-).
setenv l1_held 0
if itest.b *0x30000001 == 0xfd || itest.b *0x30000001 == 0x7d; then setenv l1_held 1; fi
if test "${key}" = "none" && test "${l1_held}" = "1"; then setenv key l1; fi
