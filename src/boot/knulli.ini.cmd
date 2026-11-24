  # knulli (boots, tested; finds its boot partition by label=KNULLI, /userdata on the
  # next partition)
  setenv bootargs "label=KNULLI console=tty3 fbcon=rotate:0 quiet loglevel=0 consoleblank=0 vt.global_cursor_default=0 net.ifnames=0 board=${hwrev}"
  load mmc 1:@BOOTX@ 0x03000000 boot/linux
  load mmc 1:@BOOTX@ 0x06000000 boot/uInitrd
  load mmc 1:@BOOTX@ 0x01000000 @DTB@
  bootm 0x03000000 0x06000000 0x01000000
