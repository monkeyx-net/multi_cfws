#!/bin/bash
# Build a dArkOS + AmberELEC dual-boot SD card for the G350.
# Usage: sudo bash flash-dualboot.sh [/dev/sdX]      (default /dev/sdb)
# ERASES THE WHOLE TARGET DEVICE.
set -euo pipefail

DEV=${1:-/dev/sdb}
HERE=$(cd "$(dirname "$0")" && pwd)
IMGDIR=${IMGDIR:-/run/media/tim/DATA/torrent.linux/img}   # override: sudo IMGDIR=/path bash ...
AMB=$IMGDIR/AmberELEC-RG351MP.aarch64-prerelease-20261019_1835.img
DARK=$IMGDIR/dArkOS_G350_trixie_01012026.img
DISKID=0x5a3c9e71            # must match root=PARTUUID=5a3c9e71-07 in boot.ini
S=512
MNT=$(mktemp -d /tmp/g350dual.XXXX)

# Source partitions inside the images (start, size in 512-byte sectors; from sfdisk -d)
AMB_P1_START=32768;   AMB_P1_SIZE=4194304     # FAT AMBRELC 2G, UUID 1910-5621
AMB_P2_START=4227072; AMB_P2_SIZE=65536       # ext4 STORAGE 32M, UUID 7ab6cd9f-...
DRK_P1_START=32768;   DRK_P1_SIZE=204800      # FAT BOOT 100M
DRK_P2_START=237568;  DRK_P2_SIZE=15360000    # btrfs ROOTFS 7.3G

log()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
p()    { echo "${DEV}$1"; }        # /dev/sdb + N  (use ${DEV}p for mmcblk/nvme)
[[ $DEV == *mmcblk* || $DEV == *nvme* || $DEV == *loop* ]] && p() { echo "${DEV}p$1"; }

