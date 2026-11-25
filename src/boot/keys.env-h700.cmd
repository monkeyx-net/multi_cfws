# Vol+ = PE1, Vol- = PE2 (gpio-keys in the CubeXX dtb), active low. Nothing has set
# them up yet: PE_CFG0 bits 4-11 = 0 (input), PE_PULL0 bits 2-5 = 01 01 (pull-up),
# changed with setexpr arithmetic (/ * + %), then PE_DAT bit 1 = Vol+, bit 2 = Vol-.
# Result: mb_up (Vol+) and mb_dn (Vol-), 1 = released, 0 = held. No hush on this U-Boot,
# so no "if" here: boot_normal runs mb_sel${mb_up}${mb_dn}.
setexpr.l mb_v *0x0300B090
setexpr mb_t ${mb_v} / 0x1000
setexpr mb_t ${mb_t} * 0x1000
setexpr mb_v ${mb_v} % 0x10
setexpr mb_v ${mb_t} + ${mb_v}
mw.l 0x0300B090 ${mb_v}
setexpr.l mb_v *0x0300B0AC
setexpr mb_t ${mb_v} / 0x40
setexpr mb_t ${mb_t} * 0x40
setexpr mb_t ${mb_t} + 0x14
setexpr mb_v ${mb_v} % 4
setexpr mb_v ${mb_t} + ${mb_v}
mw.l 0x0300B0AC ${mb_v}
sleep 0.1
setexpr.l mb_v *0x0300B0A0
setexpr mb_v ${mb_v} / 2
setexpr mb_up ${mb_v} % 2
setexpr mb_v ${mb_v} / 2
setexpr mb_dn ${mb_v} % 2
