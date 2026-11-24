  # ArkOS (UNTESTED; same commands as its boot.ini branch)
  setenv bootargs "root=PARTUUID=@ROOT_PU@ rootwait rw fsck.repair=yes net.ifnames=0 fbcon=rotate:0 console=/dev/ttyFIQ0 quiet splash plymouth.ignore-serial-consoles consoleblank=0 vt.global_cursor_default=0"
  load ${devtype} ${devnum}:@BOOTX@ 0x02000000 Image
  load ${devtype} ${devnum}:@BOOTX@ 0x01100000 uInitrd
  load ${devtype} ${devnum}:@BOOTX@ 0x01f00000 @DTB@
  booti 0x02000000 0x01100000 0x01f00000
