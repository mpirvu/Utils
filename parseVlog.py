# Python script that parses an OpenJ9 verbose log and computes
# compilation statistics
# Usage: python3 parseVlog.py vlogFilename
#
# Author: Marius Pirvu

import re # for regular expressions
import sys # for accessing parameters and exit

################## Configuration #####################
# Compilations that take more than this value (in usec) are printed on screen
compTimeThreshold = 1000000

# The following boolean controls whether vlog parsing should stop after JVM detects end of start-up
analyzeOnlyStartup = False
# Exclude the start-up from the analysis
dontAnalyzeStartup = False

# If set to True, the script will print all AOT loads that were not followed by a recompilation
# as well as all AOT loads that were followed by a recompilation
printAOTLoadsNotRecompiled = False

# If set to True, the script will print all first time compilations that are:
# (1) not AOT loads (2) not JNI (3) not DLT (4) not caused by AOT load failures
# The goal is to identify reasons for why we cannot have more AOT loads
printFirstCompilationsNonAOTLoads = False

# The following boolean controls whether the script should print the CDF
# for compilation times. The CDF is printed in a file named cdf.txt
# and has a resolution of 5%. Each line in this file specifies that
# N most expensive compilations took X% of the total compilation time,
# where X is incremeted by 5 percentage points until it reaches 100%.
printCompTimeCDF = False
compTimeCDFFilename = "cdf.txt"


#######################################################
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
    print("OptLvl\tSamples\tTOTAL(ms)\tMIN(usec)\tAVG(usec)\tMAX(ms)")

def printBodySizeHeaderStats():
    print("    \tSamples\tTOTAL(KB)\t     MIN\t     AVG\tMAX(KB)")

def printStats(name, dataList):
    numSamples = len(dataList)
    sumValue = sum(dataList)
    meanValue = sumValue/numSamples
    minValue = min(dataList)
    maxValue = max(dataList)/1000
    print("{name}\t{n:7d}\t{s:8.0f}\t{min:8.0f}\t{avg:8.0f}\t{max:6.1f}".format(name=name, n=numSamples, s=sumValue/1000, min=minValue, avg=meanValue, max=maxValue))


