# Script that takes 2 vlog files and compares the compilation times
# for each method.

import re # for regular expressions
import sys # for accessing parameters and exit
import glob




def parseVlog(vlogName, methodHash):
    """
    Parse a vlog and return a dictionary with all the methods and their compilation times
    One entry in this dictionary will have the method name as key and another dictionary as value.
    The second dictionary will have the compilation hotness as the key and a list of compilation times as value.
    This way we can take care of the case where a method is recompiled at the same opt level.
    This program does not account for a method name being loaded by different class loaders.
    {methodName --> {hotness --> list of compilation times}}

    Arguments:
        vlogName {file} -- the vlog filename
        methodHash {dict} -- the dictionary to be updated
    """
    with open(vlogName, 'r', 1) as vlog:
        compEndPattern  = re.compile('^\+ \((.+)\) (\S+) \@.+ time=(\d+)us')
        # Parse the vlog
        for line in vlog:
            m = compEndPattern.match(line)
            if m:
                # First group is the opt level
                opt = m.group(1)
                methodName = m.group(2)
                compTime = int(m.group(3))
                if methodName in methodHash:
                    optLevelHash = methodHash[methodName]
                    if opt in optLevelHash:
                        optLevelHash[opt].append(compTime)
                    else:
                        optLevelHash[opt] = [compTime]
                else:
                    methodHash[methodName] = {opt:[compTime]}


# Get the name of vlogs
if  len(sys.argv) < 3:
    print ("Program must have two arguments: the names of the vlogs to compare\n")
    sys.exit(-1)

vlog1Name = sys.argv[1]
vlog2Name = sys.argv[2]
methodHash1 = {}
methodHash2 = {}
methodHashDiff = {} # result dictionary with the difference in compilation times for each (hotness_method)
parseVlog(vlog1Name, methodHash1)
parseVlog(vlog2Name, methodHash2)

# For every method in methodHash1, check if it is present in methodHash2
# If we found a pair in both vlogs (including the opt level), add the
# difference in compilation times to methodHashDiff
for method in methodHash1:
    if method in methodHash2:
        # For every opt level in methodHash1, check if it is present in methodHash2
        for opt in methodHash1[method]:
            if opt in methodHash2[method]:
                # The same method and opt level in both vlogs, so we can compare compilation times
                avgCompTime1 = sum(methodHash1[method][opt])/len(methodHash1[method][opt])
                avgCompTime2 = sum(methodHash2[method][opt])/len(methodHash2[method][opt])
                key = opt + "_" + method
                methodHashDiff[key] = avgCompTime1 - avgCompTime2

# Print the data from methodHashDiff sorted by the difference in compilation times
for hotness_method in sorted(methodHashDiff, key=lambda k: methodHashDiff[k]):
    print(methodHashDiff[hotness_method], hotness_method)

