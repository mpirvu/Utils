# Python script that parses N OpenJ9 verbose logs and computes
# compilation statistics across all of them
# Example of invocation:  python3 parseVlogs.py "vlog*.txt"
#
# Author: Marius Pirvu

import re # for regular expressions
import sys # for accessing parameters and exit
import statistics
import glob

# Compilations that take more than this value (in usec) are printed on screen
compTimeThreshold = 10000000


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


def printGenericHeader():
    print("\tSamples\t    SUM\t    MIN\t    AVG\t    MAX")

def printGenericStats(name, dataList):
    numSamples = len(dataList)
    sumValue = sum(dataList)
    meanValue = sumValue/numSamples if numSamples > 0  else 0
    minValue = min(dataList)
    maxValue = max(dataList)

    print("{name}\t{n:7d}\t{s:7.0f}\t{min:7.0f}\t{avg:7.0f}\t{max:7.0f}".format(name=name, n=numSamples, s=sumValue, min=minValue, avg=meanValue, max=maxValue))

def printHeaderStats():
    print("OptLvl\tSamples\tTOTAL(ms)\tMIN(usec)\tAVG(usec)\tMAX(ms)")

def printBodySizeHeaderStats():
    print("    \tSamples\tTOTAL(KB)\tMIN\tAVG\tMAX(KB)")

def printStats(name, dataList):
    numSamples = len(dataList)
    sumValue = sum(dataList)
    meanValue = sumValue/numSamples
    minValue = min(dataList)
    maxValue = max(dataList)/1000
    print("{name}\t{n:7d}\t{s:8.0f}\t{min:8.0f}\t{avg:8.0f}\t{max:6.1f}".format(name=name, n=numSamples, s=sumValue/1000, min=minValue, avg=meanValue, max=maxValue))


def parseVlog(vlog):
    vlogStats = {} # this is what is getting returned
    maxQSZ = 0
    numGCRBodies = 0
    numGCR = 0
    numSync = 0
    numDLT = 0

    compTimes = [] # List with compilation times
    compTimesPerLevel = {} # the key of this hash is the name of the optimization level
    compBodySizes = [] # List with sizes of the compiled bodies
    failureHash = {}

    # + (cold) sun/reflect/Reflection.getCallerClass()Ljava/lang/Class; @ 00007FB21300003C-00007FB213000167 OrdinaryMethod - Q_SZ=1 Q_SZI=1 QW=2 j9m=000000000004D1D8 bcsz=2 JNI time=995us mem=[region=704 system=2048]KB compThreadID=0 CpuLoad=163%(10%avg) JvmCpu=0%
    compEndPattern  = re.compile('^\+ \((.+)\) (\S+) \@ (0x)?([0-9A-F]+)-(0x)?([0-9A-F]+).+ Q_SZ=(\d+).+ time=(\d+)us')
    # ! (cold) java/nio/Buffer.<init>(IIII)V Q_SZ=274 Q_SZI=274 QW=275 j9m=00000000000B3970 time=99us compilationAotClassReloFailure memLimit=206574 KB freePhysicalMemory=205 MB mem=[region=64 system=2048]KB compThreadID=0
    compFailPattern = re.compile('^\! \(.+\) (\S+) .*time=(\d+)us (\S+) ')
    # Parse the vlog
    for line in vlog:
        m = compEndPattern.match(line)
        if m:
            # First group is the opt level
            opt = m.group(1)
            methodStartAddr = int(m.group(4), base=16)
            methodEndAddr = int(m.group(6), base=16)
            qSZ = int(m.group(7))
            usec = int(m.group(8))
            if " JNI " in line: # Treat JNIs separately because they are cheaper
                opt = "jni"

            maxQSZ = max(maxQSZ, qSZ)

            compTimes.append(usec)
            if opt not in knownOptLevels:
                print("Unknown opt level encountered:", opt)
                exit(-1)
            levelName = knownOptLevels[opt]
            if levelName in compTimesPerLevel:
                compTimesPerLevel[levelName].append(usec)
            else:
                compTimesPerLevel[levelName] = [usec]

            compBodySizes.append(methodEndAddr - methodStartAddr)

            if " GCR " in line:
                numGCRBodies += 1
            if " G " in line or " g " in line:
                numGCR += 1
            if " sync " in line:
                numSync += 1
            if " DLT" in line:
                numDLT += 1

        else: # Check for compilation failures
            if line.startswith("!"):
                m = compFailPattern.match(line)
                if m:
                    usec = int(m.group(2))
                    failureReason = m.group(3)
                    levelName = " fail" # Treat compilation failures as a separate opt level
                    if levelName in compTimesPerLevel:
                        compTimesPerLevel[levelName].append(usec)
                    else:
                        compTimesPerLevel[levelName] = [usec]
                    # Update failure reasons
                    failureHash[failureReason] = failureHash.get(failureReason, 0) + 1

                    # Get the Q_SZ if it exists
                    match = re.search(r"Q_SZ=(\d+)", line)
                    if match:
                        qSZ = int(match.group(1))
                        maxQSZ = max(maxQSZ, qSZ)

    vlogStats['compTimes'] = compTimes # List with all the compilations
    vlogStats['compTimesPerLevel'] = compTimesPerLevel
    vlogStats['maxqz'] = maxQSZ
    vlogStats['numGCR'] = numGCR
    vlogStats['numSync'] = numSync
    vlogStats['numDLT'] = numDLT
    vlogStats['failureHash'] = failureHash
    return vlogStats


