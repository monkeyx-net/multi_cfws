# CLAUDE.md

Builds a multi-boot SD card with 2 or 3 OSes for one device (`--device`). `--os` is
required and must name them; there is no "all" default:
the G350 (RK3326; dArkOS, AmberELEC, MustardOS, ROCKNIX; the default) or the RGB30 (RK3566;
dArkOS, knulli, ROCKNIX). Everything lives in `src/`: `flash-dualboot.sh` and its two
G350 boot.ini templates. Per-device values sit in one `case $DEVICE` block near the
top; the G350 and RGB30 layouts are `layout_g350` / `layout_rgb30`. `README.md` is the
user-facing documentation; keep it in sync when the layout, CLI or key mapping change.

`src/flash-dualboot.py` is a Python port (stdlib only, Python 3.8+) that is being
tested alongside the bash script: one class per OS (`AmberELEC`, `DarkOS`,
`MustardOS`, `Knulli`, `Rocknix`) holds its boot commands (`boot` dict per boot mode,
`@BOOTX@`/`@ROOT@`/`@ROOT_PU@`/`@DTB@` placeholders) and its run steps; partition numbers
are `c.n("DRK_BOOT")` etc. Until the bash script is retired, make every change in both,
and check that `--plan` output stays byte-identical between them for every combination
below (`diff <(bash src/flash-dualboot.sh ARGS) <(python3 src/flash-dualboot.py ARGS)`).

## Testing without a card

The real run needs root, the three images and the ~62.5GB card, and it erases the
card. Changes to the layout or boot.ini are tested with `--plan`, which needs no root
and writes nothing:

