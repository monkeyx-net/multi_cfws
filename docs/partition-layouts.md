# Partition layouts

The card each device gets with all its OSes, from `--plan` (G350 and RGB30 on a 128 GB
card, CubeXX on 64 GB). ROM partition sizes grow with the card. Fewer OSes keep the same
order but leave partitions out; run `--plan` for your exact layout. Arrows show what
U-Boot reads to start each OS; **bold** partitions sit at numbers the OS hard-codes.

## G350 (MBR): 6 OSes, default dArkOS

With ROCKNIX on the card, U-Boot comes from the ROCKNIX image and runs `boot.scr`;
without it, dArkOS's U-Boot runs `boot.ini`. Without AmberELEC, p1 is a 64M `BOOTSEL`.

```mermaid
flowchart TB
    UB["U-Boot (sectors 64-32767)<br/>from the ROCKNIX image"] --> P1

    subgraph AMB["AmberELEC · Vol-"]
        P1["<b>p1 2G FAT AMBRELC</b><br/>KERNEL/SYSTEM + shared boot.scr"]
        P2["<b>p2 8G ext4 STORAGE</b>"]
        P3["<b>p3 exFAT GAMES</b> roms"]
    end
    P4["p4 extended (logical partitions from p5)"]
    subgraph KNU["knulli · L1"]
        P5["<b>p5 3G FAT KNULLI</b><br/>boot + squashfs"]
        P6["<b>p6 exFAT SHARE</b> /userdata"]
    end
    subgraph DRK["dArkOS · no key (default)"]
        P7["p7 exFAT EASYROMS roms"]
        P8["p8 100M FAT BOOT"]
        P9["p9 12G btrfs ROOTFS"]
    end
    subgraph ARK["ArkOS · Vol+"]
        P10["p10 112M FAT ARKBOOT"]
        P11["p11 12G ext4 root"]
        P12["p12 exFAT ARKROMS roms"]
    end
    subgraph RNX["ROCKNIX · L2"]
        P13["p13 2G FAT ROCKNIX<br/>KERNEL/SYSTEM + extlinux"]
        P14["p14 8G ext4 STORAGE"]
        P15["p15 exFAT GAMES roms"]
    end
    subgraph MUS["MustardOS · R1"]
        P16["p16 512M FAT BOOT"]
        P17["p17 8G ext4 ROOTFS"]
        P18["<b>p18 exFAT ROMS</b> (must be last)"]
    end

    P1 -- "no key" --> P8
    P1 -- "Vol-" --> P1
    P1 -- "Vol+" --> P10
    P1 -- "L1" --> P5
    P1 -- "R1" --> P16
    P1 -- "L2" --> P13
```

- AmberELEC must be p1-p3: its initramfs mounts p3 as ROMs. knulli must be p5/p6 (SHARE =
  boot + 1, one digit). MustardOS ROMS must be last (its factory reset grows it).
- AmberELEC and ROCKNIX boot by filesystem UUID (both have an ext4 `STORAGE`), the others
  by PARTUUID or label.

## RGB30 (GPT): 4 OSes, default dArkOS

U-Boot always comes from the knulli image; it boots `boot.scr` from `BOOTSEL`, the only
partition marked bootable.

```mermaid
flowchart TB
    UB["U-Boot (sectors 64-32767)<br/>from the knulli image"] --> P6

    subgraph KNU["knulli · Vol+"]
        P1["<b>p1 4G FAT KNULLI</b><br/>boot + squashfs"]
        P2["<b>p2 exFAT SHARE</b> /userdata"]
    end
    subgraph DRK["dArkOS · no key (default)"]
        P3["<b>p3 100M FAT BOOT</b>"]
        P4["<b>p4 12G btrfs ROOTFS</b>"]
        P5["<b>p5 exFAT EASYROMS</b> roms"]
    end
    P6["p6 64M FAT32 BOOTSEL<br/>shared boot.scr (bootable)"]
    subgraph RNX["ROCKNIX · L1"]
        P7["p7 2G FAT ROCKNIX<br/>KERNEL/SYSTEM"]
        P8["p8 8G ext4 STORAGE"]
        P9["p9 exFAT GAMES roms"]
    end
    subgraph ARK["ArkOS · Vol-"]
        P10["p10 100M FAT ARKBOOT"]
        P11["p11 12G ext4 root"]
        P12["p12 exFAT ARKROMS roms"]
    end

    P6 -- "no key" --> P3
    P6 -- "Vol-" --> P10
    P6 -- "Vol+" --> P1
    P6 -- "L1" --> P7
```

- dArkOS must be p3/p4/p5: its initramfs hard-codes `/dev/mmcblk1p4`. knulli must be
  p1/p2. ArkOS's initramfs is patched to follow `root=`, so it can go last.

## RG CubeXX (GPT, 16 entries): 3 OSes, default knulli

The vendor U-Boot from the knulli image finds partitions by GPT name. The shared `env`
partition holds the multi-boot (`boot_normal` reads Vol+/Vol- and runs the chosen OS).
Sectors 16-511 are zeroed (the boot ROM tries sector 16 first).

```mermaid
flowchart TB
    UB["boot0 + U-Boot (sectors 512-73727)<br/>vendor, from the knulli image"] --> P1
    P1["p1 16M env<br/>shared U-Boot env: keys + mb_&lt;os&gt;"]

    subgraph KNU["knulli · no key (default)"]
        P3["<b>p3 5G FAT KNULLI</b><br/>squashfs + bootlogo (boot-resource)"]
        P4["<b>p4 exFAT SHARE</b> /userdata"]
        P6["p6 20M raw boot<br/>kernel + ramdisk"]
    end
    subgraph MUS["MustardOS · Vol-"]
        P2["p2 32M FAT BOOT<br/>bootlogo"]
        P5["<b>p5 8G ext4 ROOTFS</b>"]
        P7["p7 64M raw boot_mustardos<br/>kernel + ramdisk"]
        P13["<b>p13 exFAT ROMS</b> (must be last)"]
    end
    subgraph RNX["ROCKNIX · Vol+"]
        P8["p8 2G FAT ROCKNIX<br/>KERNEL/SYSTEM"]
        P9["p9 8G ext4 STORAGE"]
        P10["p10 48M raw boot_rocknix<br/>KERNEL as Android boot image"]
        P11["p11 1M raw dtb_rocknix<br/>fixed CubeXX dtb"]
        P12["p12 exFAT GAMES roms"]
    end

    P1 -- "no key" --> P6
    P1 -- "Vol-" --> P7
    P1 -- "Vol+" --> P10
    P10 -.-> P11
```

- knulli's ramdisk hard-codes p3 (SHARE = p4) and MustardOS's p5. The default OS's boot
  image is named `boot`, the others `boot_<os>`; partition names, not numbers, follow the
  default.
