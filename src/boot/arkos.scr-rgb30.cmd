  # ArkOS (UNTESTED; vendor kernel). uInitrd is a plain gzip initramfs (needs :size),
  # patched by the script to follow root= instead of /dev/mmcblk1p4.
  setenv bootargs "root=PARTUUID=@ROOT_PU@ initrd=/uInitrd rootwait rw fsck.repair=yes quiet splash net.ifnames=0 console=tty1 plymouth.ignore-serial-consoles consoleblank=0 loglevel=0 video=HDMI-A-1:1280x720@60"
  load ${devtype} ${devnum}:@BOOTX@ ${kernel_addr_r} /Image
  load ${devtype} ${devnum}:@BOOTX@ ${fdt_addr_r} /@DTB@
  load ${devtype} ${devnum}:@BOOTX@ ${ramdisk_addr_r} /uInitrd
  booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
