# MustardOS: its own U-Boot dtb (knulli's names the input device differently), read
# from its raw dtb partition, then its own env's setargs_mmc. Its ramdisk mounts
# /dev/mmcblk0p5 (ROOTFS). No "if" on this U-Boot: the memory is cleared first, so
# "fdt addr" refuses it if the read failed.
mw.l 44000000 0 1
sunxi_flash read 44000000 dtb_mustardos
fdt addr 44000000
fdt resize 0x10000
setenv bootargs initcall_debug=${initcall_debug} loglevel=3 root=/dev/mmcblk0p@ROOT@ rootwait quiet splash init=${init} partitions=${partitions} cma=${cma} snum=${snum} mac_addr=${mac} wifi_mac=${wifi_mac} bt_mac=${bt_mac} selinux=${selinux} specialstr=${specialstr} gpt=1
sunxi_flash read 45000000 @ABOOT@
bootm 45000000
