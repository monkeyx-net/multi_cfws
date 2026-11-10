# multi_cfws — dArkOS + AmberELEC + MustardOS multi-boot SD card (G350, RK3326)

A single script that builds a multi-boot SD card with any 2 or all 3 of these
stock images:

- `dArkOS_G350_trixie_01012026.img`
- `AmberELEC-RG351MP.aarch64-prerelease-20261019_1835.img`
- `MustardOS_RK-G350-V_2606.0_ANDROMEDA-db2a3bd0.img`

Put the images in `img/` at the repo root (git-ignored; set `IMGDIR` to use
another folder). The dArkOS image is **always** needed, even when dArkOS is not
installed: the card's U-Boot (and `logo.bmp`) come from it.

Hold a key at power-on to pick the OS. With all three and the default settings:

| Key | OS |
|---|---|
| none | dArkOS |
| Vol- | AmberELEC |
| Vol+ | MustardOS |

In general, the `--default` OS boots with no key held. The other selected OSes,
in the fixed order AmberELEC, dArkOS, MustardOS, get Vol- and then Vol+. The
script prints the mapping before it asks for confirmation.

## How it works

The card uses dArkOS's Hardkernel `odroidgoa` U-Boot. It always runs `boot.ini`
from the first partition, so one shared `boot.ini` reads the volume keys
(Vol- = GPIO2_A1 = pin 65, Vol+ = GPIO2_A0 = pin 64, both active low) and picks the OS.
The script generates this `boot.ini` for the chosen OSes and default, and keeps a
copy as `src/boot.ini.generated`.

Layout with all three OSes:

| # | Size | Contents |
|---|---|---|
| raw | 16M | U-Boot (sectors 64–32767, taken from the dArkOS G350 image) |
| p1 | 2G | FAT32 `AMBRELC`: AmberELEC KERNEL/SYSTEM + shared `boot.ini` |
| p2 | 8G | ext4 `STORAGE`: AmberELEC `/storage` |
| p3 | 9G | exFAT `GAMES`: AmberELEC ROMs (`/storage/roms`) |
| p4 | | extended |
| p5 | 9G | exFAT `EASYROMS`: dArkOS ROMs (`/roms`) |
| p6 | 100M | FAT `BOOT`: dArkOS `/boot` |
| p7 | 12G | btrfs `ROOTFS`: dArkOS root (`root=PARTUUID=5a3c9e71-07`) |
| p8 | 512M | FAT `BOOT`: MustardOS kernel + dtbs (`/mnt/boot`) |
| p9 | 8G | ext4 `ROOTFS`: MustardOS root (`root=PARTUUID=5a3c9e71-09`) |
| p10 | rest (~9.2G) | exFAT `ROMS`: MustardOS ROMs (`/mnt/mmc`). **Must stay the last partition.** |

The space left after the system partitions is split equally between the ROM
partitions of the selected OSes. With only two OSes the numbering shifts:

- Without AmberELEC, p1 is a 64M FAT32 `BOOTSEL` partition that only holds
  `boot.ini` and `logo.bmp` (no OS update ever writes to it), and p2 is the extended
  partition. p3/p4 do not exist.
- Logical partitions always start at p5 and are handed out in order: dArkOS
  (EASYROMS, BOOT, ROOTFS), then MustardOS (BOOT, ROOTFS, ROMS). E.g. with
  AmberELEC + MustardOS, MustardOS uses p5/p6/p7.
- AmberELEC, when selected, always stays on p1/p2/p3.

`--plan` shows the exact layout for a given combination.

Each OS has its own ROMs partition:

- AmberELEC's initramfs (built into `KERNEL`, so it can't be edited) mounts the
  first existing device from `mmcblk1p3 mmcblk1p1 mmcblk1 mmcblk0p3` at
  `/storage/roms`, and **powers off** if it is not exFAT/ext4. So p3 has to be
  AmberELEC's `GAMES`. It is left empty: AmberELEC creates its system folders
  on boot (`tmpfiles.d/amberelec-dirs.conf`).
- dArkOS mounts `LABEL=EASYROMS`, so its ROMs can live in a logical partition.
- MustardOS reads its partition numbers from config files (see below), so it can
  use any logical partitions.

The script also does the first-boot work of the systems ahead of time and disables
the first-boot steps that would otherwise repartition the card:

- AmberELEC: grows STORAGE, deletes `/.please_resize_me`
- dArkOS: formats EASYROMS, extracts `roms.tar` and the themes into it, installs
  `fstab.exfat` as `/etc/fstab`, and disables `firstboot.service` /
  `expandtoexfat.sh`
- MustardOS: see below. Its own first-boot installer still runs (it reformats its ROMS
  partition as `MustardOS`, fills it and reboots), but it no longer touches the partition table.

Filesystem UUIDs are kept, because AmberELEC boots by UUID.

### MustardOS changes

Partition numbers below are for the all-three layout (p8/p9/p10); with
AmberELEC + MustardOS only they are p5/p6/p7.

