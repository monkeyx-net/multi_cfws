# CLAUDE.md

Builds a multi-boot SD card with 2 or 3 OSes for one device (`--device`). `--os` is
required and must name them; there is no "all" default:
the G350 (RK3326; dArkOS, AmberELEC, ArkOS, knulli, MustardOS, ROCKNIX; the default) or the
RGB30 (RK3566; dArkOS, ArkOS, knulli, ROCKNIX). Everything lives in `src/`: `flash-dualboot.py` (Python 3.8+,
standard library only), its three G350 boot.ini templates and the `multiboot-next` template.
`README.md` is the user-facing documentation; keep it in sync when the layout, CLI or key
mapping change.

`flash-dualboot.py` reads top to bottom in the order things happen: settings, choices,
layout, per-OS code, boot script, plan, flash. Per-device values (images, U-Boot source,
source partitions, dtbs, key pins) sit in the `g350()` / `rgb30()` `Device` factories;
the layouts are `layout_g350` / `layout_rgb30`. One class per OS (`AmberELEC`, `DarkOS`, `ArkOS`,
`MustardOS`, `Knulli`, `Rocknix`) holds its boot commands (`boot` dict per boot mode,
`@BOOTX@`/`@ROOT@`/`@ROOT_PU@`/`@DTB@` placeholders) and its run steps (`fix_fs`,
`prepare`, `verify`); partition numbers are `c.n("DRK_BOOT")` etc.

## Testing without a card

The real run needs root, the images and an SD card (32GB–2TiB; `--plan` assumes ~62.5GB), and it erases the
card. Changes to the layout or boot scripts are tested with `--plan`, which needs no root
and writes nothing:

```sh
python3 -m py_compile src/flash-dualboot.py
python3 src/flash-dualboot.py --plan --os amberelec,mustardos --default mustardos /x
```

Run `--plan` for all 4 combinations (`amberelec,darkos,mustardos`, `amberelec,darkos`,
`amberelec,mustardos`, `darkos,mustardos`) with each possible default. To check that
sfdisk accepts the layout and numbers partitions as the script expects, pipe the
printed sfdisk script into `sfdisk` on a sparse file
(`truncate -s $((122070312*512)) card.img`) and compare `sfdisk -d card.img` with the
printed `Numbers:` line.

For the G350 with ROCKNIX, also run the 6 combinations of 2–3 OSes that include
`rocknix` (at most 3 OSes: no key + 2 keys).

For the RGB30, do the same with `--device rgb30` for `darkos,knulli,rocknix`,
`darkos,knulli`, `darkos,rocknix` and `knulli,rocknix` with each default.

ArkOS (both devices) and knulli on the G350 add many more combinations (G350: every 2–3 of
6 OSes; RGB30: every 2–3 of 4). Script it: run `--plan` for each combination and default,
feed each sfdisk script to `sfdisk` on a sparse file and compare the partitions with the
listing and the `Numbers:` line.

Save the `--plan` output of every combination before a change and diff it afterwards:
only the intended lines may change. G350 changes must not alter the G350 `--plan` output
(apart from the `Device:` and `U-Boot:` lines).

With all three OSes and default dArkOS, the generated boot.ini branches must stay
equivalent to the original layout: dArkOS p6/p7 (`-07`), MustardOS p8/p9 (`-09`),
Vol- AmberELEC, Vol+ MustardOS.

## Constraints that are easy to break (G350)

- **Two boot modes (`c.bootmode`).** Without ROCKNIX (`ini`), U-Boot comes from the
  dArkOS image (sectors 64–32767), even when dArkOS is not installed, and so does
  `logo.bmp`; the shared boot.ini needs the `odroidgoa-uboot-config` header, and U-Boot
  only reads `boot.ini` from p1. With ROCKNIX (`scr-g350`), U-Boot comes from the
  ROCKNIX image, because ROCKNIX's mainline kernel crashes on the Hardkernel U-Boot
  (tested). It runs `boot.scr` from p1 (`boot_cmd_g350`, `boot["scr-g350"]`).
  The `ini` `--plan` output must not change when editing `scr-g350`.
- **`scr-g350` key reading (tested on a G350):** `gpio input` returns success, not the
  pin value, and the volume keys have no pull-up, so `boot.scr` sets the pull-ups
  (`mw.l 0xff140070 0x000f0005`) and reads GPIO2 EXT_PORTA (`0xff260050`, bit 0 Vol+,
  bit 1 Vol-) with `setexpr` (`/` and `%`; a bare `&` breaks hush; `KEYS_G350`).
  Vol+ is checked first. dArkOS, AmberELEC and ROCKNIX are tested with this U-Boot;
  MustardOS is not.
- **AmberELEC must be p1/p2/p3.** Its initramfs is built into `KERNEL` (can't be
  edited). It mounts mmcblk1p3 as ROMs and powers off if that isn't exFAT/ext4.
  AmberELEC boots by filesystem UUID, so its partitions are copied with `dd`, never
  reformatted. Its updates overwrite `boot.ini` on p1 (users restore it from
  `boot.ini.dualboot`).
