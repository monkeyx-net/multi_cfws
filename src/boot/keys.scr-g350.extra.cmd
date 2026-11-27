# Cards with more than 3 OSes, only when no volume key is held: L1 = GPIO2_A6 (bit 6),
# R1 = GPIO2_A7 (bit 7) of the same EXT_PORTA register, active low; their pull-ups are
# set like the volume keys' (GRF GPIO2A_P, write mask A6/A7 only). L1+R1 wins over either.
mw.l 0xff140070 0xf0005000
sleep 0.1
setexpr.l gpio2_ext *0xff260050
setexpr t ${gpio2_ext} / 0x40
setexpr l1_bit ${t} % 2
setexpr t ${t} / 2
setexpr r1_bit ${t} % 2
if test "${key}" = "none" && itest ${l1_bit} == 0 && itest ${r1_bit} == 0; then setenv key l1r1; fi
if test "${key}" = "none" && itest ${l1_bit} == 0; then setenv key l1; fi
if test "${key}" = "none" && itest ${r1_bit} == 0; then setenv key r1; fi
