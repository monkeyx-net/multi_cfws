#!/usr/bin/env python3
"""Build a multi-boot SD card for one device (src/devices/<device>.json).

Python version of flash-dualboot.sh: same options, same card, same --plan output.
Standard library only. Device data (images, source partitions, dtbs, key pins, UUIDs)
is in src/devices/*.json; each OS's U-Boot commands are in src/boot/<os>.<bootmode>.cmd.
The file reads top to bottom in the order things happen:

  1. settings      units, sizes, loading the device file
  2. choices       command line -> which OSes, default OS, keys, boot mode
  3. layout        partition table (one Part per partition, no hidden state)
  4. per-OS code   one class per OS: its boot commands and every step it needs
  5. boot script   the shared boot.ini / boot.cmd that picks an OS by key
  6. plan          the --plan report
  7. flash         the real run, step by step
"""
from __future__ import annotations

import glob
import gzip
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
IMGDIR = os.environ.get("IMGDIR") or os.path.dirname(HERE) + "/img"   # sudo IMGDIR=/path ...


# ============================================================================ 1. settings
S = 512                          # bytes per sector; all sizes below are in sectors
MIB = 2048
GIB = 1024 * MIB
GAP = 2048                       # MBR: gap before each logical partition
PLAN_TOTAL = 122070312           # --plan without a readable card: ~62.5GB
DISKID = "5a3c9e71"              # MBR disk ID; root=PARTUUID=5a3c9e71-NN

AMB_BOOT_SIZE = 2 * GIB          # partition sizes on the card (AmberELEC's are grown)
AMB_STOR_SIZE = 8 * GIB
RNX_STOR_SIZE = 8 * GIB
BOOTSEL_SIZE = 64 * MIB
DRK_BOOT_SIZE = 100 * MIB
DRK_ROOT_SIZE = 12 * GIB
ARK_ROOT_SIZE = 12 * GIB         # ArkOS's own first boot grows its ext4 to ~11GB
# MustardOS BOOT/ROOTFS, knulli KNULLI and ROCKNIX's FAT partition keep their image size.

# ArkOS's BOOT and ROMS are relabelled: dArkOS mounts LABEL=BOOT and LABEL=EASYROMS,
# which ArkOS's partitions would also carry. ArkOS itself mounts by PARTUUID (its fstab
# is rewritten), nothing in it uses labels.
ARK_BOOT_LABEL = "ARKBOOT"
ARK_ROMS_LABEL = "ARKROMS"

KEY_NAMES = ("Vol-", "Vol+")     # keys are handed out in this order
KEY_VARS = ("volminus", "volplus")


@dataclass
class Src:
    """A partition inside an image file (start and size in sectors, from sfdisk -d)."""
    start: int
    size: int


@dataclass
class Device:
    """One device, read from src/devices/<key>.json. Everything tied to the device's
    images (file names, offsets, dtbs, UUIDs) lives there; see load_device for the fields."""
    key: str                     # file name: g350 / rgb30
    name: str                    # G350 / RGB30
    soc: str
    all_os: tuple                # fixed order: also the order keys are handed out
    key_pins: tuple              # Vol-, Vol+ GPIO numbers
    table: str                   # mbr / gpt
    layout: str                  # LAYOUTS key: which layout function builds the card
    uboot_sectors: tuple         # raw U-Boot area copied from the U-Boot image: [first, end)
    boot: tuple                  # rules {"if": os, "bootmode", "uboot_os"}: first match wins
    images: dict                 # os -> image file (full path)
    src: dict                    # os -> partitions copied from its image, in order (Src)
    dtb: dict                    # os -> dtb path on its boot partition
    uuids: dict                  # role -> filesystem UUID inside the image
    os_opts: dict                # os -> {setting: value} for this device's image of it
    os_boot_ini: tuple           # OSes whose BOOT gets src/boot.ini.<os>
    unique_labels: tuple         # labels that must not appear twice on the card
    numbers: tuple               # role groups on the --plan "Numbers:" line
    untested: dict               # bootmode -> OSes not yet booted that way

    @property
    def gpt(self) -> bool:
        return self.table == "gpt"

    def opt(self, os_key: str, name: str, default=None):
        return self.os_opts.get(os_key, {}).get(name, default)


DEVICE_DIR = f"{HERE}/devices"
DEVICE_FIELDS = ("name", "soc", "all_os", "key_pins", "table", "layout", "uboot_sectors", "boot",
                 "images", "src", "dtb", "uuids", "os_opts", "os_boot_ini", "unique_labels",
                 "numbers", "untested")


