  # ArkOS (RG351MP image; dArkOS's G350 dtb, copied to its BOOT by the script)
  setenv bootargs "root=PARTUUID=@ROOT_PU@ rootwait rw fsck.repair=yes net.ifnames=0 fbcon=rotate:0 console=/dev/ttyFIQ0 quiet splash plymouth.ignore-serial-consoles consoleblank=0 vt.global_cursor_default=0"
  setenv loadaddr "0x02000000"
  setenv initrd_loadaddr "0x01100000"
  setenv dtb_loadaddr "0x01f00000"
  load mmc 1:@BOOTX@ ${loadaddr} Image
  load mmc 1:@BOOTX@ ${initrd_loadaddr} uInitrd
  load mmc 1:@BOOTX@ ${dtb_loadaddr} @DTB@
  booti ${loadaddr} ${initrd_loadaddr} ${dtb_loadaddr}
