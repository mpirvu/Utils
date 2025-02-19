# Python script that parses an OpenJ9 verbose log and
# prints a timeline of compilation times grouped by opt level.
# The printout consists of N columns separated by tabs
# where the first column represents the timestamp and the
# remaining columns represent the time spent in compilations
# (in ms) for a particular optimization level.
#
# Usage: python3 CompTimeTimeline.py vlogFilename
#
# Author: Marius Pirvu

import re # for regular expressions
import sys # for accessing parameters and exit

statsGranularity = 1000 # print one entry every 1000 ms

# Dictionary that maps opt levels from vlog into shorter names
knownOptLevels = {
    "AOT load"          : " aotl",
    "jni"               : "  jni",
    "no-opt"            : "noOpt",
    "cold"              : " cold",
    "AOT cold"          : " aotc",
    "warm"              : " warm",
    "AOT warm"          : " aotw",
    "hot"               : "  hot",
    "AOT hot"           : " aoth",
    "profiled hot"      : " phot",
    "very-hot"          : " vhot",
    "profiled very-hot" : "pvhot",
    "scorching"         : "scorc",
    "failure"           : " fail", # Treat compilation failures as an opt level
}



def printHeaderStats():
    stringList = []
    for opt in knownOptLevels.keys():
        levelName = knownOptLevels[opt]
        stringList.append("\t{levelName:7s}".format(levelName=levelName))
    print("".join(stringList))

'''
Print one line with stats for each defined opt level
'''
def printStatsPerOptLevel(header, compPerLevel):
    stringToPrint = header
    for opt in knownOptLevels.keys():
        levelName = knownOptLevels[opt]
        timeComp = (compPerLevel.get(levelName, 0)) // 1000 # convert to ms
        stringToPrint += "\t{timeComp:5d}".format(timeComp=timeComp)
    print(stringToPrint)


def parseVlog(vlog):
    printHeaderStats()
    # + (cold) sun/reflect/Reflection.getCallerClass()Ljava/lang/Class; @ 00007FB21300003C-00007FB213000167 OrdinaryMethod - Q_SZ=1 Q_SZI=1 QW=2 j9m=000000000004D1D8 bcsz=2 JNI time=995us mem=[region=704 system=2048]KB compThreadID=0 CpuLoad=163%(10%avg) JvmCpu=0%
    compEndPattern  = re.compile(r'^\+ \(([\w\s-]+)\) (\S+) .+time=(\d+)us')
    # ! (cold) java/nio/Buffer.<init>(IIII)V Q_SZ=274 Q_SZI=274 QW=275 j9m=00000000000B3970 time=99us compilationAotClassReloFailure memLimit=206574 KB freePhysicalMemory=205 MB mem=[region=64 system=2048]KB compThreadID=0
    crtTimeMs = 0
    oldTimeMs = 0
    compPerLevel = {} # hash with {optLevel:numComp} mappings

    for line in vlog:
        # search for lines with timestamp t= 76254
        match = re.search(r"\st=\s*(\d+)", line)
        if match:
            crtTimeMs = int(match.group(1))
            if crtTimeMs > oldTimeMs + statsGranularity:
                # Old interval finished, print values seen for last interval
                timestampSec = oldTimeMs // 1000 # convert to seconds
                printStatsPerOptLevel(str(timestampSec), compPerLevel)
                # empty my hash for queue sizes because a new interval starts
                compPerLevel = {}
                # Update time for the new interval
                oldTimeMs = crtTimeMs

        # Match the compilation ends that info about opt levels
        match = compEndPattern.match(line)
        if match:
            opt = match.group(1) # First group is the opt level
            assert opt in knownOptLevels, "Unknown opt level encountered: {opt}".format(opt=opt)
            levelName = knownOptLevels[opt]
            compTime = int(match.group(3))

            # Adjust the compilation time for given opt level
            compPerLevel[levelName] = compPerLevel.get(levelName, 0) + compTime



###############################################
# Get the name of vlog
if  len(sys.argv) < 2:
    print ("Program must have an argument: the name of the vlog\n")
    sys.exit(-1)

# Open my file in read only mode with line buffering
vlogFileName = str(sys.argv[1])
Vlog = open(vlogFileName, 'r', 1)

parseVlog(Vlog)

