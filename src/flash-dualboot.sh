#!/bin/bash
# Build a multi-boot SD card for the G350 (RK3326) or the RGB30 (RK3566).
# Usage: sudo bash src/flash-dualboot.sh [--device g350|rgb30] --os LIST [--default OS] [--plan] [/dev/sdX]
#   --device   g350 (default): amberelec, darkos, mustardos, rocknix;  rgb30: darkos, knulli, rocknix
#              Always needed (U-Boot source, even if not in --os): g350 the dArkOS image
#              (the ROCKNIX image instead when rocknix is selected), rgb30 the knulli image
#   --os       required: comma list naming 2 or 3 of the device's OSes
#   --default  OS that boots with no key held (default: darkos if selected, else the first --os entry)
#   --plan     print the partition layout, key mapping and boot script, then exit (no root, no writes)
# The target defaults to /dev/sdb. ERASES THE WHOLE TARGET DEVICE.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
IMGDIR=${IMGDIR:-$(dirname "$HERE")/img}   # override: sudo IMGDIR=/path bash ...
DISKID=0x5a3c9e71            # root=PARTUUID=5a3c9e71-NN
S=512

log()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

usage() { sed -n '2,10s/^# \{0,1\}//p' "$0"; }

# ---------------------------------------------------------------- arguments
DEVICE=g350
OS_ARG=
DEFAULT=
PLAN=0
DEV=/dev/sdb
while (($#)); do
  case $1 in
    --device)    [[ $# -ge 2 ]] || die "--device needs a value"; DEVICE=$2; shift 2 ;;
    --device=*)  DEVICE=${1#*=}; shift ;;
    --os)        [[ $# -ge 2 ]] || die "--os needs a value"; OS_ARG=$2; shift 2 ;;
    --os=*)      OS_ARG=${1#*=}; shift ;;
    --default)   [[ $# -ge 2 ]] || die "--default needs a value"; DEFAULT=$2; shift 2 ;;
    --default=*) DEFAULT=${1#*=}; shift ;;
    --plan)      PLAN=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    -*)          die "unknown option: $1 (see --help)" ;;
    *)           DEV=$1; shift ;;
  esac
done
DEVICE=${DEVICE,,}

# ---------------------------------------------------------------- per-device settings
# Image file names and the source partitions inside them (start, size in 512-byte
# sectors; from sfdisk -d) are tied to specific image releases.
case $DEVICE in
  g350)
    ALL_OS=(amberelec darkos mustardos rocknix)   # fixed order: also the order keys are handed out
    KEY_PIN=(65 64)                       # Vol-, Vol+ (GPIO2_A1, GPIO2_A0)
    AMB=$IMGDIR/AmberELEC-RG351MP.aarch64-prerelease-20261019_1835.img
    DARK=$IMGDIR/dArkOS_G350_trixie_01012026.img     # always needed: U-Boot + logo.bmp come from it
    MUS=$IMGDIR/MustardOS_RK-G350-V_2606.0_ANDROMEDA-db2a3bd0.img
    RNX=$IMGDIR/ROCKNIX-RK3326.aarch64-20261101-b.img
    UBOOT_IMG=$DARK; UBOOT_OS=darkos
    AMB_P1_START=32768;   AMB_P1_SIZE=4194304     # FAT AMBRELC 2G, UUID 1910-5621
    AMB_P2_START=4227072; AMB_P2_SIZE=65536       # ext4 STORAGE 32M, UUID 7ab6cd9f-...
    DRK_P1_START=32768;   DRK_P1_SIZE=204800      # FAT BOOT 100M
    DRK_P2_START=237568;  DRK_P2_SIZE=15360000    # btrfs ROOTFS 7.3G
    MUS_P1_START=32768;   MUS_P1_SIZE=1048576     # FAT BOOT 512M (GPT)
    MUS_P2_START=1081344; MUS_P2_SIZE=16777216    # ext4 ROOTFS 8G
    RNX_P1_START=32768;   RNX_P1_SIZE=4194304     # FAT ROCKNIX 2G, UUID 0111-5938
    RNX_P2_START=4227072; RNX_P2_SIZE=65536       # ext4 STORAGE 32M, UUID b4434352-...
    # AmberELEC's ext4 is labelled STORAGE too, so ROCKNIX boots by filesystem UUID.
    RNX_BOOT_UUID=0111-5938
    RNX_STOR_UUID=b4434352-b41f-4bef-a8d9-d81a710453da
    RNX_DTB=rk3326-batlexp-g350.dtb               # from extlinux/extlinux.conf.g350
    ;;
  rgb30)
    ALL_OS=(darkos knulli rocknix)        # fixed order: also the order keys are handed out
    KEY_PIN=(104 103)                     # Vol-, Vol+ (GPIO3_B0, GPIO3_A7)
    DARK=$IMGDIR/dArkOS_RGB30_trixie_08282026.img
    KNU=$IMGDIR/knulli-rk3568-powkiddy-rgb30-scarab-20260510.img   # always needed: U-Boot comes from it
    RNX=$IMGDIR/ROCKNIX-RK3566.aarch64-20261101-Generic.img
    UBOOT_IMG=$KNU; UBOOT_OS=knulli
    DRK_P1_START=32768;   DRK_P1_SIZE=202752      # image p3: FAT dArkOS_Fat 99M (GPT)
    DRK_P2_START=237568;  DRK_P2_SIZE=15208047    # image p4: btrfs ROOTFS 7.3G
    KNU_P1_START=32768;   KNU_P1_SIZE=8388608     # FAT KNULLI 4G (GPT); p2 SHARE is recreated
    RNX_P1_START=32768;   RNX_P1_SIZE=4194304     # FAT ROCKNIX 2G (GPT)
    RNX_P2_START=4227072; RNX_P2_SIZE=65536       # ext4 STORAGE 32M
    DRK_DTB=rk3566-rgb30.dtb                      # from each OS's extlinux.conf
    KNU_DTB=boot/rk3566-rgb30-linux.dtb           # (RGB30 v2 panel: boot/rk3566-rgb30-v2-linux.dtb)
    RNX_DTB=device_trees/rk3566-powkiddy-rgb30.dtb
    ;;
  *) die "unknown --device '$DEVICE' (choose from: g350 rgb30)" ;;
esac
KEY_NAME=(Vol- Vol+)
[[ -n $OS_ARG ]] || die "--os is required: name 2 or 3 of: ${ALL_OS[*]} (see --help)"

USER_OS=()                                  # in the order given, deduplicated
IFS=, read -ra _list <<<"${OS_ARG,,}"
for o in "${_list[@]}"; do
  o=${o//[[:space:]]/}
  [[ -z $o ]] && continue
  [[ " ${ALL_OS[*]} " == *" $o "* ]] || die "unknown OS '$o' for $DEVICE (choose from: ${ALL_OS[*]})"
  [[ " ${USER_OS[*]} " == *" $o "* ]] || USER_OS+=("$o")
done
(( ${#USER_OS[@]} >= 2 )) || die "--os needs at least 2 of: ${ALL_OS[*]}"
(( ${#USER_OS[@]} <= ${#KEY_PIN[@]} + 1 )) || die "--os takes at most $(( ${#KEY_PIN[@]} + 1 )) OSes (no key, Vol-, Vol+)"
sel() { [[ " ${USER_OS[*]} " == *" $1 "* ]]; }

DEFAULT=${DEFAULT,,}
if [[ -z $DEFAULT ]]; then
  if sel darkos; then DEFAULT=darkos; else DEFAULT=${USER_OS[0]}; fi
fi
sel "$DEFAULT" || die "--default $DEFAULT is not one of the selected OSes (${USER_OS[*]})"

osname() {
  case $1 in
    amberelec) echo AmberELEC ;; darkos) echo dArkOS ;; mustardos) echo MustardOS ;;
    knulli) echo knulli ;; rocknix) echo ROCKNIX ;;
  esac
}
devname() { case $DEVICE in g350) echo G350 ;; rgb30) echo RGB30 ;; esac; }

# Boot mode:
#   ini       G350: dArkOS's Hardkernel U-Boot 2017.09 runs boot.ini from p1.
#   scr-g350  G350 with ROCKNIX: ROCKNIX's mainline U-Boot 2025.10 runs boot.scr from p1.
#             (The Hardkernel U-Boot crashes ROCKNIX's mainline kernel.)
#   scr-rgb30 RGB30: knulli's mainline U-Boot runs boot.scr from p1.
if [[ $DEVICE == rgb30 ]]; then BOOTMODE=scr-rgb30
elif sel rocknix; then BOOTMODE=scr-g350; UBOOT_IMG=$RNX; UBOOT_OS=rocknix
else BOOTMODE=ini
fi