- **Without AmberELEC, p1 is a 64M FAT32 `BOOTSEL`** that only holds boot.ini and logo.bmp.
- **MBR logical partitions always start at p5**, whatever the number of primaries.
  The order is fixed: knulli (KNULLI, SHARE), dArkOS (EASYROMS, BOOT, ROOTFS), ArkOS
  (ARKBOOT, root, ARKROMS), ROCKNIX (BOOT, STORAGE, GAMES), then MustardOS (BOOT,
  ROOTFS, ROMS).
- **knulli (G350) must be p5/p6**: `knulli-part` takes the share as boot partition + 1
  and parses one digit only (`layout_g350` checks it). Vendor 4.4 kernel as a uImage
  (`bootm`); the uImage is loaded at `0x03000000`, not knulli's `0x02000000`, because
  `bootm` copies it to `0x01080000` and mainline U-Boot refuses overlapping copies. Boots
  in `ini` mode (tested), but vibrates from ~3 s after power-on until the knulli logo
  (rumble motor on PWM0, GPIO0_B7, active low; only knulli's `S01pwm` stops it). Tried on
  a G350, none helped: `gpio set 15`; PWM0 at 100% duty via `mw.l` (no change); plus pin
  mux to PWM0, or PWM0 disabled in knulli's dtb (both: constant vibration). Left as is. `scr-g350` untested.
- **ArkOS (both devices)** boots by `root=PARTUUID`; its fstab is rewritten to PARTUUIDs
  (`ArkOS.fstab`) and its BOOT/ROMS are labelled `ARKBOOT`/`ARKROMS`, because dArkOS
  mounts `LABEL=BOOT`/`LABEL=EASYROMS`. The G350 build is the RG351MP image; its dtb drives
  the G350 panel wrong (white/yellow screen, tested), so it boots with dArkOS's
  `rk3326-g350-linux.dtb`, copied from the dArkOS image (`copy_from_darkos_boot`). Boots
  on a G350 with that dtb in `ini` mode (tested); `scr-g350` and the RGB30 are untested.
- **U-Boot `dev:part` numbers are hex** (both U-Boots). MustardOS can be p11–p13, so
  the boot commands use `@BOOTX@` (`hx(n)`), in the `boot` dicts and the templates.
- **ROCKNIX on the G350 boots by filesystem UUID** (`RNX_BOOT_UUID`/`RNX_STOR_UUID`,
  in the `extlinux.conf.dualboot` the script writes on its partition, `Rocknix.EXTLINUX`;
  `sysboot` as in ROCKNIX's own boot.scr), because AmberELEC's ext4 is labelled `STORAGE`
  too. Its STORAGE resize flag is removed and `system.gamesdevice` is seeded, as on the RGB30.
- **MustardOS ROMS must be the last partition**: its factory reset resizes that
  partition to 100%. The `fdisk`/`parted` lines in `reset.sh` are commented out too
  (`MustardOS.prepare`).
- **MustardOS finds its partitions through** `/opt/muos/device/config/storage/{boot,root,rom}/num`
  on its rootfs. The script writes the computed numbers there.
- **Never hard-code partition numbers.** Use the layout roles: `c.n("DRK_ROMS")`,
  `DRK_BOOT DRK_ROOT MUS_BOOT MUS_ROOT MUS_ROM` (and `AMB_*`, `ARK_*`, `KNU_*`, `RNX_*`, `BOOTSEL`);
  `c.part(role)` gives the device path. Per-OS steps live in the OS class (only selected
  OSes run, via `c.oses`); anything else is guarded with `c.sel("<os>")`.
- **Root partitions are passed as `root=PARTUUID=5a3c9e71-NN`**: the disk ID
  `0x5a3c9e71` (`DISKID`) plus the partition number in two-digit hex (`c.partuuid(n)`).
  The disk ID is set in the sfdisk script.
- **Key mapping:** the default OS gets no key. The other OSes, in `Device.all_os` order
  (amberelec, arkos, darkos, knulli, mustardos, rocknix), get Vol- (pin 65) and then Vol+ (pin 64)
  (`c.key_os`). Both keys are active low, and in `ini` mode `gpio input N` succeeds only
  while the key is held.

## Constraints that are easy to break (RGB30)

Tested on an RGB30 with all three OSes (with dArkOS root on p9 and a patched initramfs;
the GPT layout below then replaced that).

- **GPT, not MBR** (`layout_rgb30`, `Layout.gpt`). `c.partuuid(n)` gives fixed GUIDs
  `5a3c9e71-0000-4000-8000-0000000000NN`. Partitions end before the backup GPT (`end`
  in `make_layout`).
- **dArkOS must be p3/p4/p5** (BOOT, ROOTFS, EASYROMS): its initramfs hard-codes
  `/dev/mmcblk1p4` and ignores `root=`; its fstab.exfat is copied unchanged.
  `uInitrd` is a plain gzip initramfs: `booti` needs `addr:${filesize}`.
- **knulli must be p1/p2**: `knulli-part` takes the share as boot partition + 1 and
  only parses one digit. Its `autoresize` in `knulli-boot.conf` is set to false.
- **U-Boot always comes from the knulli image** (mainline 2024.07, Rockchip BL31),
  even when knulli is not installed. It runs `bootflow scan` and boots `boot.scr` from
  `BOOTSEL` (`c.n("BOOTSEL")`), the only partition with `LegacyBIOSBootable`; never put
  `extlinux.conf` on it.
- **Keys:** that U-Boot has no `gpio` or `setexpr`. `boot.scr` (`KEYS_RGB30`) sets the
  pull-ups with `mw.l` (SYS_GRF `0xfdc600a0`/`0xfdc600a4`), copies GPIO3 EXT_PORT
  (`0xfe760070`) to RAM with `cp.l` and compares bytes with `itest.b` (Vol+ = byte 0 <
  0x80, Vol- = byte 1 == 0xfe/0x7e). Keys: `all_os` order arkos, darkos, knulli, rocknix.
- **ArkOS (RGB30)**: its initramfs hard-codes `/dev/mmcblk1p4` like dArkOS's. dArkOS
  keeps p3/p4/p5; ArkOS's `uInitrd` is patched instead (`ArkOS.patch_initrd`: the
  `local dev_id=` line in `scripts/local` back to `"$1"`, same length, regzipped), so it
  goes after the other OSes (or fills p2 with dArkOS only).
- **U-Boot `dev:part` numbers are hex**: the `boot["scr-rgb30"]` commands use `@BOOTX@`.
  `${devtype} ${devnum}` are set by U-Boot's script bootmeth.
- **ROCKNIX needs `system.gamesdevice`** in `/storage/.config/system/configs/system.cfg`.
  Otherwise its `automount` mounts another OS's partition as its games card. The whole
  `/usr/config` is seeded first, because `chksysconfig` rsyncs it over `system.cfg`
  while `retroarch.cfg` is missing. Its STORAGE resize flag is removed (`grow_storage`).
- Partition roles: `KNU_BOOT KNU_SHARE RNX_BOOT RNX_STOR RNX_ROMS BOOTSEL` plus the
  shared `DRK_*` and `ARK_*`.

## One-shot next boot (`multiboot-next`)

- Only in the `boot.scr` modes (`scr-g350`, `scr-rgb30`); `ini` mode is unchanged until
  the Hardkernel U-Boot is shown to have `fatwrite` and `env import`.
- `src/multiboot-next` is a POSIX sh template (`@OSES@ @PARTUUID@ @PARTNUM@ @DISKID_AT@
  @DISKID_HEX@ @DEVICE@`), filled by `next_helper` and installed by `install_next` at
  `next_path(os)` (`NEXT_PATHS`). It writes `next=<os>` to `nextboot.txt` on `BOOTSEL`.
- `boot.cmd` (`next_block`): after the keys are read, it loads the file with
  `env import -t ... next`, clears it with `fatwrite` (6 bytes `next=\n`, only when it held a
  request), and the branches (`boot_branches`) test `${next}` after the key branches, so a
  held key wins.
- Not yet tested on a device.

## Boot scripts

- The shared p1 boot.ini / boot.cmd is generated (`boot_config`: `boot_ini_g350`,
  `boot_cmd_g350`, `boot_cmd_rgb30`, with `boot_branches` filling in each OS's
  `boot[c.bootmode]`). Nothing is stored in the repo; each run writes a copy to
  `src/boot.ini.generated` or `src/boot.cmd.generated` (gitignored).
- `src/boot.ini.darkos`, `src/boot.ini.arkos` and `src/boot.ini.mustardos` are templates with `@BOOT@`, `@BOOTX@` (hex), `@ROOT@`
  and `@ROOT2@` (hex, two digits), filled in by `render`. They go on each OS's own
  BOOT partition; U-Boot never reads them.
- If you change boot commands, change them in the OS class's `boot` dict (every boot mode
  it has) **and** in the matching template.
- Hardware debugging without a serial console: in a test boot.scr, `env export -t <addr>
  <vars>` then `fatwrite ${devtype} ${devnum}:1 <addr> log.txt ${filesize}` writes
  variables to p1 (works on the mainline U-Boots; `env export` failed on the Hardkernel
  one). Use addresses inside RAM (both devices have 1GB).

## Image-specific values

Image file names (`Device.images`), the source partition offsets/sizes (`Device.src`,
`Src(start, size)` taken from `sfdisk -d <image>`; for the RGB30 dArkOS image they are its
p3/p4) and the dtb paths (`Device.dtb`) in `g350()` / `rgb30()` are tied to specific image
releases. When an image is updated, re-check them and the filesystem UUIDs
(`AMB_*_UUID`, `RNX_*_UUID`) used in the boot commands and the verification step. Images
are read from `img/` at the repo root by default (git-ignored except `img/.gitkeep`;
override with `IMGDIR=`).
