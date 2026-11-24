mw.l 0xfdc600a0 0xc0004000
mw.l 0xfdc600a4 0xffff5555
sleep 0.1
cp.l 0xfe760070 0x30000000 1
setenv key none
if itest.b *0x30000000 < 0x80; then setenv key volplus; elif itest.b *0x30000001 == 0xfe || itest.b *0x30000001 == 0x7e; then setenv key volminus; fi