def load_device(key: str, imgdir: str) -> Device:
    """src/devices/<key>.json -> Device. Keys starting with "_" are comments."""
    path = f"{DEVICE_DIR}/{key}.json"
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        die(f"cannot read {path}: {e}")
    missing = [k for k in DEVICE_FIELDS if k not in d]
    if missing:
        die(f"{path}: missing {', '.join(missing)}")
    d = {k: v for k, v in d.items() if not k.startswith("_")}
    dev = Device(key=key, **{**d,
        "all_os": tuple(d["all_os"]), "key_pins": tuple(d["key_pins"]),
        "uboot_sectors": tuple(d["uboot_sectors"]), "boot": tuple(d["boot"]),
        "images": {o: f"{imgdir}/{f}" for o, f in d["images"].items()},
        "src": {o: tuple(Src(p[0], p[1]) for p in parts) for o, parts in d["src"].items()},
        "os_boot_ini": tuple(d["os_boot_ini"]), "unique_labels": tuple(d["unique_labels"]),
        "numbers": tuple(tuple(g) for g in d["numbers"])})
    for o in dev.all_os:
        if o not in dev.images or o not in dev.src:
            die(f"{path}: {o} has no image or src")
    if dev.table not in ("mbr", "gpt"):
        die(f"{path}: table must be mbr or gpt")
    if not dev.boot or "if" in dev.boot[-1]:
        die(f"{path}: the last boot rule must have no \"if\"")
    return dev


def device_keys() -> list:
    """Every src/devices/*.json, the default device first."""
    keys = sorted(os.path.basename(p)[:-5] for p in glob.glob(f"{DEVICE_DIR}/*.json"))
    return [DEFAULT_DEVICE] + [k for k in keys if k != DEFAULT_DEVICE]


DEFAULT_DEVICE = "g350"


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
def usage() -> str:
    devs = [load_device(k, IMGDIR) for k in device_keys()]
    lines = [
        "Build a multi-boot SD card for the " + " or the ".join(f"{d.name} ({d.soc})" for d in devs) + ".",
        f"Usage: sudo python3 src/flash-dualboot.py [--device {'|'.join(d.key for d in devs)}] "
        "--os LIST [--default OS] [--plan] [/dev/sdX]",
    ]
    for i, d in enumerate(devs):
        first = "  --device   " if i == 0 else "             "
        lines.append(f"{first}{d.key}{' (default)' if i == 0 else ''}: {', '.join(d.all_os)}")
    lines.append("             Always needed (U-Boot source, even if not in --os):")
    for d in devs:
        rule = d.boot[-1]
        extra = "".join(f" (the {r['uboot_os']} image instead when {r['if']} is selected)"
                        for r in d.boot[:-1])
        lines.append(f"               {d.key}: the {rule['uboot_os']} image{extra}")
    lines += [
        "  --os       required: comma list naming 2 or 3 of the device's OSes",
        "  --default  OS that boots with no key held (default: darkos if selected, else the first --os entry)",
        "  --plan     print the partition layout, key mapping and boot script, then exit (no root, no writes)",
        "The target defaults to /dev/sdb. ERASES THE WHOLE TARGET DEVICE.",
    ]
    return "\n".join(lines) + "\n"


def parse_args(argv: list) -> dict:
    opts = {"device": DEFAULT_DEVICE, "os": "", "default": "", "plan": False, "dev": "/dev/sdb"}
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
            print(usage(), end="")
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
        if key not in device_keys():
            die(f"unknown --device '{key}' (choose from: {' '.join(device_keys())})")
        d = self.device = load_device(key, IMGDIR)
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

        # Boot mode (which U-Boot runs, and what it reads from p1), from the device's
        # boot rules (first match wins):
        #   ini       G350: dArkOS's Hardkernel U-Boot 2017.09 runs boot.ini.
        #   scr-g350  G350 with ROCKNIX: ROCKNIX's mainline U-Boot 2025.10 runs boot.scr.
        #             (The Hardkernel U-Boot crashes ROCKNIX's mainline kernel.)
        #   scr-rgb30 RGB30: knulli's mainline U-Boot runs boot.scr.
        rule = next(r for r in d.boot if "if" not in r or self.sel(r["if"]))
        self.bootmode, self.uboot_os = rule["bootmode"], rule["uboot_os"]
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
        order = ("darkos", "amberelec", "arkos", "mustardos", "rocknix", "knulli")
        imgs += [self.device.images[o] for o in order
                 if self.sel(o) and self.device.images[o] not in imgs]
        # G350 ArkOS boots with dArkOS's G350 dtb, copied from the dArkOS image.
        if (self.sel("arkos") and self.device.opt("arkos", "dtb_from_darkos")
                and self.device.images["darkos"] not in imgs):
            imgs.append(self.device.images["darkos"])
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
        """MBR: disk ID + N in hex. GPT: a fixed GUID ending in N."""
        if self.device.gpt:
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
        self.pos = c.device.uboot_sectors[1]   # next free sector: right after U-Boot
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
    fixed, nlog = d.uboot_sectors[1], 0
    if c.sel("amberelec"):                         # AmberELEC's FAT partition holds p1's boot file
        fixed += AMB_BOOT_SIZE + AMB_STOR_SIZE
    else:
        fixed += BOOTSEL_SIZE
    if c.sel("darkos"):
        fixed += DRK_BOOT_SIZE + DRK_ROOT_SIZE
        nlog += 3
    if c.sel("arkos"):
        fixed += src["arkos"][0].size + ARK_ROOT_SIZE
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
    if not d.gpt:
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

    if d.gpt:
        lay = Layout(c, f"label: gpt\nlabel-id: {c.partuuid(0)}\nunit: sectors\n\n", 1, gap, rom_sizes)
    else:
        lay = Layout(c, f"label: dos\nlabel-id: 0x{DISKID}\nunit: sectors\n\n", 5, gap, rom_sizes)
    if d.layout not in LAYOUTS:
        die(f"unknown layout '{d.layout}' in src/devices/{d.key}.json (choose from: {' '.join(LAYOUTS)})")
    LAYOUTS[d.layout](c, lay)
    if lay.pos > end:
        die(f"layout overflows the card ({lay.pos} > {end} sectors)")
    return lay


