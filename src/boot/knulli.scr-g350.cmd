  # knulli (UNTESTED; same commands as its boot.ini branch, without board=${hwrev})
  setenv bootargs "label=KNULLI console=tty3 fbcon=rotate:0 quiet loglevel=0 consoleblank=0 vt.global_cursor_default=0 net.ifnames=0"
  load ${devtype} ${devnum}:@BOOTX@ 0x03000000 boot/linux
  load ${devtype} ${devnum}:@BOOTX@ 0x06000000 boot/uInitrd
  load ${devtype} ${devnum}:@BOOTX@ 0x01000000 @DTB@
  bootm 0x03000000 0x06000000 0x01000000
