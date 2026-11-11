#!/usr/bin/env python3
"""Build a multi-boot SD card for the G350 (RK3326) or the RGB30 (RK3566).

Python version of flash-dualboot.sh: same options, same card, same --plan output.
Standard library only. The file reads top to bottom in the order things happen:

  1. settings      units, sizes, per-device images and source partitions
  2. choices       command line -> which OSes, default OS, keys, boot mode
  3. layout        partition table (one Part per partition, no hidden state)
  4. per-OS code   one class per OS: its boot commands and every step it needs
  5. boot script   the shared boot.ini / boot.cmd that picks an OS by key
  6. plan          the --plan report
  7. flash         the real run, step by step
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field

USAGE = """\
Build a multi-boot SD card for the G350 (RK3326) or the RGB30 (RK3566).
Usage: sudo python3 src/flash-dualboot.py [--device g350|rgb30] --os LIST [--default OS] [--plan] [/dev/sdX]
  --device   g350 (default): amberelec, darkos, mustardos, rocknix;  rgb30: darkos, knulli, rocknix
             Always needed (U-Boot source, even if not in --os): g350 the dArkOS image
             (the ROCKNIX image instead when rocknix is selected), rgb30 the knulli image
  --os       required: comma list naming 2 or 3 of the device's OSes
  --default  OS that boots with no key held (default: darkos if selected, else the first --os entry)
  --plan     print the partition layout, key mapping and boot script, then exit (no root, no writes)
