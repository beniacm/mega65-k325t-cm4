# scan_jtag.tcl — read-only JTAG chain scan for the QMTech XC7K325T dev board.
#
# Confirms the FPGA is reachable over JTAG (Xilinx Platform Cable USB 03fd:0013 on the
# board's JTAG header) and reports each device's part + IDCODE. Programs NOTHING.
#
# Expected: one device, IDCODE 0x03651093 (XC7K325T), part xc7k325t*.
#
#   Run via:  scripts/jtag/run_vivado.sh scan_jtag.tcl
set_param labtools.enable_cs_server false
open_hw_manager
connect_hw_server -allow_non_jtag

set found 0
source [file join [file dirname [info script]] targets.tcl]
foreach tgt [m65_hw_targets 0] {
    puts "TARGET=$tgt"
    catch { close_hw_target }
    if { [catch { open_hw_target $tgt } err] } {
        puts "  (open failed: $err)"
        catch { close_hw_target }
        continue
    }
    if { [catch { get_hw_devices } devs] || [llength $devs] == 0 } {
        puts "  (no devices on this target)"
        catch { close_hw_target }
        continue
    }
    foreach dev $devs {
        incr found
        puts "  DEVICE=$dev"
        catch { puts "    PART    = [get_property PART $dev]" }
        catch { puts "    IDCODE  = [get_property IDCODE $dev]" }
        catch { puts "    ID_CODE = [get_property ID_CODE $dev]" }
        catch { puts "    REG.DNA = [get_property REGISTER.DNA $dev]" }
    }
    catch { close_hw_target }
}
puts "DEVICES_FOUND=$found"

disconnect_hw_server
close_hw_manager
puts "SCAN_DONE"