# Keys: default = no key; the others, in ALL_OS order, get Vol- then Vol+.
KEY_OS=()
for o in "${ALL_OS[@]}"; do sel "$o" && [[ $o != "$DEFAULT" ]] && KEY_OS+=("$o"); done

# ---------------------------------------------------------------- device helpers
p()    { echo "${DEV}$1"; }        # /dev/sdb + N  (use ${DEV}p for mmcblk/nvme/loop)
[[ $DEV == *mmcblk* || $DEV == *nvme* || $DEV == *loop* ]] && p() { echo "${DEV}p$1"; }

unmount_dev() {
  udevadm settle || true
  local m
  for m in $(awk -v d="$DEV" 'index($1,d)==1 {print $2}' /proc/mounts | sort -r); do
    umount "$m" || umount -l "$m"
  done
}

# ---------------------------------------------------------------- sanity
IMAGES=("$UBOOT_IMG")
if [[ $DEVICE == g350 ]]; then
  [[ $UBOOT_IMG != "$DARK" ]] && sel darkos && IMAGES+=("$DARK")
  sel amberelec && IMAGES+=("$AMB")
  sel mustardos && IMAGES+=("$MUS")
  [[ $UBOOT_IMG != "$RNX" ]] && sel rocknix && IMAGES+=("$RNX")
else
  sel darkos  && IMAGES+=("$DARK")
  sel rocknix && IMAGES+=("$RNX")
fi
if (( PLAN )); then
  TOTAL=$(blockdev --getsz "$DEV" 2>/dev/null) || TOTAL=
  if [[ -z $TOTAL ]]; then
    TOTAL=122070312                          # ~62.5GB card
    echo "(plan: $DEV not readable, assuming a $TOTAL-sector / 62.5GB card)"
  fi
  for f in "${IMAGES[@]}"; do
    [[ -f $f ]] || echo "(plan: WARNING image not found: $f)"
    [[ -f $f || $f != "$UBOOT_IMG" ]] || echo "(plan: that image is always needed on the $(devname): U-Boot comes from it)"
  done
else
  [[ $EUID -eq 0 ]] || die "run as root (sudo)"
  [[ -b $DEV ]] || die "$DEV is not a block device"
  [[ -f $UBOOT_IMG ]] || die "image not found: $UBOOT_IMG (always needed on the $(devname): U-Boot comes from it, even without $UBOOT_OS in --os)"
  for f in "${IMAGES[@]}"; do [[ -f $f ]] || die "image not found: $f"; done
  if [[ $DEVICE == g350 ]]; then
    for f in $(sel darkos && echo boot.ini.darkos) $(sel mustardos && echo boot.ini.mustardos); do
      [[ -f $HERE/$f ]] || die "missing $HERE/$f"
    done
  fi
  [[ $BOOTMODE == ini || -f $HERE/multiboot-next ]] || die "missing $HERE/multiboot-next"
  tools=(sfdisk wipefs mkfs.exfat blkid partprobe)
  if [[ $DEVICE == g350 ]]; then
    sel amberelec && tools+=(e2fsck resize2fs debugfs dumpe2fs)
    sel amberelec || tools+=(mkfs.vfat)
    sel mustardos && tools+=(e2fsck)
    [[ $BOOTMODE == scr-g350 ]] && tools+=(mkimage)
  else
    tools+=(mkfs.vfat mkimage)
  fi
  sel rocknix && tools+=(e2fsck resize2fs debugfs unsquashfs)
  sel darkos && tools+=(btrfs tar)
  for t in "${tools[@]}"; do command -v "$t" >/dev/null || die "missing tool: $t"; done
  SIZE=$(blockdev --getsize64 "$DEV")
  (( SIZE > 55*1000**3 && SIZE < 66*1000**3 )) || die "$DEV is $SIZE bytes, expected the ~62.5GB (58.2GiB) card"
  findmnt -rno SOURCE / | grep -q "^$DEV" && die "$DEV holds the running root fs!"
  TOTAL=$(blockdev --getsz "$DEV")
fi

# ---------------------------------------------------------------- layout
MIB=2048; GIB=$((1024*MIB)); GAP=2048                     # sectors; GAP before each logical
P1_START=32768
AMB_BOOT_SIZE=$((2*GIB)); AMB_STOR_SIZE=$((8*GIB))
RNX_STOR_SIZE=$((8*GIB))
BOOTSEL_SIZE=$((64*MIB))
DRK_BOOT_SIZE=$((100*MIB)); DRK_ROOT_SIZE=$((12*GIB))
# MUS_P1_SIZE (512M), MUS_P2_SIZE (8G), KNU_P1_SIZE (4G) and RNX_P1_SIZE (2G) are copied 1:1

# Space left for ROM partitions, split equally between the selected OSes.
fixed=$P1_START; nlog=0
if [[ $DEVICE == g350 ]] && sel amberelec; then fixed=$((fixed+AMB_BOOT_SIZE+AMB_STOR_SIZE)); else fixed=$((fixed+BOOTSEL_SIZE)); fi
if sel darkos;    then fixed=$((fixed+DRK_BOOT_SIZE+DRK_ROOT_SIZE)); nlog=$((nlog+3)); fi
if sel mustardos; then fixed=$((fixed+MUS_P1_SIZE+MUS_P2_SIZE));     nlog=$((nlog+3)); fi
if sel knulli;    then fixed=$((fixed+KNU_P1_SIZE));                 nlog=$((nlog+2)); fi
if sel rocknix;   then fixed=$((fixed+RNX_P1_SIZE+RNX_STOR_SIZE));   nlog=$((nlog+3)); fi
END=$TOTAL
if [[ $DEVICE == rgb30 ]]; then
  GAP=0                                   # GPT: no extended partition, no gaps
  END=$(( (TOTAL-34) / MIB * MIB ))       # keep the last 33 sectors for the backup GPT
