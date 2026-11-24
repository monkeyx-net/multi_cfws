  # ROCKNIX: as its own boot.scr does; extlinux.conf.dualboot is written by the script
  # (G350 dtb, boot=UUID=@RNX_BOOT_UUID@ disk=UUID=@RNX_STOR_UUID@).
  setenv kernel_addr_r "0x09000000"
  setenv fdtoverlay_addr_r "0x08000000"
  sysboot ${devtype} ${devnum}:@BOOTX@ any 0x100000 /extlinux/extlinux.conf.dualboot
