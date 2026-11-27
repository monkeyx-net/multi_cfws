# CLAUDE.md

Builds a multi-boot SD card with 2 to 6 OSes for one device (`--device`; up to one more
than the device's keys: G350 6, RGB30 4, CubeXX 3). `--os` is
required and must name them; there is no "all" default:
the G350 (RK3326; dArkOS, AmberELEC, ArkOS, knulli, MustardOS, ROCKNIX; the default), the
RGB30 (RK3566; dArkOS, ArkOS, knulli, ROCKNIX) or the RG CubeXX (H700; knulli, MustardOS, ROCKNIX). Everything lives in `src/`: `flash-dualboot.py` (Python 3.8+,
standard library only), one JSON file per device (`devices/`), the boot command templates
(`boot/`), its three G350 boot.ini templates and the `multiboot-next` template.
`README.md` is the user-facing documentation; keep it in sync when the layout, CLI or key
mapping change.

`flash-dualboot.py` reads top to bottom in the order things happen: settings, choices,
layout, per-OS code, boot script, plan, flash. Per-device data (images, U-Boot source
and boot-mode rules, source partitions, dtbs, key pins, UUIDs, per-OS options) is in
`src/devices/<device>.json`, read by `load_device` into a `Device`; keys starting with `_`
are comments. Never test the device name in code (`device.key == ...`): use a device field
(`table`, `boot`, `os_opts` via `c.device.opt(os, name)`, `os_boot_ini`, `uuids`, ...) or add
one. A new device needs a JSON file and, as the partition order is code, a layout function
in `LAYOUTS` (`layout_g350` / `layout_rgb30` / `layout_cubexx`), named by its `layout` field.
The ROM partition sizes come from a dry run of the layout function (`Layout` with
`rom_sizes=None`), so it must place the same fixed partitions on both calls. One class per OS
(`AmberELEC`, `DarkOS`, `ArkOS`, `MustardOS`, `Knulli`, `Rocknix`) holds its run steps
(`fix_fs`, `prepare`, `verify`) and `tools`; its U-Boot commands are in
`src/boot/<os>.<bootmode>.cmd` (`@BOOTX@`/`@ROOT@`/`@ROOT_PU@`/`@DTB@` placeholders) and the
key-reading blocks in `src/boot/keys.<bootmode>.cmd`. Partition numbers are
`c.n("DRK_BOOT")` etc.

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

For the G350 with ROCKNIX, also run the combinations that include `rocknix`. Cards with
4+ OSes need a bigger card than `--plan`'s default 62.5GB: give a sparse file as the
target (`truncate -s 128G card128`; `blockdev --getsz` reads its size).

For the CubeXX, run `--device cubexx` for `knulli,mustardos`, `knulli,rocknix`,
`mustardos,rocknix` and `knulli,mustardos,rocknix` with each default (the same sfdisk
check; `knulli,mustardos` output must not change when adding ROCKNIX features). Without root, the env code can be checked against the images
(`env_read`/`env_build`); `fw_printenv -l /tmp -c <config>` reads a
built env (config line `<file> 0x0 0x20000`).

For the RGB30, do the same with `--device rgb30` for `darkos,knulli,rocknix`,
`darkos,knulli`, `darkos,rocknix` and `knulli,rocknix` with each default.

ArkOS (both devices) and knulli on the G350 add many more combinations (G350: every 2–6 of
6 OSes, 223 plans over all three devices with each default; RGB30: every 2–4 of 4). Script it: run `--plan` for each combination and default,
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
  bit 1 Vol-) with `setexpr` (`/` and `%`; a bare `&` breaks hush; `boot/keys.scr-g350.cmd`).
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
  the boot commands use `@BOOTX@` (`hx(n)`), in `src/boot/` and the templates.