def layout_g350(c: Choices, lay: Layout):
    """MBR. Logical partitions always start at p5, in the order knulli, dArkOS, ArkOS,
    ROCKNIX, MustardOS."""
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
    if c.sel("knulli"):
        # knulli-part takes /userdata as "boot partition + 1" and parses one digit only,
        # so knulli comes first: p5/p6.
        lay.logical(src["knulli"][0].size, "c", "FAT KNULLI: knulli boot + squashfs", "KNU_BOOT")
        lay.logical(lay.rom_size(), "7", "exFAT SHARE: knulli /userdata (roms)", "KNU_SHARE", rom=True)
    if c.sel("darkos"):
        # dArkOS mounts LABEL=EASYROMS, so its roms can live in a logical partition.
        lay.logical(lay.rom_size(), "7", "exFAT EASYROMS: dArkOS roms", "DRK_ROMS", rom=True)
        lay.logical(DRK_BOOT_SIZE, "c", "FAT BOOT: dArkOS /boot", "DRK_BOOT")
        lay.logical(DRK_ROOT_SIZE, "83", "btrfs ROOTFS: dArkOS root", "DRK_ROOT")
    if c.sel("arkos"):
        # ArkOS boots by PARTUUID and mounts by PARTUUID (fstab rewritten in ArkOS.prepare).
        lay.logical(src["arkos"][0].size, "c", f"FAT {ARK_BOOT_LABEL}: ArkOS /boot", "ARK_BOOT")
        lay.logical(ARK_ROOT_SIZE, "83", "ext4 root: ArkOS root", "ARK_ROOT")
        lay.logical(lay.rom_size(), "7", f"exFAT {ARK_ROMS_LABEL}: ArkOS roms", "ARK_ROMS", rom=True)
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
    n = lay.n
    if c.sel("knulli") and not (n["KNU_SHARE"] == n["KNU_BOOT"] + 1 <= 9):
        die("internal: knulli SHARE must be its boot partition + 1, at most p9")


