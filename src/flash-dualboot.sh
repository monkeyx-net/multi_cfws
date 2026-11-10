#!/bin/bash
# Build a G350 multi-boot SD card with any 2 or 3 of dArkOS / AmberELEC / MustardOS.
# Usage: sudo bash src/flash-dualboot.sh [--os darkos,amberelec,mustardos] [--default OS] [--plan] [/dev/sdX]
#   --os       comma list of OSes to install (at least 2; default: all three)
#   --default  OS that boots with no key held (default: darkos if selected, else the first --os entry)
#   --plan     print the partition layout, key mapping and boot.ini, then exit (no root, no writes)
# The target defaults to /dev/sdb. ERASES THE WHOLE TARGET DEVICE.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
IMGDIR=${IMGDIR:-$(dirname "$HERE")/img}   # override: sudo IMGDIR=/path bash ...
AMB=$IMGDIR/AmberELEC-RG351MP.aarch64-prerelease-20261019_1835.img
DARK=$IMGDIR/dArkOS_G350_trixie_01012026.img     # always needed: U-Boot + logo.bmp come from it
MUS=$IMGDIR/MustardOS_RK-G350-V_2606.0_ANDROMEDA-db2a3bd0.img
DISKID=0x5a3c9e71            # root=PARTUUID=5a3c9e71-NN in boot.ini
S=512

# Source partitions inside the images (start, size in 512-byte sectors; from sfdisk -d)
AMB_P1_START=32768;   AMB_P1_SIZE=4194304     # FAT AMBRELC 2G, UUID 1910-5621
AMB_P2_START=4227072; AMB_P2_SIZE=65536       # ext4 STORAGE 32M, UUID 7ab6cd9f-...
DRK_P1_START=32768;   DRK_P1_SIZE=204800      # FAT BOOT 100M
DRK_P2_START=237568;  DRK_P2_SIZE=15360000    # btrfs ROOTFS 7.3G
MUS_P1_START=32768;   MUS_P1_SIZE=1048576     # FAT BOOT 512M (GPT)
MUS_P2_START=1081344; MUS_P2_SIZE=16777216    # ext4 ROOTFS 8G

log()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

usage() { sed -n '2,7s/^# \{0,1\}//p' "$0"; }