fi
fixed=$((fixed+nlog*GAP))
FREE=$((END-fixed))
NROM=${#USER_OS[@]}
ROM_SIZE=$(( FREE / NROM / MIB * MIB ))
LAST_ROM_SIZE=$(( (FREE - (NROM-1)*ROM_SIZE) / MIB * MIB ))   # last ROM partition takes the remainder
(( ROM_SIZE > 2*GIB )) || die "card too small ($TOTAL sectors)"

pu() {  # PARTUUID of partition N: MBR disk ID + N; on the RGB30 (GPT) a fixed GUID
  if [[ $DEVICE == rgb30 ]]; then printf '%s-0000-4000-8000-%012x' "${DISKID#0x}" "$1"
  else printf '%s-%02x' "${DISKID#0x}" "$1"; fi
}
hx() { printf '%x' "$1"; }                          # U-Boot parses "dev:part" as hex

hsize() { awk -v s="$1" 'BEGIN { if (s >= 1024*1024*2) printf "%.1fG", s/2097152; else printf "%dM", s/2048 }'; }

SFD="label: dos
label-id: $DISKID
unit: sectors

"
LAYOUT=()          # "pN  size  description"
ALL_N=()           # every partition number that must exist afterwards
ROM_N=()           # exFAT ROM partitions (wiped before mkfs)
pos=$P1_START; n=5; LAST_N=
roms_left=$NROM
next_rom_size() { roms_left=$((roms_left-1)); if ((roms_left)); then RSZ=$ROM_SIZE; else RSZ=$LAST_ROM_SIZE; fi; }   # sets RSZ

primary() {  # num size type desc [bootable]
  SFD+="start=$pos, size=$2, type=$3${5:+, bootable}"$'\n'
  LAYOUT+=("$(printf 'p%-3s %7s  %s' "$1" "$(hsize "$2")" "$4")"); ALL_N+=("$1")
  pos=$((pos+$2))
}
extended() {  # num
  SFD+="start=$pos, type=5"$'\n'
  LAYOUT+=("$(printf 'p%-3s %7s  %s' "$1" "" "extended")")
}
logical() {  # size type desc   (size "" = fill to the end of the card)
  pos=$((pos+GAP))
  local sz=$1 shown
  if [[ -n $sz ]]; then SFD+="start=$pos, size=$sz, type=$2"$'\n'; shown=$(hsize "$sz")
  else SFD+="start=$pos, type=$2"$'\n'; shown="~$(hsize $((TOTAL-pos)))"; sz=$((TOTAL-pos)); fi
  LAYOUT+=("$(printf 'p%-3s %7s  %s' "$n" "$shown" "$3")"); ALL_N+=("$n")
  LAST_N=$n; n=$((n+1)); pos=$((pos+sz))
}

layout_g350() {
  BOOTSEL_N=1
  if [[ $BOOTMODE == ini ]]; then BOOTFILE=boot.ini; else BOOTFILE=boot.scr; fi
  if sel amberelec; then
    # p3 must be AmberELEC's games partition: its initramfs (inside KERNEL) mounts the
    # first of mmcblk1p3/mmcblk1p1/mmcblk1/mmcblk0p3 at /storage/roms. U-Boot reads
    # boot.ini from p1, so AmberELEC's FAT partition stays p1.
    AMB_BOOT_N=1; AMB_STOR_N=2; AMB_ROMS_N=3
    primary 1 $AMB_BOOT_SIZE c "FAT32 AMBRELC: AmberELEC KERNEL/SYSTEM + shared $BOOTFILE" bootable
    primary 2 $AMB_STOR_SIZE 83 "ext4 STORAGE: AmberELEC /storage"
    next_rom_size; primary 3 "$RSZ" 7 "exFAT GAMES: AmberELEC roms"; ROM_N+=(3)
    extended 4
  else
    # No AmberELEC: a small FAT partition only holds the shared boot.ini and logo.bmp
    # (or boot.scr with the ROCKNIX U-Boot).
    if [[ $BOOTMODE == ini ]]; then
      primary 1 $BOOTSEL_SIZE c "FAT32 BOOTSEL: shared boot.ini + logo.bmp" bootable
    else
      primary 1 $BOOTSEL_SIZE c "FAT32 BOOTSEL: shared boot.scr" bootable
    fi
    extended 2
  fi
  if sel darkos; then
    # dArkOS mounts LABEL=EASYROMS, so its roms can live in a logical partition.
    next_rom_size
    logical "$RSZ" 7 "exFAT EASYROMS: dArkOS roms";   DRK_ROMS_N=$LAST_N; ROM_N+=("$LAST_N")
    logical $DRK_BOOT_SIZE c     "FAT BOOT: dArkOS /boot";        DRK_BOOT_N=$LAST_N
    logical $DRK_ROOT_SIZE 83    "btrfs ROOTFS: dArkOS root";     DRK_ROOT_N=$LAST_N
  fi
  if sel rocknix; then
    # ROCKNIX boots by filesystem UUID (set in boot.scr) and finds its games
    # partition through system.gamesdevice (set below).
    logical $RNX_P1_SIZE c       "FAT ROCKNIX: ROCKNIX KERNEL/SYSTEM"; RNX_BOOT_N=$LAST_N
    logical $RNX_STOR_SIZE 83    "ext4 STORAGE: ROCKNIX /storage";     RNX_STOR_N=$LAST_N
    next_rom_size
    logical "$RSZ" 7             "exFAT GAMES: ROCKNIX roms";          RNX_ROMS_N=$LAST_N; ROM_N+=("$LAST_N")
  fi
  if sel mustardos; then
    logical $MUS_P1_SIZE c       "FAT BOOT: MustardOS kernel + dtbs"; MUS_BOOT_N=$LAST_N
    logical $MUS_P2_SIZE 83      "ext4 ROOTFS: MustardOS root";       MUS_ROOT_N=$LAST_N
    # MustardOS ROMS MUST be the last partition: its factory reset resizes its rom
    # partition to 100% (patched out below, but harmless if it runs).
    logical "" 7                 "exFAT ROMS: MustardOS roms (last)"; MUS_ROM_N=$LAST_N; ROM_N+=("$LAST_N")
  fi
}

T_DATA=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7   # GPT "Microsoft basic data" (FAT, exFAT)
T_LINUX=0FC63DAF-8483-4772-8E79-3D69D8477DE4  # GPT Linux filesystem
gpart() {  # size type name desc [bootable]   (GPT, numbered in order from 1)
  SFD+="start=$pos, size=$1, type=$2, uuid=$(pu "$n"), name=\"$3\"${5:+, attrs=\"LegacyBIOSBootable\"}"$'\n'
  LAYOUT+=("$(printf 'p%-3s %7s  %s' "$n" "$(hsize "$1")" "$4")"); ALL_N+=("$n")
  LAST_N=$n; n=$((n+1)); pos=$((pos+$1))
}

layout_rgb30() {
  # GPT. dArkOS's initramfs ignores root= and always mounts /dev/mmcblk1p4, and its
  # scripts expect BOOT on p3 and roms on p5, so dArkOS gets p3/p4/p5 as in its image.
  # knulli takes "its boot partition + 1" as /userdata (one digit only): p1/p2.
  # U-Boot (knulli's) runs boot.scr from BOOTSEL, the only partition marked bootable.
  SFD="label: gpt
label-id: $(pu 0)
unit: sectors

"
  n=1
  rgb_bootsel() { gpart $BOOTSEL_SIZE $T_DATA BOOTSEL "FAT32 BOOTSEL: shared boot.scr (bootable)" bootable; BOOTSEL_N=$LAST_N; }
  rgb_knulli() {
    gpart $KNU_P1_SIZE $T_DATA KNULLI "FAT KNULLI: knulli boot + squashfs"; KNU_BOOT_N=$LAST_N
    next_rom_size
    gpart "$RSZ" $T_DATA SHARE "exFAT SHARE: knulli /userdata (roms)"; KNU_SHARE_N=$LAST_N; ROM_N+=("$LAST_N")
  }
  rgb_darkos() {
    gpart $DRK_BOOT_SIZE $T_DATA BOOT "FAT BOOT: dArkOS /boot"; DRK_BOOT_N=$LAST_N
    gpart $DRK_ROOT_SIZE $T_LINUX ROOTFS "btrfs ROOTFS: dArkOS root"; DRK_ROOT_N=$LAST_N
    next_rom_size
    gpart "$RSZ" $T_DATA EASYROMS "exFAT EASYROMS: dArkOS roms"; DRK_ROMS_N=$LAST_N; ROM_N+=("$LAST_N")
  }
  # ROCKNIX finds KERNEL/SYSTEM and /storage by label (boot=LABEL=ROCKNIX disk=LABEL=STORAGE)
  # and its games partition through system.gamesdevice (set below).
  rgb_rocknix_sys() {
    gpart $RNX_P1_SIZE $T_DATA ROCKNIX "FAT ROCKNIX: ROCKNIX KERNEL/SYSTEM"; RNX_BOOT_N=$LAST_N
    gpart $RNX_STOR_SIZE $T_LINUX STORAGE "ext4 STORAGE: ROCKNIX /storage"; RNX_STOR_N=$LAST_N
  }
  rgb_rocknix_games() {
    next_rom_size
    gpart "$RSZ" $T_DATA GAMES "exFAT GAMES: ROCKNIX roms"; RNX_ROMS_N=$LAST_N; ROM_N+=("$LAST_N")
  }
  if sel darkos && sel knulli; then
    rgb_knulli; rgb_darkos; rgb_bootsel
    if sel rocknix; then rgb_rocknix_sys; rgb_rocknix_games; fi
  elif sel darkos; then                    # dArkOS + ROCKNIX
    rgb_bootsel; rgb_rocknix_games; rgb_darkos; rgb_rocknix_sys
  else                                     # knulli + ROCKNIX
    rgb_knulli; rgb_bootsel; rgb_rocknix_sys; rgb_rocknix_games
  fi
  if sel darkos; then
    (( DRK_BOOT_N == 3 && DRK_ROOT_N == 4 && DRK_ROMS_N == 5 )) || die "internal: dArkOS must be p3/p4/p5"
  fi
  if sel knulli; then
    (( KNU_BOOT_N == 1 && KNU_SHARE_N == 2 )) || die "internal: knulli must be p1/p2"
  fi
}
"layout_$DEVICE"
(( pos <= END )) || die "layout overflows the card ($pos > $END sectors)"


# ---------------------------------------------------------------- G350 boot.ini
branch_amberelec() {
  cat <<'EOF'
  # AmberELEC
  setenv fdt_addr_r "0x01f00000"
  setenv loadaddr "0x00200000"
  setenv scriptaddr "0x00500000"
  setenv kernel_addr_r "0x02008000"
  sysboot mmc 1:1 any ${scriptaddr} /extlinux/rk3326-rg351mp-linux.dtb.conf
EOF
}
branch_darkos() {  # BOOT ROOT
  set -- "$(hx "$1")" "$2"
  cat <<EOF
  # dArkOS
  setenv bootargs "root=PARTUUID=$(pu "$2") rootwait rw fsck.repair=yes net.ifnames=0 fbcon=rotate:0 console=/dev/ttyFIQ0 quiet splash consoleblank=0 vt.global_cursor_default=0"
  setenv loadaddr "0x02000000"
  setenv initrd_loadaddr "0x04000000"
  setenv dtb_loadaddr "0x01f00000"
  load mmc 1:$1 \${loadaddr} Image
  load mmc 1:$1 \${initrd_loadaddr} uInitrd
  load mmc 1:$1 \${dtb_loadaddr} rk3326-g350-linux.dtb
  booti \${loadaddr} \${initrd_loadaddr} \${dtb_loadaddr}
EOF
}
branch_mustardos() {  # BOOT ROOT
  set -- "$(hx "$1")" "$2"
  cat <<EOF
  # MustardOS (no initrd). rk3326-batlexp-linux.dtb has the same panel init as
  # dArkOS's rk3326-g350-linux.dtb. If it hangs, try dtb_loadaddr "0x1000000"
  # (MustardOS's own value).
  setenv bootargs "root=PARTUUID=$(pu "$2") rootfstype=ext4 rw rootwait quiet loglevel=0 cma=64M fbcon=nodefer vt.global_cursor_default=0 init=/sbin/init"
  setenv loadaddr "0x02000000"
  setenv dtb_loadaddr "0x01f00000"
  load mmc 1:$1 \${loadaddr} Image
  load mmc 1:$1 \${dtb_loadaddr} rk3326-batlexp-linux.dtb
  booti \${loadaddr} - \${dtb_loadaddr}
EOF
}

# ---------------------------------------------------------------- G350 boot.scr (ROCKNIX U-Boot)
# Same commands as the boot.ini branches, but devices come from the script bootmeth
# (${devtype} ${devnum}). Tested on a G350 with dArkOS, AmberELEC and ROCKNIX.
sbranch_amberelec() {
  cat <<'EOF'
  # AmberELEC
  setenv fdt_addr_r "0x01f00000"
  setenv loadaddr "0x00200000"
  setenv scriptaddr "0x00500000"
  setenv kernel_addr_r "0x02008000"
  sysboot ${devtype} ${devnum}:1 any ${scriptaddr} /extlinux/rk3326-rg351mp-linux.dtb.conf
EOF
}
sbranch_darkos() {  # BOOT ROOT
  local b; b=$(hx "$1")
  cat <<EOF
  # dArkOS
  setenv bootargs "root=PARTUUID=$(pu "$2") rootwait rw fsck.repair=yes net.ifnames=0 fbcon=rotate:0 console=/dev/ttyFIQ0 quiet splash consoleblank=0 vt.global_cursor_default=0"
  load \${devtype} \${devnum}:$b 0x02000000 Image
  load \${devtype} \${devnum}:$b 0x04000000 uInitrd
  load \${devtype} \${devnum}:$b 0x01f00000 rk3326-g350-linux.dtb
  booti 0x02000000 0x04000000 0x01f00000
EOF
}
sbranch_mustardos() {  # BOOT ROOT
  local b; b=$(hx "$1")
  cat <<EOF
  # MustardOS (UNTESTED with the ROCKNIX U-Boot; same commands as its boot.ini branch)
  setenv bootargs "root=PARTUUID=$(pu "$2") rootfstype=ext4 rw rootwait quiet loglevel=0 cma=64M fbcon=nodefer vt.global_cursor_default=0 init=/sbin/init"
  load \${devtype} \${devnum}:$b 0x02000000 Image
  load \${devtype} \${devnum}:$b 0x01f00000 rk3326-batlexp-linux.dtb
  booti 0x02000000 - 0x01f00000
EOF
}
sbranch_rocknix() {  # BOOT
  cat <<EOF
  # ROCKNIX: as its own boot.scr does; extlinux.conf.dualboot is written by the script
  # (G350 dtb, boot=UUID=$RNX_BOOT_UUID disk=UUID=$RNX_STOR_UUID).
  setenv kernel_addr_r "0x09000000"
  setenv fdtoverlay_addr_r "0x08000000"
  sysboot \${devtype} \${devnum}:$(hx "$1") any 0x100000 /extlinux/extlinux.conf.dualboot
EOF
}

# ---------------------------------------------------------------- RGB30 boot.scr
# Mainline U-Boot (knulli's 2024.07) runs boot.scr through its script bootmeth, which
# sets ${devtype}/${devnum} to the SD card. Kernel/initrd/dtb and bootargs are taken
# from each image's extlinux.conf; load addresses are U-Boot's defaults.
rbranch_darkos() {  # BOOT ROOT
  local b; b=$(hx "$1")
  cat <<EOF
  # dArkOS (vendor 5.10 kernel). Despite its name, uInitrd is a plain gzip initramfs
  # (no U-Boot header), so it needs :size; it is loaded last for \${filesize}.
  # Its initramfs ignores root= and always mounts /dev/mmcblk1p4 (hence dArkOS on p4).
  setenv bootargs "root=/dev/mmcblk1p$2 initrd=/uInitrd rootwait rw fsck.repair=yes quiet splash vt.global_cursor_default=0 net.ifnames=0 console=tty1 plymouth.ignore-serial-consoles consoleblank=0 loglevel=5 video=HDMI-A-1:1280x720@60"
  load \${devtype} \${devnum}:$b \${kernel_addr_r} /Image
  load \${devtype} \${devnum}:$b \${fdt_addr_r} /$DRK_DTB
  load \${devtype} \${devnum}:$b \${ramdisk_addr_r} /uInitrd
  booti \${kernel_addr_r} \${ramdisk_addr_r}:\${filesize} \${fdt_addr_r}
EOF
}
rbranch_knulli() {  # BOOT
  local b; b=$(hx "$1")
  cat <<EOF
  # knulli (finds its boot partition by label=KNULLI, /userdata on the next partition)
  setenv bootargs "initrd=/boot/initrd.lz4 label=KNULLI rootwait quiet loglevel=0 console=tty3 console=ttyS2,1500000n8 fbcon=rotate:0"
  load \${devtype} \${devnum}:$b \${kernel_addr_r} /boot/linux
  load \${devtype} \${devnum}:$b \${fdt_addr_r} /$KNU_DTB
  load \${devtype} \${devnum}:$b \${ramdisk_addr_r} /boot/initrd.lz4
  booti \${kernel_addr_r} \${ramdisk_addr_r}:\${filesize} \${fdt_addr_r}
EOF
}
rbranch_rocknix() {  # BOOT
  local b; b=$(hx "$1")
  cat <<EOF
  # ROCKNIX (initramfs built into KERNEL; finds its partitions by label)
  setenv bootargs "boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet console=ttyS2,1500000 console=tty0 systemd.debug_shell=ttyS2"
  load \${devtype} \${devnum}:$b \${kernel_addr_r} /KERNEL
  load \${devtype} \${devnum}:$b \${fdt_addr_r} /$RNX_DTB
  booti \${kernel_addr_r} - \${fdt_addr_r}
EOF
}

branch() {
  case $BOOTMODE:$1 in
    ini:amberelec)      branch_amberelec ;;
    ini:darkos)         branch_darkos "$DRK_BOOT_N" "$DRK_ROOT_N" ;;
    ini:mustardos)      branch_mustardos "$MUS_BOOT_N" "$MUS_ROOT_N" ;;
    scr-g350:amberelec) sbranch_amberelec ;;
    scr-g350:darkos)    sbranch_darkos "$DRK_BOOT_N" "$DRK_ROOT_N" ;;
    scr-g350:mustardos) sbranch_mustardos "$MUS_BOOT_N" "$MUS_ROOT_N" ;;
    scr-g350:rocknix)   sbranch_rocknix "$RNX_BOOT_N" ;;
    scr-rgb30:darkos)   rbranch_darkos "$DRK_BOOT_N" "$DRK_ROOT_N" ;;
    scr-rgb30:knulli)   rbranch_knulli "$KNU_BOOT_N" ;;
    scr-rgb30:rocknix)  rbranch_rocknix "$RNX_BOOT_N" ;;
  esac
}