def layout_rgb30(c: Choices, lay: Layout):
    """GPT. dArkOS's initramfs ignores root= and always mounts /dev/mmcblk1p4, and its
    scripts expect BOOT on p3 and roms on p5, so dArkOS gets p3/p4/p5 as in its image.
    knulli takes "its boot partition + 1" as /userdata (one digit only): p1/p2.
    ArkOS's initramfs is patched to follow root= (ArkOS.prepare), so it goes anywhere.
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

    def arkos_sys():
        lay.gpt(src["arkos"][0].size, T_DATA, ARK_BOOT_LABEL, f"FAT {ARK_BOOT_LABEL}: ArkOS /boot", "ARK_BOOT")
        lay.gpt(ARK_ROOT_SIZE, T_LINUX, "ARKROOT", "ext4 root: ArkOS root", "ARK_ROOT")

    def arkos_roms():
        lay.gpt(lay.rom_size(), T_DATA, ARK_ROMS_LABEL, f"exFAT {ARK_ROMS_LABEL}: ArkOS roms", "ARK_ROMS", rom=True)

    systems = {"rocknix": rocknix_sys, "arkos": arkos_sys}
    games = {"rocknix": rocknix_games, "arkos": arkos_roms}
    others = [o for o in ("rocknix", "arkos") if c.sel(o)]     # placed after the fixed ones
    if c.sel("darkos") and c.sel("knulli"):
        knulli(); darkos(); bootsel()
    elif c.sel("darkos"):
        # p2 is free before dArkOS's p3: the first other OS's ROM partition goes there.
        first = others.pop(0)
        bootsel(); games[first](); darkos(); systems[first]()
    elif c.sel("knulli"):
        knulli(); bootsel()
    else:
        bootsel()
    for o in others:
        systems[o](); games[o]()

    n = lay.n
    if c.sel("darkos") and (n["DRK_BOOT"], n["DRK_ROOT"], n["DRK_ROMS"]) != (3, 4, 5):
        die("internal: dArkOS must be p3/p4/p5")
    if c.sel("knulli") and (n["KNU_BOOT"], n["KNU_SHARE"]) != (1, 2):
        die("internal: knulli must be p1/p2")


# The layout functions by name ("layout" in src/devices/<device>.json). The partition
# order and the numbers each OS needs are code, not data: see the checks at their ends.
LAYOUTS = {"g350": layout_g350, "rgb30": layout_rgb30}


# ============================================================================ 4. per-OS code
# Each OS class holds everything about that OS:
#   prefix   partition roles are <prefix>_BOOT, <prefix>_ROOT, ...
#   copied   roles that receive the image's partitions (Device.src), in order
#   roms     (role, exFAT label, what) of its ROM partition
#   tools    commands its steps need (checked before the real run)
#   boot_ini where src/boot.ini.<os> goes on its BOOT partition (devices listing it in
#            "os_boot_ini")
#   fix_fs / prepare / verify   steps of the real run (copying and mkfs are generic)
# Its U-Boot commands, as they appear in the shared boot script, are in
# src/boot/<os>.<bootmode>.cmd (@BOOTX@ = BOOT number in hex, @ROOT@ / @ROOT_PU@ = root
# number / PARTUUID, @DTB@ = the device's dtb for it). If you change them, change
# src/boot.ini.<os> too where it exists.
BOOT_DIR = f"{HERE}/boot"


class OS:
    key = name = prefix = ""
    copied: tuple = ()
    roms: tuple = ()
    tools: tuple = ()
    boot_ini = ""

    def branch(self, c: Choices) -> str:
        path = f"{BOOT_DIR}/{self.key}.{c.bootmode}.cmd"
        if not os.path.isfile(path):
            die(f"{self.name} has no boot commands for boot mode {c.bootmode} ({path})")
        return fill(read(path), **self.boot_values(c))

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
    tools = ("e2fsck", "resize2fs", "debugfs", "dumpe2fs")

    def fix_fs(self, c):
        log("AmberELEC STORAGE: fsck + grow to 8G + remove .please_resize_me")
        grow_storage(c.part("AMB_STOR"))
        if c.bootmode != "ini":
            with mounted(c, c.part("AMB_STOR"), "ext4", "ambstor") as stor:   # /storage
                install_next(c, "amberelec", stor)

    def verify(self, c):
        check_label(c, "AMB_ROMS", "GAMES")
        if blkid(c.part("AMB_BOOT"), "UUID") != c.device.uuids["AMB_BOOT"]:
            die("AMBRELC UUID changed")
        if blkid(c.part("AMB_STOR"), "UUID") != c.device.uuids["AMB_STOR"]:
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
    tools = ("btrfs", "tar")
    boot_ini = "/boot/boot.ini"

    def prepare(self, c):
        """Do what dArkOS's first boot would do (it would repartition the card)."""
        with mounted(c, c.part("DRK_ROOT"), "btrfs", "root", "compress=zlib:1") as root, \
             mounted(c, c.part("DRK_BOOT"), "vfat", "boot") as boot, \
             mounted(c, c.part("DRK_ROMS"), "exfat", "roms") as roms:
            log(f"Growing btrfs ROOTFS to fill {c.part('DRK_ROOT')}")
            run(["btrfs", "filesystem", "resize", "max", root])

            unpack_roms(root, roms, "EASYROMS")
            # roms.tar is kept: expandtoexfat.sh keeps it too (G350 and RGB30)

            log("Finishing dArkOS first-boot steps (fstab, disable firstboot.service)")
            # On the RGB30 dArkOS is on p3/p4/p5 as in its image, so its fstab.exfat
            # (/dev/mmcblk1p3/p4/p5) is used as is, like on the G350.
            shutil.copyfile(f"{boot}/fstab.exfat", f"{root}/etc/fstab")
            if "darkos" in c.device.dtb:               # loaded by the boot script itself
                for f in ("Image", "uInitrd", c.device.dtb["darkos"]):
                    need_file(f"{boot}/{f}")
            rm_f(f"{root}/etc/systemd/system/multi-user.target.wants/firstboot.service",
                 f"{boot}/firstboot.sh", f"{boot}/expandtoexfat.sh", f"{boot}/fstab.exfat",
                 *glob.glob(f"{boot}/doneit*"))
            if self.key in c.device.os_boot_ini:
                write(f"{boot}{self.boot_ini}", render(c, self.key))
            print("--- dArkOS /etc/fstab:")
            print(read(f"{root}/etc/fstab"), end="")
            if c.bootmode != "ini":
                install_next(c, "darkos", root)

    def verify(self, c):
        check_label(c, "DRK_ROMS", "EASYROMS")
        check_partuuid(c, "DRK_ROOT")
        if c.device.gpt:
            check_partuuid(c, "DRK_BOOT")


def unpack_roms(root: str, roms: str, label: str):
    """dArkOS / ArkOS first boot (expandtoexfat.sh): /roms.tar and /tempthemes into roms."""
    log(f"Extracting /roms.tar into {label} (as expandtoexfat.sh does)")
    tarball = f"{root}/roms.tar"
    names = output(["tar", "-tf", tarball]).splitlines()
    bad = sum(1 for n in names if not re.match(r"roms(/|$)", n))
    if bad:
        die(f"roms.tar has {bad} entries outside roms/")
    r = run(["tar", "--no-same-permissions", "--no-same-owner", "-xf", tarball,
             "-C", roms, "--strip-components=1"], check=False)
    if r.returncode:
        print("WARN: tar reported errors (expected for symlinks/ownership on exFAT)")

    log(f"Installing themes from /tempthemes into {label}/themes")
    shutil.rmtree(f"{roms}/themes/es-theme-nes-box", ignore_errors=True)
    os.makedirs(f"{roms}/themes", exist_ok=True)
    if not tar_copy(f"{root}/tempthemes", f"{roms}/themes"):
        print("WARN: theme copy reported errors (expected for symlinks on exFAT)")
    shutil.rmtree(f"{root}/tempthemes", ignore_errors=True)