- MustardOS boots without an initrd. The shared `boot.ini` loads `Image` and
  `rk3326-batlexp-linux.dtb` from `mmc 1:8` and passes `root=PARTUUID=5a3c9e71-09`.
  That dtb has the same panel init as dArkOS's `rk3326-g350-linux.dtb`.
  (MustardOS's own boot.ini picks `…-linux.dtb` or `…-linux1.dtb` from `hwrev`.)
- Partition numbers live in `/opt/muos/device/config/storage/{boot,root,rom}/num`
  on the MustardOS root. The script sets them to the MustardOS partition numbers
  (`8`, `9`, `10` with all three; originally `1`, `2`, `3`).
- `/opt/muos/script/system/reset.sh` (first boot and factory reset) runs
  `fdisk … w`, `parted resizepart <rom> 100%` and `parted set` on the whole card.
  The script comments those lines out (`: # …`). MustardOS ROMS is also the last partition,
  so a resize to 100% would do nothing anyway.
- MustardOS BOOT gets `boot.ini.mustardos` (rendered for its partitions) as `boot.ini` for reference (the original is kept
  as `boot.ini.orig`). U-Boot never reads it.
- MustardOS's integrity check (`tegridy.sh`) only writes a report. It will list the
  edited files and the dArkOS U-Boot as "differs"; that is expected.
- A MustardOS factory reset from its menu reformats its ROMS partition only.
- MustardOS's "prepare storage" tool repartitions the device you pick, but it
  refuses the system card (mmcblk0).

## Usage

**This erases the whole target device.**

```sh
sudo bash src/flash-dualboot.sh [--os darkos,amberelec,mustardos] [--default OS] [--plan] [/dev/sdX]

sudo bash src/flash-dualboot.sh /dev/sdb                    # all three, dArkOS default
sudo bash src/flash-dualboot.sh --os amberelec,mustardos --default mustardos /dev/sdb
bash src/flash-dualboot.sh --plan --os darkos,mustardos     # dry run, no root needed
# images somewhere else:
sudo IMGDIR=/path/to/images bash src/flash-dualboot.sh /dev/sdb
```

- `--os`: comma list of at least 2 OSes (default: all three).
- `--default`: the OS that boots with no key held. Must be one of `--os`. Default:
  dArkOS if selected, otherwise the first OS listed in `--os`.
- `--plan`: print the key mapping, partition layout, sfdisk script and generated
  `boot.ini` files, then exit without touching anything. The target defaults to `/dev/sdb`.

The script checks that the device is about 58GiB and is not the system disk, asks
you to type `YES`, then does everything, verifies the result and ejects the card.

Requires: `sfdisk wipefs e2fsprogs btrfs-progs exfatprogs tar dd`, plus
`dosfstools` (`mkfs.vfat`) when AmberELEC is not selected.

## Files

- `src/flash-dualboot.sh`: builds the card
- `src/boot.ini.generated` (not in git): the shared boot script from the last run,
  installed on p1 as `boot.ini` and `boot.ini.dualboot`
- `src/boot.ini.darkos`: template for dArkOS's own `/boot/boot.ini` (`@BOOT@`/`@ROOT@`/`@ROOT2@`
  are filled in by the script)
- `src/boot.ini.mustardos`: template for the MustardOS-only `boot.ini`, installed on
  MustardOS BOOT for reference

## Notes

- AmberELEC updates overwrite `boot.ini` on p1. Restore it with
  `cp boot.ini.dualboot boot.ini` on that partition.
  The original is kept as `boot.ini.amberelec`.
- **MustardOS updates** may overwrite `reset.sh` and the `storage/*/num` files.
  If the numbers go back to `1`/`2`/`3`, MustardOS would mount p1 as its boot
  partition and p3 (or nothing) as its ROMs. After every MustardOS update, check
  those files on the MustardOS root and re-apply the edits (the numbers the script
  printed as `MUS_BOOT_N`/`MUS_ROOT_N`/`MUS_ROM_N`, and comment out the
  `fdisk`/`parted` lines in `reset.sh`).
- If a key is ignored, try `gpio input gpio2_a1` (Vol-) or `gpio input gpio2_a0`
  (Vol+) in `boot.ini`; the pin name depends on the U-Boot build.
- If `elif` is not accepted by U-Boot's hush shell, nest an `if gpio input 64` inside
  the `else` branch instead.
- If MustardOS hangs at boot, try `setenv dtb_loadaddr "0x1000000"` (MustardOS's own
  value) in the MustardOS branch of `boot.ini`. Last resort: MustardOS's
  `opt/muos/device/package/uboot.img`. Its boot.ini header is `BATLEXP-UBOOT-CONFIG`,
  and dArkOS and AmberELEC would need retesting with it.
- If AmberELEC does not start with the dArkOS U-Boot, write AmberELEC's U-Boot
  to sectors 64–32767 instead.