unmount_dev() {
  udevadm settle || true
  local m
  for m in $(awk -v d="$DEV" 'index($1,d)==1 {print $2}' /proc/mounts | sort -r); do
    umount "$m" || umount -l "$m"
  done
}
cleanup() { set +e; for d in "$MNT"/*; do mountpoint -q "$d" && umount "$d"; done; rm -rf "$MNT"; }
trap cleanup EXIT

# ---------------------------------------------------------------- sanity
[[ $EUID -eq 0 ]] || die "run as root (sudo)"
[[ -b $DEV ]] || die "$DEV is not a block device"
[[ -f $AMB && -f $DARK ]] || die "images not found in $IMGDIR"
for f in boot.ini boot.ini.darkos; do [[ -f $HERE/$f ]] || die "missing $HERE/$f"; done
for t in sfdisk wipefs e2fsck resize2fs debugfs btrfs mkfs.exfat tar; do
  command -v $t >/dev/null || die "missing tool: $t"; done
SIZE=$(blockdev --getsize64 "$DEV")
(( SIZE > 55*1000**3 && SIZE < 66*1000**3 )) || die "$DEV is $SIZE bytes, expected the ~62.5GB (58.2GiB) card"
findmnt -rno SOURCE / | grep -q "^$DEV" && die "$DEV holds the running root fs!"

lsblk -o NAME,SIZE,FSTYPE,LABEL,MODEL,TRAN "$DEV"
echo
read -rp "ALL DATA ON $DEV WILL BE DESTROYED. Type YES to continue: " ans
[[ $ans == YES ]] || die "aborted"

# ---------------------------------------------------------------- 1. partition
log "Unmounting and wiping $DEV"
unmount_dev
for part in "$DEV"?*; do [[ -b $part ]] && wipefs -a "$part" >/dev/null || true; done
wipefs -a "$DEV"

TOTAL=$(blockdev --getsz "$DEV")
P1_START=32768;            P1_SIZE=$((2*1024*1024*2))      # 2 GiB  AMBRELC
P2_START=$((P1_START+P1_SIZE)); P2_SIZE=$((8*1024*1024*2)) # 8 GiB  STORAGE
# p3 must be AmberELEC's games partition: its initramfs (inside KERNEL) mounts the
# first of mmcblk1p3/mmcblk1p1/mmcblk1/mmcblk0p3 at /storage/roms. dArkOS mounts
# LABEL=EASYROMS, so its roms live in a logical partition (p5) instead.
P3_START=$((P2_START+P2_SIZE)); P3_SIZE=$((16*1024*1024*2)) # 16 GiB GAMES (AmberELEC)
EXT_START=$((P3_START+P3_SIZE))                            # p4 extended
P5_START=$((EXT_START+2048))                               # EASYROMS (dArkOS), rest
BOOT_SIZE=$((100*1024*2)); ROOT_SIZE=$((16*1024*1024*2))   # 100M BOOT, 16G ROOTFS
P5_SIZE=$(( (TOTAL - P5_START - 2048 - BOOT_SIZE - 2048 - ROOT_SIZE - 2048) / 2048 * 2048 ))
(( P5_SIZE > 0 )) || die "card too small"
P6_START=$((P5_START+P5_SIZE+2048))                        # BOOT
P7_START=$((P6_START+BOOT_SIZE+2048))                      # ROOTFS, fills to end

log "Writing MBR (label-id $DISKID)"
sfdisk --wipe always --wipe-partitions always "$DEV" <<EOF
label: dos
label-id: $DISKID
unit: sectors

start=$P1_START, size=$P1_SIZE, type=c, bootable
start=$P2_START, size=$P2_SIZE, type=83
start=$P3_START, size=$P3_SIZE, type=7
start=$EXT_START, type=5
start=$P5_START, size=$P5_SIZE, type=7
start=$P6_START, size=$BOOT_SIZE, type=c
start=$P7_START, type=83
EOF
partprobe "$DEV" 2>/dev/null || blockdev --rereadpt "$DEV" || true
unmount_dev
for n in 1 2 3 5 6 7; do [[ -b $(p $n) ]] || die "$(p $n) missing after partitioning"; done

# ---------------------------------------------------------------- 2. U-Boot
log "Writing U-Boot (sectors 64-32767) from dArkOS G350 image"
dd if="$DARK" of="$DEV" bs=$S skip=64 seek=64 count=32704 conv=notrunc,fsync status=none

# ---------------------------------------------------------------- 3. copy partitions
copy_part() {  # image start size target
  log "dd $(basename "$1") [$2 +$3] -> $4"
  dd if="$1" of="$4" bs=4M iflag=skip_bytes,count_bytes skip=$(($2*S)) count=$(($3*S)) \
     conv=fsync status=progress
}
copy_part "$AMB"  $AMB_P1_START $AMB_P1_SIZE "$(p 1)"
copy_part "$AMB"  $AMB_P2_START $AMB_P2_SIZE "$(p 2)"
copy_part "$DARK" $DRK_P1_START $DRK_P1_SIZE "$(p 6)"
copy_part "$DARK" $DRK_P2_START $DRK_P2_SIZE "$(p 7)"
unmount_dev

log "AmberELEC STORAGE: fsck + grow to 8G + remove .please_resize_me"
e2fsck -fy "$(p 2)" || [[ $? -le 1 ]] || die "e2fsck failed on $(p 2)"
resize2fs "$(p 2)"
debugfs -w -R "rm /.please_resize_me" "$(p 2)"
listing=$(debugfs -R "ls -l /" "$(p 2)" 2>/dev/null)
[[ $listing != *please_resize_me* ]] || die "resize flag still present"

log "Creating exFAT GAMES (AmberELEC roms) on $(p 3)"
mkfs.exfat -c 16K -L GAMES "$(p 3)"      # AmberELEC creates its folders via tmpfiles.d
log "Creating exFAT EASYROMS (dArkOS roms) on $(p 5)"
mkfs.exfat -c 16K -L EASYROMS "$(p 5)"
unmount_dev

# ---------------------------------------------------------------- 4. dArkOS first-boot work
mkdir -p "$MNT/root" "$MNT/boot" "$MNT/roms" "$MNT/amb"
mount -t btrfs -o compress=zlib:1 "$(p 7)" "$MNT/root"
mount -t vfat  "$(p 6)" "$MNT/boot"
mount -t exfat "$(p 5)" "$MNT/roms"

log "Growing btrfs ROOTFS to fill $(p 7)"
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
cp -f "$HERE/boot.ini.darkos" "$MNT/boot/boot.ini"
echo "--- dArkOS /etc/fstab:"; cat "$MNT/root/etc/fstab"

# ---------------------------------------------------------------- 5. shared boot.ini on p1
log "Installing dual-boot boot.ini on AMBRELC ($(p 1))"
mount -t vfat "$(p 1)" "$MNT/amb"
cp -f "$MNT/amb/boot.ini" "$MNT/amb/boot.ini.amberelec"
cp -f "$HERE/boot.ini"    "$MNT/amb/boot.ini"
cp -f "$HERE/boot.ini"    "$MNT/amb/boot.ini.dualboot"
cp -f "$MNT/boot/logo.bmp" "$MNT/amb/logo.bmp"     # U-Boot loads logo.bmp from mmc 1:1
ls -l "$MNT/amb"

sync
for d in amb roms boot root; do umount "$MNT/$d"; done

# ---------------------------------------------------------------- 6. verify
log "Verification"
sfdisk -d "$DEV"
blkid "$(p 1)" "$(p 2)" "$(p 3)" "$(p 5)" "$(p 6)" "$(p 7)"
blkid -s PARTUUID -o value "$(p 7)" | grep -qi '^5a3c9e71-07$' || die "PARTUUID of $(p 7) is not 5a3c9e71-07"
blkid -s LABEL -o value "$(p 3)" | grep -q '^GAMES$'    || die "$(p 3) is not GAMES"
blkid -s LABEL -o value "$(p 5)" | grep -q '^EASYROMS$' || die "$(p 5) is not EASYROMS"
blkid -s UUID -o value "$(p 1)" | grep -q '^1910-5621$' || die "AMBRELC UUID changed"
blkid -s UUID -o value "$(p 2)" | grep -q '^7ab6cd9f-77f8-4d13-905e-aa8fc5baa800$' || die "STORAGE UUID changed"
dumpe2fs -h "$(p 2)" 2>/dev/null | grep -E 'Block count|Block size'

sync
eject "$DEV" 2>/dev/null || udisksctl power-off -b "$DEV" 2>/dev/null || true
log "Done. Card is safe to remove."