class ArkOS(OS):
    """dArkOS's parent. Boots by PARTUUID; its fstab (/dev/mmcblk0pN on the G350,
    mmcblk1pN on the RGB30) is rewritten to PARTUUIDs, and its BOOT/ROMS labels are
    changed so they don't clash with dArkOS's. The RGB30 initramfs hard-codes
    /dev/mmcblk1p4 as root (like dArkOS's); it is patched to follow root=."""
    key, name, prefix = "arkos", "ArkOS", "ARK"
    copied = ("ARK_BOOT", "ARK_ROOT")
    roms = ("ARK_ROMS", ARK_ROMS_LABEL, "ArkOS roms")
    tools = ("e2fsck", "resize2fs", "fatlabel", "tar")
    boot_ini = "/boot/boot.ini"
    INITRD_OLD = b'local dev_id="/dev/mmcblk1p4"'     # scripts/local in the RGB30 uInitrd
    INITRD_NEW = b'local dev_id="$1"'.ljust(len(INITRD_OLD))   # same length: cpio sizes stay valid

    def fix_fs(self, c):
        log(f"ArkOS root: fsck + grow to {hsize(ARK_ROOT_SIZE)}; BOOT label -> {ARK_BOOT_LABEL}")
        fsck(c.part("ARK_ROOT"))
        run(["resize2fs", c.part("ARK_ROOT")])
        run(["fatlabel", c.part("ARK_BOOT"), ARK_BOOT_LABEL])

    def fstab(self, c, image_fstab: str) -> str:
        """The image's fstab.exfat with PARTUUIDs for /, /boot and /roms."""
        roles = {"/": "ARK_ROOT", "/boot": "ARK_BOOT", "/roms": "ARK_ROMS"}
        out, seen = [], set()
        for line in image_fstab.splitlines():
            f = line.split()
            if len(f) >= 2 and not f[0].startswith("#") and f[1] in roles:
                seen.add(f[1])
                line = f"PARTUUID={c.partuuid(c.n(roles[f[1]]))} " + " ".join(f[1:])
            out.append(line)
        if seen != set(roles):
            die(f"ArkOS fstab.exfat lacks {' '.join(sorted(set(roles) - seen))}")
        return "\n".join(out) + "\n"

    def patch_initrd(self, path: str):
        with open(path, "rb") as f:
            data = gzip.decompress(f.read())
        if data.count(self.INITRD_OLD) != 1:
            die(f"{self.INITRD_OLD.decode()} not found once in ArkOS uInitrd (image changed?)")
        data = data.replace(self.INITRD_OLD, self.INITRD_NEW)
        shutil.copyfile(path, f"{path}.orig")
        with open(path, "wb") as f:
            f.write(gzip.compress(data, mtime=0))

    def prepare(self, c):
        """Do what ArkOS's first boot would do (it would repartition the card)."""
        with mounted(c, c.part("ARK_ROOT"), "ext4", "arkroot") as root, \
             mounted(c, c.part("ARK_BOOT"), "vfat", "arkboot") as boot, \
             mounted(c, c.part("ARK_ROMS"), "exfat", "arkroms") as roms:
            if c.device.opt("arkos", "dtb_from_darkos"):
                log(f"ArkOS: copying {c.device.dtb['arkos']} from the dArkOS image ({c.device.name} panel)")
                copy_from_darkos_boot(c, c.device.dtb["arkos"], boot)
            for f in ("Image", "uInitrd", c.device.dtb["arkos"], "fstab.exfat"):
                need_file(f"{boot}/{f}")
            unpack_roms(root, roms, ARK_ROMS_LABEL)
            # roms.tar is kept: expandtoexfat.sh keeps it too on the RG351MP and RK3566

            log("Finishing ArkOS first-boot steps (fstab by PARTUUID, disable firstboot.service)")
            write(f"{root}/etc/fstab", self.fstab(c, read(f"{boot}/fstab.exfat")))
            aspect = c.device.opt("arkos", "aspect_ratio_index")
            if aspect:
                # firstboot.sh's own fix (RK3566), done before it calls expandtoexfat.sh
                for cfg in ("retroarch.cfg", "retroarch.cfg.bak"):
                    p = f"{root}/home/ark/.config/retroarch/{cfg}"
                    if os.path.isfile(p):
                        text = re.sub(r'(?m)^aspect_ratio_index = ".*$', f'aspect_ratio_index = "{aspect}"', read(p))
                        write(p, text)
                        os.chown(p, 1002, 1002)              # ark:ark
            if c.device.opt("arkos", "patch_initrd"):
                log("ArkOS: patching uInitrd to follow root= (it hard-codes /dev/mmcblk1p4)")
                self.patch_initrd(f"{boot}/uInitrd")
            rm_f(f"{root}/etc/systemd/system/multi-user.target.wants/firstboot.service",
                 f"{boot}/firstboot.sh", f"{boot}/expandtoexfat.sh", f"{boot}/fstab.exfat",
                 *glob.glob(f"{boot}/doneit*"))
            if self.key in c.device.os_boot_ini:
                shutil.copyfile(f"{boot}{self.boot_ini}", f"{boot}{self.boot_ini}.orig")
                write(f"{boot}{self.boot_ini}", render(c, self.key))
            print("--- ArkOS /etc/fstab:")
            print(read(f"{root}/etc/fstab"), end="")
            if c.bootmode != "ini":
                install_next(c, "arkos", root)

    def verify(self, c):
        check_label(c, "ARK_BOOT", ARK_BOOT_LABEL)
        check_label(c, "ARK_ROMS", ARK_ROMS_LABEL)
        check_partuuid(c, "ARK_ROOT")
        check_partuuid(c, "ARK_BOOT")
        check_partuuid(c, "ARK_ROMS")


