# Python script that parses an OpenJ9 verbose log and
# prints compilation queue size and JVM CPU utilization in time
# the printout consists of 3 columns separated by tabs
# so that they can be printed in excel
# The first column represents the time since the start of the JVM,
# the second colums is the compilation queue size and the third
# column is the CPU utilization of teh JVM in percentage points
# (e.g. 350 means the JVM is using 3.5P worth of CPU)
# Note that for the timestamps have a 10ms granularity, so in between
# two timestamps we can have a lot of Q_SZ fluctuation. In such cases
# we print the maximum value of the Q_SZ seen in between two timestamps
#
# Usage: python3 queueSizeFromVlog.py vlogFilename
#
# Author: Marius Pirvu

import re # for regular expressions
import sys # for accessing parameters and exit


def parseVlog(vlog):
    # + (cold) sun/reflect/Reflection.getCallerClass()Ljava/lang/Class; @ 00007FB21300003C-00007FB213000167 OrdinaryMethod - Q_SZ=1 Q_SZI=1 QW=2 j9m=000000000004D1D8 bcsz=2 JNI time=995us mem=[region=704 system=2048]KB compThreadID=0 CpuLoad=163%(10%avg) JvmCpu=0%
    compEndPattern  = re.compile('^\+ \((.+)\) (\S+) \@ (0x)?([0-9A-F]+)-(0x)?([0-9A-F]+).+ Q_SZ=(\d+).+ time=(\d+)us')
    # ! (cold) java/nio/Buffer.<init>(IIII)V Q_SZ=274 Q_SZI=274 QW=275 j9m=00000000000B3970 time=99us compilationAotClassReloFailure memLimit=206574 KB freePhysicalMemory=205 MB mem=[region=64 system=2048]KB compThreadID=0

    crtTimeMs = 0
    oldTimeMs = 0
    qszList = []
    lastQSZvalue = 0
    lastCPUvalue = 0
    jvmCpuList = []
    for line in vlog:
        # search for lines with timestamp t= 76254
        match = re.search(r"\st=\s*(\d+)", line)
        if match:
            crtTimeMs = int(match.group(1))
            if crtTimeMs > oldTimeMs:
                # Time has changed, print the maximum value for Q_SZ and JVM CPU seen in the previous interval
                qsz = 0

                if len(qszList) > 0: # if there are several entries, print the maximum
                    qsz = max(qszList)
                else: # no entries for previous interval; print the last seen Q_SZ value
                    qsz = lastQSZvalue

                cpu = 0
                if len(jvmCpuList) > 0:
                    cpu = max(jvmCpuList)
                else:
                    cpu = lastCPUvalue

                print("{time:8d}\t{qsz:5d}\t{cpu:4d}".format(time=oldTimeMs, qsz=qsz, cpu=cpu))
                # empty the list of values for queue sizes because a new interval starts
                qszList = []
                jvmCpuList = []
                # Update time for the new interval
                oldTimeMs = crtTimeMs

        # Get the Q_SZ if it exists
        match = re.search(r"Q_SZ=(\d+)", line)
        if match:
            lastQSZvalue = int(match.group(1))
            qszList.append(lastQSZvalue)
        match = re.search(r"\sJvmCpu=(\d+)\%", line)
        if match:
            lastCPUvalue = int(match.group(1))
            jvmCpuList.append(lastCPUvalue)


###############################################
# Get the name of vlog
if  len(sys.argv) < 2:
    print ("Program must have an argument: the name of the vlog\n")
    sys.exit(-1)

# Open my file in read only mode with line buffering
vlogFileName = str(sys.argv[1])
Vlog = open(vlogFileName, 'r', 1)

parseVlog(Vlog)