KEY_VAR=(volminus volplus)
gen_branches() {
  local i cond
  for i in "${!KEY_OS[@]}"; do
    if [[ $BOOTMODE == scr-* ]]; then cond="test \"\${key}\" = \"${KEY_VAR[$i]}\""; else cond="gpio input ${KEY_PIN[$i]}"; fi
    if (( i == 0 )); then echo "if $cond; then"; else echo "elif $cond; then"; fi
    branch "${KEY_OS[$i]}"
  done
  if [[ $BOOTMODE == scr-* ]]; then        # one-shot request from multiboot-next (no key held)
    for i in "${!KEY_OS[@]}"; do
      echo "elif test \"\${next}\" = \"${KEY_OS[$i]}\"; then"
      branch "${KEY_OS[$i]}"
    done
  fi
  echo "else"
  branch "$DEFAULT"
  echo "fi"
}

boot_title() {
  local i title
  title="$(osname "$DEFAULT") (no key)"
  for i in "${!KEY_OS[@]}"; do title+=" / $(osname "${KEY_OS[$i]}") (${KEY_NAME[$i]})"; done
  echo "$title"
}

gen_boot_ini() {
  local l oslist
  oslist=$(IFS=,; echo "${USER_OS[*]}")
  echo "odroidgoa-uboot-config"
  echo
  echo "# ---- G350 multi-boot: $(boot_title) ----"
  echo "# Generated by flash-dualboot.sh --os $oslist --default $DEFAULT"
  echo "# Hold the key at power-on. Pins are from the gpio-keys node in rk3326-g350-linux.dtb:"
  echo "#   Vol- = GPIO2_A1 = pin 65, Vol+ = GPIO2_A0 = pin 64, both active low."
  echo "# U-Boot 2017.09 \"gpio input\" returns the pin value as exit status, so it is"
  echo "# \"true\" (0) only while the key is held. If the command fails, $(osname "$DEFAULT") boots."
  echo "#"
  echo "# Layout:"
  for l in "${LAYOUT[@]}"; do echo "#   $l"; done
  if sel amberelec; then
    echo "# AmberELEC updates overwrite this file: restore it from boot.ini.dualboot."
  fi
  echo
  gen_branches
}