class MustardOS(OS):
    """Finds its partitions through /opt/muos/device/config/storage/*/num on its rootfs.
    Its ROMS must be the last partition (its factory reset resizes it to 100%)."""
    key, name, prefix = "mustardos", "MustardOS", "MUS"
    copied = ("MUS_BOOT", "MUS_ROOT")
    roms = ("MUS_ROM", "ROMS", "MustardOS roms")      # MustardOS reformats it on first boot
    tools = ("e2fsck",)
    boot_ini = "/boot.ini"
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

            if self.key in c.device.os_boot_ini:           # reference only; U-Boot reads p1
                shutil.copyfile(f"{boot}{self.boot_ini}", f"{boot}{self.boot_ini}.orig")
                write(f"{boot}{self.boot_ini}", render(c, self.key))
            if c.bootmode != "ini":
                install_next(c, "mustardos", root)

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
    """knulli-part takes /userdata as boot partition + 1 (one digit): p1/p2 on the RGB30,
    p5/p6 on the G350. The G350 image has a vendor 4.4 kernel (uImage, bootm)."""
    key, name, prefix = "knulli", "knulli", "KNU"
    copied = ("KNU_BOOT",)
    roms = ("KNU_SHARE", "SHARE", "knulli /userdata")   # what knulli's autoresize would leave
    # G350: vibrates from ~3 s after power-on until knulli's logo (its S01pwm stops the
    # rumble motor). Tried from boot.ini, all on a G350: "gpio set 15" (no change), PWM0
    # at 100% duty (no change), that plus pin mux to PWM0 and PWM0 disabled in its dtb
    # (both: vibration that never stops). Left as is.
    # G350: knulli's boot.ini loads boot/linux (a uImage that bootm copies to 0x01080000)
    # at 0x02000000; that overlaps its copy, which mainline U-Boot refuses, so the
    # uImage and uInitrd are loaded higher up.

    def prepare(self, c):
        log(f"knulli: disable autoresize (it would resize p{c.n('KNU_SHARE')} to 100% of the card)")
        with mounted(c, c.part("KNU_BOOT"), "vfat", "knboot") as boot:
            conf = f"{boot}/knulli-boot.conf"
            need_file(conf)
            initrd = c.device.opt("knulli", "initrd")
            for f in ("boot/linux", initrd, c.device.dtb["knulli"]):
                need_file(f"{boot}/{f}")
            shutil.copyfile(conf, f"{conf}.orig")
            text = re.sub(r"(?m)^[ \t\v\f\r]*autoresize[ \t\v\f\r]*=.*$", "autoresize=false", read(conf))
            write(conf, text)
            if re.search(r"(?m)^ *autoresize *= *true *$", text):
                die("autoresize still enabled in knulli-boot.conf")
            if re.search(r"(?m)^ *format-internal *=", text):
                die("format-internal is set in knulli-boot.conf")
            print("\n".join(grep_n(conf, "autoresize")))
        if c.bootmode != "ini":
            with mounted(c, c.part("KNU_SHARE"), "exfat", "knshare") as share:   # /userdata
                install_next(c, "knulli", share)

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
    tools = ("e2fsck", "resize2fs", "debugfs", "unsquashfs")

    def boot_values(self, c):
        u = c.device.uuids
        return {**super().boot_values(c), "RNX_BOOT_UUID": u.get("RNX_BOOT", ""),
                "RNX_STOR_UUID": u.get("RNX_STOR", "")}

    def extlinux(self, c) -> str:
        """G350: /extlinux/extlinux.conf.dualboot on ROCKNIX's FAT partition."""
        return fill(read(f"{BOOT_DIR}/rocknix.extlinux.conf"), **self.boot_values(c))

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
            install_next(c, "rocknix", stor)                 # /storage

    def verify(self, c):
        check_label(c, "RNX_BOOT", "ROCKNIX")
        check_label(c, "RNX_STOR", "STORAGE")
        check_label(c, "RNX_ROMS", "GAMES")
        check_partuuid(c, "RNX_ROMS")
        boot_uuid = c.device.uuids.get("RNX_BOOT")
        if boot_uuid:                                    # the device boots it by UUID
            if blkid(c.part("RNX_BOOT"), "UUID") != boot_uuid:
                die("ROCKNIX boot UUID changed")
            if blkid(c.part("RNX_STOR"), "UUID") != c.device.uuids["RNX_STOR"]:
                die("ROCKNIX STORAGE UUID changed")
            uuids = [blkid(c.path(n), "UUID") for n in c.lay.numbered()]
            if uuids.count(boot_uuid) != 1:
                die(f"UUID {boot_uuid} is used twice")