- **ROCKNIX on the G350 boots by filesystem UUID** (`uuids` `RNX_BOOT`/`RNX_STOR` in
  `g350.json`, in the `extlinux.conf.dualboot` the script writes on its partition,
  `src/boot/rocknix.extlinux.conf`;
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
  (`c.key_os`), then the JSON `extra_keys` (`Device.keys`): L1 (GPIO2_A6, pin 70), R1
  (GPIO2_A7, pin 71), L1+R1 (both held; tested before single keys in `boot_branches`). All
  active low; in `ini` mode `gpio input N` succeeds only while the key is held. The extra
  keys need pull-ups (`mw.l 0xff140070 0xf0005000`, A6/A7 only) and their reading is
  `src/boot/keys.<bootmode>.extra.cmd`, added only on cards with more than 3 OSes
  (`extra_keys_block`), so 2–3 OS output is unchanged. Extra keys untested on a device.

## Constraints that are easy to break (RGB30)

Tested on an RGB30 with all three OSes (with dArkOS root on p9 and a patched initramfs;
the GPT layout below then replaced that).

- **GPT, not MBR** (`layout_rgb30`, `Layout.gpt`). `c.partuuid(n)` gives fixed GUIDs
  `5a3c9e71-0000-4000-8000-0000000000NN`. Partitions end before the backup GPT (`end`
  in `make_layout`).
- **dArkOS must be p3/p4/p5** (BOOT, ROOTFS, EASYROMS): its initramfs hard-codes
  `/dev/mmcblk1p4` and ignores `root=`; its fstab.exfat is copied unchanged.
  `uInitrd` is a plain gzip initramfs: `booti` needs `addr:${filesize}`.
  Its bootargs use `loglevel=0`, not its `extlinux.conf`'s `loglevel=5`: kernel
  warnings on tty1 showed instead of its splash. dArkOS has no graphical splash: its
  Rockchip U-Boot draws `logo.bmp` and hands it to the vendor DRM, then
  `welcome-message.service` (`boot_text.sh`) prints "Welcome to dArkOS" on tty1.
  knulli's U-Boot draws no logo (not fixable here), so the screen stays blank until
  that text. Its `firstboot.sh` only runs `expandtoexfat.sh` (nothing for
  console or splash).
- **knulli must be p1/p2**: `knulli-part` takes the share as boot partition + 1 and
  only parses one digit. Its `autoresize` in `knulli-boot.conf` is set to false.
- **U-Boot always comes from the knulli image** (mainline 2024.07, Rockchip BL31),
  even when knulli is not installed. It runs `bootflow scan` and boots `boot.scr` from
  `BOOTSEL` (`c.n("BOOTSEL")`), the only partition with `LegacyBIOSBootable`; never put
  `extlinux.conf` on it.
- **Keys:** that U-Boot has no `gpio` or `setexpr`. `boot.scr` (`boot/keys.scr-rgb30.cmd`) sets the
  pull-ups with `mw.l` (SYS_GRF `0xfdc600a0`/`0xfdc600a4`), copies GPIO3 EXT_PORT
  (`0xfe760070`) to RAM with `cp.l` and compares bytes with `itest.b` (Vol+ = byte 0 <
  0x80, Vol- = byte 1 == 0xfe/0x7e). Keys: `all_os` order arkos, darkos, knulli, rocknix.
  A 4th OS gets L1 (GPIO3_B1, byte 1 == 0xfd/0x7d; `keys.scr-rgb30.extra.cmd`, untested).
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

## Constraints that are easy to break (CubeXX)

- **Vendor U-Boot (`env-h700`), from the knulli image** (sectors 512–73727: boot0 at 512,
  the boot package at 32800). After DRAM training the firmware writes boot0 to sector 16,
  which destroyed a standard GPT's entry array (sectors 2–33; nothing booted), so the GPT
  has 16 entries (`gpt_entries`, sfdisk `table-length: 16`, sectors 2–5; at most 56 end
  before sector 16; MustardOS's image has 8). The boot ROM tries sector 16 before 512, so
  `flash` zeroes from the end of the table (`table_end`) to U-Boot: a stale ROCKNIX SPL
  there gave a black screen (tested). Tested with either default: the GPT survives, the
  env is read, both OSes boot (`boot_knulli`, `boot`). It finds partitions
  by **GPT name** and replaces `bootcmd` with `run setargs_mmc boot_normal` itself, so the
  multi-boot is a new `boot_normal` (+ `mb_keys`, `mb_<os>`) in the shared `env`
  partition: the uboot_os image's env (`env_src`, `env_size` = 0x20000, CRC32, no
  redundant copy) with the generated variables added (`env_set_vars`). No boot script,
  no `multiboot-next` (`c.scr` is false). Templates are `src/boot/<os>.env-h700.cmd`,
  joined into one line with `; ` (`env_line`).
- **Simple command parser, no hush** (tested: `if`/`then`/`fi` are unknown commands and
  skipped; a failing command doesn't stop the `;` list). No conditionals: `mb_keys` leaves
  `mb_up`/`mb_dn` (1 = released) and `boot_normal` runs `mb_sel${mb_up}${mb_dn}`, then
  the default OS as a fallback. Keys: PE1 = Vol+, PE2 = Vol-, active low; `keys.env-h700.cmd`
  sets PE_CFG0/PE_PULL0 with `setexpr` arithmetic (`/ * + %` only). Tested: no key and Vol- both pick the right OS.
- **`fatload`/`fatwrite mmc 0:N` don't work** (tested: no file written); `sunxi_flash
  read <addr> <partname>` does. Data U-Boot must read goes in a raw, named partition.
- **Partitions the ramdisks hard-code**: knulli `/dev/mmcblk0p3` (KNULLI) with SHARE =
  p4; MustardOS `/dev/mmcblk0p5` (ROOTFS). MustardOS ROMS last. `layout_cubexx` checks this;
  without knulli, ROCKNIX's FAT/STORAGE fill p3/p4, so MustardOS ROOTFS stays p5. U-Boot
  and env always come from the knulli image.
- **Android boot images** (`KNU_ABOOT`, `MUS_ABOOT`, the extra entries in each OS's
  `copied`): the default OS's is named `boot`, the other `boot_<os>` (`aboot_name`).
  The default OS's FAT partition is named `boot-resource` (U-Boot's `bootlogo.bmp`); with
  ROCKNIX as default, knulli's (else MustardOS's), as ROCKNIX's FAT has no bootlogo.
- **knulli and MustardOS use knulli's U-Boot dtb.** `bootm` always passes U-Boot's control
  dtb (`fdt addr` to another dtb is ignored, tested; `fdt addr -c` would skip the startup
  fixups). MustardOS's `muinput` needs the input device name `muOS-Input-Source`, so its
  branch renames `/soc@03000000/gpio_keys` with `fdt set` (no `fdt addr`, no resize;
  tested: controls work). Fallback if that ever fails: `MUOS_INPUT_SOURCE_NAME` on
  muinput's line in MustardOS's `module.sh`.
- **ROCKNIX (tested on Vol+ of a 3-OS card, default knulli: interface, controls, sound,
  Wi-Fi, reboot, power-off; untested: suspend, default/Vol- ROCKNIX, 2-OS cards)**: its mainline U-Boot can't start Android boot images,
  so the vendor U-Boot starts its mainline kernel (`os_opts.rocknix`, `Rocknix.write_vendor_boot`):
  - `KERNEL` (arm64 Image, text_offset 0, initramfs built in) goes in an Android v0 image
    built by `android_boot_image` (same bytes as mkbootimg; kernel_addr 0x40200000,
    2 MB-aligned; no ramdisk; empty cmdline: the vendor bootm appends the header cmdline
    to `bootargs`, which the branch sets). The vendor bootm takes at most 32 MB of kernel
    (`max_kernel`, CONFIG_SYS_BOOTM_LEN); the 20261101 KERNEL is 32135176 bytes.
  - Its CubeXX dtb goes raw in `dtb_rocknix` (`RNX_DTB`, 1M, read whole to 0x43000000,
    below the 0x44000000 bootm copies the dtb to), fixed by `fix_dtb` (fdtput): `/memory`
    added, the vendor BL31 (0x48000000, 16M, knulli package 'monitor') reserved no-map, and
    vdd-dram's min/max removed and the regulator renamed (vendor boot0 set the DRAM voltage;
    ROCKNIX's update.sh writes its own U-Boot at sector 16 when it finds vdd-dram at
    1.1/1.2 V). The DDR3 overlay is not applied.
  - `mb_rocknix` reads both, sets bootargs (boot by LABEL: no clash with KNULLI, SHARE,
    BOOT, ROOTFS), then `fdt addr` + `fdt addr -c` to ROCKNIX's dtb (bootm writes initrd
    props to working_fdt, then passes gd->fdt_blob, copied gd->fdt_size bytes), `bootm`,
    and if that returns, both back to `${fdtcontroladdr}` for the fallback OS. Tested: the
    kernel boots (LED heartbeat via fdt set on led-1) and `fdt addr -c` + bootm works.
  - **`fdt resize 1000`** after `fdt addr 43000000`: without free space in the dtb, bootm's
    fixups fail and this U-Boot stops ("FDT creation failed! hanging...", tested twice:
    the kernel never ran).
  - **Display engine reset** (`mw.l 0300160c 1`, CCU DE_BGR_REG reset low, before bootm):
    the vendor U-Boot leaves DE33 running its logo setup, which overrides the mainline
    driver (DRM reported the panel active, backlight on, Sway running, screen still showed
    the logo; tested). With the reset ROCKNIX's interface appears. Released again
    (`10001`) if bootm returns.
  - `updates.enabled=0` (updates replace KERNEL/SYSTEM but not the boot image, and
    update.sh writes ROCKNIX's U-Boot). `install_next` only with `c.scr`. ROCKNIX's
    initramfs enters USB recovery if Vol- is still held.

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
- If you change boot commands, change them in `src/boot/<os>.<bootmode>.cmd` (every boot
  mode it has) **and** in the matching template.
- Hardware debugging without a serial console: in a test boot.scr, `env export -t <addr>
  <vars>` then `fatwrite ${devtype} ${devnum}:1 <addr> log.txt ${filesize}` writes
  variables to p1 (works on the mainline U-Boots; `env export` failed on the Hardkernel
  one). Use addresses inside RAM (both devices have 1GB).

## Image-specific values

Image file names (`images`) may contain `*`: `resolve_image` uses the newest matching
file in `img/` (by mtime); a plain name pins one file. `--plan` prints the chosen files
(`Images:`). The source partitions (`src`, `["pN", start, size, note]`) name partition N
of the image; `load_src` reads start/size from the image's MBR/GPT (`image_partitions`)
when the image exists and uses those, printing a `NOTE:` in `--plan` when they differ
from the JSON values (the fallback when the image is missing; for the RGB30 dArkOS image
they are its p3/p4). So a new release usually needs no JSON change. Still tied to a
release: the dtb paths (`dtb`), `env_src` (CubeXX), `os_opts` values and the filesystem
UUIDs
(`uuids`: `AMB_*`, `RNX_*`) used in the boot commands and the verification step. Images
are read from `img/` at the repo root by default (git-ignored except `img/.gitkeep`;
override with `IMGDIR=`).
