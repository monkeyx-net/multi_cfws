# multi_cfws — multi-boot SD cards for the G350 (RK3326) and RGB30 (RK3566)

A single script that builds a multi-boot SD card with 2 or 3 of the stock images
for one device (3 at most: no key, Vol- and Vol+):

| Device | OSes you can pick (`--os`) | Image that is **always** needed |
|---|---|---|
| **G350** (`--device g350`, the default) | `amberelec darkos mustardos rocknix` | **dArkOS G350**, even without `darkos` in `--os`. With `rocknix`: **ROCKNIX** instead |
| **RGB30** (`--device rgb30`, see [RGB30](#rgb30)) | `darkos knulli rocknix` | **knulli**, even without `knulli` in `--os` |

The card's U-Boot is copied from that image. It must be in `img/` even if you don't
install that OS. The script stops before touching the card if it is missing, and
`--plan` prints which image it uses.

G350 images:

- `dArkOS_G350_trixie_01012026.img`
- `AmberELEC-RG351MP.aarch64-prerelease-20261019_1835.img`
- `MustardOS_RK-G350-V_2606.0_ANDROMEDA-db2a3bd0.img`
- `ROCKNIX-RK3326.aarch64-20261101-b.img` (see [ROCKNIX on the G350](#rocknix-on-the-g350))

Put the images in `img/` at the repo root (git-ignored; set `IMGDIR` to use
another folder). **Always needed on the G350: the dArkOS G350 image**, even when
dArkOS is not installed. The card's U-Boot (and `logo.bmp`) come from it.

Hold a key at power-on to pick the OS. With `--os amberelec,darkos,mustardos`
and the default settings:

| Key | OS |
|---|---|
| none | dArkOS |
| Vol- | AmberELEC |
| Vol+ | MustardOS |

In general, the `--default` OS boots with no key held. The other selected OSes,
in the fixed order AmberELEC, dArkOS, MustardOS, ROCKNIX, get Vol- and then Vol+. The
script prints the mapping before it asks for confirmation.

### Booting another OS once (`multiboot-next`)

On the RGB30, and on the G350 with ROCKNIX, a script running in one OS can pick the
OS for the next boot only, without a key:

```sh
multiboot-next rocknix && reboot      # boots ROCKNIX once, then back to normal
multiboot-next --show                 # pending request (prints nothing if none)
multiboot-next --clear                # cancel it
```

It needs root and does not reboot by itself. The OS names are the `--os` names. The
script installs it on every OS of the card, here (`--plan` prints the list too):

| OS | Path |
|---|---|
| dArkOS, MustardOS | `/usr/local/bin/multiboot-next` |
| AmberELEC, ROCKNIX | `/storage/bin/multiboot-next` |
| knulli | `/userdata/multiboot-next` (exFAT: run it with `sh /userdata/multiboot-next`) |

How it works: it writes `next=<os>` (one line, LF) to `nextboot.txt` on the partition
holding `boot.scr` (p1 on the G350, `BOOTSEL` on the RGB30), which it finds by its fixed
PARTUUID (or by the disk ID when `/dev/disk/by-partuuid` is missing). If that partition is
mounted read-only, as AmberELEC's `/flash` is, it is remounted read-write for the write
and back to read-only. Your own tools can write the file directly instead. At power-on,
`boot.scr` loads the file, overwrites it with `next=` (so the request is used once) and
boots that OS. A held volume key still wins, and an empty, missing or unknown value
gives the normal boot.

Not available yet on the G350 without ROCKNIX: its old U-Boot (`boot.ini`) may lack
`fatwrite`, which clearing the request needs. That still has to be tested on a device.
This feature has not been tested on a device yet either.

## How it works (G350)

The card uses dArkOS's Hardkernel `odroidgoa` U-Boot. It always runs `boot.ini`
from the first partition, so one shared `boot.ini` reads the volume keys
(Vol- = GPIO2_A1 = pin 65, Vol+ = GPIO2_A0 = pin 64, both active low) and picks the OS.
The script generates this `boot.ini` for the chosen OSes and default, and keeps a
copy as `src/boot.ini.generated`.

Layout with AmberELEC, dArkOS and MustardOS:

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
  (EASYROMS, BOOT, ROOTFS), then ROCKNIX (ROCKNIX, STORAGE, GAMES), then MustardOS
  (BOOT, ROOTFS, ROMS), so MustardOS ROMS stays last. E.g. with
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

### ROCKNIX on the G350

**Tested on a G350** with dArkOS + AmberELEC + ROCKNIX: all three boot, and the keys
pick the right one.

The `b` build of ROCKNIX for RK3326 has a mainline 7.1 kernel. It crashes (black
flash, then a reboot) when started from dArkOS's Hardkernel U-Boot. So **when ROCKNIX
is selected, the card uses ROCKNIX's own mainline U-Boot (2025.10)** instead, and the
dArkOS image is then only needed if dArkOS is installed. That U-Boot also boots dArkOS
and AmberELEC (tested). MustardOS has not been tested with it; `--plan` warns.

ROCKNIX's U-Boot does not read `boot.ini`. It runs `boot.scr` from p1 (only the script
boot method is enabled, so AmberELEC's `extlinux.conf` on p1 is ignored). The script
generates `boot.cmd`, compiles it with `mkimage`, and keeps a copy as
`src/boot.cmd.generated`. Two things differ from `boot.ini`:

- **Keys.** In this U-Boot `gpio input` returns "success" instead of the pin value, and
  nothing enables the pull-ups of the volume keys, so they always read 0. `boot.scr`
  enables the pull-ups itself (`mw.l 0xff140070 0x000f0005`: PX30 GRF GPIO2A pull
  register, A0/A1 = pull-up) and reads GPIO2's input register (`0xff260050`;
  bit 0 = Vol+, bit 1 = Vol-) with `setexpr`. If both read as pressed, Vol+ wins: a
  press at an angle can pull Vol- low too.
- **ROCKNIX boots like its own `boot.scr`**: `sysboot` with kernel at `0x09000000` and
  `/extlinux/extlinux.conf.dualboot` on the ROCKNIX partition, which the script writes
  (`KERNEL`, `rk3326-batlexp-g350.dtb` from its `extlinux.conf.g350`, no panel overlay).

- AmberELEC's ext4 is also labelled `STORAGE`, so ROCKNIX is passed filesystem
  UUIDs instead of labels: `boot=UUID=0111-5938 disk=UUID=b4434352-…`. Its partitions
  are copied with `dd`, so the UUIDs stay the same.
- As on the RGB30: STORAGE is grown to 8G and `/.please_resize_me` deleted (its
  first boot would `parted resizepart` STORAGE to 100%), `/storage/.config` is
  seeded, and `system.gamesdevice` points at ROCKNIX's own `GAMES` partition so
  that `automount` does not take another OS's partition.
- With dArkOS + ROCKNIX + MustardOS, MustardOS moves to p11–p13. U-Boot reads
  partition numbers as hex, so `boot.scr` says `${devnum}:b` for p11.
- AmberELEC's own `boot.ini` stays on p1 (unused); its updates rewriting it don't matter
  with this U-Boot.
- To go back to the dArkOS U-Boot, write sectors 64–32767 from the dArkOS image; the
  card would then need a `boot.ini` again (re-run the script without ROCKNIX).

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

## RGB30

RGB30 images (also in `img/`):

- `dArkOS_RGB30_trixie_08282026.img`
- `knulli-rk3568-powkiddy-rgb30-scarab-20260510.img`
- `ROCKNIX-RK3566.aarch64-20261101-Generic.img`

**Always needed on the RGB30: the knulli image**, even when knulli is not installed.
The card's U-Boot comes from it.

**Tested on an RGB30** with dArkOS + knulli + ROCKNIX: all three boot and the keys pick
the right one. The GPT layout below is the final design; the hardware test used the
same boot commands with dArkOS's root on p9 and a patched initramfs (see dArkOS below),
so check the first dArkOS boot on a freshly built card.

With `--device rgb30 --os darkos,knulli,rocknix` and the default settings:

| Key | OS |
|---|---|
| none | dArkOS |
| Vol- | knulli |
| Vol+ | ROCKNIX |

Keys are handed out as on the G350, in the fixed order dArkOS, knulli, ROCKNIX.

### How it works (RGB30)

The three images use three different U-Boots (dArkOS: Rockchip 2017.09, knulli:
mainline 2024.07 with Rockchip's BL31, ROCKNIX: mainline 2026.01 with upstream
TF-A). The card uses knulli's: it runs a boot script and boots all three kernels
(dArkOS's vendor 5.10, knulli's vendor 4.19, ROCKNIX's mainline).

That U-Boot runs `bootflow scan` and finds `boot.scr` on `BOOTSEL`, the only partition
marked bootable (GPT attribute `LegacyBIOSBootable`). `boot.scr` loads the chosen OS's
kernel, dtb and initrd directly, with the bootargs from that OS's `extlinux.conf`. Its
source is kept on `BOOTSEL` as `boot.cmd` and in `src/boot.cmd.generated`.

**Keys.** knulli's U-Boot was built without the `gpio` and `setexpr` commands, and
nothing enables the pull-ups of the volume keys (Vol+ = GPIO3_A7, Vol- = GPIO3_B0,
active low, `bias-pull-up` in the dtb). So `boot.scr`:

1. enables the pull-ups: `mw.l 0xfdc600a0 0xc0004000` (A7) and
   `mw.l 0xfdc600a4 0xffff5555` (B0–B7), RK3568 SYS_GRF GPIO3A_P/GPIO3B_P, `01` = pull-up,
   upper 16 bits = write mask;
2. copies GPIO3's input register (`0xfe760070`) to RAM with `cp.l`;
3. compares bytes with `itest.b`: byte 0 below `0x80` = Vol+ (bit 7), byte 1 `0xfe`
   (or `0x7e`) = Vol- (bit 0; the other B pins are pulled up). Vol+ is checked first.

The card is **GPT** (the G350 card is MBR). Layout with all three OSes:

| # | Size | Contents |
|---|---|---|
| raw | 16M | U-Boot (sectors 64–32767, taken from the knulli image) |
| p1 | 4G | FAT `KNULLI`: knulli boot + squashfs |
| p2 | 10.7G | exFAT `SHARE`: knulli `/userdata` (ROMs, BIOS, saves) |
| p3 | 100M | FAT `dArkOS_Fat`: dArkOS `/boot` |
| p4 | 12G | btrfs `ROOTFS`: dArkOS root |
| p5 | 10.7G | exFAT `EASYROMS`: dArkOS ROMs (`/roms`) |
| p6 | 64M | FAT32 `BOOTSEL`: `boot.scr` + `boot.cmd` (bootable) |
| p7 | 2G | FAT `ROCKNIX`: ROCKNIX KERNEL/SYSTEM |
| p8 | 8G | ext4 `STORAGE`: ROCKNIX `/storage` |
| p9 | 10.7G | exFAT `GAMES`: ROCKNIX ROMs (`/storage/roms`) |

With two OSes: dArkOS + knulli is p1–p6 as above; dArkOS + ROCKNIX is
p1 `BOOTSEL`, p2 ROCKNIX `GAMES`, p3–p5 dArkOS, p6/p7 ROCKNIX; knulli + ROCKNIX is
p1/p2 knulli, p3 `BOOTSEL`, p4–p6 ROCKNIX. `--plan` shows the exact layout.

What each OS needs, and what the script does about it:

- **dArkOS** must be on **p3/p4/p5** as in its own image: its initramfs ignores `root=`
  and always mounts `/dev/mmcblk1p4` (a customised `scripts/local`), and its
  `fstab.exfat` and scripts use p3/p5. Its `uInitrd` is a plain gzip initramfs despite
  the name, so `booti` gets its size. The script does the same first-boot work as on
  the G350 (EASYROMS, `roms.tar`, themes, `fstab.exfat` as `/etc/fstab`, disables
  `firstboot.service`).
- **knulli** finds its boot partition by `label=KNULLI` and takes the *next*
  partition as `/userdata` (`knulli-part`, single digit only), so it gets p1/p2. Its
  first boot would run `parted resizepart <share> 100%` and reformat the share
  (`autoresize=true` in `knulli-boot.conf`). The script sets `autoresize=false`
  (original kept as `knulli-boot.conf.orig`) and creates the exFAT `SHARE` itself,
  which is what the resize would have produced.
- **ROCKNIX** finds its partitions by label (`boot=LABEL=ROCKNIX disk=LABEL=STORAGE`).
  As with AmberELEC, the script grows STORAGE to 8G and deletes `/.please_resize_me`.
  Its games mount (`automount`) uses `system.gamesdevice` if set. Otherwise it
  mounts the first unmounted partition of any disk over 8GB as "external games",
  which on this card would be another OS's partition. The script therefore seeds
  `/storage/.config` from the image's `/usr/config` (what ROCKNIX does on first boot)
  and sets `system.gamesdevice=/dev/disk/by-partuuid/<GUID>` (its `GAMES`).

### RGB30 notes

- **mkimage** (`uboot-tools`) is needed to compile `boot.scr`, and `unsquashfs`
  (`squashfs-tools`) for ROCKNIX.
- PARTUUIDs are fixed GUIDs `5a3c9e71-0000-4000-8000-0000000000NN` (NN = partition
  number in hex).
- To change `boot.scr`, edit `boot.cmd` on `BOOTSEL`, then rebuild with
  `mkimage -A arm64 -O linux -T script -C none -d boot.cmd boot.scr`.
- knulli's own dtb for the RGB30 **v2** panel is `boot/rk3566-rgb30-v2-linux.dtb`.
  Change `KNU_DTB` in the script (or the knulli branch of `boot.cmd`) on a v2 unit.
- knulli and ROCKNIX updates only touch files on their own boot partitions.
  ROCKNIX's update copies DTBs but does not rewrite U-Boot.

## Usage

**This erases the whole target device.**

```sh
sudo bash src/flash-dualboot.sh [--device g350|rgb30] --os LIST [--default OS] [--plan] [/dev/sdX]

sudo bash src/flash-dualboot.sh --os amberelec,darkos,mustardos /dev/sdb   # G350, dArkOS default
sudo bash src/flash-dualboot.sh --os amberelec,mustardos --default mustardos /dev/sdb
sudo bash src/flash-dualboot.sh --device rgb30 --os darkos,knulli,rocknix /dev/sdb
bash src/flash-dualboot.sh --plan --os darkos,mustardos     # dry run, no root needed
bash src/flash-dualboot.sh --plan --device rgb30 --os knulli,rocknix
# images somewhere else:
sudo IMGDIR=/path/to/images bash src/flash-dualboot.sh --os darkos,mustardos /dev/sdb
```

- `--device`: `g350` (default) or `rgb30`.
- `--os` (required): comma list naming 2 or 3 OSes of that device. There is no
  default: G350 `amberelec darkos mustardos rocknix`, RGB30 `darkos knulli rocknix`.
- `--default`: the OS that boots with no key held. Must be one of `--os`. Default:
  dArkOS if selected, otherwise the first OS listed in `--os`.
- `--plan`: print the key mapping, partition layout, sfdisk script and generated
  `boot.ini` / `boot.cmd` files (and `multiboot-next`), then exit without touching anything. The target defaults to `/dev/sdb`.

The script checks that the device is about 58GiB and is not the system disk, asks
you to type `YES`, then does everything, verifies the result and ejects the card.

### Python version (being tested)

`src/flash-dualboot.py` does the same job with the same options. It needs Python 3.8 or
newer, standard library only:

```sh
sudo python3 src/flash-dualboot.py --os amberelec,darkos,mustardos /dev/sdb
python3 src/flash-dualboot.py --plan --os darkos,mustardos
```

Its `--plan` output is byte-identical to the bash script's for every OS combination,
and the two scripts leave the same files on a simulated card. It has not flashed a
real card yet, so the bash script stays the reference until it has, on both devices.

### Installing the tools

Requires: `sfdisk wipefs e2fsprogs btrfs-progs exfatprogs tar dd`, plus
`dosfstools` (`mkfs.vfat`) when AmberELEC is not selected, and `squashfs-tools`
(`unsquashfs`) with ROCKNIX. The RGB30, and the G350 with ROCKNIX, also need
`uboot-tools` (`mkimage`). The script checks for the tools it needs before it
touches the card. The commands below install everything for every combination,
plus Python 3 for `flash-dualboot.py`.

Ubuntu / Debian (Ubuntu 22.04 or newer, for `exfatprogs`):

```sh
sudo apt update
sudo apt install fdisk util-linux e2fsprogs btrfs-progs exfatprogs dosfstools \
  squashfs-tools u-boot-tools parted udev eject tar coreutils python3
```

Arch Linux:

```sh
sudo pacman -Syu --needed util-linux e2fsprogs btrfs-progs exfatprogs dosfstools \
  squashfs-tools uboot-tools parted systemd tar coreutils python
```

| Tool | Ubuntu package | Arch package |
| --- | --- | --- |
| `sfdisk` | `fdisk` | `util-linux` |
| `wipefs`, `blockdev`, `lsblk`, `findmnt`, `mount` | `util-linux` | `util-linux` |
| `eject` | `eject` | `util-linux` |
| `e2fsck`, `resize2fs`, `debugfs`, `dumpe2fs` | `e2fsprogs` | `e2fsprogs` |
| `btrfs` | `btrfs-progs` | `btrfs-progs` |
| `mkfs.exfat` | `exfatprogs` | `exfatprogs` |
| `mkfs.vfat` | `dosfstools` | `dosfstools` |
| `unsquashfs` | `squashfs-tools` | `squashfs-tools` |
| `mkimage` | `u-boot-tools` | `uboot-tools` |
| `partprobe` | `parted` | `parted` |
| `udevadm` | `udev` | `systemd` |
| `dd`, `tar` | `coreutils`, `tar` | `coreutils`, `tar` |

Most of these are already installed on a desktop system; the ones usually missing
are `btrfs-progs`, `exfatprogs`, `squashfs-tools` and `u-boot-tools`.

## Files

- `src/flash-dualboot.sh`: builds the card
- `src/flash-dualboot.py`: the same in Python (see Usage), one class per OS
- `src/boot.ini.generated` (not in git): the shared boot script from the last run,
  installed on p1 as `boot.ini` and `boot.ini.dualboot`
- `src/boot.cmd.generated` (not in git): the RGB30 boot script source from the last
  run, installed on p1 as `boot.cmd` and compiled to `boot.scr`
- `src/boot.ini.darkos`: template for dArkOS's own `/boot/boot.ini` (`@BOOT@`/`@ROOT@`/`@ROOT2@`
  are filled in by the script)
- `src/boot.ini.mustardos`: template for the MustardOS-only `boot.ini`, installed on
  MustardOS BOOT for reference
- `src/multiboot-next`: template for the [`multiboot-next`](#booting-another-os-once-multiboot-next)
  helper installed on each OS (`@PARTUUID@` etc. are filled in by the script)

## Notes (G350)

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
- ROCKNIX updates rewrite DTBs on their own partition only. If ROCKNIX hangs at
  boot, try `setenv dtb_loadaddr "0x08000000"` in its branch of `boot.ini`.
- If AmberELEC does not start with the dArkOS U-Boot, write AmberELEC's U-Boot
  to sectors 64–32767 instead.