The target defaults to /dev/sdb. ERASES THE WHOLE TARGET DEVICE.
"""

HERE = os.path.dirname(os.path.abspath(__file__))
IMGDIR = os.environ.get("IMGDIR") or os.path.dirname(HERE) + "/img"   # sudo IMGDIR=/path ...


# ============================================================================ 1. settings
S = 512                          # bytes per sector; all sizes below are in sectors
MIB = 2048
GIB = 1024 * MIB
P1_START = 32768                 # first partition; U-Boot sits in sectors 64-32767
GAP = 2048                       # MBR: gap before each logical partition
PLAN_TOTAL = 122070312           # --plan without a readable card: ~62.5GB
DISKID = "5a3c9e71"              # MBR disk ID; root=PARTUUID=5a3c9e71-NN

AMB_BOOT_SIZE = 2 * GIB          # partition sizes on the card (AmberELEC's are grown)
AMB_STOR_SIZE = 8 * GIB
RNX_STOR_SIZE = 8 * GIB
BOOTSEL_SIZE = 64 * MIB
DRK_BOOT_SIZE = 100 * MIB
DRK_ROOT_SIZE = 12 * GIB
# MustardOS BOOT/ROOTFS, knulli KNULLI and ROCKNIX's FAT partition keep their image size.

# Filesystem UUIDs inside the images (checked after flashing; ROCKNIX boots by them).
AMB_BOOT_UUID = "1910-5621"
AMB_STOR_UUID = "7ab6cd9f-77f8-4d13-905e-aa8fc5baa800"
RNX_BOOT_UUID = "0111-5938"                              # G350 image
RNX_STOR_UUID = "b4434352-b41f-4bef-a8d9-d81a710453da"   # G350 image

KEY_NAMES = ("Vol-", "Vol+")     # keys are handed out in this order
KEY_VARS = ("volminus", "volplus")


@dataclass
class Src:
    """A partition inside an image file (start and size in sectors, from sfdisk -d)."""
    start: int
    size: int


@dataclass
class Device:
    key: str                     # g350 / rgb30
    name: str                    # G350 / RGB30
    all_os: tuple                # fixed order: also the order keys are handed out
    key_pins: tuple              # Vol-, Vol+ GPIO numbers
    images: dict                 # os -> image file
    uboot_os: str                # U-Boot comes from this OS's image (see Choices.bootmode)
    src: dict                    # os -> partitions copied from its image, in order
    dtb: dict = field(default_factory=dict)


# Image names, offsets and dtb paths are tied to specific image releases: re-check
# them (sfdisk -d <image>) and the UUIDs above when an image is updated.
def g350(imgdir: str) -> Device:
    return Device(
        key="g350", name="G350",
        all_os=("amberelec", "darkos", "mustardos", "rocknix"),
        key_pins=(65, 64),                       # Vol-, Vol+ (GPIO2_A1, GPIO2_A0)
        images={
            "amberelec": f"{imgdir}/AmberELEC-RG351MP.aarch64-prerelease-20261019_1835.img",
            "darkos": f"{imgdir}/dArkOS_G350_trixie_01012026.img",  # U-Boot + logo.bmp
            "mustardos": f"{imgdir}/MustardOS_RK-G350-V_2606.0_ANDROMEDA-db2a3bd0.img",
            "rocknix": f"{imgdir}/ROCKNIX-RK3326.aarch64-20261101-b.img",
        },
        uboot_os="darkos",
        src={
            "amberelec": (Src(32768, 4194304),     # FAT AMBRELC 2G, UUID 1910-5621
                          Src(4227072, 65536)),    # ext4 STORAGE 32M, UUID 7ab6cd9f-...
            "darkos": (Src(32768, 204800),         # FAT BOOT 100M
                       Src(237568, 15360000)),     # btrfs ROOTFS 7.3G
            "mustardos": (Src(32768, 1048576),     # FAT BOOT 512M (GPT)
                          Src(1081344, 16777216)), # ext4 ROOTFS 8G
            "rocknix": (Src(32768, 4194304),       # FAT ROCKNIX 2G, UUID 0111-5938
                        Src(4227072, 65536)),      # ext4 STORAGE 32M, UUID b4434352-...
        },
        dtb={"rocknix": "rk3326-batlexp-g350.dtb"},   # from extlinux/extlinux.conf.g350
    )


def rgb30(imgdir: str) -> Device:
    return Device(
        key="rgb30", name="RGB30",
        all_os=("darkos", "knulli", "rocknix"),
        key_pins=(104, 103),                     # Vol-, Vol+ (GPIO3_B0, GPIO3_A7)
        images={
            "darkos": f"{imgdir}/dArkOS_RGB30_trixie_08282026.img",
            "knulli": f"{imgdir}/knulli-rk3568-powkiddy-rgb30-scarab-20260510.img",  # U-Boot
            "rocknix": f"{imgdir}/ROCKNIX-RK3566.aarch64-20261101-Generic.img",
        },
        uboot_os="knulli",
        src={
            "darkos": (Src(32768, 202752),         # image p3: FAT dArkOS_Fat 99M (GPT)
                       Src(237568, 15208047)),     # image p4: btrfs ROOTFS 7.3G
            "knulli": (Src(32768, 8388608),),      # FAT KNULLI 4G (GPT); SHARE is recreated
            "rocknix": (Src(32768, 4194304),       # FAT ROCKNIX 2G (GPT)
                        Src(4227072, 65536)),      # ext4 STORAGE 32M
        },
        dtb={  # from each OS's extlinux.conf
            "darkos": "rk3566-rgb30.dtb",
            "knulli": "boot/rk3566-rgb30-linux.dtb",   # (RGB30 v2 panel: boot/rk3566-rgb30-v2-linux.dtb)
            "rocknix": "device_trees/rk3566-powkiddy-rgb30.dtb",
        },
    )


DEVICES = {"g350": g350, "rgb30": rgb30}


# ---------------------------------------------------------------------------- helpers
def die(msg: str):
    sys.stderr.write(f"\033[1;31mERROR: {msg}\033[0m\n")
    sys.exit(1)


def log(msg: str):
    print(f"\n\033[1;32m==> {msg}\033[0m")


def hx(n: int) -> str:
    """U-Boot parses the part of "dev:part" as hex."""
    return format(n, "x")


def hsize(sectors: int) -> str:
    if sectors >= 2 * 1024 * 1024:
        return f"{sectors / 2097152:.1f}G"
    return f"{sectors // 2048}M"


def fill(text: str, **values) -> str:
    """Replace @NAME@ placeholders; every placeholder in text must be given."""
    missing = set(re.findall(r"@([A-Z][A-Z0-9_]*)@", text)) - set(values)
    if missing:
        die(f"internal: no value for {', '.join(sorted(missing))}")
    for name, value in values.items():
        text = text.replace(f"@{name}@", str(value))
    return text


def run(cmd: list, check: bool = True, **kw) -> subprocess.CompletedProcess:
    """Run a command, its output going to the terminal unless redirected in kw."""
    sys.stdout.flush()
    try:
        r = subprocess.run(cmd, **kw)
    except FileNotFoundError:
        if check:
            die(f"missing tool: {cmd[0]}")
        return subprocess.CompletedProcess(cmd, 127, "", "")
    if check and r.returncode != 0:
        die(f"command failed (exit {r.returncode}): {' '.join(map(str, cmd))}")
    return r


def output(cmd: list, check: bool = True) -> str:
    return run(cmd, check, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True).stdout


def need_file(path: str):
    if not os.path.isfile(path):
        die(f"missing {path} (image layout changed?)")


def rm_f(*paths: str):
    for p in paths:
        if os.path.lexists(p):
            os.remove(p)


def grep_n(path: str, regex: str) -> list:
    """Lines of a file matching regex, as grep -n prints them."""
    with open(path, errors="surrogateescape") as f:
        return [f"{i}:{line.rstrip(chr(10))}" for i, line in enumerate(f, 1) if re.search(regex, line)]


# ============================================================================ 2. choices
def parse_args(argv: list) -> dict:
    opts = {"device": "g350", "os": "", "default": "", "plan": False, "dev": "/dev/sdb"}
    args = list(argv)
    while args:
        a = args.pop(0)
        name, eq, value = a.partition("=")
        if name in ("--device", "--os", "--default") and eq:
            opts[name[2:]] = value
        elif a in ("--device", "--os", "--default"):
            if not args:
                die(f"{a} needs a value")
            opts[a[2:]] = args.pop(0)
        elif a == "--plan":
            opts["plan"] = True
        elif a in ("-h", "--help"):
            print(USAGE, end="")
            sys.exit(0)
        elif a.startswith("-"):
            die(f"unknown option: {a} (see --help)")
        else:
            opts["dev"] = a
    return opts


class Choices:
    """What the command line asks for, checked: device, OSes, default OS, keys, boot mode."""

    def __init__(self, opts: dict):
        self.plan = opts["plan"]
        self.dev = opts["dev"]                       # target block device
        key = opts["device"].lower()
        if key not in DEVICES:
            die(f"unknown --device '{key}' (choose from: g350 rgb30)")
        d = self.device = DEVICES[key](IMGDIR)
        all_os = " ".join(d.all_os)
        if not opts["os"]:
            die(f"--os is required: name 2 or 3 of: {all_os} (see --help)")

        self.user_os = []                            # in the order given, deduplicated
        for o in opts["os"].lower().split("\n")[0].split(","):
            o = re.sub(r"[ \t\n\v\f\r]", "", o)
            if not o:
                continue
            if o not in d.all_os:
                die(f"unknown OS '{o}' for {d.key} (choose from: {all_os})")
            if o not in self.user_os:
                self.user_os.append(o)
        max_os = len(d.key_pins) + 1
        if len(self.user_os) < 2:
            die(f"--os needs at least 2 of: {all_os}")
        if len(self.user_os) > max_os:
            die(f"--os takes at most {max_os} OSes (no key, Vol-, Vol+)")

        self.default = opts["default"].lower()
        if not self.default:
            self.default = "darkos" if self.sel("darkos") else self.user_os[0]
        if not self.sel(self.default):
            die(f"--default {self.default} is not one of the selected OSes ({' '.join(self.user_os)})")

        # Keys: the default OS gets no key; the others, in all_os order, get Vol- then Vol+.
        self.key_os = [o for o in d.all_os if self.sel(o) and o != self.default]

        # Boot mode (which U-Boot runs, and what it reads from p1):
        #   ini       G350: dArkOS's Hardkernel U-Boot 2017.09 runs boot.ini.
        #   scr-g350  G350 with ROCKNIX: ROCKNIX's mainline U-Boot 2025.10 runs boot.scr.
        #             (The Hardkernel U-Boot crashes ROCKNIX's mainline kernel.)
        #   scr-rgb30 RGB30: knulli's mainline U-Boot runs boot.scr.
        self.uboot_os = d.uboot_os
        if d.key == "rgb30":
            self.bootmode = "scr-rgb30"
        elif self.sel("rocknix"):
            self.bootmode, self.uboot_os = "scr-g350", "rocknix"
        else:
            self.bootmode = "ini"
        self.uboot_img = d.images[self.uboot_os]

        self.total = 0          # card size in sectors (set by check_plan / preflight)
        self.lay: Layout        # set by make_layout
        self.mnt = ""           # mount directory during the real run

    def sel(self, os_key: str) -> bool:
        return os_key in self.user_os

    @property
    def oses(self) -> list:
        """The selected OS objects, in the order the flashing steps handle them."""
        return [o for o in ALL_OSES if self.sel(o.key)]

    def images(self) -> list:
        """Image files the run reads: the U-Boot source first."""
        imgs = [self.uboot_img]
        order = ("darkos", "amberelec", "mustardos", "rocknix", "knulli")
        imgs += [self.device.images[o] for o in order
                 if self.sel(o) and self.device.images[o] not in imgs]
        return imgs

    # Partition numbers and device paths
    def n(self, role: str) -> int:
        return self.lay.n[role]

    def path(self, num: int) -> str:
        """/dev/sdb + N, or /dev/mmcblk0 + pN."""
        if any(s in self.dev for s in ("mmcblk", "nvme", "loop")):
            return f"{self.dev}p{num}"
        return f"{self.dev}{num}"

    def part(self, role: str) -> str:
        return self.path(self.n(role))

    def partuuid(self, num: int) -> str:
        """MBR: disk ID + N in hex. GPT (RGB30): a fixed GUID ending in N."""
        if self.device.key == "rgb30":
            return f"{DISKID}-0000-4000-8000-{num:012x}"
        return f"{DISKID}-{num:02x}"


# ============================================================================ 3. layout
@dataclass
class Part:
    num: int
    shown: str                   # size as shown in the layout listing
    desc: str
    rom: bool = False            # exFAT ROM partition (wiped before mkfs)
    extended: bool = False

    def line(self) -> str:
        return f"p{str(self.num):<3} {self.shown:>7}  {self.desc}"


class Layout:
    """Builds the sfdisk script one partition at a time, keeping its own position."""

    def __init__(self, c: Choices, header: str, first_logical: int, gap: int, rom_sizes: list):
        self.c = c
        self.sfdisk = header
        self.parts: list = []    # in listing order (MBR extended partition included)
        self.n: dict = {}        # role -> partition number, e.g. n["DRK_BOOT"]
        self.pos = P1_START      # next free sector
        self.next_num = first_logical
        self.gap = gap
        self.rom_sizes = rom_sizes

    def rom_size(self) -> int:
        """Size of the next ROM partition; the last one takes the remainder."""
        return self.rom_sizes.pop(0)

    def _add(self, sfd: str, part: Part, role: str, size: int):
        self.sfdisk += sfd + "\n"
        self.parts.append(part)
        if role:
            self.n[role] = part.num
        self.pos += size

    def primary(self, num, size, typ, desc, role="", rom=False, bootable=False):
        sfd = f"start={self.pos}, size={size}, type={typ}" + (", bootable" if bootable else "")
        self._add(sfd, Part(num, hsize(size), desc, rom), role, size)

    def extended(self, num):
        self._add(f"start={self.pos}, type=5", Part(num, "", "extended", extended=True), "", 0)

    def logical(self, size, typ, desc, role="", rom=False):
        """MBR logical partition; size None fills the rest of the card."""
        self.pos += self.gap
        if size is None:
            size = self.c.total - self.pos
            sfd, shown = f"start={self.pos}, type={typ}", "~" + hsize(size)
        else:
            sfd, shown = f"start={self.pos}, size={size}, type={typ}", hsize(size)
        num, self.next_num = self.next_num, self.next_num + 1
        self._add(sfd, Part(num, shown, desc, rom), role, size)

    def gpt(self, size, typ, name, desc, role="", rom=False, bootable=False):
        num, self.next_num = self.next_num, self.next_num + 1
        sfd = (f'start={self.pos}, size={size}, type={typ}, uuid={self.c.partuuid(num)}, name="{name}"'
               + (', attrs="LegacyBIOSBootable"' if bootable else ""))
        self._add(sfd, Part(num, hsize(size), desc, rom), role, size)

    def numbered(self) -> list:
        """Every partition that must exist afterwards (not the extended one)."""
        return [p.num for p in self.parts if not p.extended]


T_DATA = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"    # GPT "Microsoft basic data" (FAT, exFAT)
T_LINUX = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"   # GPT Linux filesystem


def make_layout(c: Choices) -> Layout:
    d = c.device
    src = d.src
    # Space left for ROM partitions is split equally between the selected OSes.
    fixed, nlog = P1_START, 0
    if d.key == "g350" and c.sel("amberelec"):
        fixed += AMB_BOOT_SIZE + AMB_STOR_SIZE
    else:
        fixed += BOOTSEL_SIZE
    if c.sel("darkos"):
        fixed += DRK_BOOT_SIZE + DRK_ROOT_SIZE
        nlog += 3
    if c.sel("mustardos"):
        fixed += src["mustardos"][0].size + src["mustardos"][1].size
        nlog += 3
    if c.sel("knulli"):
        fixed += src["knulli"][0].size
        nlog += 2
    if c.sel("rocknix"):
        fixed += src["rocknix"][0].size + RNX_STOR_SIZE
        nlog += 3
    if d.key == "g350":
        gap, end = GAP, c.total
    else:
        gap = 0                                    # GPT: no extended partition, no gaps
        end = (c.total - 34) // MIB * MIB          # keep the last 33 sectors for the backup GPT
    free = end - fixed - nlog * gap
    nrom = len(c.user_os)
    rom = free // nrom // MIB * MIB
    last = (free - (nrom - 1) * rom) // MIB * MIB
    if not rom > 2 * GIB:
        die(f"card too small ({c.total} sectors)")
    rom_sizes = [rom] * (nrom - 1) + [last]

    if d.key == "g350":
        lay = Layout(c, f"label: dos\nlabel-id: 0x{DISKID}\nunit: sectors\n\n", 5, gap, rom_sizes)
        layout_g350(c, lay)
    else:
        lay = Layout(c, f"label: gpt\nlabel-id: {c.partuuid(0)}\nunit: sectors\n\n", 1, gap, rom_sizes)
        layout_rgb30(c, lay)
    if lay.pos > end:
        die(f"layout overflows the card ({lay.pos} > {end} sectors)")
    return lay


def layout_g350(c: Choices, lay: Layout):
    """MBR. Logical partitions always start at p5, in the order dArkOS, ROCKNIX, MustardOS."""
    src = c.device.src
    lay.n["BOOTSEL"] = 1                     # U-Boot reads boot.ini / boot.scr from p1
    bootfile = "boot.ini" if c.bootmode == "ini" else "boot.scr"
    if c.sel("amberelec"):
        # p3 must be AmberELEC's games partition: its initramfs (inside KERNEL) mounts the
        # first of mmcblk1p3/mmcblk1p1/mmcblk1/mmcblk0p3 at /storage/roms. U-Boot reads
        # boot.ini from p1, so AmberELEC's FAT partition stays p1.
        lay.primary(1, AMB_BOOT_SIZE, "c", f"FAT32 AMBRELC: AmberELEC KERNEL/SYSTEM + shared {bootfile}",
                    "AMB_BOOT", bootable=True)
        lay.primary(2, AMB_STOR_SIZE, "83", "ext4 STORAGE: AmberELEC /storage", "AMB_STOR")
        lay.primary(3, lay.rom_size(), "7", "exFAT GAMES: AmberELEC roms", "AMB_ROMS", rom=True)
        lay.extended(4)
    else:
        # No AmberELEC: a small FAT partition only holds the shared boot.ini and logo.bmp
        # (or boot.scr with the ROCKNIX U-Boot).
        what = "shared boot.ini + logo.bmp" if c.bootmode == "ini" else "shared boot.scr"
        lay.primary(1, BOOTSEL_SIZE, "c", f"FAT32 BOOTSEL: {what}", bootable=True)
        lay.extended(2)
    if c.sel("darkos"):
        # dArkOS mounts LABEL=EASYROMS, so its roms can live in a logical partition.
        lay.logical(lay.rom_size(), "7", "exFAT EASYROMS: dArkOS roms", "DRK_ROMS", rom=True)
        lay.logical(DRK_BOOT_SIZE, "c", "FAT BOOT: dArkOS /boot", "DRK_BOOT")
        lay.logical(DRK_ROOT_SIZE, "83", "btrfs ROOTFS: dArkOS root", "DRK_ROOT")
    if c.sel("rocknix"):
        # ROCKNIX boots by filesystem UUID (set in boot.scr) and finds its games
        # partition through system.gamesdevice (set in Rocknix.prepare).
        lay.logical(src["rocknix"][0].size, "c", "FAT ROCKNIX: ROCKNIX KERNEL/SYSTEM", "RNX_BOOT")
        lay.logical(RNX_STOR_SIZE, "83", "ext4 STORAGE: ROCKNIX /storage", "RNX_STOR")
        lay.logical(lay.rom_size(), "7", "exFAT GAMES: ROCKNIX roms", "RNX_ROMS", rom=True)
    if c.sel("mustardos"):
        lay.logical(src["mustardos"][0].size, "c", "FAT BOOT: MustardOS kernel + dtbs", "MUS_BOOT")
        lay.logical(src["mustardos"][1].size, "83", "ext4 ROOTFS: MustardOS root", "MUS_ROOT")
        # MustardOS ROMS MUST be the last partition: its factory reset resizes its rom
        # partition to 100% (patched out in MustardOS.prepare, but harmless if it runs).
        lay.logical(None, "7", "exFAT ROMS: MustardOS roms (last)", "MUS_ROM", rom=True)


def layout_rgb30(c: Choices, lay: Layout):
    """GPT. dArkOS's initramfs ignores root= and always mounts /dev/mmcblk1p4, and its
    scripts expect BOOT on p3 and roms on p5, so dArkOS gets p3/p4/p5 as in its image.
    knulli takes "its boot partition + 1" as /userdata (one digit only): p1/p2.
    U-Boot (knulli's) runs boot.scr from BOOTSEL, the only partition marked bootable."""
    src = c.device.src

    def bootsel():
        lay.gpt(BOOTSEL_SIZE, T_DATA, "BOOTSEL", "FAT32 BOOTSEL: shared boot.scr (bootable)",
                "BOOTSEL", bootable=True)

    def knulli():
        lay.gpt(src["knulli"][0].size, T_DATA, "KNULLI", "FAT KNULLI: knulli boot + squashfs", "KNU_BOOT")
        lay.gpt(lay.rom_size(), T_DATA, "SHARE", "exFAT SHARE: knulli /userdata (roms)", "KNU_SHARE", rom=True)

    def darkos():
        lay.gpt(DRK_BOOT_SIZE, T_DATA, "BOOT", "FAT BOOT: dArkOS /boot", "DRK_BOOT")
        lay.gpt(DRK_ROOT_SIZE, T_LINUX, "ROOTFS", "btrfs ROOTFS: dArkOS root", "DRK_ROOT")
        lay.gpt(lay.rom_size(), T_DATA, "EASYROMS", "exFAT EASYROMS: dArkOS roms", "DRK_ROMS", rom=True)

    # ROCKNIX finds KERNEL/SYSTEM and /storage by label (boot=LABEL=ROCKNIX disk=LABEL=STORAGE)
    # and its games partition through system.gamesdevice (set in Rocknix.prepare).
    def rocknix_sys():
        lay.gpt(src["rocknix"][0].size, T_DATA, "ROCKNIX", "FAT ROCKNIX: ROCKNIX KERNEL/SYSTEM", "RNX_BOOT")
        lay.gpt(RNX_STOR_SIZE, T_LINUX, "STORAGE", "ext4 STORAGE: ROCKNIX /storage", "RNX_STOR")

    def rocknix_games():
        lay.gpt(lay.rom_size(), T_DATA, "GAMES", "exFAT GAMES: ROCKNIX roms", "RNX_ROMS", rom=True)

    if c.sel("darkos") and c.sel("knulli"):
        knulli(); darkos(); bootsel()
        if c.sel("rocknix"):
            rocknix_sys(); rocknix_games()
    elif c.sel("darkos"):                      # dArkOS + ROCKNIX
        bootsel(); rocknix_games(); darkos(); rocknix_sys()
    else:                                      # knulli + ROCKNIX
        knulli(); bootsel(); rocknix_sys(); rocknix_games()

    n = lay.n
    if c.sel("darkos") and (n["DRK_BOOT"], n["DRK_ROOT"], n["DRK_ROMS"]) != (3, 4, 5):
        die("internal: dArkOS must be p3/p4/p5")
    if c.sel("knulli") and (n["KNU_BOOT"], n["KNU_SHARE"]) != (1, 2):
        die("internal: knulli must be p1/p2")


# ============================================================================ 4. per-OS code
# Each OS class holds everything about that OS:
#   prefix   partition roles are <prefix>_BOOT, <prefix>_ROOT, ...
#   copied   roles that receive the image's partitions (Device.src), in order
#   roms     (role, exFAT label, what) of its ROM partition
#   boot     U-Boot commands per boot mode, as they appear in the shared boot script
#            (@BOOTX@ = BOOT number in hex, @ROOT@ / @ROOT_PU@ = root number / PARTUUID).
#            If you change them, change src/boot.ini.<os> too where it exists.
#   fix_fs / prepare / verify   steps of the real run (copying and mkfs are generic)
class OS:
    key = name = prefix = ""
    copied: tuple = ()
    roms: tuple = ()
    boot: dict = {}

    def branch(self, c: Choices) -> str:
        return fill(self.boot[c.bootmode], **self.boot_values(c))

    def boot_values(self, c: Choices) -> dict:
        v = {"BOOTX": hx(c.n(f"{self.prefix}_BOOT")), "DTB": c.device.dtb.get(self.key, "")}
        root = c.lay.n.get(f"{self.prefix}_ROOT")
        if root:
            v.update(ROOT=root, ROOT_PU=c.partuuid(root))
        return v

    def copy(self, c: Choices):
        image = c.device.images[self.key]
        for src, role in zip(c.device.src[self.key], self.copied):
            target = c.part(role)
            log(f"dd {os.path.basename(image)} [{src.start} +{src.size}] -> {target}")
            run(["dd", f"if={image}", f"of={target}", "bs=4M", "iflag=skip_bytes,count_bytes",
                 f"skip={src.start * S}", f"count={src.size * S}", "conv=fsync", "status=progress"])

    def format(self, c: Choices):
        role, label, what = self.roms
        log(f"Creating exFAT {label} ({what}) on {c.part(role)}")
        run(["mkfs.exfat", "-F", "-c", "16K", "-L", label, c.part(role)])

    def fix_fs(self, c: Choices):
        pass

    def prepare(self, c: Choices):
        pass

    def verify(self, c: Choices):
        pass


class AmberELEC(OS):
    """Must be p1/p2/p3: its initramfs is inside KERNEL and mounts mmcblk1p3 as ROMs.
    Boots by filesystem UUID, so its partitions are copied, never reformatted."""
    key, name, prefix = "amberelec", "AmberELEC", "AMB"
    copied = ("AMB_BOOT", "AMB_STOR")
    roms = ("AMB_ROMS", "GAMES", "AmberELEC roms")   # AmberELEC creates its folders via tmpfiles.d
    boot = {
        "ini": """\
  # AmberELEC
  setenv fdt_addr_r "0x01f00000"
  setenv loadaddr "0x00200000"
  setenv scriptaddr "0x00500000"
  setenv kernel_addr_r "0x02008000"
  sysboot mmc 1:@BOOTX@ any ${scriptaddr} /extlinux/rk3326-rg351mp-linux.dtb.conf
""",
        "scr-g350": """\
  # AmberELEC
  setenv fdt_addr_r "0x01f00000"
  setenv loadaddr "0x00200000"
  setenv scriptaddr "0x00500000"
  setenv kernel_addr_r "0x02008000"
  sysboot ${devtype} ${devnum}:@BOOTX@ any ${scriptaddr} /extlinux/rk3326-rg351mp-linux.dtb.conf
""",
    }

    def fix_fs(self, c):
        log("AmberELEC STORAGE: fsck + grow to 8G + remove .please_resize_me")
        grow_storage(c.part("AMB_STOR"))

    def verify(self, c):
        check_label(c, "AMB_ROMS", "GAMES")
        if blkid(c.part("AMB_BOOT"), "UUID") != AMB_BOOT_UUID:
            die("AMBRELC UUID changed")
        if blkid(c.part("AMB_STOR"), "UUID") != AMB_STOR_UUID:
            die("STORAGE UUID changed")
        for line in output(["dumpe2fs", "-h", c.part("AMB_STOR")]).splitlines():
            if re.search("Block count|Block size", line):
                print(line)


class DarkOS(OS):
    """G350: any partitions (boots by PARTUUID, mounts LABEL=EASYROMS).
    RGB30: must be p3/p4/p5, its initramfs hard-codes /dev/mmcblk1p4."""
    key, name, prefix = "darkos", "dArkOS", "DRK"
    copied = ("DRK_BOOT", "DRK_ROOT")
    roms = ("DRK_ROMS", "EASYROMS", "dArkOS roms")
    boot = {
        "ini": """\
  # dArkOS
  setenv bootargs "root=PARTUUID=@ROOT_PU@ rootwait rw fsck.repair=yes net.ifnames=0 fbcon=rotate:0 console=/dev/ttyFIQ0 quiet splash consoleblank=0 vt.global_cursor_default=0"
  setenv loadaddr "0x02000000"
  setenv initrd_loadaddr "0x04000000"
  setenv dtb_loadaddr "0x01f00000"
  load mmc 1:@BOOTX@ ${loadaddr} Image
  load mmc 1:@BOOTX@ ${initrd_loadaddr} uInitrd
  load mmc 1:@BOOTX@ ${dtb_loadaddr} rk3326-g350-linux.dtb
  booti ${loadaddr} ${initrd_loadaddr} ${dtb_loadaddr}
""",
        "scr-g350": """\
  # dArkOS
  setenv bootargs "root=PARTUUID=@ROOT_PU@ rootwait rw fsck.repair=yes net.ifnames=0 fbcon=rotate:0 console=/dev/ttyFIQ0 quiet splash consoleblank=0 vt.global_cursor_default=0"
  load ${devtype} ${devnum}:@BOOTX@ 0x02000000 Image
  load ${devtype} ${devnum}:@BOOTX@ 0x04000000 uInitrd
  load ${devtype} ${devnum}:@BOOTX@ 0x01f00000 rk3326-g350-linux.dtb
  booti 0x02000000 0x04000000 0x01f00000
""",
        "scr-rgb30": """\
  # dArkOS (vendor 5.10 kernel). Despite its name, uInitrd is a plain gzip initramfs
  # (no U-Boot header), so it needs :size; it is loaded last for ${filesize}.
  # Its initramfs ignores root= and always mounts /dev/mmcblk1p4 (hence dArkOS on p4).
  setenv bootargs "root=/dev/mmcblk1p@ROOT@ initrd=/uInitrd rootwait rw fsck.repair=yes quiet splash vt.global_cursor_default=0 net.ifnames=0 console=tty1 plymouth.ignore-serial-consoles consoleblank=0 loglevel=5 video=HDMI-A-1:1280x720@60"
  load ${devtype} ${devnum}:@BOOTX@ ${kernel_addr_r} /Image
  load ${devtype} ${devnum}:@BOOTX@ ${fdt_addr_r} /@DTB@
  load ${devtype} ${devnum}:@BOOTX@ ${ramdisk_addr_r} /uInitrd
  booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
""",
    }

    def prepare(self, c):
        """Do what dArkOS's first boot would do (it would repartition the card)."""
        with mounted(c, c.part("DRK_ROOT"), "btrfs", "root", "compress=zlib:1") as root, \
             mounted(c, c.part("DRK_BOOT"), "vfat", "boot") as boot, \
             mounted(c, c.part("DRK_ROMS"), "exfat", "roms") as roms:
            log(f"Growing btrfs ROOTFS to fill {c.part('DRK_ROOT')}")
            run(["btrfs", "filesystem", "resize", "max", root])

            log("Extracting /roms.tar into EASYROMS (as expandtoexfat.sh does)")
            tarball = f"{root}/roms.tar"
            names = output(["tar", "-tf", tarball]).splitlines()
            bad = sum(1 for n in names if not re.match(r"roms(/|$)", n))
            if bad:
                die(f"roms.tar has {bad} entries outside roms/")
            r = run(["tar", "--no-same-permissions", "--no-same-owner", "-xf", tarball,
                     "-C", roms, "--strip-components=1"], check=False)
            if r.returncode:
                print("WARN: tar reported errors (expected for symlinks/ownership on exFAT)")

            log("Installing themes from /tempthemes into EASYROMS/themes")
            shutil.rmtree(f"{roms}/themes/es-theme-nes-box", ignore_errors=True)
            os.makedirs(f"{roms}/themes", exist_ok=True)
            if not tar_copy(f"{root}/tempthemes", f"{roms}/themes"):
                print("WARN: theme copy reported errors (expected for symlinks on exFAT)")
            shutil.rmtree(f"{root}/tempthemes", ignore_errors=True)
            # roms.tar is kept: expandtoexfat.sh keeps it too (G350 and RGB30)

            log("Finishing dArkOS first-boot steps (fstab, disable firstboot.service)")
            # On the RGB30 dArkOS is on p3/p4/p5 as in its image, so its fstab.exfat
            # (/dev/mmcblk1p3/p4/p5) is used as is, like on the G350.
            shutil.copyfile(f"{boot}/fstab.exfat", f"{root}/etc/fstab")
            if c.device.key == "rgb30":
                for f in ("Image", "uInitrd", c.device.dtb["darkos"]):
                    need_file(f"{boot}/{f}")
            rm_f(f"{root}/etc/systemd/system/multi-user.target.wants/firstboot.service",
                 f"{boot}/firstboot.sh", f"{boot}/expandtoexfat.sh", f"{boot}/fstab.exfat",
                 *glob.glob(f"{boot}/doneit*"))
            if c.device.key == "g350":
                write(f"{boot}/boot.ini", render(c, "darkos"))
            print("--- dArkOS /etc/fstab:")
            print(read(f"{root}/etc/fstab"), end="")

    def verify(self, c):
        check_label(c, "DRK_ROMS", "EASYROMS")
        check_partuuid(c, "DRK_ROOT")
        if c.device.key == "rgb30":
            check_partuuid(c, "DRK_BOOT")


class MustardOS(OS):
    """Finds its partitions through /opt/muos/device/config/storage/*/num on its rootfs.
    Its ROMS must be the last partition (its factory reset resizes it to 100%)."""
    key, name, prefix = "mustardos", "MustardOS", "MUS"
    copied = ("MUS_BOOT", "MUS_ROOT")
    roms = ("MUS_ROM", "ROMS", "MustardOS roms")      # MustardOS reformats it on first boot
    boot = {
        "ini": """\
  # MustardOS (no initrd). rk3326-batlexp-linux.dtb has the same panel init as
  # dArkOS's rk3326-g350-linux.dtb. If it hangs, try dtb_loadaddr "0x1000000"
  # (MustardOS's own value).
  setenv bootargs "root=PARTUUID=@ROOT_PU@ rootfstype=ext4 rw rootwait quiet loglevel=0 cma=64M fbcon=nodefer vt.global_cursor_default=0 init=/sbin/init"
  setenv loadaddr "0x02000000"
  setenv dtb_loadaddr "0x01f00000"
  load mmc 1:@BOOTX@ ${loadaddr} Image
  load mmc 1:@BOOTX@ ${dtb_loadaddr} rk3326-batlexp-linux.dtb
  booti ${loadaddr} - ${dtb_loadaddr}
""",
        "scr-g350": """\
  # MustardOS (UNTESTED with the ROCKNIX U-Boot; same commands as its boot.ini branch)
  setenv bootargs "root=PARTUUID=@ROOT_PU@ rootfstype=ext4 rw rootwait quiet loglevel=0 cma=64M fbcon=nodefer vt.global_cursor_default=0 init=/sbin/init"
  load ${devtype} ${devnum}:@BOOTX@ 0x02000000 Image
  load ${devtype} ${devnum}:@BOOTX@ 0x01f00000 rk3326-batlexp-linux.dtb
  booti 0x02000000 - 0x01f00000
""",
    }
    NUM_FILES = {"boot": "MUS_BOOT", "root": "MUS_ROOT", "rom": "MUS_ROM"}
    STORAGE_CFG = "opt/muos/device/config/storage"

    def fix_fs(self, c):
        log("MustardOS ROOTFS: fsck")
        fsck(c.part("MUS_ROOT"))

    def prepare(self, c):
        n = c.lay.n
        log(f"MustardOS: point storage config at p{n['MUS_BOOT']}/p{n['MUS_ROOT']}/p{n['MUS_ROM']}, "
            "neutralise partition-table edits")
        with mounted(c, c.part("MUS_ROOT"), "ext4", "muroot") as root, \
             mounted(c, c.part("MUS_BOOT"), "vfat", "muboot") as boot:
            scfg = f"{root}/{self.STORAGE_CFG}"
            for d in self.NUM_FILES:
                if not os.path.isfile(f"{scfg}/{d}/num"):
                    die(f"missing {scfg}/{d}/num")
            for d, role in self.NUM_FILES.items():
                write(f"{scfg}/{d}/num", str(n[role]))

            # Comment out the fdisk/parted lines of the factory reset (": # ...").
            reset = f"{root}/opt/muos/script/system/reset.sh"
            if not os.path.isfile(reset):
                die(f"missing {reset}")
            lines = read(reset).split("\n")
            target = re.compile(r'\| fdisk /dev/"\$ROM_DEV"|parted ---pretend-input-tty')
            lines = [re.sub(r"^([ \t\v\f\r]*)", r"\1: # ", ln, count=1) if target.search(ln) else ln
                     for ln in lines]
            write(reset, "\n".join(lines))
            active = [ln for ln in grep_n(reset, r"(^|[^A-Za-z0-9_])(fdisk|parted)([ \t\n\v\f\r]|$)")
                      if not re.match(r"[0-9]+:[ \t\v\f\r]*: #", ln)]
            if active:
                print("\n".join(active))
                die("active fdisk/parted lines remain in reset.sh")
            patched = grep_n(reset, r"^[ \t\v\f\r]*: #")
            if not patched:
                die("no fdisk/parted lines found to patch in reset.sh (script changed?)")
            print("--- patched reset.sh lines:")
            print("\n".join(patched))

            shutil.copyfile(f"{boot}/boot.ini", f"{boot}/boot.ini.orig")
            write(f"{boot}/boot.ini", render(c, "mustardos"))   # reference only; U-Boot reads p1

    def verify(self, c):
        check_label(c, "MUS_ROM", "ROMS")
        check_partuuid(c, "MUS_ROOT")
        n = c.lay.n
        with mounted(c, c.part("MUS_ROOT"), "ext4", "muroot", "ro") as root:
            for d, role in self.NUM_FILES.items():
                got = read(f"{root}/{self.STORAGE_CFG}/{d}/num")
                if got != str(n[role]):
                    die(f"MustardOS storage/{d}/num is '{got}', expected {n[role]}")
        print(f"MustardOS storage num files: boot={n['MUS_BOOT']} root={n['MUS_ROOT']} rom={n['MUS_ROM']}")


class Knulli(OS):
    """Must be p1/p2: knulli-part takes /userdata as boot partition + 1 (one digit)."""
    key, name, prefix = "knulli", "knulli", "KNU"
    copied = ("KNU_BOOT",)
    roms = ("KNU_SHARE", "SHARE", "knulli /userdata")   # what knulli's autoresize would leave
    boot = {
        "scr-rgb30": """\
  # knulli (finds its boot partition by label=KNULLI, /userdata on the next partition)
  setenv bootargs "initrd=/boot/initrd.lz4 label=KNULLI rootwait quiet loglevel=0 console=tty3 console=ttyS2,1500000n8 fbcon=rotate:0"
  load ${devtype} ${devnum}:@BOOTX@ ${kernel_addr_r} /boot/linux
  load ${devtype} ${devnum}:@BOOTX@ ${fdt_addr_r} /@DTB@
  load ${devtype} ${devnum}:@BOOTX@ ${ramdisk_addr_r} /boot/initrd.lz4
  booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
""",
    }

    def prepare(self, c):
        log(f"knulli: disable autoresize (it would resize p{c.n('KNU_SHARE')} to 100% of the card)")
        with mounted(c, c.part("KNU_BOOT"), "vfat", "knboot") as boot:
            conf = f"{boot}/knulli-boot.conf"
            need_file(conf)
            for f in ("boot/linux", "boot/initrd.lz4", c.device.dtb["knulli"]):
                need_file(f"{boot}/{f}")
            shutil.copyfile(conf, f"{conf}.orig")
            text = re.sub(r"(?m)^[ \t\v\f\r]*autoresize[ \t\v\f\r]*=.*$", "autoresize=false", read(conf))
            write(conf, text)
            if re.search(r"(?m)^ *autoresize *= *true *$", text):
                die("autoresize still enabled in knulli-boot.conf")
            if re.search(r"(?m)^ *format-internal *=", text):
                die("format-internal is set in knulli-boot.conf")
            print("\n".join(grep_n(conf, "autoresize")))

    def verify(self, c):
        check_label(c, "KNU_BOOT", "KNULLI")
        check_label(c, "KNU_SHARE", "SHARE")
        if c.n("KNU_SHARE") != c.n("KNU_BOOT") + 1:
            die("knulli SHARE must directly follow its boot partition")


class Rocknix(OS):
    """Needs system.gamesdevice, or its automount takes another OS's partition as games.
    On the G350 it needs its own U-Boot and boots by filesystem UUID (AmberELEC's ext4
    is labelled STORAGE too)."""
    key, name, prefix = "rocknix", "ROCKNIX", "RNX"
    copied = ("RNX_BOOT", "RNX_STOR")
    roms = ("RNX_ROMS", "GAMES", "ROCKNIX roms")
    boot = {
        "scr-g350": """\
  # ROCKNIX: as its own boot.scr does; extlinux.conf.dualboot is written by the script
  # (G350 dtb, boot=UUID=@RNX_BOOT_UUID@ disk=UUID=@RNX_STOR_UUID@).
  setenv kernel_addr_r "0x09000000"
  setenv fdtoverlay_addr_r "0x08000000"
  sysboot ${devtype} ${devnum}:@BOOTX@ any 0x100000 /extlinux/extlinux.conf.dualboot
""",
        "scr-rgb30": """\
  # ROCKNIX (initramfs built into KERNEL; finds its partitions by label)
  setenv bootargs "boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet console=ttyS2,1500000 console=tty0 systemd.debug_shell=ttyS2"
  load ${devtype} ${devnum}:@BOOTX@ ${kernel_addr_r} /KERNEL
  load ${devtype} ${devnum}:@BOOTX@ ${fdt_addr_r} /@DTB@
  booti ${kernel_addr_r} - ${fdt_addr_r}
""",
    }
    EXTLINUX = """\
LABEL ROCKNIX
  LINUX /KERNEL
  FDT /@DTB@
  APPEND boot=UUID=@RNX_BOOT_UUID@ disk=UUID=@RNX_STOR_UUID@ quiet console=ttyS2,1500000 console=tty0 systemd.debug_shell=ttyS2
"""

    def boot_values(self, c):
        return {**super().boot_values(c), "RNX_BOOT_UUID": RNX_BOOT_UUID, "RNX_STOR_UUID": RNX_STOR_UUID}

    def extlinux(self, c) -> str:
        """G350: /extlinux/extlinux.conf.dualboot on ROCKNIX's FAT partition."""
        return fill(self.EXTLINUX, **self.boot_values(c))

    def fix_fs(self, c):
        log("ROCKNIX STORAGE: fsck + grow to 8G + remove .please_resize_me")
        grow_storage(c.part("RNX_STOR"))

    def prepare(self, c):
        # automount mounts system.gamesdevice if it exists; otherwise it mounts the first
        # unmounted partition of any >8GB disk as "external games", which here would be
        # another OS's partition. system.cfg lives on /storage, and chksysconfig restores
        # all of /usr/config over it on boot while retroarch.cfg is missing, so the whole
        # default config is seeded first (what ROCKNIX does on its first boot).
        log(f"ROCKNIX: seed /storage/.config and set system.gamesdevice to p{c.n('RNX_ROMS')}")
        with mounted(c, c.part("RNX_BOOT"), "vfat", "rnboot") as boot, \
             mounted(c, c.part("RNX_STOR"), "ext4", "rnstor") as stor:
            for f in ("KERNEL", "SYSTEM", c.device.dtb["rocknix"]):
                need_file(f"{boot}/{f}")
            rnsys = f"{c.mnt}/rnsys"
            run(["unsquashfs", "-q", "-f", "-d", rnsys, f"{boot}/SYSTEM", "usr/config"],
                stdout=subprocess.DEVNULL)
            if not os.path.isdir(f"{rnsys}/usr/config"):
                die("usr/config not found in ROCKNIX SYSTEM")
            os.makedirs(f"{stor}/.config", exist_ok=True)
            run(["cp", "-a", f"{rnsys}/usr/config/.", f"{stor}/.config/"])
            shutil.rmtree(rnsys)

            cfg = f"{stor}/.config/system/configs/system.cfg"
            need_file(cfg)
            need_file(f"{stor}/.config/retroarch/retroarch.cfg")
            lines = read(cfg).splitlines()
            if not any(ln.startswith("system.hostname=") for ln in lines):
                die("system.hostname missing from ROCKNIX system.cfg")
            lines = [ln for ln in lines
                     if not ln.startswith(("system.gamesdevice=", "system.automount="))]
            lines += ["system.automount=1",
                      f"system.gamesdevice=/dev/disk/by-partuuid/{c.partuuid(c.n('RNX_ROMS'))}"]
            write(cfg, "\n".join(lines) + "\n")
            print("\n".join(grep_n(cfg, r"^system\.(automount|gamesdevice)=")))

            if c.bootmode == "scr-g350":
                conf = f"{boot}/extlinux/extlinux.conf.dualboot"
                write(conf, self.extlinux(c))
                print("--- ROCKNIX extlinux.conf.dualboot:")
                print(read(conf), end="")

    def verify(self, c):
        check_label(c, "RNX_BOOT", "ROCKNIX")
        check_label(c, "RNX_STOR", "STORAGE")
        check_label(c, "RNX_ROMS", "GAMES")
        check_partuuid(c, "RNX_ROMS")
        if c.device.key == "g350":
            if blkid(c.part("RNX_BOOT"), "UUID") != RNX_BOOT_UUID:
                die("ROCKNIX boot UUID changed")
            if blkid(c.part("RNX_STOR"), "UUID") != RNX_STOR_UUID:
                die("ROCKNIX STORAGE UUID changed")
            uuids = [blkid(c.path(n), "UUID") for n in c.lay.numbered()]
            if uuids.count(RNX_BOOT_UUID) != 1:
                die(f"UUID {RNX_BOOT_UUID} is used twice")


ALL_OSES = [AmberELEC(), DarkOS(), MustardOS(), Knulli(), Rocknix()]
OS_BY_KEY = {o.key: o for o in ALL_OSES}


def osname(key: str) -> str:
    return OS_BY_KEY[key].name


def render(c: Choices, os_key: str) -> str:
    """Fill src/boot.ini.<os> (the copy on the OS's own BOOT partition; U-Boot never reads it)."""
    o = OS_BY_KEY[os_key]
    boot, root = c.n(f"{o.prefix}_BOOT"), c.n(f"{o.prefix}_ROOT")
    path = f"{HERE}/boot.ini.{os_key}"
    if not os.path.isfile(path):
        die(f"missing {path}")
    return fill(read(path), BOOT=boot, BOOTX=hx(boot), ROOT=root, ROOT2=f"{root:02x}")


# ============================================================================ 5. boot script
def boot_title(c: Choices) -> str:
    title = f"{osname(c.default)} (no key)"
    for key, os_key in zip(KEY_NAMES, c.key_os):
        title += f" / {osname(os_key)} ({key})"
    return title


def boot_branches(c: Choices) -> str:
    """if <Vol- held> ... elif <Vol+ held> ... else <default> fi"""
    text = ""
    for i, os_key in enumerate(c.key_os):
        if c.bootmode == "ini":
            cond = f"gpio input {c.device.key_pins[i]}"
        else:
            cond = f'test "${{key}}" = "{KEY_VARS[i]}"'
        text += f"{'if' if i == 0 else 'elif'} {cond}; then\n" + OS_BY_KEY[os_key].branch(c)
    return text + "else\n" + OS_BY_KEY[c.default].branch(c) + "fi\n"


def layout_comment(c: Choices) -> list:
    return ["# Layout:"] + [f"#   {p.line()}" for p in c.lay.parts]


# G350, Hardkernel U-Boot: "gpio input" works, so the keys are tested in the branches.
def boot_ini_g350(c: Choices) -> str:
    lines = [
        "odroidgoa-uboot-config",
        "",
        f"# ---- G350 multi-boot: {boot_title(c)} ----",
        f"# Generated by flash-dualboot.sh --os {','.join(c.user_os)} --default {c.default}",
        "# Hold the key at power-on. Pins are from the gpio-keys node in rk3326-g350-linux.dtb:",
        "#   Vol- = GPIO2_A1 = pin 65, Vol+ = GPIO2_A0 = pin 64, both active low.",
        '# U-Boot 2017.09 "gpio input" returns the pin value as exit status, so it is',
        f'# "true" (0) only while the key is held. If the command fails, {osname(c.default)} boots.',
        "#",
        *layout_comment(c),
    ]
    if c.sel("amberelec"):
        lines.append("# AmberELEC updates overwrite this file: restore it from boot.ini.dualboot.")
    return "\n".join(lines) + "\n\n" + boot_branches(c)


# G350, ROCKNIX's mainline U-Boot: read the keys from the GPIO register into ${key}.
KEYS_G350 = """
gpio input C1; gpio input C0
mw.l 0xff140070 0x000f0005
sleep 0.1
setexpr.l gpio2_ext *0xff260050
setexpr volup_bit ${gpio2_ext} % 2
setexpr t ${gpio2_ext} / 2
setexpr voldn_bit ${t} % 2
# Vol+ first: a press at an angle can also pull Vol- low; Vol- alone reads cleanly.
setenv key none
if itest ${volup_bit} == 0; then setenv key volplus; elif itest ${voldn_bit} == 0; then setenv key volminus; fi

"""


def boot_cmd_g350(c: Choices) -> str:
    lines = [
        f"# ---- G350 multi-boot: {boot_title(c)} ----",
        f"# Generated by flash-dualboot.sh --os {','.join(c.user_os)} --default {c.default}",
        "# Run by ROCKNIX's U-Boot 2025.10 (script bootmeth). Source of boot.scr:",
        "#   mkimage -A arm64 -O linux -T script -C none -d boot.cmd boot.scr",
        "# Hold the key at power-on. Vol+ = GPIO2_A0, Vol- = GPIO2_A1 (gpio-keys in",
        '# rk3326-batlexp-g350.dtb), both active low. In this U-Boot "gpio input" returns',
        "# success instead of the pin value and nothing enables the pins' pull-ups, so the",
        "# script sets the pull-ups (PX30 GRF GPIO2A_P, 01 = pull-up, upper 16 bits = write",
        "# mask) and reads GPIO2's EXT_PORTA register (bit 0 = Vol+, bit 1 = Vol-).",
        "# Partition numbers after ${devnum}: are hex.",
        "#",
        *layout_comment(c),
    ]
    return "\n".join(lines) + "\n" + KEYS_G350 + boot_branches(c)


# RGB30, knulli's U-Boot (no gpio, no setexpr): compare register bytes copied to RAM.
KEYS_RGB30 = """
mw.l 0xfdc600a0 0xc0004000
mw.l 0xfdc600a4 0xffff5555
sleep 0.1
cp.l 0xfe760070 0x30000000 1
setenv key none
if itest.b *0x30000000 < 0x80; then setenv key volplus; elif itest.b *0x30000001 == 0xfe || itest.b *0x30000001 == 0x7e; then setenv key volminus; fi

"""


def boot_cmd_rgb30(c: Choices) -> str:
    lines = [
        f"# ---- RGB30 multi-boot: {boot_title(c)} ----",
        f"# Generated by flash-dualboot.sh --device rgb30 --os {','.join(c.user_os)} --default {c.default}",
        "# Source of boot.scr (mkimage -A arm64 -T script -C none -d boot.cmd boot.scr).",
        "# Hold the key at power-on. Vol+ = GPIO3_A7, Vol- = GPIO3_B0 (gpio-keys-vol in",
        "# rk3566-powkiddy-rgb30.dtb), both active low. knulli's U-Boot has no gpio or setexpr",
        "# command, so the script enables the pull-ups (RK3568 SYS_GRF GPIO3A_P/GPIO3B_P,",
        "# 01 = pull-up, upper 16 bits = write mask), copies GPIO3's EXT_PORT register to RAM",
        "# and compares bytes: byte 0 bit 7 = Vol+ (< 0x80 = held), byte 1 bit 0 = Vol-",
        "# (0xfe = held, the other B pins are pulled up).",
        "# Partition numbers after ${devnum}: are hex.",
        "#",
        *layout_comment(c),
    ]
    return "\n".join(lines) + "\n" + KEYS_RGB30 + boot_branches(c)


def boot_config(c: Choices) -> str:
    """The shared boot.ini (ini) or boot.cmd (scr-*) for p1, ending in one newline."""
    gen = {"ini": boot_ini_g350, "scr-g350": boot_cmd_g350, "scr-rgb30": boot_cmd_rgb30}[c.bootmode]
    return gen(c).rstrip("\n") + "\n"


# ============================================================================ 6. plan
def card_size_for_plan(c: Choices):
    r = run(["blockdev", "--getsz", c.dev], check=False,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    c.total = int(r.stdout) if r.returncode == 0 and r.stdout.strip() else 0
    if not c.total:
        c.total = PLAN_TOTAL
        print(f"(plan: {c.dev} not readable, assuming a {PLAN_TOTAL}-sector / 62.5GB card)")
    for f in c.images():
        if not os.path.isfile(f):
            print(f"(plan: WARNING image not found: {f})")
            if f == c.uboot_img:
                print(f"(plan: that image is always needed on the {c.device.name}: U-Boot comes from it)")


def show_plan(c: Choices):
    note = "" if c.sel(c.uboot_os) else f", although {c.uboot_os} is not installed"
    print(f"Device:   {c.device.name}")
    print(f"OSes:     {' '.join(c.user_os)}")
    print(f"U-Boot:   {os.path.basename(c.uboot_img)} (always needed{note})")
    if c.bootmode == "scr-g350" and c.sel("mustardos"):
        print("WARNING:  MustardOS has not been tested with the ROCKNIX U-Boot (needed for ROCKNIX).")
    print(f"Keys:     no key = {osname(c.default)}")
    for i, os_key in enumerate(c.key_os):
        print(f"          {KEY_NAMES[i]} (pin {c.device.key_pins[i]}) = {osname(os_key)}")
    print(f"Partitions on {c.dev} ({hsize(c.total)}):")
    for p in c.lay.parts:
        print(f"  {p.line()}")
    n = c.lay.n
    if c.device.key == "g350":
        roles = ["DRK_ROMS", "DRK_BOOT", "DRK_ROOT", "MUS_BOOT", "MUS_ROOT", "MUS_ROM"]
        if "RNX_BOOT" in n:
            roles += ["RNX_BOOT", "RNX_STOR", "RNX_ROMS"]
    else:
        roles = ["KNU_BOOT", "KNU_SHARE", "DRK_ROMS", "DRK_BOOT", "DRK_ROOT", "RNX_BOOT", "RNX_STOR", "RNX_ROMS"]
    print("Numbers:  " + " ".join(f"{r}_N={n.get(r, '-')}" for r in roles))


def print_plan(c: Choices, boot_cfg: str):
    show_plan(c)
    print("\n---- sfdisk script")
    print(c.lay.sfdisk, end="")
    if c.bootmode == "ini":
        print("\n---- boot.ini (p1)")
        print(boot_cfg, end="")
    else:
        print("\n---- boot.cmd (p1, compiled to boot.scr)")
        print(boot_cfg, end="")
    if c.bootmode == "scr-g350":
        print(f"\n---- ROCKNIX /extlinux/extlinux.conf.dualboot (p{c.n('RNX_BOOT')})")
        print(OS_BY_KEY["rocknix"].extlinux(c), end="")
    if c.device.key == "g350":
        if c.sel("darkos"):
            print(f"\n---- dArkOS /boot/boot.ini (p{c.n('DRK_BOOT')})")
            print(render(c, "darkos"), end="")
        if c.sel("mustardos"):
            print(f"\n---- MustardOS /boot.ini (p{c.n('MUS_BOOT')})")
            print(render(c, "mustardos"), end="")


# ============================================================================ 7. flash
def read(path: str) -> str:
    with open(path, errors="surrogateescape") as f:
        return f.read()


def write(path: str, text: str):
    with open(path, "w", errors="surrogateescape") as f:
        f.write(text)


def is_block(path: str) -> bool:
    try:
        return stat.S_ISBLK(os.stat(path).st_mode)
    except OSError:
        return False


@contextmanager
def mounted(c: Choices, source: str, fstype: str, name: str, options: str = ""):
    """Mount source on <tmp>/<name>; sync and unmount when the block ends. After an
    error the mount is left to cleanup(), as everything is unmounted there."""
    path = f"{c.mnt}/{name}"
    os.makedirs(path, exist_ok=True)
    run(["mount", "-t", fstype, *(["-o", options] if options else []), source, path])
    yield path
    run(["sync"])
    run(["umount", path])


def cleanup(mnt: str):
    for d in sorted(glob.glob(f"{mnt}/*")):
        if os.path.ismount(d):
            run(["umount", d], check=False)
    if any(os.path.ismount(d) for d in glob.glob(f"{mnt}/*")):
        print(f"WARNING: could not unmount everything under {mnt}; not removing it", file=sys.stderr)
    else:
        shutil.rmtree(mnt, ignore_errors=True)


def unmount_dev(dev: str):
    """Unmount every partition of dev that the desktop may have auto-mounted."""
    run(["udevadm", "settle"], check=False)
    with open("/proc/mounts") as f:
        mounts = [line.split()[:2] for line in f]
    unescape = lambda s: re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)
    for target in sorted((unescape(m[1]) for m in mounts if m[0].startswith(dev)), reverse=True):
        if run(["umount", target], check=False).returncode:
            run(["umount", "-l", target])


def fsck(part: str):
    """e2fsck -fy; exit code 1 (errors corrected) is fine."""
    if run(["e2fsck", "-fy", part], check=False).returncode > 1:
        die(f"e2fsck failed on {part}")


def grow_storage(part: str):
    """AmberELEC and ROCKNIX: grow the copied ext4 STORAGE and drop the first-boot resize
    flag (their fs-resize would "parted resizepart 100%" the STORAGE partition)."""
    fsck(part)
    run(["resize2fs", part])
    run(["debugfs", "-w", "-R", "rm /.please_resize_me", part])
    if "please_resize_me" in output(["debugfs", "-R", "ls -l /", part]):
        die(f"resize flag still present on {part}")


def tar_copy(src: str, dst: str) -> bool:
    """Copy a tree with tar (no owners/permissions, as exFAT has none)."""
    flags = ["--no-same-permissions", "--no-same-owner"]
    try:
        pack = subprocess.Popen(["tar", *flags, "-cf", "-", "."], cwd=src, stdout=subprocess.PIPE)
    except OSError:
        return False
    unpack = subprocess.run(["tar", *flags, "-xf", "-", "-C", dst], stdin=pack.stdout)
    pack.stdout.close()
    return pack.wait() == 0 and unpack.returncode == 0


def blkid(part: str, tag: str) -> str:
    return output(["blkid", "-s", tag, "-o", "value", part], check=False).strip()


def check_label(c: Choices, role: str, label: str):
    if blkid(c.part(role), "LABEL") != label:
        die(f"{c.part(role)} is not {label}")


def check_partuuid(c: Choices, role: str):
    want = c.partuuid(c.n(role))
    if blkid(c.part(role), "PARTUUID").lower() != want:
        die(f"PARTUUID of {c.part(role)} is not {want}")


def preflight(c: Choices):
    """Real run: refuse anything that looks wrong before touching the card."""
    if os.geteuid() != 0:
        die("run as root (sudo)")
    if not is_block(c.dev):
        die(f"{c.dev} is not a block device")
    if not os.path.isfile(c.uboot_img):
        die(f"image not found: {c.uboot_img} (always needed on the {c.device.name}: U-Boot comes "
            f"from it, even without {c.uboot_os} in --os)")
    for f in c.images():
        if not os.path.isfile(f):
            die(f"image not found: {f}")
    if c.device.key == "g350":
        for o in ("darkos", "mustardos"):
            if c.sel(o) and not os.path.isfile(f"{HERE}/boot.ini.{o}"):
                die(f"missing {HERE}/boot.ini.{o}")

    tools = ["sfdisk", "wipefs", "mkfs.exfat", "blkid", "partprobe"]
    if c.device.key == "g350":
        tools += ["e2fsck", "resize2fs", "debugfs", "dumpe2fs"] if c.sel("amberelec") else ["mkfs.vfat"]
        if c.sel("mustardos"):
            tools += ["e2fsck"]
        if c.bootmode == "scr-g350":
            tools += ["mkimage"]
    else:
        tools += ["mkfs.vfat", "mkimage"]
    if c.sel("rocknix"):
        tools += ["e2fsck", "resize2fs", "debugfs", "unsquashfs"]
    if c.sel("darkos"):
        tools += ["btrfs", "tar"]
    for t in tools:
        if not shutil.which(t):
            die(f"missing tool: {t}")

    size = int(output(["blockdev", "--getsize64", c.dev]))
    if not 55 * 1000**3 < size < 66 * 1000**3:
        die(f"{c.dev} is {size} bytes, expected the ~62.5GB (58.2GiB) card")
    if output(["findmnt", "-rno", "SOURCE", "/"], check=False).startswith(c.dev):
        die(f"{c.dev} holds the running root fs!")
    c.total = int(output(["blockdev", "--getsz", c.dev]))


def flash(c: Choices, boot_cfg: str):
    dev, lay = c.dev, c.lay
    run(["lsblk", "-o", "NAME,SIZE,FSTYPE,LABEL,MODEL,TRAN", dev])
    print()
    show_plan(c)
    print()
    try:
        answer = input(f"ALL DATA ON {dev} WILL BE DESTROYED. Type YES to continue: ")
    except EOFError:
        answer = ""
    if answer != "YES":
        die("aborted")

    # 1. partition table
    log(f"Unmounting and wiping {dev}")
    unmount_dev(dev)
    for part in sorted(glob.glob(f"{dev}?*")):
        if is_block(part):
            run(["wipefs", "-a", part], check=False, stdout=subprocess.DEVNULL)
    run(["wipefs", "-a", dev])
    table = "MBR" if c.device.key == "g350" else "GPT"
    log(f"Writing {table} (label-id {lay.sfdisk.splitlines()[1].split()[1]})")
    run(["sfdisk", "--wipe", "always", "--wipe-partitions", "always", dev], input=lay.sfdisk, text=True)
    if run(["partprobe", dev], check=False, stderr=subprocess.DEVNULL).returncode:
        run(["blockdev", "--rereadpt", dev], check=False)
    unmount_dev(dev)
    for num in lay.numbered():
        if not is_block(c.path(num)):
            die(f"{c.path(num)} missing after partitioning")

    # 2. U-Boot
    log(f"Writing U-Boot (sectors 64-32767) from {os.path.basename(c.uboot_img)}")
    run(["dd", f"if={c.uboot_img}", f"of={dev}", f"bs={S}", "skip=64", "seek=64", "count=32704",
         "conv=notrunc,fsync", "status=none"])

    # 3. copy the OS partitions, fix their filesystems, create the ROM filesystems
    for o in c.oses:
        o.copy(c)
    unmount_dev(dev)
    for o in c.oses:
        o.fix_fs(c)
    # Stale data from an earlier layout can look like a filesystem to libblkid, and
    # mkfs.exfat (exfatprogs >= 1.4) refuses with "Device has existing signatures".
    unmount_dev(dev)
    to_wipe = [c.path(p.num) for p in lay.parts if p.rom]
    if not c.sel("amberelec"):
        to_wipe.append(c.part("BOOTSEL"))
    run(["wipefs", "-a", *to_wipe], stdout=subprocess.DEVNULL)
    if not c.sel("amberelec"):
        log(f"Creating FAT32 BOOTSEL on {c.part('BOOTSEL')}")
        run(["mkfs.vfat", "-F", "32", "-n", "BOOTSEL", c.part("BOOTSEL")])
    for o in c.oses:
        o.format(c)
    unmount_dev(dev)

    # 4. per-OS first-boot work
    for o in c.oses:
        o.prepare(c)

    # 5. shared boot config on p1
    install_boot_config(c, boot_cfg)

    # 6. verify
    log("Verification")
    run(["sfdisk", "-d", dev])
    run(["blkid", *[c.path(n) for n in lay.numbered()]])
    if not c.sel("amberelec"):
        check_label(c, "BOOTSEL", "BOOTSEL")
    for o in c.oses:
        o.verify(c)
    if c.device.key == "rgb30":
        labels = [blkid(c.path(n), "LABEL") for n in lay.numbered()]
        for label in ("ROCKNIX", "STORAGE", "KNULLI", "SHARE"):
            if labels.count(label) > 1:
                die(f"label {label} is used twice")

    run(["sync"])
    if run(["eject", dev], check=False, stderr=subprocess.DEVNULL).returncode:
        run(["udisksctl", "power-off", "-b", dev], check=False, stderr=subprocess.DEVNULL)
    log("Done. Card is safe to remove.")
    show_plan(c)


def install_boot_config(c: Choices, boot_cfg: str):
    ini = c.bootmode == "ini"
    name = "boot.ini" if ini else "boot.cmd"
    log(f"Installing multi-boot {'boot.ini' if ini else 'boot.scr'} on {c.part('BOOTSEL')}")
    generated = f"{HERE}/{name}.generated"          # a copy for the user (gitignored)
    write(generated, boot_cfg)
    if os.environ.get("SUDO_USER"):
        run(["chown", f"{os.environ['SUDO_USER']}:", generated], check=False)
    with mounted(c, c.part("BOOTSEL"), "vfat", "p1") as p1:
        if ini:
            if c.sel("amberelec"):
                shutil.copyfile(f"{p1}/boot.ini", f"{p1}/boot.ini.amberelec")
            shutil.copyfile(generated, f"{p1}/boot.ini")
            shutil.copyfile(generated, f"{p1}/boot.ini.dualboot")
            # U-Boot loads logo.bmp from mmc 1:1; take it from the dArkOS image's BOOT partition.
            img, src = c.device.images["darkos"], c.device.src["darkos"][0]
            loop_opts = f"ro,loop,offset={src.start * S},sizelimit={src.size * S}"
            with mounted(c, img, "vfat", "drkimg", loop_opts) as drk:
                shutil.copyfile(f"{drk}/logo.bmp", f"{p1}/logo.bmp")
        else:
            shutil.copyfile(generated, f"{p1}/boot.cmd")
            run(["mkimage", "-A", "arm64", "-O", "linux", "-T", "script", "-C", "none",
                 "-n", f"{c.device.name} multi-boot", "-d", f"{p1}/boot.cmd", f"{p1}/boot.scr"])
        run(["ls", "-l", p1])


# ============================================================================ main
def main(argv: list):
    sys.stdout.reconfigure(line_buffering=True)   # keep our output in order with the tools'
    c = Choices(parse_args(argv))
    if c.plan:
        card_size_for_plan(c)
    else:
        preflight(c)
    c.lay = make_layout(c)
    boot_cfg = boot_config(c)
    if c.plan:
        print_plan(c, boot_cfg)
        return
    c.mnt = tempfile.mkdtemp(prefix="multiboot.", dir="/tmp")
    try:
        flash(c, boot_cfg)
    finally:
        cleanup(c.mnt)


if __name__ == "__main__":
    main(sys.argv[1:])
