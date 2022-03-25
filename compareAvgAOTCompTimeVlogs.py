# Given two directories with verbose logs, parse all the vlogs
# and gather statistics about AOT compilation times.
# Then, display methods for which AOT compilations in one directory
# took more time than the AOT compilations (for the same method)
# in the other directory.
# To make the output more mageables, only the methods that are AOTed in
# all vlogs will be considered. Moreover, the output will include only
# the methods for which all instances in dir1 take more time than 
# instances in dir2, or viceversa.
#
# Use case: determine if one JDK takes more time to perform AOT compilations
# than another JDK. Each JDK will write verbose files in their own directory.
#
# Author: Marius Pirvu

import re # for regular expressions
import sys # for accessing parameters and exit
from pathlib import Path



'''
Parse a verbose log and collect information about the time needed to perform AOT compilations
Update a dictionary where the key is a method name and the value is a list of compilation times
'''
def parseVlog(vlog, methodHash):
    # + (cold) sun/reflect/Reflection.getCallerClass()Ljava/lang/Class; @ 00007FB21300003C-00007FB213000167 OrdinaryMethod - Q_SZ=1 Q_SZI=1 QW=2 j9m=000000000004D1D8 bcsz=2 JNI time=995us mem=[region=704 system=2048]KB compThreadID=0 CpuLoad=163%(10%avg) JvmCpu=0%
    compEndPattern  = re.compile('^\+ \(AOT warm\) (\S+) \@.+ time=(\d+)us')
    for line in vlog:
        m = compEndPattern.match(line)
        if m:
            methodName = m.group(1)
            compTime = int(m.group(2))
            if methodName in methodHash:
                methodHash[methodName].append(compTime)
            else:
                methodHash[methodName] = [compTime]


def processAllFilesInDirectory(dirName):
    methodHash = {}
    numFiles = 0
    directory = Path(dirName)
    for entry in directory.iterdir():
        if entry.is_file():
            numFiles += 1
            # print(entry.name)
            print("Processing", entry.absolute())
            Vlog = open(entry.absolute(), 'r', 1)
            parseVlog(Vlog, methodHash)
    total = 0
    for method in methodHash:
        total += sum(methodHash[method])
    print("Total compilation time:", total)
    return methodHash, numFiles

def findCompTimeDiffs(methodHash1, methodHash2, numFiles1, numFiles2):
    resultingHash = {} # this is my result
    for method in methodHash1:
        if method in methodHash2: # must be present in both dictionaries
            list1 = methodHash1[method]
            list2 = methodHash2[method]
            mean1 = sum(list1)/len(list1)
            mean2 = sum(list2)/len(list2)
            resultingHash[method] = {'mean1':mean1, 'mean2':mean2, 'len1':len(list1), 'len2':len(list2)}
            # Print only if the method appears in all the files
            if len(list1) == numFiles1 and len(list2) == numFiles2:
                # Print only if all entries in one list are larger than the entries in the other list
                if min(list1) > max(list2) or min(list2) > max(list1):
                    print("{mean1:8.1f} {mean2:8.1f} {l1:2d} {l2:2d} {ratio:8.1f} {method:s}".format(mean1=mean1, mean2=mean2, l1=len(list1), l2=len(list2), ratio=mean1/mean2, method=method))
    #for method in resultingHash:
    #    print()


###################################################
# Get the name of the two directories
if  len(sys.argv) != 3:
    print ("Program must have 2 arguments representing directory names that are to be processed\n")
    sys.exit(-1)

dir1Name = sys.argv[1]
dir2Name = sys.argv[2]
(methodHash1, numFiles1) = processAllFilesInDirectory(dir1Name)
(methodHash2, numFiles2) = processAllFilesInDirectory(dir2Name)
findCompTimeDiffs(methodHash1, methodHash2, numFiles1, numFiles2)




