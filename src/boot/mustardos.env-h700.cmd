# MustardOS: knulli's U-Boot dtb, which bootm always passes to the kernel, with the
# gpio_keys input device renamed to what MustardOS's muinput looks for (knulli's dtb
# calls it "Anbernic RG CubeXX Controller"; shorter, so no resize). fdt edits the live
# control dtb without "fdt addr". Then its own env's setargs_mmc. Its ramdisk mounts
# /dev/mmcblk0p5 (ROOTFS).
fdt set /soc@03000000/gpio_keys label muOS-Input-Source
fdt set /soc@03000000/gpio_keys poll-interval <1>
setenv bootargs initcall_debug=${initcall_debug} loglevel=3 root=/dev/mmcblk0p@ROOT@ rootwait quiet splash init=${init} partitions=${partitions} cma=${cma} snum=${snum} mac_addr=${mac} wifi_mac=${wifi_mac} bt_mac=${bt_mac} selinux=${selinux} specialstr=${specialstr} gpt=1
sunxi_flash read 45000000 @ABOOT@
bootm 45000000
