# ROCKNIX: mainline kernel (initramfs built in) in an Android boot image the script builds
# from KERNEL (loaded at a 2MB-aligned address), and its own dtb (fixed for the vendor
# firmware, fix_dtb) in a raw partition. bootm always passes U-Boot's control dtb, so
# "fdt addr -c" points that at ROCKNIX's; "fdt addr" too, as bootm writes the initrd
# properties to the working dtb first. Both last, after the reads. Finds its partitions
# by label. "fdt resize" gives the dtb room for bootm's edits (bootargs, memory): without
# it this U-Boot hangs ("FDT creation failed! hanging...", tested). The vendor U-Boot
# leaves the display engine (DE33) showing its logo, and its state overrides what the mainline driver writes (tested: logo stays), so the DE is put in
# reset (CCU DE_BGR_REG 0x0300160C: reset bit 16 low, gate on); the driver releases it.
# If bootm returns, both go back to the vendor dtb and the DE out of reset for the
# fallback OS.
sunxi_flash read 45000000 @ABOOT@
sunxi_flash read 43000000 @DTBPART@
setenv bootargs boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet console=ttyS0,115200 console=tty0
fdt addr 43000000
fdt resize 1000
fdt addr -c 43000000
mw.l 0300160c 1
bootm 45000000
fdt addr -c ${fdtcontroladdr}
fdt addr ${fdtcontroladdr}
mw.l 0300160c 10001
