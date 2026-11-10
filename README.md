# multi_cfws — dArkOS + AmberELEC dual-boot on one SD card (G350, RK3326)

A single script that builds a dual-boot SD card from two stock images:

- `dArkOS_G350_trixie_01012026.img`
- `AmberELEC-RG351MP.aarch64-prerelease-20261019_1835.img`

**Normal power-on boots dArkOS. Hold Vol- at power-on to boot AmberELEC.**

## How it works

Both systems use the same Hardkernel `odroidgoa` U-Boot. It always runs `boot.ini`
from the first partition, so one shared `boot.ini` reads the Vol- key
(GPIO2_A1 = pin 65, active low) and picks the OS.

| # | Size | Contents |
|---|---|---|
| raw | 16M | U-Boot (sectors 64–32767, taken from the dArkOS G350 image) |
| p1 | 2G | FAT32 `AMBRELC`: AmberELEC KERNEL/SYSTEM + shared `boot.ini` |
| p2 | 8G | ext4 `STORAGE`: AmberELEC `/storage` |
| p3 | 16G | exFAT `GAMES`: AmberELEC ROMs (`/storage/roms`) |
| p4 | | extended |
| p5 | rest (~16G) | exFAT `EASYROMS`: dArkOS ROMs (`/roms`) |
| p6 | 100M | FAT `BOOT`: dArkOS `/boot` |
| p7 | 16G | btrfs `ROOTFS`: dArkOS root (`root=PARTUUID=5a3c9e71-07`) |

Each OS has its own ROMs partition:

- AmberELEC's initramfs (built into `KERNEL`, so it can't be edited) mounts the
  first existing device from `mmcblk1p3 mmcblk1p1 mmcblk1 mmcblk0p3` at
  `/storage/roms`, and **powers off** if it is not exFAT/ext4. So p3 has to be
  AmberELEC's `GAMES`. It is left empty: AmberELEC creates its system folders
  on boot (`tmpfiles.d/amberelec-dirs.conf`).
- dArkOS mounts `LABEL=EASYROMS`, so its ROMs can live in logical partition p5.

The script also does the first-boot work of both systems ahead of time and disables
their first-boot scripts, which would otherwise repartition the card:

- AmberELEC: grows STORAGE, deletes `/.please_resize_me`
- dArkOS: formats EASYROMS (p5), extracts `roms.tar` and the themes into it, installs
  `fstab.exfat` as `/etc/fstab`, and disables `firstboot.service` /
  `expandtoexfat.sh`

Filesystem UUIDs are kept, because AmberELEC boots by UUID.

## Usage

**This erases the whole target device.**

```sh
sudo bash flash-dualboot.sh /dev/sdb
# images somewhere else:
sudo IMGDIR=/path/to/images bash flash-dualboot.sh /dev/sdb
```

The script checks that the device is about 58GiB and is not the system disk, asks
you to type `YES`, then does everything, verifies the result and ejects the card.

Requires: `sfdisk wipefs e2fsprogs btrfs-progs exfatprogs tar dd`.

## Files

- `flash-dualboot.sh`: builds the card
- `boot.ini`: shared dual-boot script, installed on p1 (also as `boot.ini.dualboot`)
- `boot.ini.darkos`: dArkOS's own `/boot/boot.ini`, adjusted for p6/p7

## Notes

- AmberELEC updates overwrite `boot.ini` on p1. Restore it with
  `cp boot.ini.dualboot boot.ini` on that partition.
  The original is kept as `boot.ini.amberelec`.
- If Vol- is ignored, try `gpio input gpio2_a1` in `boot.ini`; the pin name
  depends on the U-Boot build.
- If AmberELEC does not start with the dArkOS U-Boot, write AmberELEC's U-Boot
  to sectors 64–32767 instead.