# One-shot "boot OS next": multiboot-next writes next=<os> to nextboot.txt on the
# boot selector partition; boot.scr reads it, clears it (fatwrite "next=" + newline)
# and boots that OS unless a key is held. Not in boot.ini mode (needs fatwrite).
NEXT_FILE=nextboot.txt
gen_next() {
  local b; b=$(hx "$BOOTSEL_N")
  cat <<EOF
# One-shot boot request: multiboot-next (installed on each OS) writes next=<os> to
# $NEXT_FILE on this partition. It is read and cleared here, before booting; a held
# key still wins. The file is only rewritten when it held a request.
setenv next
if load \${devtype} \${devnum}:$b 0x30001000 $NEXT_FILE; then
  env import -t 0x30001000 \${filesize} next
  if test -n "\${next}"; then
    mw.b 0x30002000 0x6e 1
    mw.b 0x30002001 0x65 1
    mw.b 0x30002002 0x78 1
    mw.b 0x30002003 0x74 1
    mw.b 0x30002004 0x3d 1
    mw.b 0x30002005 0x0a 1
    fatwrite \${devtype} \${devnum}:$b 0x30002000 $NEXT_FILE 6
  fi
fi

EOF
}

# Where multiboot-next is installed on each OS (a writable place on its own partitions).
next_path() {
  case $1 in
    amberelec|rocknix) echo /storage/bin/multiboot-next ;;
    darkos|mustardos)  echo /usr/local/bin/multiboot-next ;;
    knulli)            echo /userdata/multiboot-next ;;
  esac
}
gen_next_helper() {  # src/multiboot-next with this card's values
  local at=440 id=${DISKID#0x}
  [[ $DEVICE == rgb30 ]] && at=568                 # GPT: disk GUID in the header at LBA 1
  sed -e "s/@DEVICE@/$(devname)/g" -e "s/@OSES@/${USER_OS[*]}/g" -e "s/@PARTUUID@/$(pu "$BOOTSEL_N")/g" \
      -e "s/@PARTNUM@/$BOOTSEL_N/g" -e "s/@DISKID_AT@/$at/g" \
      -e "s/@DISKID_HEX@/${id:6:2}${id:4:2}${id:2:2}${id:0:2}/g" "$HERE/multiboot-next"
}
install_next() {  # os mountpoint path-under-it
  local dst=$2/${3#/}
  mkdir -p "$(dirname "$dst")"
  gen_next_helper > "$dst"
  chmod 755 "$dst" 2>/dev/null || true             # exFAT (knulli /userdata) has no modes: run it with sh
  echo "--- multiboot-next for $(osname "$1"): $(next_path "$1")"
}

gen_boot_cmd() {
  local l oslist
  oslist=$(IFS=,; echo "${USER_OS[*]}")
  echo "# ---- RGB30 multi-boot: $(boot_title) ----"
  echo "# Generated by flash-dualboot.sh --device rgb30 --os $oslist --default $DEFAULT"
  echo "# Source of boot.scr (mkimage -A arm64 -T script -C none -d boot.cmd boot.scr)."
  echo "# Hold the key at power-on. Vol+ = GPIO3_A7, Vol- = GPIO3_B0 (gpio-keys-vol in"
  echo "# rk3566-powkiddy-rgb30.dtb), both active low. knulli's U-Boot has no gpio or setexpr"
  echo "# command, so the script enables the pull-ups (RK3568 SYS_GRF GPIO3A_P/GPIO3B_P,"
  echo "# 01 = pull-up, upper 16 bits = write mask), copies GPIO3's EXT_PORT register to RAM"
  echo "# and compares bytes: byte 0 bit 7 = Vol+ (< 0x80 = held), byte 1 bit 0 = Vol-"
  echo "# (0xfe = held, the other B pins are pulled up)."
  echo "# Partition numbers after \${devnum}: are hex."
  echo "#"
  echo "# Layout:"
  for l in "${LAYOUT[@]}"; do echo "#   $l"; done
  cat <<'EOF'

mw.l 0xfdc600a0 0xc0004000
mw.l 0xfdc600a4 0xffff5555
sleep 0.1
cp.l 0xfe760070 0x30000000 1
setenv key none
if itest.b *0x30000000 < 0x80; then setenv key volplus; elif itest.b *0x30000001 == 0xfe || itest.b *0x30000001 == 0x7e; then setenv key volminus; fi

EOF
  gen_next
  gen_branches
}

gen_boot_cmd_g350() {
  local l oslist
  oslist=$(IFS=,; echo "${USER_OS[*]}")
  cat <<EOF
# ---- G350 multi-boot: $(boot_title) ----
# Generated by flash-dualboot.sh --os $oslist --default $DEFAULT
# Run by ROCKNIX's U-Boot 2025.10 (script bootmeth). Source of boot.scr:
#   mkimage -A arm64 -O linux -T script -C none -d boot.cmd boot.scr
# Hold the key at power-on. Vol+ = GPIO2_A0, Vol- = GPIO2_A1 (gpio-keys in
# rk3326-batlexp-g350.dtb), both active low. In this U-Boot "gpio input" returns
# success instead of the pin value and nothing enables the pins' pull-ups, so the
# script sets the pull-ups (PX30 GRF GPIO2A_P, 01 = pull-up, upper 16 bits = write
# mask) and reads GPIO2's EXT_PORTA register (bit 0 = Vol+, bit 1 = Vol-).
# Partition numbers after \${devnum}: are hex.
#
# Layout:
EOF
  for l in "${LAYOUT[@]}"; do echo "#   $l"; done
  cat <<'EOF'

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

EOF
  gen_next
  gen_branches
}

case $BOOTMODE in
  ini)       BOOT_CFG=$(gen_boot_ini) ;;
  scr-g350)  BOOT_CFG=$(gen_boot_cmd_g350) ;;
  scr-rgb30) BOOT_CFG=$(gen_boot_cmd) ;;
esac

rnx_extlinux() {
  cat <<EOF
LABEL ROCKNIX
  LINUX /KERNEL
  FDT /$RNX_DTB
  APPEND boot=UUID=$RNX_BOOT_UUID disk=UUID=$RNX_STOR_UUID quiet console=ttyS2,1500000 console=tty0 systemd.debug_shell=ttyS2
EOF
}

render() {  # template BOOT ROOT -> stdout  (@BOOTX@ = BOOT in hex, for U-Boot dev:part)
  sed -e "s/@BOOT@/$2/g" -e "s/@BOOTX@/$(hx "$2")/g" -e "s/@ROOT@/$3/g" -e "s/@ROOT2@/$(printf %02x "$3")/g" "$1"
}