###################################################


# Get the name of vlog
if  len(sys.argv) < 2:
    print ("Program must have an argument: the name of the vlog\n")
    sys.exit(-1)

filesWithWildCard = sys.argv[1]
print("Processing", filesWithWildCard)

maxqszList = []
gcrList = []
syncList = []
dltList = []
compTimePerLevel = {}
compTimes = []
failureReasons = {}
numVlogs = 0
for filepath in glob.iglob(filesWithWildCard):
    # Open my file in read only mode with line buffering
    print("processing", filepath)
    numVlogs = numVlogs + 1
    Vlog = open(filepath, 'r', 1)
    vlogStats = parseVlog(Vlog)

    compTimes.extend(vlogStats['compTimes'])

    compTPerLevel = vlogStats['compTimesPerLevel']
    for opt in knownOptLevels.keys():
        levelName = knownOptLevels[opt]
        perLevelList = compTPerLevel.get(levelName, [])
        # If list is not empty, add it to global one
        if perLevelList:
            if levelName in compTimePerLevel:
                compTimePerLevel[levelName].extend(perLevelList)
            else:
                compTimePerLevel[levelName] = perLevelList

    # For each failure reason, add it to the global hash
    failureHash = vlogStats['failureHash']
    for reason, samples in failureHash.items():
        failureReasons[reason] = failureReasons.get(reason, 0) + samples

    maxqszList.append(vlogStats['maxqz'])
    gcrList.append(vlogStats['numGCR'])
    syncList.append(vlogStats['numSync'])
    dltList.append(vlogStats['numDLT'])


printHeaderStats()
printStats(" All", compTimes)
for opt in knownOptLevels.keys():
    levelName = knownOptLevels[opt]
    valueList = compTimePerLevel.get(levelName, [])
    if valueList: # if not empty
        printStats(levelName, valueList)

print("")
printGenericHeader()
printGenericStats("MaxQSZ", maxqszList)
printGenericStats("NumGCR", gcrList)
printGenericStats("NumSync", syncList)
printGenericStats("NumDLT", dltList)

print("\nFailure reasons (average per vlog):")
for reason, samples in failureReasons.items():
    print("{reason} = {avg:10.4}".format(reason=reason, avg=samples/numVlogs))

