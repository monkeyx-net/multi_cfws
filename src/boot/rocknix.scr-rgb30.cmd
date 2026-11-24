  # ROCKNIX (initramfs built into KERNEL; finds its partitions by label)
  setenv bootargs "boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet console=ttyS2,1500000 console=tty0 systemd.debug_shell=ttyS2"
  load ${devtype} ${devnum}:@BOOTX@ ${kernel_addr_r} /KERNEL
  load ${devtype} ${devnum}:@BOOTX@ ${fdt_addr_r} /@DTB@
  booti ${kernel_addr_r} - ${fdt_addr_r}
