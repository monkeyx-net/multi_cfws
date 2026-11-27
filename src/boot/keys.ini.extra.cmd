# Cards with more than 3 OSes: L1 = GPIO2_A6 = pin 70, R1 = GPIO2_A7 = pin 71 (gpio-keys
# in rk3326-g350-linux.dtb, active low). Enable their pull-ups first (PX30 GRF GPIO2A_P,
# 01 = pull-up, upper 16 bits = write mask: A6/A7 only), as the kernel's dtb does.
mw.l 0xff140070 0xf0005000
sleep 0.1