ALL_OSES = [AmberELEC(), DarkOS(), ArkOS(), MustardOS(), Knulli(), Rocknix()]
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


# One-shot "boot OS next" (boot.scr modes only; boot.ini mode would need fatwrite):
# multiboot-next writes next=<os> to NEXT_FILE on the boot selector partition, and
# boot.scr reads it, clears it (fatwrite "next=" + newline) and boots that OS unless
# a key is held.
NEXT_FILE = "nextboot.txt"
NEXT_PATHS = {  # where multiboot-next is installed on each OS: (mount, path under it)
    "amberelec": ("/storage", "/bin/multiboot-next"),
    "darkos": ("", "/usr/local/bin/multiboot-next"),
    "arkos": ("", "/usr/local/bin/multiboot-next"),
    "mustardos": ("", "/usr/local/bin/multiboot-next"),
    "knulli": ("/userdata", "/multiboot-next"),
    "rocknix": ("/storage", "/bin/multiboot-next"),
}


def next_path(os_key: str) -> str:
    return "".join(NEXT_PATHS[os_key])


def next_helper(c: Choices) -> str:
    """src/multiboot-next with this card's values."""
    bootsel = c.n("BOOTSEL")
    path = f"{HERE}/multiboot-next"
    if not os.path.isfile(path):
        die(f"missing {path}")
    return fill(read(path), DEVICE=c.device.name, OSES=" ".join(c.user_os),
                PARTUUID=c.partuuid(bootsel), PARTNUM=bootsel,
                DISKID_AT=568 if c.device.gpt else 440,   # GPT: disk GUID in the header
                DISKID_HEX=bytes.fromhex(DISKID)[::-1].hex())          # as stored (little endian)


def install_next(c: Choices, os_key: str, mountpoint: str):
    """Write multiboot-next onto an OS partition mounted at mountpoint."""
    dst = mountpoint + NEXT_PATHS[os_key][1]
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    write(dst, next_helper(c))
    try:
        os.chmod(dst, 0o755)
    except OSError:
        pass                    # exFAT (knulli /userdata) has no modes: run it with sh
    print(f"--- multiboot-next for {osname(os_key)}: {next_path(os_key)}")


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
    if c.bootmode != "ini":                  # one-shot request from multiboot-next (no key held)
        for os_key in c.key_os:
            text += f'elif test "${{next}}" = "{os_key}"; then\n' + OS_BY_KEY[os_key].branch(c)
    return text + "else\n" + OS_BY_KEY[c.default].branch(c) + "fi\n"


def next_block(c: Choices) -> str:
    """boot.scr: read and clear a one-shot request into ${next}."""
    b = hx(c.n("BOOTSEL"))
    return f"""\
# One-shot boot request: multiboot-next (installed on each OS) writes next=<os> to
# {NEXT_FILE} on this partition. It is read and cleared here, before booting; a held
# key still wins. The file is only rewritten when it held a request.
setenv next
if load ${{devtype}} ${{devnum}}:{b} 0x30001000 {NEXT_FILE}; then
  env import -t 0x30001000 ${{filesize}} next
  if test -n "${{next}}"; then
    mw.b 0x30002000 0x6e 1
    mw.b 0x30002001 0x65 1
    mw.b 0x30002002 0x78 1
    mw.b 0x30002003 0x74 1
    mw.b 0x30002004 0x3d 1
    mw.b 0x30002005 0x0a 1
    fatwrite ${{devtype}} ${{devnum}}:{b} 0x30002000 {NEXT_FILE} 6
  fi
fi

"""


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


