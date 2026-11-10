# CLAUDE.md

Builds a multi-boot SD card for the G350 (RK3326) with any 2 or 3 of dArkOS,
AmberELEC and MustardOS. Everything lives in `src/`: `flash-dualboot.sh` and its two
boot.ini templates. `README.md` is the
user-facing documentation; keep it in sync when the layout, CLI or key mapping change.

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

With all three OSes and default dArkOS, the generated boot.ini branches must stay
equivalent to the original layout: dArkOS p6/p7 (`-07`), MustardOS p8/p9 (`-09`),
Vol- AmberELEC, Vol+ MustardOS.

## Constraints that are easy to break

- **U-Boot always comes from the dArkOS image** (sectors 64–32767), even when dArkOS
  is not installed, and so does `logo.bmp`. The shared boot.ini therefore needs the
  `odroidgoa-uboot-config` header. U-Boot only reads `boot.ini` from p1.
- **AmberELEC must be p1/p2/p3.** Its initramfs is built into `KERNEL` (can't be
  edited). It mounts mmcblk1p3 as ROMs and powers off if that isn't exFAT/ext4.
  AmberELEC boots by filesystem UUID, so its partitions are copied with `dd`, never
  reformatted. Its updates overwrite `boot.ini` on p1 (users restore it from
  `boot.ini.dualboot`).
- **Without AmberELEC, p1 is a 64M FAT32 `BOOTSEL`** that only holds boot.ini and logo.bmp.
- **MBR logical partitions always start at p5**, whatever the number of primaries.
  The order is fixed: dArkOS (EASYROMS, BOOT, ROOTFS), then MustardOS (BOOT, ROOTFS, ROMS).
- **MustardOS ROMS must be the last partition**: its factory reset resizes that
  partition to 100%. The `fdisk`/`parted` lines in `reset.sh` are commented out too.
- **MustardOS finds its partitions through** `/opt/muos/device/config/storage/{boot,root,rom}/num`
  on its rootfs. The script writes the computed numbers there.
- **Never hard-code partition numbers.** Use `DRK_ROMS_N DRK_BOOT_N DRK_ROOT_N
  MUS_BOOT_N MUS_ROOT_N MUS_ROM_N` (and `AMB_*_N`). Every per-OS step is guarded with
  `if sel <os>`.
- **Root partitions are passed as `root=PARTUUID=5a3c9e71-NN`**: the disk ID
  `0x5a3c9e71` plus the partition number in two-digit hex (`pu N`). The disk ID is
  set in the sfdisk script.
- **Key mapping:** the default OS gets no key. The other OSes, in `ALL_OS` order
  (amberelec, darkos, mustardos), get Vol- (pin 65) and then Vol+ (pin 64). Both keys
  are active low, and `gpio input N` succeeds only while the key is held.

## boot.ini files

- The shared p1 boot.ini is generated (`gen_boot_ini`, `branch_<os>` functions).
  Nothing is stored in the repo; each run writes a copy to `src/boot.ini.generated`
  (gitignored).
- `src/boot.ini.darkos` and `src/boot.ini.mustardos` are templates with `@BOOT@`, `@ROOT@`
  and `@ROOT2@` (hex, two digits), filled in by `render`. They go on each OS's own
  BOOT partition; U-Boot never reads them.
- If you change boot commands, change them in the `branch_<os>` function **and** in
  the matching template.

## Image-specific values

Image file names (`AMB`, `DARK`, `MUS`) and the source partition offsets/sizes
(`*_P1_START` etc., taken from `sfdisk -d <image>`) are tied to specific image
releases. When an image is updated, re-check the offsets and the AmberELEC UUIDs
used in the verification step. Images are read from `img/` at the repo root by
default (git-ignored except `img/.gitkeep`; override with `IMGDIR=`).
