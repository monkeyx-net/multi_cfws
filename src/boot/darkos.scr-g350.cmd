  # dArkOS
  setenv bootargs "root=PARTUUID=@ROOT_PU@ rootwait rw fsck.repair=yes net.ifnames=0 fbcon=rotate:0 console=/dev/ttyFIQ0 quiet splash consoleblank=0 vt.global_cursor_default=0"
  load ${devtype} ${devnum}:@BOOTX@ 0x02000000 Image
  load ${devtype} ${devnum}:@BOOTX@ 0x04000000 uInitrd
  load ${devtype} ${devnum}:@BOOTX@ 0x01f00000 rk3326-g350-linux.dtb
  booti 0x02000000 0x04000000 0x01f00000
