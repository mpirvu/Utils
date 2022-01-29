# Script that takes an OpenJ9 verbose log and, for
# each method, computes the time spent in profiling mode
# Caveat: the key for the main dictionary is the method name,
# so if several different methods with the same name exist in
# the system (because of different class loaders), the script
# mai produce incorrect results.
# Author: Marius Pirvu

import re # for regular expressions
import sys # for accessing parameters and exit

# We will keep a dictionary where the key is the method name and the value
# is a list of hashes. Those hashes have 4 keys
# 1. 'optLevel': The opt level of the compilation (includes "profiling" for profiling compilations)
# 2. 'tStart': Time when compilation started (ms)
# 3. 'tComp': Duration of compilation (usec)
# 4. 'success': True for successful compilation and False for failures

def printMethodCompHistory(methodName, compList):
    print("Compilation history for", methodName)
    for comp in compList:
        tStart = comp.get('tStart', 0)
        tComp  = comp.get('tComp', 0)
        success = comp.get('success', False)
        print("\t{s} {optLvl:14s} tStart:{t1:8d} ms  tComp:{t2:8d} usec  tEnd:{t3:8d}".format(s="+" if success else "!",
              optLvl=comp['optLevel'], t1=tStart, t2=tComp, t3=tStart + tComp//1000))


'''
Parse an OpenJ9 verbose log obtained with -Xjit:verbose={compilePerformance}
and populate a dictionary called 'methodHash' that has the structure described above
'''
def parseVlog(vlog, methodHash):
    #  (cold) Compiling java/lang/Double.longBitsToDouble(J)D  OrdinaryMethod j9m=0000000000097B18 t=20 compThreadID=0 memLimit=262144 KB freePhysicalMemory=75755 MB
    compStartPattern = re.compile('^ \((.+)\) Compiling (\S+) .+ t=(\d+)')
    # + (cold) sun/reflect/Reflection.getCallerClass()Ljava/lang/Class; @ 00007FB21300003C-00007FB213000167 OrdinaryMethod - Q_SZ=1 Q_SZI=1 QW=2 j9m=000000000004D1D8 bcsz=2 JNI time=995us mem=[region=704 system=2048]KB compThreadID=0 CpuLoad=163%(10%avg) JvmCpu=0%
    compEndPattern   = re.compile('^\+ \((.+)\) (\S+) .*time=(\d+)us')
    # ! (profiled hot) java/util/zip/ZipInputStream.read([BII)I time=27214us compilationEnforceProfiling memLimit=262144 KB freePhysicalMemory=72199 MB mem=[region=4224 system=16384]KB
    compFailPattern  = re.compile('^\! \((.+)\) (\S+) .*time=(\d+)us')
    # TODO: look for other patterns of failures
    # Parse the vlog
    for line in vlog:
        # Skip over DLT compilations because they can go in parallel with other 'ordinary' compilations
        if " DLT" in line:
            continue
        compEnd  = compEndPattern.match(line)
        compFail = compFailPattern.match(line)
        opt = ""
        methodName = ""
        usec = 0
        success = True
        if compEnd or compFail:
            if compEnd:
                # First group is the opt level
                opt = compEnd.group(1)
                methodName = compEnd.group(2)
                usec = int(compEnd.group(3))
            else:
                opt = compFail.group(1)
                methodName = compFail.group(2)
                usec = int(compFail.group(3))
                success = False
            # add the method to my hash
            assert methodName in methodHash, "Compilation end without a compilation start for line: {l}".format(l=line)
            compList = methodHash[methodName]
            # Last entry in the compilation list must have the compStart populated, but not the compTime
            assert compList, "compList must not be empty for method {m}".format(m=methodName)
            lastEntry = compList[-1] # This is a dictionary with 4 keys
            assert 'tStart' in lastEntry, "We must have seen the compilation start for method {m}".format(m=methodName)
            assert 'tComp' not in lastEntry, "We must not have seen another compilation end for method {m}".format(m=methodName)
            lastEntry['optLevel'] = opt # update the opt level
            lastEntry['tComp'] = usec
            lastEntry['success'] = success
        else:
            m = compStartPattern.match(line)
            if m:
                # First group is the opt level
                opt = m.group(1)
                methodName = m.group(2)
                ms = int(m.group(3))
                # add the method to my hash
                if methodName not in methodHash: # First compilation for this method
                    methodHash[methodName] = [{'optLevel':opt, 'tStart':ms}]
                else:
                    compList = methodHash[methodName]
                    # Check that last entry for this method has both the end and the start of the compilation
                    assert 'tComp' in compList[-1], "lastEntry for this method must be a compilation with start and end. Line: {l}".format(l=line)
                    compList.append({'optLevel':opt, 'tStart':ms})


'''
Walk the give method hash and for each method present determine
1. If the method remains in profiling (last successful compilation is profiling)
2. How much time is spent in profiling mode
'''
def walkMethodHash(methodHash):

    totalTimeProfiling = 0
    for method in methodHash:
        compList = methodHash[method]
        prevCompWasProfiling = False
        profilingTime = 0 # Reset
        atLeastOneProfilingComp = False

        for compilation in compList:
            if 'tComp' in compilation: # We have a compilation end
                opt = compilation['optLevel'] # Will throw if it doesn't exist
                if "profiled" in opt: # This is a profiling compilation
                    tEnd = compilation['tStart'] + compilation['tComp']//1000 # convert to ms
                    if compilation['success']: # successful profiling compilation
                        tLastProfComp = tEnd
                        prevCompWasProfiling = True
                        atLeastOneProfilingComp = True
                else: # Non-profiling compilation
                    # Was the previous compilation a profiling one?
                    if prevCompWasProfiling:
                        tEnd = compilation['tStart'] + compilation['tComp']//1000 # convert to ms
                        if tEnd < tLastProfComp:
                            print("Time goes backwards for method:", method)
                            print("tLastProfComp =", tLastProfComp, " tEnd =", tEnd)
                            printMethodCompHistory(method, compList)
                            #exit(-1)
                        else:
                            profilingTime += tEnd - tLastProfComp
                    # If this non-profiling compilation ended successfuly we reset 'prevCompWasProfiling`
                    # Otherwise, we keep it because we still have a profiling method executing
                    if compilation['success']:
                        prevCompWasProfiling = False
            else:
                print("Compilation start without compilation end for method:", method)
        if prevCompWasProfiling:
            print("Stuck in profiling for method", method)
            printMethodCompHistory(method, compList)
        if atLeastOneProfilingComp:
            totalTimeProfiling += profilingTime
            print("Spent", profilingTime, "ms profiling for method", method)
    print("Total time spent profiling:", totalTimeProfiling, "ms")


# Get the name of vlog
if  len(sys.argv) < 2:
    print ("Program must have an argument: the name of the vlog\n")
    sys.exit(-1)

# Open my file in read only mode with line buffering
vlogFileName = str(sys.argv[1])
Vlog = open(vlogFileName, 'r', 1)
methodHash = {}
parseVlog(Vlog, methodHash)
walkMethodHash(methodHash)