# ---------------------------------------------------------------- summary
show_plan() {
  local i
  echo "Device:   $(devname)"
  echo "OSes:     ${USER_OS[*]}"
  echo "U-Boot:   $(basename "$UBOOT_IMG") (always needed$(sel "$UBOOT_OS" || echo ", although $UBOOT_OS is not installed"))"
  if [[ $BOOTMODE == scr-g350 ]] && sel mustardos; then
    echo "WARNING:  MustardOS has not been tested with the ROCKNIX U-Boot (needed for ROCKNIX)."
  fi
  echo "Keys:     no key = $(osname "$DEFAULT")"
  for i in "${!KEY_OS[@]}"; do echo "          ${KEY_NAME[$i]} (pin ${KEY_PIN[$i]}) = $(osname "${KEY_OS[$i]}")"; done
  if [[ $BOOTMODE == scr-* ]]; then
    echo "Next:     multiboot-next OS boots OS once ($NEXT_FILE on p$BOOTSEL_N; a held key wins)"
    for i in "${USER_OS[@]}"; do echo "          $(osname "$i"): $(next_path "$i")"; done
  fi
  echo "Partitions on $DEV ($(hsize "$TOTAL")):"
  printf '  %s\n' "${LAYOUT[@]}"
  if [[ $DEVICE == g350 ]]; then
    echo "Numbers:  DRK_ROMS_N=${DRK_ROMS_N:--} DRK_BOOT_N=${DRK_BOOT_N:--} DRK_ROOT_N=${DRK_ROOT_N:--}" \
         "MUS_BOOT_N=${MUS_BOOT_N:--} MUS_ROOT_N=${MUS_ROOT_N:--} MUS_ROM_N=${MUS_ROM_N:--}" \
         ${RNX_BOOT_N:+"RNX_BOOT_N=$RNX_BOOT_N RNX_STOR_N=$RNX_STOR_N RNX_ROMS_N=$RNX_ROMS_N"}
  else
    echo "Numbers:  KNU_BOOT_N=${KNU_BOOT_N:--} KNU_SHARE_N=${KNU_SHARE_N:--}" \
         "DRK_ROMS_N=${DRK_ROMS_N:--} DRK_BOOT_N=${DRK_BOOT_N:--} DRK_ROOT_N=${DRK_ROOT_N:--}" \
         "RNX_BOOT_N=${RNX_BOOT_N:--} RNX_STOR_N=${RNX_STOR_N:--} RNX_ROMS_N=${RNX_ROMS_N:--}"
  fi
}

if (( PLAN )); then
  show_plan
  echo; echo "---- sfdisk script"; printf '%s' "$SFD"
  if [[ $DEVICE == g350 ]]; then
    if [[ $BOOTMODE == ini ]]; then
      echo; echo "---- boot.ini (p1)"; echo "$BOOT_CFG"
    else
      echo; echo "---- boot.cmd (p1, compiled to boot.scr)"; echo "$BOOT_CFG"
      echo; echo "---- ROCKNIX /extlinux/extlinux.conf.dualboot (p$RNX_BOOT_N)"; rnx_extlinux
    fi
    if sel darkos; then
      echo; echo "---- dArkOS /boot/boot.ini (p$DRK_BOOT_N)"; render "$HERE/boot.ini.darkos" "$DRK_BOOT_N" "$DRK_ROOT_N"
    fi
    if sel mustardos; then
      echo; echo "---- MustardOS /boot.ini (p$MUS_BOOT_N)"; render "$HERE/boot.ini.mustardos" "$MUS_BOOT_N" "$MUS_ROOT_N"
    fi
  else
    echo; echo "---- boot.cmd (p1, compiled to boot.scr)"; echo "$BOOT_CFG"
  fi
  if [[ $BOOTMODE == scr-* ]]; then
    echo; echo "---- multiboot-next (installed on each OS)"; gen_next_helper
  fi
  exit 0
fi

