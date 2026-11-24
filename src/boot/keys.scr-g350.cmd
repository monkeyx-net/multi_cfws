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
