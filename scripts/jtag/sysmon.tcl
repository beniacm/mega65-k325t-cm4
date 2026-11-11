# sysmon.tcl -- read the XC7K325T's XADC (temperature, VCCINT, VCCAUX, VCCBRAM) over JTAG;
# read-only: the running design is not touched.
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
catch { set_property PARAM.FREQUENCY 15000000 [current_hw_target] }
refresh_hw_device -update_hw_probes false $dev
catch { puts "DEVICE=[get_property PART $dev]" }
catch { puts "DONE_PIN=[get_property REGISTER.CONFIG_STATUS.BIT14_DONE_PIN $dev]" }
set sm [get_hw_sysmons -of_objects $dev]
foreach s $sm {
  refresh_hw_sysmon $s
  foreach p {TEMPERATURE VCCINT VCCAUX VCCBRAM MAX_TEMPERATURE MAX_VCCINT MAX_VCCAUX MAX_VCCBRAM MIN_VCCINT MIN_VCCAUX MIN_VCCBRAM} {
    catch { puts "$p=[get_property $p $s]" }
  }
}
catch { puts "CONFIG_STATUS: [report_property -return_string -regexp $dev {REGISTER.CONFIG_STATUS.*}]" }
close_hw_target
disconnect_hw_server
close_hw_manager
