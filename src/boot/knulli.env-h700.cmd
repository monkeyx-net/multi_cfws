# knulli: as its own env (setargs_mmc has run). Its ramdisk mounts /dev/mmcblk0p3
# (KNULLI) and takes the next partition as SHARE.
sunxi_flash read 45000000 @ABOOT@
bootm 45000000