```sh
bash -n src/flash-dualboot.sh && shellcheck -S warning src/flash-dualboot.sh
bash src/flash-dualboot.sh --plan --os amberelec,mustardos --default mustardos /x
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

G350 changes must not alter the G350 `--plan` output (apart from the `Device:` and `U-Boot:` lines).
Save the output before a change and diff it afterwards.

With all three OSes and default dArkOS, the generated boot.ini branches must stay
equivalent to the original layout: dArkOS p6/p7 (`-07`), MustardOS p8/p9 (`-09`),
Vol- AmberELEC, Vol+ MustardOS.

## Constraints that are easy to break (G350)

- **Two boot modes (`BOOTMODE`).** Without ROCKNIX (`ini`), U-Boot comes from the
  dArkOS image (sectors 64–32767), even when dArkOS is not installed, and so does
  `logo.bmp`; the shared boot.ini needs the `odroidgoa-uboot-config` header, and U-Boot
  only reads `boot.ini` from p1. With ROCKNIX (`scr-g350`), U-Boot comes from the
  ROCKNIX image, because ROCKNIX's mainline kernel crashes on the Hardkernel U-Boot
  (tested). It runs `boot.scr` from p1 (`gen_boot_cmd_g350`, `sbranch_<os>`).
  The `ini` `--plan` output must not change when editing `scr-g350`.
- **`scr-g350` key reading (tested on a G350):** `gpio input` returns success, not the
  pin value, and the volume keys have no pull-up, so `boot.scr` sets the pull-ups
  (`mw.l 0xff140070 0x000f0005`) and reads GPIO2 EXT_PORTA (`0xff260050`, bit 0 Vol+,
  bit 1 Vol-) with `setexpr` (`/` and `%`; a bare `&` breaks hush). Vol+ is checked first.
  dArkOS, AmberELEC and ROCKNIX are tested with this U-Boot; MustardOS is not.
- **AmberELEC must be p1/p2/p3.** Its initramfs is built into `KERNEL` (can't be
  edited). It mounts mmcblk1p3 as ROMs and powers off if that isn't exFAT/ext4.
  AmberELEC boots by filesystem UUID, so its partitions are copied with `dd`, never
  reformatted. Its updates overwrite `boot.ini` on p1 (users restore it from
  `boot.ini.dualboot`).
- **Without AmberELEC, p1 is a 64M FAT32 `BOOTSEL`** that only holds boot.ini and logo.bmp.
- **MBR logical partitions always start at p5**, whatever the number of primaries.
  The order is fixed: dArkOS (EASYROMS, BOOT, ROOTFS), then ROCKNIX (BOOT, STORAGE,
  GAMES), then MustardOS (BOOT, ROOTFS, ROMS).
- **U-Boot `dev:part` numbers are hex** (both U-Boots). MustardOS can be p11–p13, so
  `load mmc 1:` uses `hx N` in the branches and `@BOOTX@` in the templates.
- **ROCKNIX on the G350 boots by filesystem UUID** (`RNX_BOOT_UUID`/`RNX_STOR_UUID`,
  in the `extlinux.conf.dualboot` the script writes on its partition; `sysboot` as in
  ROCKNIX's own boot.scr), because AmberELEC's ext4 is labelled `STORAGE` too. Its STORAGE resize flag is
  removed and `system.gamesdevice` is seeded, as on the RGB30.
- **MustardOS ROMS must be the last partition**: its factory reset resizes that
  partition to 100%. The `fdisk`/`parted` lines in `reset.sh` are commented out too.
- **MustardOS finds its partitions through** `/opt/muos/device/config/storage/{boot,root,rom}/num`
  on its rootfs. The script writes the computed numbers there.
- **Never hard-code partition numbers.** Use `DRK_ROMS_N DRK_BOOT_N DRK_ROOT_N
  MUS_BOOT_N MUS_ROOT_N MUS_ROM_N` (and `AMB_*_N`, `RNX_*_N`). Every per-OS step is guarded with
  `if sel <os>`.
- **Root partitions are passed as `root=PARTUUID=5a3c9e71-NN`**: the disk ID
  `0x5a3c9e71` plus the partition number in two-digit hex (`pu N`). The disk ID is
  set in the sfdisk script.
- **Key mapping:** the default OS gets no key. The other OSes, in `ALL_OS` order
  (amberelec, darkos, mustardos, rocknix), get Vol- (pin 65) and then Vol+ (pin 64). Both keys
  are active low, and `gpio input N` succeeds only while the key is held.

## Constraints that are easy to break (RGB30)

Tested on an RGB30 with all three OSes (with dArkOS root on p9 and a patched initramfs;
the GPT layout below then replaced that).

- **GPT, not MBR** (`layout_rgb30`, `gpart`). `pu N` gives fixed GUIDs
  `5a3c9e71-0000-4000-8000-0000000000NN`. Partitions end before the backup GPT (`END`).
- **dArkOS must be p3/p4/p5** (BOOT, ROOTFS, EASYROMS): its initramfs hard-codes
  `/dev/mmcblk1p4` and ignores `root=`; its fstab.exfat is copied unchanged.
  `uInitrd` is a plain gzip initramfs: `booti` needs `addr:${filesize}`.
- **knulli must be p1/p2**: `knulli-part` takes the share as boot partition + 1 and
  only parses one digit. Its `autoresize` in `knulli-boot.conf` is set to false.
- **U-Boot always comes from the knulli image** (mainline 2024.07, Rockchip BL31),
  even when knulli is not installed. It runs `bootflow scan` and boots `boot.scr` from
  `BOOTSEL` (`BOOTSEL_N`), the only partition with `LegacyBIOSBootable`; never put
  `extlinux.conf` on it.
- **Keys:** that U-Boot has no `gpio` or `setexpr`. `boot.scr` sets the pull-ups with
  `mw.l` (SYS_GRF `0xfdc600a0`/`0xfdc600a4`), copies GPIO3 EXT_PORT (`0xfe760070`) to
  RAM with `cp.l` and compares bytes with `itest.b` (Vol+ = byte 0 < 0x80, Vol- =
  byte 1 == 0xfe/0x7e). Keys: `ALL_OS` order darkos, knulli, rocknix.
- **U-Boot `dev:part` numbers are hex**: the `rbranch_<os>` functions use `hx N`.
  `${devtype} ${devnum}` are set by U-Boot's script bootmeth.
- **ROCKNIX needs `system.gamesdevice`** in `/storage/.config/system/configs/system.cfg`.
  Otherwise its `automount` mounts another OS's partition as its games card. The whole
  `/usr/config` is seeded first, because `chksysconfig` rsyncs it over `system.cfg`
  while `retroarch.cfg` is missing. Its STORAGE resize flag is removed (`grow_storage`).
- Partition-number variables: `KNU_BOOT_N KNU_SHARE_N RNX_BOOT_N RNX_STOR_N RNX_ROMS_N
  BOOTSEL_N` plus the shared `DRK_*_N`.

## One-shot next boot (`multiboot-next`)

- Only in the `boot.scr` modes (`scr-g350`, `scr-rgb30`); `ini` mode is unchanged until
  the Hardkernel U-Boot is shown to have `fatwrite` and `env import`.
- `src/multiboot-next` is a POSIX sh template (`@OSES@ @PARTUUID@ @PARTNUM@ @DISKID_AT@
  @DISKID_HEX@ @DEVICE@`), filled by `gen_next_helper` / `next_helper` and installed by
  `install_next` at `next_path <os>` (`NEXT_PATHS` in Python). It writes `next=<os>` to
  `nextboot.txt` on `BOOTSEL_N`.
- `boot.cmd` (`gen_next` / `next_block`): after the keys are read, it loads the file with
  `env import -t ... next`, clears it with `fatwrite` (6 bytes `next=\n`, only when it held a
  request), and the branches test `${next}` after the key branches, so a held key wins.
- Not yet tested on a device.

## boot.ini files

- The shared p1 boot.ini is generated (`gen_boot_ini`, `branch_<os>` functions).
  Nothing is stored in the repo; each run writes a copy to `src/boot.ini.generated`
  (gitignored).
- `src/boot.ini.darkos` and `src/boot.ini.mustardos` are templates with `@BOOT@`, `@BOOTX@` (hex), `@ROOT@`
  and `@ROOT2@` (hex, two digits), filled in by `render`. They go on each OS's own
  BOOT partition; U-Boot never reads them.
- If you change boot commands, change them in the `branch_<os>` function, the
  `sbranch_<os>` function (G350 with ROCKNIX), the OS class's `boot` dict in
  `flash-dualboot.py` **and** the matching template.
- Hardware debugging without a serial console: in a test boot.scr, `env export -t <addr>
  <vars>` then `fatwrite ${devtype} ${devnum}:1 <addr> log.txt ${filesize}` writes
  variables to p1 (works on the mainline U-Boots; `env export` failed on the Hardkernel
  one). Use addresses inside RAM (both devices have 1GB).

## Image-specific values

Image file names (`AMB`, `DARK`, `MUS`; RGB30: `DARK`, `KNU`, `RNX`) and the source partition offsets/sizes
(`*_P1_START` etc., taken from `sfdisk -d <image>`; for the RGB30 dArkOS image,
`DRK_P1/P2` are its p3/p4) and the RGB30 dtb paths (`*_DTB`) are tied to specific image
releases. When an image is updated, re-check the offsets and the AmberELEC UUIDs
used in the verification step. Images are read from `img/` at the repo root by
default (git-ignored except `img/.gitkeep`; override with `IMGDIR=`).
