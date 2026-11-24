  # knulli (finds its boot partition by label=KNULLI, /userdata on the next partition)
  setenv bootargs "initrd=/boot/initrd.lz4 label=KNULLI rootwait quiet loglevel=0 console=tty3 console=ttyS2,1500000n8 fbcon=rotate:0"
  load ${devtype} ${devnum}:@BOOTX@ ${kernel_addr_r} /boot/linux
  load ${devtype} ${devnum}:@BOOTX@ ${fdt_addr_r} /@DTB@
  load ${devtype} ${devnum}:@BOOTX@ ${ramdisk_addr_r} /boot/initrd.lz4
  booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
