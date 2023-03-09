# Python script that parses an OpenJ9 verbose log and
# prints a timeline of JVM CPU utilizations.
#
# Usage: python3 jvmCPUTimelineFromVlog.py vlogFilename
#
# Author: Marius Pirvu

import re # for regular expressions
import sys # for accessing parameters and exit

def parseVlog(vlog):
    crtTimeMs = 0
    lastPrintedTime = 0
    jvmCpu = 0
    lastPrintedCpu = 0
    for line in vlog:
        # search for lines with timestamp t= 76254
        match = re.search(r"\st=\s*(\d+)", line)
        if match:
            crtTimeMs = max(crtTimeMs, int(match.group(1)))

        match = re.search(r"\sJvmCpu=(\d+)%", line, re.IGNORECASE) # JvmCpu or JvmCPU
        if match:
            jvmCpu = int(match.group(1)) # last seen CPU utilization

            printIt = True
            # We are about to print the newly read CPU utilization
            # If the timestamp has not changed since the last printout,
            # then we either add a small time bump if the new CPU utilization is different
            # or suppress printing altogether if the new CPU utilization is the same
            if lastPrintedTime == crtTimeMs:
                if lastPrintedCpu != jvmCpu:
                    crtTimeMs += 1
                else:
                    printIt = False
            if printIt:
                print("{time:10d}\t{jvmCpu:3d}".format(time=crtTimeMs, jvmCpu=jvmCpu))
                lastPrintedTime = crtTimeMs
                lastPrintedCpu = jvmCpu

###############################################
# Get the name of vlog
if  len(sys.argv) < 2:
    print ("Program must have an argument: the name of the vlog\n")
    sys.exit(-1)

# Open my file in read only mode with line buffering
vlogFileName = str(sys.argv[1])
Vlog = open(vlogFileName, 'r', 1)

parseVlog(Vlog)