def parseVlog(vlog):
    maxCompLine = "" # Remember the compilation that took the longest
    maxCompTime = 0
    maxQSZ = 0
    numGCRBodies = 0
    numGCR = 0
    numSync = 0
    numDLT = 0
    numRemote = 0
    numDeserialized = 0
    numLocalNonAOTLoad = 0
    maxJvmCPU = 0
    minFreeMem = sys.maxsize
    maxScratchMem = 0
    maxRegionMem = 0
    numLowPhysicalMemEvents = 0
    numRecomp = 0
    compilationWasDisabled = False
    numInterpreted = 0 # number of messages "will continue as interpreted"
    interpretedMethods = set() # set of methods that will continue as interpreted
    compTimes = [] # List with compilation times
    compTimesPerLevel = {} # the key of this hash is the name of the optimization level
    compBodySizes = [] # List with sizes of the compiled bodies
    failureHash = {}
    failedMethods = set() # set for tracking whether methods remain interpreted after a failure
    recompMethods = set() # set for computing the number of recompilations
    aotLoadsNotRecompiled = set()
    aotLoadsRecompiled = set()
    crtTimeMs = 0 # current time in millis since the start of the JVM
    veryLongCompilations = []
    methodCompTimes = {} # hash that maps method names to compilation times
    firstTimeCompsExplainNonAOTLoad = {} # hash that maps method names to a tuple {vlogCompLine, AOTLoadFail?, JNI?, AOTLoad?, FollowAOTLoadFail}
    startTime = 0 # ms

    #  (cold) Compiling java/lang/Double.longBitsToDouble(J)D  OrdinaryMethod j9m=0000000000097B18 t=20 compThreadID=0 memLimit=262144 KB freePhysicalMemory=75755 MB
    compStartPattern = re.compile('^.+\((.+)\) Compiling (\S+) .+ t=(\d+)')
    # + (cold) sun/reflect/Reflection.getCallerClass()Ljava/lang/Class; @ 00007FB21300003C-00007FB213000167 OrdinaryMethod - Q_SZ=1 Q_SZI=1 QW=2 j9m=000000000004D1D8 bcsz=2 JNI time=995us mem=[region=704 system=2048]KB compThreadID=0 CpuLoad=163%(10%avg) JvmCpu=0%
    # + (AOT load) java/lang/String.lengthInternal()I @ 00007FA6F8001140-00007FA6F8001168 Q_SZ=1 Q_SZI=1 QW=2 j9m=00000000000493F8 bcsz=37 time=51us compThreadID=0 queueTime=293us
    compEndPattern  = re.compile('^\+ \(([\S -]+)\) (\S+) \@ (0x)?([0-9A-F]+)-(0x)?([0-9A-F]+)\s.*Q_SZ=(\d+).+ time=(\d+)us')
    # ! (cold) java/nio/Buffer.<init>(IIII)V Q_SZ=274 Q_SZI=274 QW=275 j9m=00000000000B3970 time=99us compilationAotClassReloFailure memLimit=206574 KB freePhysicalMemory=205 MB mem=[region=64 system=2048]KB compThreadID=0
    compFailPattern = re.compile('^\! \(.+\) (\S+) .*time=(\d+)us (\S+) ')
    jvmCpuPattern = re.compile('^.+jvmCPU=(\d+)', re.IGNORECASE)
    freeMemPattern = re.compile('^.+freePhysicalMemory=(\d+) MB')
    scratchMemPattern = re.compile('^.+mem=\[region=(\d+) system=(\d+)\]KB')
    # Parse the vlog
    for line in vlog:
        # #JITSTATE:  t=  6544 VM changed state to NOT_STARTUP
        if "VM changed state to NOT_STARTUP" in line: # start-up point detected
            # Determine the start time
            m = re.match("#JITSTATE:\s+t=\s*(\d+)\s+VM", line)
            if m:
                startTime = int(m.group(1))
            if analyzeOnlyStartup:
                break
            if dontAnalyzeStartup:
                # reset all compilation stats
                maxCompLine = "" # Remember the compilation that took the longest
                maxCompTime = 0
                maxQSZ = 0
                numGCRBodies = 0
                numGCR = 0
                numSync = 0
                numDLT = 0
                numRemote = 0
                numDeserialized = 0
                numLocalNonAOTLoad = 0
                maxJvmCPU = 0
                minFreeMem = sys.maxsize
                maxScratchMem = 0
                maxRegionMem = 0
                numLowPhysicalMemEvents = 0
                numRecomp = 0
                compilationWasDisabled = False
                numInterpreted = 0 # number of messages "will continue as interpreted"
                interpretedMethods.clear()
                compTimes.clear() # List with compilation times
                compTimesPerLevel.clear() # the key of this hash is the name of the optimization level
                compBodySizes.clear() # List with sizes of the compiled bodies
                continue
        m = compEndPattern.match(line)
        if m:
            # First group is the opt level
            opt = m.group(1)
            methodName = m.group(2)
            methodStartAddr = int(m.group(4), base=16)
            methodEndAddr = int(m.group(6), base=16)
            qSZ = int(m.group(7))
            usec = int(m.group(8))
            if " JNI " in line: # Treat JNIs separately because they are cheaper
                opt = "jni"
            # print very long compilations
            if usec > compTimeThreshold:
                veryLongCompilations.append(line)

            if usec > maxCompTime:
                maxCompTime = usec
                maxCompLine = line

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

            bodySize = methodEndAddr - methodStartAddr
            assert bodySize > 0, "Method size must be positive"
            compBodySizes.append(bodySize)

            if printCompTimeCDF:
                mName = methodName + "_" + str(usec)
                if mName in methodCompTimes:
                    mName = mName + "_2" # hack just in case two methods have the same name and compilation time
                methodCompTimes[mName] = usec

            if " GCR " in line:
                numGCRBodies += 1
            if " G " in line or " g " in line:
                numGCR += 1
            if " sync " in line:
                numSync += 1
            if " DLT" in line:
                numDLT += 1
            if " remote " in line:
                numRemote += 1
                if " deserialized " in line:
                    numDeserialized += 1
            elif "AOT load" not in line:
                numLocalNonAOTLoad += 1

            # If a method has compiled successfully after a failure, delete entry from the failure set
            failedMethods.discard(methodName) # no change if entry does not exist

            if opt == "AOT load":
                aotLoadsNotRecompiled.add(methodName)

            # Count recompilations
            if methodName not in recompMethods:
                # First time compilation
                recompMethods.add(methodName)
            else: # Possible recomp
                if opt != "AOT load": # AOT loads after AOT compilations are not counted as recompilations
                    numRecomp += 1
                    if methodName in aotLoadsNotRecompiled:
                        # Recompilation of an AOT load; remove from set
                        aotLoadsNotRecompiled.remove(methodName)
                        # and add to set of recompiled AOT loads
                        aotLoadsRecompiled.add(methodName)

            # Logic for determining the first time compilations that are not AOT loads
            # DLT compilations are special and they should be totally ignored
            if printFirstCompilationsNonAOTLoads and not " DLT" in line:
                if methodName not in firstTimeCompsExplainNonAOTLoad:
                    # First time compilation
                    firstTimeCompsExplainNonAOTLoad[methodName] =  {"line": line, "AOTLoadFail":False, "FollowAOTLoadFail":False, "JNI":" JNI " in line, "AOTLoad":"AOT load" in line}
                else:
                    # Recomps and compilations following AOT load failures can be ignored
                    info = firstTimeCompsExplainNonAOTLoad[methodName]
                    if info["AOTLoadFail"]:
                        info["FollowAOTLoadFail"] = True

        else: # Check for compilation failures
            if line.startswith("!"):
                m = compFailPattern.match(line)
                if m:
                    methodName = m.group(1)
                    usec = int(m.group(2))
                    failureReason = m.group(3)
                    levelName = " fail" # Treat compilation failures as a separate opt level
                    if levelName in compTimesPerLevel:
                        compTimesPerLevel[levelName].append(usec)
                    else:
                        compTimesPerLevel[levelName] = [usec]
                    # Update failure reasons
                    failureHash[failureReason] = failureHash.get(failureReason, 0) + 1
                    # Track methods that failed to compile
                    failedMethods.add(methodName)

                    # Get the Q_SZ if it exists
                    match = re.search(r"Q_SZ=(\d+)", line)
                    if match:
                        qSZ = int(match.group(1))
                        maxQSZ = max(maxQSZ, qSZ)
                    if printFirstCompilationsNonAOTLoads and not " DLT" in line:
                        # Look for first time compilations that are AOT loads that failed
                        if methodName not in firstTimeCompsExplainNonAOTLoad:
                            if "AOT load" in line:
                                firstTimeCompsExplainNonAOTLoad[methodName] =  {"line": line, "AOTLoadFail":True, "FollowAOTLoadFail":False, "JNI":" JNI " in line, "AOTLoad":False}
                else:
                    # Failure line that is not matched could look like
                    # ! sun/misc/Unsafe.ensureClassInitialized(Ljava/lang/Class;)V cannot be translated
                    # <clinit> is in this category as well
                    if re.search(r"cannot be translated$", line):
                        failureReason = "uncompilable"
                        failureHash[failureReason] = failureHash.get(failureReason, 0) + 1
                    else:
                        print(line)
            else: # Look for compilation starts that have the current timestamp
                m = compStartPattern.match(line)
                if m:
                    opt = m.group(1)
                    methodName = m.group(2)
                    ms = int(m.group(3))
                    crtTimeMs = ms
                else: # Other lines may contain time as well
                    match = re.search(r"\st=\s*(\d+)", line)
                    if match:
                        crtTimeMs = int(match.group(1))

        m = jvmCpuPattern.match(line)
        if m:
            jvmCPU = int(m.group(1))
            maxJvmCPU = max(jvmCPU, maxJvmCPU)
        m = freeMemPattern.match(line)
        if m:
            freeMem = int(m.group(1))
            minFreeMem = min(minFreeMem, freeMem)
        m = scratchMemPattern.match(line)
        if m:
            regionMem = int(m.group(1))
            systemMem = int(m.group(2))
            maxScratchMem = max(maxScratchMem, systemMem)
            maxRegionMem = max(maxRegionMem, regionMem)
        if "Low On Physical Memory" in line: # JIT aborts the compilation if this is seen
            numLowPhysicalMemEvents += 1
        if "Disable further compilation" in line:
            compilationWasDisabled = True
        #INFO:  Method jdk/internal/loader/NativeLibraries.load(Ljdk/internal/loader/NativeLibraries$NativeLibraryImpl;Ljava/lang/String;ZZZ)Z will continue as interpreted
        if "will continue as interpreted" in line:
            m = re.match("#INFO:\s+Method (\S+) will continue as interpreted", line)
            if m:
                interpretedMethods.add(m.group(1))
            else:
                print("Interpreted method could not be identified from line:", line)
            numInterpreted += 1

    # Print statistics
    printHeaderStats()
    printStats("Total", compTimes)
    for opt in knownOptLevels.keys():
        levelName = knownOptLevels[opt]
        valueList = compTimesPerLevel.get(levelName, [])
        if valueList: # if not empty
            printStats(levelName, valueList)

    print("\nFailure reasons:")
    for reason, samples in failureHash.items():
        print(reason, "=",   samples)

    print("\nMAXLINE:", maxCompLine)

    print("Stats regarding compiled body sizes")
    printBodySizeHeaderStats()
    printStats("All", compBodySizes)
    print("")

    print("Num recomps   =", numRecomp)
    print("GCR bodies    =", numGCRBodies) # not accurate for remote compilations
    print("GCR recomp    =", numGCR)
    print("Sync          =", numSync)
    print("DLT           =", numDLT)
    if numRemote > 0:
        print("Remote        =", numRemote, " Deserialized =", numDeserialized, " Local-Non-AOTLoad =", numLocalNonAOTLoad)
    print("MAX Q_SZ      =", maxQSZ)
    print("MAX JvmCPU    =", maxJvmCPU, "%")
    print("MaxScratchMem =", maxScratchMem, "KB")
    print("MaxRegionMem  =", maxRegionMem, "KB")
    print("Min free mem  =", minFreeMem, "MB")
    if numLowPhysicalMemEvents > 0:
        print("NumLowPhysMem =", numLowPhysicalMemEvents)
    if len(failedMethods) > 0:
        print("Methods that remain interpreted after a failure:")
        for method in failedMethods:
            # Method could have been compiled and a recompilation could have failed
            # Those failures don't result in an interpreted method
            if method not in recompMethods:
                print(method)
    print("Start Timestamp =", startTime, "ms")
    print("Last TimeStamp  =", crtTimeMs, "ms")
    if len(veryLongCompilations) > 0:
        print("\nVery long compilations:")
        for l in veryLongCompilations:
            print(l)
    if compilationWasDisabled:
        print("WARNING: compilation was disabled at some point during JVM lifetime")
    if numInterpreted > 0:
        print(numInterpreted, "methods will continue as interpreted due to compilation filters")
        for method in interpretedMethods:
            print("\t", method)
    if printAOTLoadsNotRecompiled:
        print("\nAOT loads that were not recompiled:")
        sortedMethods = sorted(aotLoadsNotRecompiled)
        for method in sortedMethods:
            print(method)
        print("\nAOT loads that were recompiled:")
        sortedMethods = sorted(aotLoadsRecompiled)
        for method in sortedMethods:
            print(method)

    if printCompTimeCDF:
        # Sort our methodCompTimes hash by compilation time
        sortedMethodCompTimes = sorted(methodCompTimes.items(), key=lambda kv: kv[1], reverse=True)
        # Iterate through the sorted hash and print the CDF
        print("\nWill print compilation time CDF into file", compTimeCDFFilename, "\n ")
        cdfFile = open(compTimeCDFFilename, "w")
        cdfFile.write("CDF for compilation times\n")
        totalCompTime = sum(compTimes)
        totalCompilations = len(compTimes)
        crtCompTime = 0
        nextTarget = 5.0
        compCounter = 0
        for methodTuple in sortedMethodCompTimes:
            compCounter += 1
            crtCompTime += methodTuple[1]
            percentage = crtCompTime * 100.0 / totalCompTime
            if percentage >= nextTarget:
                cdfFile.write("{n:7d} methods took {p:4.1f}% of total compilation time\n".format(n=compCounter, p=percentage))
                nextTarget += 5.0
        cdfFile.close()

    if printFirstCompilationsNonAOTLoads:
        print("\nFirst time compilations that are not AOT loads:")
        for method, info in firstTimeCompsExplainNonAOTLoad.items():
            if not info["AOTLoad"] and not info["JNI"] and not info["FollowAOTLoadFail"]:
                # Also ignore AOT compilations and EDO triggerred compilations
                if not ("+ (AOT" in info["line"]) and not (" EDO " in info["line"]):
                    print(info["line"], end='')
###################################################


# Get the name of vlog
if  len(sys.argv) < 2:
    print ("Program must have an argument: the name of the vlog\n")
    sys.exit(-1)

# Open my file in read only mode with line buffering
vlogFileName = str(sys.argv[1])
Vlog = open(vlogFileName, 'r', 1)

parseVlog(Vlog)

