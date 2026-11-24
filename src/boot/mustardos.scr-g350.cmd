  # MustardOS (UNTESTED with the ROCKNIX U-Boot; same commands as its boot.ini branch)
  setenv bootargs "root=PARTUUID=@ROOT_PU@ rootfstype=ext4 rw rootwait quiet loglevel=0 cma=64M fbcon=nodefer vt.global_cursor_default=0 init=/sbin/init"
  load ${devtype} ${devnum}:@BOOTX@ 0x02000000 Image
  load ${devtype} ${devnum}:@BOOTX@ 0x01f00000 rk3326-batlexp-linux.dtb
  booti 0x02000000 - 0x01f00000