MNT=$(mktemp -d /tmp/g350dual.XXXX)
cleanup() { set +e; for d in "$MNT"/*; do mountpoint -q "$d" && umount "$d"; done; rm -rf "$MNT"; }
trap cleanup EXIT
mkdir -p "$MNT"/{p1,drkimg,root,boot,roms,muroot,muboot,knboot,knshare,rnboot,rnstor,rnsys,ambstor}

lsblk -o NAME,SIZE,FSTYPE,LABEL,MODEL,TRAN "$DEV"
echo
show_plan
echo
read -rp "ALL DATA ON $DEV WILL BE DESTROYED. Type YES to continue: " ans
[[ $ans == YES ]] || die "aborted"

# ---------------------------------------------------------------- 1. partition
log "Unmounting and wiping $DEV"
unmount_dev
for part in "$DEV"?*; do [[ -b $part ]] && wipefs -a "$part" >/dev/null || true; done
wipefs -a "$DEV"

log "Writing MBR (label-id $DISKID)"
printf '%s' "$SFD" | sfdisk --wipe always --wipe-partitions always "$DEV"
partprobe "$DEV" 2>/dev/null || blockdev --rereadpt "$DEV" || true
unmount_dev
for n in "${ALL_N[@]}"; do [[ -b $(p "$n") ]] || die "$(p "$n") missing after partitioning"; done

# ---------------------------------------------------------------- 2. U-Boot
log "Writing U-Boot (sectors 64-32767) from $(basename "$UBOOT_IMG")"
dd if="$UBOOT_IMG" of="$DEV" bs=$S skip=64 seek=64 count=32704 conv=notrunc,fsync status=none

# ---------------------------------------------------------------- 3. copy partitions
copy_part() {  # image start size target
  log "dd $(basename "$1") [$2 +$3] -> $4"
  dd if="$1" of="$4" bs=4M iflag=skip_bytes,count_bytes skip=$(($2*S)) count=$(($3*S)) \
     conv=fsync status=progress
}
if sel amberelec; then
  copy_part "$AMB" $AMB_P1_START $AMB_P1_SIZE "$(p "$AMB_BOOT_N")"
  copy_part "$AMB" $AMB_P2_START $AMB_P2_SIZE "$(p "$AMB_STOR_N")"
fi
if sel darkos; then
  copy_part "$DARK" $DRK_P1_START $DRK_P1_SIZE "$(p "$DRK_BOOT_N")"
  copy_part "$DARK" $DRK_P2_START $DRK_P2_SIZE "$(p "$DRK_ROOT_N")"
fi
if sel mustardos; then
  copy_part "$MUS" $MUS_P1_START $MUS_P1_SIZE "$(p "$MUS_BOOT_N")"
  copy_part "$MUS" $MUS_P2_START $MUS_P2_SIZE "$(p "$MUS_ROOT_N")"
fi
if sel knulli; then
  copy_part "$KNU" $KNU_P1_START $KNU_P1_SIZE "$(p "$KNU_BOOT_N")"
fi
if sel rocknix; then
  copy_part "$RNX" $RNX_P1_START $RNX_P1_SIZE "$(p "$RNX_BOOT_N")"
  copy_part "$RNX" $RNX_P2_START $RNX_P2_SIZE "$(p "$RNX_STOR_N")"
fi
unmount_dev

# AmberELEC and ROCKNIX: grow the copied ext4 STORAGE and drop the first-boot resize
# flag (their fs-resize would "parted resizepart 100%" the STORAGE partition).
grow_storage() {  # partition number
  local part listing; part=$(p "$1")
  e2fsck -fy "$part" || [[ $? -le 1 ]] || die "e2fsck failed on $part"
  resize2fs "$part"
  debugfs -w -R "rm /.please_resize_me" "$part"
  listing=$(debugfs -R "ls -l /" "$part" 2>/dev/null)
  [[ $listing != *please_resize_me* ]] || die "resize flag still present on $part"
}
if sel amberelec; then
  log "AmberELEC STORAGE: fsck + grow to 8G + remove .please_resize_me"
  grow_storage "$AMB_STOR_N"
  if [[ $BOOTMODE != ini ]]; then
    mount -t ext4 "$(p "$AMB_STOR_N")" "$MNT/ambstor"                          # /storage
    install_next amberelec "$MNT/ambstor" "$(next_path amberelec | sed 's|^/storage||')"
    sync
    umount "$MNT/ambstor"
  fi
fi
if sel rocknix; then
  log "ROCKNIX STORAGE: fsck + grow to 8G + remove .please_resize_me"
  grow_storage "$RNX_STOR_N"
fi

if sel mustardos; then
  log "MustardOS ROOTFS: fsck"
  e2fsck -fy "$(p "$MUS_ROOT_N")" || [[ $? -le 1 ]] || die "e2fsck failed on $(p "$MUS_ROOT_N")"
fi

# Stale data from an earlier layout can look like a filesystem to libblkid, and
# mkfs.exfat (exfatprogs >= 1.4) refuses with "Device has existing signatures".
unmount_dev
wipefs_list=()
for n in "${ROM_N[@]}"; do wipefs_list+=("$(p "$n")"); done
sel amberelec || wipefs_list+=("$(p "$BOOTSEL_N")")
wipefs -a "${wipefs_list[@]}" >/dev/null
if sel amberelec; then
  log "Creating exFAT GAMES (AmberELEC roms) on $(p "$AMB_ROMS_N")"
  mkfs.exfat -F -c 16K -L GAMES "$(p "$AMB_ROMS_N")"   # AmberELEC creates its folders via tmpfiles.d
else
  log "Creating FAT32 BOOTSEL on $(p "$BOOTSEL_N")"
  mkfs.vfat -F 32 -n BOOTSEL "$(p "$BOOTSEL_N")"
fi
if sel knulli; then
  log "Creating exFAT SHARE (knulli /userdata) on $(p "$KNU_SHARE_N")"
  mkfs.exfat -F -c 16K -L SHARE "$(p "$KNU_SHARE_N")"   # what knulli's autoresize would leave
fi
if sel darkos; then
  log "Creating exFAT EASYROMS (dArkOS roms) on $(p "$DRK_ROMS_N")"
  mkfs.exfat -F -c 16K -L EASYROMS "$(p "$DRK_ROMS_N")"
fi
if sel mustardos; then
  log "Creating exFAT ROMS (MustardOS roms) on $(p "$MUS_ROM_N")"
  mkfs.exfat -F -c 16K -L ROMS "$(p "$MUS_ROM_N")"     # MustardOS reformats it on first boot
fi
if sel rocknix; then
  log "Creating exFAT GAMES (ROCKNIX roms) on $(p "$RNX_ROMS_N")"
  mkfs.exfat -F -c 16K -L GAMES "$(p "$RNX_ROMS_N")"
fi
unmount_dev

need_file() {  # path  (on a mounted partition)
  [[ -f $1 ]] || die "missing $1 (image layout changed?)"
}

# ---------------------------------------------------------------- 4. dArkOS first-boot work
if sel darkos; then
  mount -t btrfs -o compress=zlib:1 "$(p "$DRK_ROOT_N")" "$MNT/root"
  mount -t vfat  "$(p "$DRK_BOOT_N")" "$MNT/boot"
  mount -t exfat "$(p "$DRK_ROMS_N")" "$MNT/roms"

  log "Growing btrfs ROOTFS to fill $(p "$DRK_ROOT_N")"
  btrfs filesystem resize max "$MNT/root"

  log "Extracting /roms.tar into EASYROMS (as expandtoexfat.sh does)"
  bad=$(tar -tf "$MNT/root/roms.tar" | grep -cv '^roms\(/\|$\)' || true)
  [[ $bad -eq 0 ]] || die "roms.tar has $bad entries outside roms/"
  tar --no-same-permissions --no-same-owner -xf "$MNT/root/roms.tar" -C "$MNT/roms" --strip-components=1 \
    || echo "WARN: tar reported errors (expected for symlinks/ownership on exFAT)"

  log "Installing themes from /tempthemes into EASYROMS/themes"
  rm -rf "$MNT/roms/themes/es-theme-nes-box"
  mkdir -p "$MNT/roms/themes"
  ( cd "$MNT/root/tempthemes" && tar --no-same-permissions --no-same-owner -cf - . ) \
    | tar --no-same-permissions --no-same-owner -xf - -C "$MNT/roms/themes" \
    || echo "WARN: theme copy reported errors (expected for symlinks on exFAT)"
  rm -rf "$MNT/root/tempthemes"
  # roms.tar is kept: expandtoexfat.sh keeps it too (G350 and RGB30)

  log "Finishing dArkOS first-boot steps (fstab, disable firstboot.service)"
  if [[ $DEVICE == g350 ]]; then
    cp -f "$MNT/boot/fstab.exfat" "$MNT/root/etc/fstab"
  else
    # dArkOS is on p3/p4/p5 as in its image, so its fstab.exfat (/dev/mmcblk1p3/p4/p5)
    # is used as is, like on the G350.
    cp -f "$MNT/boot/fstab.exfat" "$MNT/root/etc/fstab"
    for f in Image uInitrd "$DRK_DTB"; do need_file "$MNT/boot/$f"; done
  fi
  rm -f "$MNT/root/etc/systemd/system/multi-user.target.wants/firstboot.service"
  rm -f "$MNT/boot/firstboot.sh" "$MNT/boot/expandtoexfat.sh" "$MNT/boot/fstab.exfat" "$MNT"/boot/doneit*
  if [[ $DEVICE == g350 ]]; then
    render "$HERE/boot.ini.darkos" "$DRK_BOOT_N" "$DRK_ROOT_N" > "$MNT/boot/boot.ini"
  fi
  echo "--- dArkOS /etc/fstab:"; cat "$MNT/root/etc/fstab"
  [[ $BOOTMODE == ini ]] || install_next darkos "$MNT/root" "$(next_path darkos)"
  sync
  umount "$MNT/roms" "$MNT/boot" "$MNT/root"
fi

# ---------------------------------------------------------------- 5. MustardOS prep
if sel mustardos; then
  log "MustardOS: point storage config at p$MUS_BOOT_N/p$MUS_ROOT_N/p$MUS_ROM_N, neutralise partition-table edits"
  mount -t ext4 "$(p "$MUS_ROOT_N")" "$MNT/muroot"
  mount -t vfat "$(p "$MUS_BOOT_N")" "$MNT/muboot"
  SCFG=$MNT/muroot/opt/muos/device/config/storage
  for d in boot root rom; do [[ -f $SCFG/$d/num ]] || die "missing $SCFG/$d/num"; done
  printf '%s' "$MUS_BOOT_N" > "$SCFG/boot/num"
  printf '%s' "$MUS_ROOT_N" > "$SCFG/root/num"
  printf '%s' "$MUS_ROM_N"  > "$SCFG/rom/num"
  RESET=$MNT/muroot/opt/muos/script/system/reset.sh
  [[ -f $RESET ]] || die "missing $RESET"
  sed -i -E '/\| fdisk \/dev\/"\$ROM_DEV"|parted ---pretend-input-tty/ s/^([[:space:]]*)/\1: # /' "$RESET"
  if grep -nE '(^|[^[:alnum:]_])(fdisk|parted)([[:space:]]|$)' "$RESET" | grep -vE '^[0-9]+:[[:space:]]*: #'; then
    die "active fdisk/parted lines remain in reset.sh"
  fi
  echo "--- patched reset.sh lines:"; grep -n '^[[:space:]]*: #' "$RESET"
  cp -f "$MNT/muboot/boot.ini" "$MNT/muboot/boot.ini.orig"
  render "$HERE/boot.ini.mustardos" "$MUS_BOOT_N" "$MUS_ROOT_N" > "$MNT/muboot/boot.ini"   # reference only; U-Boot reads p1
  [[ $BOOTMODE == ini ]] || install_next mustardos "$MNT/muroot" "$(next_path mustardos)"
  sync
  umount "$MNT/muboot" "$MNT/muroot"
fi

# ---------------------------------------------------------------- 6. knulli prep
if sel knulli; then
  log "knulli: disable autoresize (it would resize p$KNU_SHARE_N to 100% of the card)"
  mount -t vfat "$(p "$KNU_BOOT_N")" "$MNT/knboot"
  KCONF=$MNT/knboot/knulli-boot.conf
  need_file "$KCONF"
  for f in boot/linux boot/initrd.lz4 "$KNU_DTB"; do need_file "$MNT/knboot/$f"; done
  cp -f "$KCONF" "$KCONF.orig"
  sed -i -E 's/^[[:space:]]*autoresize[[:space:]]*=.*/autoresize=false/' "$KCONF"
  ! grep -qE '^[ ]*autoresize[ ]*=[ ]*true[ ]*$' "$KCONF" || die "autoresize still enabled in knulli-boot.conf"
  grep -qE '^[ ]*format-internal[ ]*=' "$KCONF" && die "format-internal is set in knulli-boot.conf"
  grep -n 'autoresize' "$KCONF"
  sync
  umount "$MNT/knboot"
  mount -t exfat "$(p "$KNU_SHARE_N")" "$MNT/knshare"                          # /userdata
  install_next knulli "$MNT/knshare" "$(next_path knulli | sed 's|^/userdata||')"
  sync
  umount "$MNT/knshare"
fi

# ---------------------------------------------------------------- 7. ROCKNIX prep
if sel rocknix; then
  # automount mounts system.gamesdevice if it exists; otherwise it mounts the first
  # unmounted partition of any >8GB disk as "external games", which here would be
  # another OS's partition. system.cfg lives on /storage, and chksysconfig restores
  # all of /usr/config over it on boot while retroarch.cfg is missing, so the whole
  # default config is seeded first (what ROCKNIX does on its first boot).
  log "ROCKNIX: seed /storage/.config and set system.gamesdevice to p$RNX_ROMS_N"
  mount -t vfat "$(p "$RNX_BOOT_N")" "$MNT/rnboot"
  mount -t ext4 "$(p "$RNX_STOR_N")" "$MNT/rnstor"
  for f in KERNEL SYSTEM "$RNX_DTB"; do need_file "$MNT/rnboot/$f"; done
  unsquashfs -q -f -d "$MNT/rnsys" "$MNT/rnboot/SYSTEM" usr/config >/dev/null
  [[ -d $MNT/rnsys/usr/config ]] || die "usr/config not found in ROCKNIX SYSTEM"
  mkdir -p "$MNT/rnstor/.config"
  cp -a "$MNT/rnsys/usr/config/." "$MNT/rnstor/.config/"
  rm -rf "$MNT/rnsys"
  RCFG=$MNT/rnstor/.config/system/configs/system.cfg
  need_file "$RCFG"
  need_file "$MNT/rnstor/.config/retroarch/retroarch.cfg"
  grep -q '^system.hostname=' "$RCFG" || die "system.hostname missing from ROCKNIX system.cfg"
  sed -i '/^system\.gamesdevice=/d; /^system\.automount=/d' "$RCFG"
  printf 'system.automount=1\nsystem.gamesdevice=/dev/disk/by-partuuid/%s\n' "$(pu "$RNX_ROMS_N")" >> "$RCFG"
  grep -nE '^system\.(automount|gamesdevice)=' "$RCFG"
  if [[ $BOOTMODE == scr-g350 ]]; then
    rnx_extlinux > "$MNT/rnboot/extlinux/extlinux.conf.dualboot"
    echo "--- ROCKNIX extlinux.conf.dualboot:"; cat "$MNT/rnboot/extlinux/extlinux.conf.dualboot"
  fi
  install_next rocknix "$MNT/rnstor" "$(next_path rocknix | sed 's|^/storage||')"   # /storage
  sync
  umount "$MNT/rnstor" "$MNT/rnboot"
fi

# ---------------------------------------------------------------- 8. shared boot config on p1
if [[ $BOOTMODE == ini ]]; then
  log "Installing multi-boot boot.ini on $(p 1)"
  printf '%s\n' "$BOOT_CFG" > "$HERE/boot.ini.generated"
  [[ -n ${SUDO_USER:-} ]] && chown "$SUDO_USER:" "$HERE/boot.ini.generated" || true
  mount -t vfat "$(p 1)" "$MNT/p1"
  if sel amberelec; then cp -f "$MNT/p1/boot.ini" "$MNT/p1/boot.ini.amberelec"; fi
  cp -f "$HERE/boot.ini.generated" "$MNT/p1/boot.ini"
  cp -f "$HERE/boot.ini.generated" "$MNT/p1/boot.ini.dualboot"
  # U-Boot loads logo.bmp from mmc 1:1; take it from the dArkOS image's BOOT partition.
  mount -o ro,loop,offset=$((DRK_P1_START*S)),sizelimit=$((DRK_P1_SIZE*S)) -t vfat "$DARK" "$MNT/drkimg"
  cp -f "$MNT/drkimg/logo.bmp" "$MNT/p1/logo.bmp"
  umount "$MNT/drkimg"
else
  log "Installing multi-boot boot.scr on $(p "$BOOTSEL_N")"
  printf '%s\n' "$BOOT_CFG" > "$HERE/boot.cmd.generated"
  [[ -n ${SUDO_USER:-} ]] && chown "$SUDO_USER:" "$HERE/boot.cmd.generated" || true
  mount -t vfat "$(p "$BOOTSEL_N")" "$MNT/p1"
  cp -f "$HERE/boot.cmd.generated" "$MNT/p1/boot.cmd"
  mkimage -A arm64 -O linux -T script -C none -n "$(devname) multi-boot" -d "$MNT/p1/boot.cmd" "$MNT/p1/boot.scr"
fi
ls -l "$MNT/p1"
sync
umount "$MNT/p1"

# ---------------------------------------------------------------- 9. verify
log "Verification"
sfdisk -d "$DEV"
blk=(); for n in "${ALL_N[@]}"; do blk+=("$(p "$n")"); done
blkid "${blk[@]}"
check_label() {  # num label
  blkid -s LABEL -o value "$(p "$1")" | grep -q "^$2\$" || die "$(p "$1") is not $2"
}
check_partuuid() {  # num
  blkid -s PARTUUID -o value "$(p "$1")" | grep -qi "^$(pu "$1")\$" || die "PARTUUID of $(p "$1") is not $(pu "$1")"
}
if sel amberelec; then
  check_label "$AMB_ROMS_N" GAMES
  blkid -s UUID -o value "$(p "$AMB_BOOT_N")" | grep -q '^1910-5621$' || die "AMBRELC UUID changed"
  blkid -s UUID -o value "$(p "$AMB_STOR_N")" | grep -q '^7ab6cd9f-77f8-4d13-905e-aa8fc5baa800$' || die "STORAGE UUID changed"
  dumpe2fs -h "$(p "$AMB_STOR_N")" 2>/dev/null | grep -E 'Block count|Block size'
else
  check_label "$BOOTSEL_N" BOOTSEL
fi
if sel darkos; then
  check_label "$DRK_ROMS_N" EASYROMS
  check_partuuid "$DRK_ROOT_N"
  [[ $DEVICE == g350 ]] || check_partuuid "$DRK_BOOT_N"
fi
if sel mustardos; then
  check_label "$MUS_ROM_N" ROMS
  check_partuuid "$MUS_ROOT_N"
  mount -o ro -t ext4 "$(p "$MUS_ROOT_N")" "$MNT/muroot"
  for pair in boot:$MUS_BOOT_N root:$MUS_ROOT_N rom:$MUS_ROM_N; do
    got=$(cat "$MNT/muroot/opt/muos/device/config/storage/${pair%%:*}/num")
    [[ $got == "${pair#*:}" ]] || { umount "$MNT/muroot"; die "MustardOS storage/${pair%%:*}/num is '$got', expected ${pair#*:}"; }
  done
  umount "$MNT/muroot"
  echo "MustardOS storage num files: boot=$MUS_BOOT_N root=$MUS_ROOT_N rom=$MUS_ROM_N"
fi
if sel knulli; then
  check_label "$KNU_BOOT_N" KNULLI
  check_label "$KNU_SHARE_N" SHARE
  (( KNU_SHARE_N == KNU_BOOT_N + 1 )) || die "knulli SHARE must directly follow its boot partition"
fi
if sel rocknix; then
  check_label "$RNX_BOOT_N" ROCKNIX
  check_label "$RNX_STOR_N" STORAGE
  check_label "$RNX_ROMS_N" GAMES
  check_partuuid "$RNX_ROMS_N"
fi
if [[ $DEVICE == g350 ]] && sel rocknix; then
  blkid -s UUID -o value "$(p "$RNX_BOOT_N")" | grep -q "^$RNX_BOOT_UUID\$" || die "ROCKNIX boot UUID changed"
  blkid -s UUID -o value "$(p "$RNX_STOR_N")" | grep -q "^$RNX_STOR_UUID\$" || die "ROCKNIX STORAGE UUID changed"
  (( $(blkid -o value -s UUID "${blk[@]}" | grep -cx "$RNX_BOOT_UUID") == 1 )) || die "UUID $RNX_BOOT_UUID is used twice"
fi
if [[ $DEVICE == rgb30 ]]; then
  for lbl in ROCKNIX STORAGE KNULLI SHARE; do
    (( $(blkid -o value -s LABEL "${blk[@]}" | grep -cx "$lbl") <= 1 )) || die "label $lbl is used twice"
  done
fi

sync
eject "$DEV" 2>/dev/null || udisksctl power-off -b "$DEV" 2>/dev/null || true
log "Done. Card is safe to remove."
show_plan
