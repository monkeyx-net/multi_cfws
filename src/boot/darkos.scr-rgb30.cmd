  # dArkOS (vendor 5.10 kernel). Despite its name, uInitrd is a plain gzip initramfs
  # (no U-Boot header), so it needs :size; it is loaded last for ${filesize}.
  # Its initramfs ignores root= and always mounts /dev/mmcblk1p4 (hence dArkOS on p4).
  # loglevel=0, not the image's 5: kernel messages on tty1 cover the splash.
  setenv bootargs "root=/dev/mmcblk1p@ROOT@ initrd=/uInitrd rootwait rw fsck.repair=yes quiet splash vt.global_cursor_default=0 net.ifnames=0 console=tty1 plymouth.ignore-serial-consoles consoleblank=0 loglevel=0 video=HDMI-A-1:1280x720@60"
  load ${devtype} ${devnum}:@BOOTX@ ${kernel_addr_r} /Image
  load ${devtype} ${devnum}:@BOOTX@ ${fdt_addr_r} /@DTB@
  load ${devtype} ${devnum}:@BOOTX@ ${ramdisk_addr_r} /uInitrd
  booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
