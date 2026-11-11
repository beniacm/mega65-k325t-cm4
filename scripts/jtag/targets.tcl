# targets.tcl -- which JTAG cable a script may use (sourced by the scripts in this directory).
# With one cable on the PC nothing changes.  With several (two rigs on one PC) the cable has to be
# named: M65_JTAG=<part of the target name>, e.g. the cable's serial number as scan_jtag.tcl prints
# it (localhost:3121/xilinx_tcf/<vendor>/<serial>).  Without it the scripts stop instead of
# programming whichever board happens to come first.
proc m65_hw_targets {{need_one 1}} {
    set all [get_hw_targets]
    if {[info exists ::env(M65_JTAG)] && $::env(M65_JTAG) ne ""} {
        set sel {}
        foreach t $all { if {[string match "*$::env(M65_JTAG)*" $t]} { lappend sel $t } }
        if {[llength $sel] == 0} {
            puts "ERROR: no JTAG target matches M65_JTAG=$::env(M65_JTAG) (present: $all)"; exit 1
        }
        if {$need_one && [llength $sel] > 1} {
            puts "ERROR: M65_JTAG=$::env(M65_JTAG) matches several targets: $sel"; exit 1
        }
        return $sel
    }
    if {$need_one && [llength $all] > 1} {
        puts "ERROR: several JTAG cables present ($all): set M65_JTAG=<part of the target name>"; exit 1
    }
    return $all
}