def keys_block(c: Choices) -> str:
    """boot.scr: read the keys into ${key} (src/boot/keys.<bootmode>.cmd).
    scr-g350: ROCKNIX's mainline U-Boot reads the GPIO register with setexpr.
    scr-rgb30: knulli's U-Boot (no gpio, no setexpr) compares register bytes copied to RAM."""
    path = f"{BOOT_DIR}/keys.{c.bootmode}.cmd"
    if not os.path.isfile(path):
        die(f"missing {path}")
    return "\n" + read(path) + "\n"


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
    return "\n".join(lines) + "\n" + keys_block(c) + next_block(c) + boot_branches(c)




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
    return "\n".join(lines) + "\n" + keys_block(c) + next_block(c) + boot_branches(c)


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
    untested = [osname(o) for o in c.device.untested.get(c.bootmode, ()) if c.sel(o)]
    if untested:
        uboot = " with the ROCKNIX U-Boot" if c.bootmode == "scr-g350" else ""
        print(f"WARNING:  not tested on a {c.device.name}{uboot} yet: {', '.join(untested)}.")
    print(f"Keys:     no key = {osname(c.default)}")
    for i, os_key in enumerate(c.key_os):
        print(f"          {KEY_NAMES[i]} (pin {c.device.key_pins[i]}) = {osname(os_key)}")
    if c.bootmode != "ini":
        print(f"Next:     multiboot-next OS boots OS once ({NEXT_FILE} on p{c.n('BOOTSEL')}; a held key wins)")
        for os_key in c.user_os:
            print(f"          {osname(os_key)}: {next_path(os_key)}")
    print(f"Partitions on {c.dev} ({hsize(c.total)}):")
    for p in c.lay.parts:
        print(f"  {p.line()}")
    n = c.lay.n
    first, *more = c.device.numbers           # later groups only when their first role exists
    roles = list(first) + [r for g in more if g[0] in n for r in g]
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
    for os_key in c.device.os_boot_ini:
        if c.sel(os_key):
            o = OS_BY_KEY[os_key]
            print(f"\n---- {o.name} {o.boot_ini} (p{c.n(o.prefix + '_BOOT')})")
            print(render(c, os_key), end="")
    if c.bootmode != "ini":
        print("\n---- multiboot-next (installed on each OS)")
        print(next_helper(c), end="")


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
    for o in c.device.os_boot_ini:
        if c.sel(o) and not os.path.isfile(f"{HERE}/boot.ini.{o}"):
            die(f"missing {HERE}/boot.ini.{o}")
    if c.bootmode != "ini" and not os.path.isfile(f"{HERE}/multiboot-next"):
        die(f"missing {HERE}/multiboot-next")

    tools = ["sfdisk", "wipefs", "mkfs.exfat", "blkid", "partprobe"]
    if not c.sel("amberelec"):
        tools += ["mkfs.vfat"]                     # BOOTSEL
    if c.bootmode != "ini":
        tools += ["mkimage"]                       # boot.cmd -> boot.scr
    for o in c.oses:
        tools += o.tools
    for t in dict.fromkeys(tools):
        if not shutil.which(t):
            die(f"missing tool: {t}")

    # Guard against the wrong disk: a whole removable disk (USB reader) or an SD slot
    # (mmcblk), 32GB-2TiB (MBR limit). The layout itself scales to the card size and
    # refuses cards too small for the selected OSes (make_layout).
    name = os.path.basename(os.path.realpath(c.dev))
    sysfs = f"/sys/block/{name}"
    if not os.path.isdir(sysfs):
        die(f"{c.dev} is not a whole disk (give the disk, e.g. /dev/sdb, not a partition)")
    with open(f"{sysfs}/removable") as f:
        removable = f.read().strip() == "1"
    if not (removable or name.startswith("mmcblk")):
        die(f"{c.dev} is not a removable disk or SD card slot")
    size = int(output(["blockdev", "--getsize64", c.dev]))
    if not 32 * 1000**3 <= size < 2 * 1024**4:
        die(f"{c.dev} is {size} bytes, expected an SD card of 32GB to 2TiB")
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
    table = c.device.table.upper()
    log(f"Writing {table} (label-id {lay.sfdisk.splitlines()[1].split()[1]})")
    run(["sfdisk", "--wipe", "always", "--wipe-partitions", "always", dev], input=lay.sfdisk, text=True)
    if run(["partprobe", dev], check=False, stderr=subprocess.DEVNULL).returncode:
        run(["blockdev", "--rereadpt", dev], check=False)
    unmount_dev(dev)
    for num in lay.numbered():
        if not is_block(c.path(num)):
            die(f"{c.path(num)} missing after partitioning")

    # 2. U-Boot
    first, end = c.device.uboot_sectors
    log(f"Writing U-Boot (sectors {first}-{end - 1}) from {os.path.basename(c.uboot_img)}")
    run(["dd", f"if={c.uboot_img}", f"of={dev}", f"bs={S}", f"skip={first}", f"seek={first}",
         f"count={end - first}", "conv=notrunc,fsync", "status=none"])

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
    labels = [blkid(c.path(n), "LABEL") for n in lay.numbered()]
    for label in c.device.unique_labels:
        if labels.count(label) > 1:
            die(f"label {label} is used twice")

    run(["sync"])
    if run(["eject", dev], check=False, stderr=subprocess.DEVNULL).returncode:
        run(["udisksctl", "power-off", "-b", dev], check=False, stderr=subprocess.DEVNULL)
    log("Done. Card is safe to remove.")
    show_plan(c)


def copy_from_darkos_boot(c: Choices, name: str, dst_dir: str):
    """Copy a file from the BOOT partition inside the dArkOS image (G350)."""
    img, src = c.device.images["darkos"], c.device.src["darkos"][0]
    loop_opts = f"ro,loop,offset={src.start * S},sizelimit={src.size * S}"
    with mounted(c, img, "vfat", "drkimg", loop_opts) as drk:
        need_file(f"{drk}/{name}")
        shutil.copyfile(f"{drk}/{name}", f"{dst_dir}/{name}")


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
            copy_from_darkos_boot(c, "logo.bmp", p1)
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
