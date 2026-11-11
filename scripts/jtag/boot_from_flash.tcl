# boot_from_flash.tcl -- make the XC7K325T reconfigure itself from the configuration flash
# (slot 0), like a power-on: use after JTAG-loading a design that has no rk_bridge, when the
# Rockchip's core switch (IPROG through the bridge) cannot reach the FPGA any more.
set_param labtools.enable_cs_server false
open_hw_manager
connect_hw_server -allow_non_jtag
set dev ""
source [file join [file dirname [info script]] targets.tcl]
foreach tgt [m65_hw_targets] {
    puts "JTAG_TARGET=$tgt"
    catch { close_hw_target }
    if {[catch { open_hw_target $tgt }]} { catch { close_hw_target }; continue }
    if {[catch { get_hw_devices } devs] || [llength $devs] == 0} { catch { close_hw_target }; continue }
    set dev [lindex $devs 0]; break
}
if {$dev eq ""} { puts "ERROR: no JTAG device"; exit 1 }
current_hw_device $dev
boot_hw_device $dev
puts "BOOT_FROM_FLASH_DONE"
close_hw_target
disconnect_hw_server
close_hw_manager
