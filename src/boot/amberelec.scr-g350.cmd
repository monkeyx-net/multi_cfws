  # AmberELEC
  setenv fdt_addr_r "0x01f00000"
  setenv loadaddr "0x00200000"
  setenv scriptaddr "0x00500000"
  setenv kernel_addr_r "0x02008000"
  sysboot ${devtype} ${devnum}:@BOOTX@ any ${scriptaddr} /extlinux/rk3326-rg351mp-linux.dtb.conf