# ---------------------------------------------------------------- arguments
ALL_OS=(amberelec darkos mustardos)     # fixed order: also the order keys are handed out
OS_ARG=darkos,amberelec,mustardos
DEFAULT=
PLAN=0
DEV=/dev/sdb
while (($#)); do
  case $1 in
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

USER_OS=()                                  # in the order given, deduplicated
IFS=, read -ra _list <<<"${OS_ARG,,}"
for o in "${_list[@]}"; do
  o=${o//[[:space:]]/}
  [[ -z $o ]] && continue
  [[ " ${ALL_OS[*]} " == *" $o "* ]] || die "unknown OS '$o' (choose from: ${ALL_OS[*]})"
  [[ " ${USER_OS[*]} " == *" $o "* ]] || USER_OS+=("$o")
done
(( ${#USER_OS[@]} >= 2 )) || die "--os needs at least 2 of: ${ALL_OS[*]}"
sel() { [[ " ${USER_OS[*]} " == *" $1 "* ]]; }

DEFAULT=${DEFAULT,,}
if [[ -z $DEFAULT ]]; then
  if sel darkos; then DEFAULT=darkos; else DEFAULT=${USER_OS[0]}; fi
fi
sel "$DEFAULT" || die "--default $DEFAULT is not one of the selected OSes (${USER_OS[*]})"

osname() { case $1 in amberelec) echo AmberELEC ;; darkos) echo dArkOS ;; mustardos) echo MustardOS ;; esac; }

# Keys: default = no key; the others, in ALL_OS order, get Vol- (pin 65) then Vol+ (pin 64).
KEY_OS=()
for o in "${ALL_OS[@]}"; do sel "$o" && [[ $o != "$DEFAULT" ]] && KEY_OS+=("$o"); done
KEY_PIN=(65 64)
KEY_NAME=(Vol- Vol+)

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
if (( PLAN )); then
  TOTAL=$(blockdev --getsz "$DEV" 2>/dev/null) || TOTAL=
  if [[ -z $TOTAL ]]; then
    TOTAL=122070312                          # ~62.5GB card
    echo "(plan: $DEV not readable, assuming a $TOTAL-sector / 62.5GB card)"
  fi
  for f in "$DARK" $(sel amberelec && echo "$AMB") $(sel mustardos && echo "$MUS"); do
    [[ -f $f ]] || echo "(plan: WARNING image not found: $f)"
  done
else
  [[ $EUID -eq 0 ]] || die "run as root (sudo)"
  [[ -b $DEV ]] || die "$DEV is not a block device"
  [[ -f $DARK ]] || die "image not found: $DARK (needed for U-Boot even without dArkOS)"
  if sel amberelec; then [[ -f $AMB ]] || die "image not found: $AMB"; fi
  if sel mustardos; then [[ -f $MUS ]] || die "image not found: $MUS"; fi
  for f in $(sel darkos && echo boot.ini.darkos) $(sel mustardos && echo boot.ini.mustardos); do
    [[ -f $HERE/$f ]] || die "missing $HERE/$f"
  done
  tools=(sfdisk wipefs mkfs.exfat blkid partprobe)
  sel amberelec && tools+=(e2fsck resize2fs debugfs dumpe2fs)
  sel amberelec || tools+=(mkfs.vfat)
  sel darkos    && tools+=(btrfs tar)
  sel mustardos && tools+=(e2fsck)
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
BOOTSEL_SIZE=$((64*MIB))
DRK_BOOT_SIZE=$((100*MIB)); DRK_ROOT_SIZE=$((12*GIB))
# MUS_P1_SIZE (512M) and MUS_P2_SIZE (8G) are copied 1:1

# Space left for ROM partitions, split equally between the selected OSes.
fixed=$P1_START; nlog=0
if sel amberelec; then fixed=$((fixed+AMB_BOOT_SIZE+AMB_STOR_SIZE)); else fixed=$((fixed+BOOTSEL_SIZE)); fi
if sel darkos;    then fixed=$((fixed+DRK_BOOT_SIZE+DRK_ROOT_SIZE)); nlog=$((nlog+3)); fi
if sel mustardos; then fixed=$((fixed+MUS_P1_SIZE+MUS_P2_SIZE));     nlog=$((nlog+3)); fi
fixed=$((fixed+nlog*GAP))
FREE=$((TOTAL-fixed))
NROM=${#USER_OS[@]}
ROM_SIZE=$(( FREE / NROM / MIB * MIB ))
LAST_ROM_SIZE=$(( (FREE - (NROM-1)*ROM_SIZE) / MIB * MIB ))   # last ROM partition takes the remainder
(( ROM_SIZE > 2*GIB )) || die "card too small ($TOTAL sectors)"

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

if sel amberelec; then
  # p3 must be AmberELEC's games partition: its initramfs (inside KERNEL) mounts the
  # first of mmcblk1p3/mmcblk1p1/mmcblk1/mmcblk0p3 at /storage/roms. U-Boot reads
  # boot.ini from p1, so AmberELEC's FAT partition stays p1.
  AMB_BOOT_N=1; AMB_STOR_N=2; AMB_ROMS_N=3
  primary 1 $AMB_BOOT_SIZE c "FAT32 AMBRELC: AmberELEC KERNEL/SYSTEM + shared boot.ini" bootable
  primary 2 $AMB_STOR_SIZE 83 "ext4 STORAGE: AmberELEC /storage"
  next_rom_size; primary 3 "$RSZ" 7 "exFAT GAMES: AmberELEC roms"; ROM_N+=(3)
  extended 4
else
  # No AmberELEC: a small FAT partition only holds the shared boot.ini and logo.bmp.
  primary 1 $BOOTSEL_SIZE c "FAT32 BOOTSEL: shared boot.ini + logo.bmp" bootable
  extended 2
fi
if sel darkos; then
  # dArkOS mounts LABEL=EASYROMS, so its roms can live in a logical partition.
  next_rom_size
  logical "$RSZ" 7 "exFAT EASYROMS: dArkOS roms";   DRK_ROMS_N=$LAST_N; ROM_N+=("$LAST_N")
  logical $DRK_BOOT_SIZE c     "FAT BOOT: dArkOS /boot";        DRK_BOOT_N=$LAST_N
  logical $DRK_ROOT_SIZE 83    "btrfs ROOTFS: dArkOS root";     DRK_ROOT_N=$LAST_N
fi
if sel mustardos; then
  logical $MUS_P1_SIZE c       "FAT BOOT: MustardOS kernel + dtbs"; MUS_BOOT_N=$LAST_N
  logical $MUS_P2_SIZE 83      "ext4 ROOTFS: MustardOS root";       MUS_ROOT_N=$LAST_N
  # MustardOS ROMS MUST be the last partition: its factory reset resizes its rom
  # partition to 100% (patched out below, but harmless if it runs).
  logical "" 7                 "exFAT ROMS: MustardOS roms (last)"; MUS_ROM_N=$LAST_N; ROM_N+=("$LAST_N")
fi
(( pos <= TOTAL )) || die "layout overflows the card ($pos > $TOTAL sectors)"

pu() { printf '%s-%02x' "${DISKID#0x}" "$1"; }      # MBR PARTUUID of partition N

# ---------------------------------------------------------------- boot.ini
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
branch() {
  case $1 in
    amberelec) branch_amberelec ;;
    darkos)    branch_darkos "$DRK_BOOT_N" "$DRK_ROOT_N" ;;
    mustardos) branch_mustardos "$MUS_BOOT_N" "$MUS_ROOT_N" ;;
  esac
}

gen_boot_ini() {
  local i l title oslist
  title="$(osname "$DEFAULT") (no key)"
  oslist=$(IFS=,; echo "${USER_OS[*]}")
  for i in "${!KEY_OS[@]}"; do title+=" / $(osname "${KEY_OS[$i]}") (${KEY_NAME[$i]})"; done
  echo "odroidgoa-uboot-config"
  echo
  echo "# ---- G350 multi-boot: $title ----"
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
  for i in "${!KEY_OS[@]}"; do
    if (( i == 0 )); then echo "if gpio input ${KEY_PIN[$i]}; then"; else echo "elif gpio input ${KEY_PIN[$i]}; then"; fi
    branch "${KEY_OS[$i]}"
  done
  echo "else"
  branch "$DEFAULT"
  echo "fi"
}
BOOT_INI=$(gen_boot_ini)

render() {  # template BOOT ROOT -> stdout
  sed -e "s/@BOOT@/$2/g" -e "s/@ROOT@/$3/g" -e "s/@ROOT2@/$(printf %02x "$3")/g" "$1"
}

# ---------------------------------------------------------------- summary
show_plan() {
  local i
  echo "OSes:     ${USER_OS[*]}"
  echo "Keys:     no key = $(osname "$DEFAULT")"
  for i in "${!KEY_OS[@]}"; do echo "          ${KEY_NAME[$i]} (pin ${KEY_PIN[$i]}) = $(osname "${KEY_OS[$i]}")"; done
  echo "Partitions on $DEV ($(hsize "$TOTAL")):"
  printf '  %s\n' "${LAYOUT[@]}"
  echo "Numbers:  DRK_ROMS_N=${DRK_ROMS_N:--} DRK_BOOT_N=${DRK_BOOT_N:--} DRK_ROOT_N=${DRK_ROOT_N:--}" \
       "MUS_BOOT_N=${MUS_BOOT_N:--} MUS_ROOT_N=${MUS_ROOT_N:--} MUS_ROM_N=${MUS_ROM_N:--}"
}

if (( PLAN )); then
  show_plan
  echo; echo "---- sfdisk script"; printf '%s' "$SFD"
  echo; echo "---- boot.ini (p1)"; echo "$BOOT_INI"
  if sel darkos; then
    echo; echo "---- dArkOS /boot/boot.ini (p$DRK_BOOT_N)"; render "$HERE/boot.ini.darkos" "$DRK_BOOT_N" "$DRK_ROOT_N"
  fi
  if sel mustardos; then
    echo; echo "---- MustardOS /boot.ini (p$MUS_BOOT_N)"; render "$HERE/boot.ini.mustardos" "$MUS_BOOT_N" "$MUS_ROOT_N"
  fi
  exit 0
fi

MNT=$(mktemp -d /tmp/g350dual.XXXX)
cleanup() { set +e; for d in "$MNT"/*; do mountpoint -q "$d" && umount "$d"; done; rm -rf "$MNT"; }
trap cleanup EXIT
mkdir -p "$MNT"/{p1,drkimg,root,boot,roms,muroot,muboot}

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
log "Writing U-Boot (sectors 64-32767) from dArkOS G350 image"
dd if="$DARK" of="$DEV" bs=$S skip=64 seek=64 count=32704 conv=notrunc,fsync status=none

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
unmount_dev

if sel amberelec; then
  log "AmberELEC STORAGE: fsck + grow to 8G + remove .please_resize_me"
  e2fsck -fy "$(p "$AMB_STOR_N")" || [[ $? -le 1 ]] || die "e2fsck failed on $(p "$AMB_STOR_N")"
  resize2fs "$(p "$AMB_STOR_N")"
  debugfs -w -R "rm /.please_resize_me" "$(p "$AMB_STOR_N")"
  listing=$(debugfs -R "ls -l /" "$(p "$AMB_STOR_N")" 2>/dev/null)
  [[ $listing != *please_resize_me* ]] || die "resize flag still present"
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
sel amberelec || wipefs_list+=("$(p 1)")
wipefs -a "${wipefs_list[@]}" >/dev/null
if sel amberelec; then
  log "Creating exFAT GAMES (AmberELEC roms) on $(p "$AMB_ROMS_N")"
  mkfs.exfat -F -c 16K -L GAMES "$(p "$AMB_ROMS_N")"   # AmberELEC creates its folders via tmpfiles.d
else
  log "Creating FAT32 BOOTSEL on $(p 1)"
  mkfs.vfat -F 32 -n BOOTSEL "$(p 1)"
fi
if sel darkos; then
  log "Creating exFAT EASYROMS (dArkOS roms) on $(p "$DRK_ROMS_N")"
  mkfs.exfat -F -c 16K -L EASYROMS "$(p "$DRK_ROMS_N")"
fi
if sel mustardos; then
  log "Creating exFAT ROMS (MustardOS roms) on $(p "$MUS_ROM_N")"
  mkfs.exfat -F -c 16K -L ROMS "$(p "$MUS_ROM_N")"     # MustardOS reformats it on first boot
fi
unmount_dev

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
  # roms.tar is kept: expandtoexfat.sh keeps it on G350 (rk3326-g350-linux.dtb present)

  log "Finishing dArkOS first-boot steps (fstab, disable firstboot.service)"
  cp -f "$MNT/boot/fstab.exfat" "$MNT/root/etc/fstab"
  rm -f "$MNT/root/etc/systemd/system/multi-user.target.wants/firstboot.service"
  rm -f "$MNT/boot/firstboot.sh" "$MNT/boot/expandtoexfat.sh" "$MNT/boot/fstab.exfat" "$MNT"/boot/doneit*
  render "$HERE/boot.ini.darkos" "$DRK_BOOT_N" "$DRK_ROOT_N" > "$MNT/boot/boot.ini"
  echo "--- dArkOS /etc/fstab:"; cat "$MNT/root/etc/fstab"
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
  sync
  umount "$MNT/muboot" "$MNT/muroot"
fi

# ---------------------------------------------------------------- 6. shared boot.ini on p1
log "Installing multi-boot boot.ini on $(p 1)"
printf '%s\n' "$BOOT_INI" > "$HERE/boot.ini.generated"
[[ -n ${SUDO_USER:-} ]] && chown "$SUDO_USER:" "$HERE/boot.ini.generated" || true
mount -t vfat "$(p 1)" "$MNT/p1"
if sel amberelec; then cp -f "$MNT/p1/boot.ini" "$MNT/p1/boot.ini.amberelec"; fi
cp -f "$HERE/boot.ini.generated" "$MNT/p1/boot.ini"
cp -f "$HERE/boot.ini.generated" "$MNT/p1/boot.ini.dualboot"
# U-Boot loads logo.bmp from mmc 1:1; take it from the dArkOS image's BOOT partition.
mount -o ro,loop,offset=$((DRK_P1_START*S)),sizelimit=$((DRK_P1_SIZE*S)) -t vfat "$DARK" "$MNT/drkimg"
cp -f "$MNT/drkimg/logo.bmp" "$MNT/p1/logo.bmp"
umount "$MNT/drkimg"
ls -l "$MNT/p1"
sync
umount "$MNT/p1"

# ---------------------------------------------------------------- 7. verify
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
  check_label 1 BOOTSEL
fi
if sel darkos; then
  check_label "$DRK_ROMS_N" EASYROMS
  check_partuuid "$DRK_ROOT_N"
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

sync
eject "$DEV" 2>/dev/null || udisksctl power-off -b "$DEV" 2>/dev/null || true
log "Done. Card is safe to remove."
show_plan
