  # MustardOS (no initrd). rk3326-batlexp-linux.dtb has the same panel init as
  # dArkOS's rk3326-g350-linux.dtb. If it hangs, try dtb_loadaddr "0x1000000"
  # (MustardOS's own value).
  setenv bootargs "root=PARTUUID=@ROOT_PU@ rootfstype=ext4 rw rootwait quiet loglevel=0 cma=64M fbcon=nodefer vt.global_cursor_default=0 init=/sbin/init"
  setenv loadaddr "0x02000000"
  setenv dtb_loadaddr "0x01f00000"
  load mmc 1:@BOOTX@ ${loadaddr} Image
  load mmc 1:@BOOTX@ ${dtb_loadaddr} rk3326-batlexp-linux.dtb
  booti ${loadaddr} - ${dtb_loadaddr}
