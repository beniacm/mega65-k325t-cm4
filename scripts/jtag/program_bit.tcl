# program_bit.tcl — JTAG-program a .bit onto the QMTech XC7K325T over the Xilinx
# cable (03fd:0013).  This loads the design into SRAM (volatile) — it is lost on FPGA
# power cycle (use rockchip/m65flash.py for the configuration flash).
#   scripts/jtag/run_vivado.sh program_bit.tcl <bit-path>
if {$argc < 1} { puts "ERROR: usage: program_bit.tcl <file.bit>"; exit 1 }
set bit [lindex $argv 0]
if {![file exists $bit]} { puts "ERROR: bitstream not found: $bit"; exit 1 }

set_param labtools.enable_cs_server false
open_hw_manager
connect_hw_server -allow_non_jtag
set dev ""
foreach tgt [get_hw_targets] {
    catch { close_hw_target }
    if {[catch { open_hw_target $tgt }]} { catch { close_hw_target }; continue }
    if {[catch { get_hw_devices } devs] || [llength $devs] == 0} { catch { close_hw_target }; continue }
    set dev [lindex $devs 0]; break
}
if {$dev eq ""} { puts "ERROR: no JTAG device (board powered?)"; exit 1 }
current_hw_device $dev
catch { set_property PARAM.FREQUENCY 15000000 [current_hw_target] }
refresh_hw_device -update_hw_probes false $dev
set_property PROGRAM.FILE $bit $dev
program_hw_devices $dev
refresh_hw_device -update_hw_probes false $dev
set prog "unknown"
catch { set prog [get_property PROGRAM.IS_PROGRAMMED $dev] }
puts "IS_PROGRAMMED=$prog"
puts "PROGRAMMED=$bit"
puts "PROGRAM_BIT_DONE"
close_hw_target
disconnect_hw_server
close_hw_manager
