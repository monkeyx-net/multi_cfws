  # dArkOS
  setenv bootargs "root=PARTUUID=@ROOT_PU@ rootwait rw fsck.repair=yes net.ifnames=0 fbcon=rotate:0 console=/dev/ttyFIQ0 quiet splash consoleblank=0 vt.global_cursor_default=0"
  setenv loadaddr "0x02000000"
  setenv initrd_loadaddr "0x04000000"
  setenv dtb_loadaddr "0x01f00000"
  load mmc 1:@BOOTX@ ${loadaddr} Image
  load mmc 1:@BOOTX@ ${initrd_loadaddr} uInitrd
  load mmc 1:@BOOTX@ ${dtb_loadaddr} rk3326-g350-linux.dtb
  booti ${loadaddr} ${initrd_loadaddr} ${dtb_loadaddr}
