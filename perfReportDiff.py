# Python script that processes 2N perf profiles and shows the diffs
# The first half of the profiles must belong to one configuration
# and the second hald to another configuration.
# The script cummulates the profiling samples for the two configurations
# in order to avoid fluctuations in the sampling mechanism.
# Then, the two configurations are compared head-to-head.
# The comparison includes information about (1) contributions of various dlls
# and (2) contributions of various symbols in the dlls
# Usage: python3 perfReportDiff.py A1.perf A2.perf ... An.perf B1.perf B2.perf ... Bn.perf

# Author: Marius Pirvu (mpirvu@ca.ibm.com)


import operator # for sorting the dictionary
import re # for regular expressions
import sys # for accessing parameters and exit
import shlex, subprocess


def processPerfProfile(perfFileName, globalDictionary):
    # Process the profile getting the ticks for various symbols
    # Example of text to be parsed
    '''
    # Overhead       Samples  Command          Shared Object       Symbol
    # ........  ............  ...............  ..................  ........................................................................................................................................................
    #
        0.26%           268  Default Executo  libj9vm29.so        [.] convertClassNameToStackMapType
        0.19%           201  Default Executo  libj9vm29.so        [.] computeVTable
        0.17%           178  Default Executo  libj9vm29.so        [.] internalCreateRAMClassFromROMClassImpl
        0.17%           177  Default Executo  [JIT] tid 31473     [.] org/apache/felix/resolver/ResolverImpl.parseUses(Ljava/lang/String;)Ljava/util/List;_cold
    '''
    cmd = f"perf report --header --stdio -n -i {perfFileName}"
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()

    foundHeader = False
    for line in lines:
        # Search for the header first
        if not foundHeader:
            pattern = re.compile('^# Overhead\s+Samples\s+Command\s+Shared Object\s+Symbol')
            m = pattern.match(line)
            if m:
                foundHeader = True
            continue
        else: # Now parse the lines with samples
            pattern = re.compile('^\s+(\d+\.\d+)%\s+(\d+)\s+(...............)\s+(..................)\s+\[.\]\s+(.+)')
            m = pattern.match(line)
            if m:
                #print(line)
                percentage = float(m.group(1))  # First group is percentage contribution
                samples    = int(m.group(2))
                thrName    = (m.group(3)).strip()
                dsoName    = (m.group(4)).strip()
                symbolName = (m.group(5)).strip()

                if dsoName.startswith("[JIT]"): # delete the thread ID from the jitted thread
                    dsoName = dsoName[0:5]

                # Add to our dictionary
                if dsoName in globalDictionary:
                    symbolDictionary = globalDictionary[dsoName]
                    symbolDictionary[symbolName] = symbolDictionary.get(symbolName, 0) + samples
                else:
                    globalDictionary[dsoName] = {symbolName:samples}
    if not foundHeader:
        print("perf report output from ", cmd, " is not in expected format\n")
        sys.exit(-1)


def printLibraryContribution(globalDictionary, dsoName):
    print("===========", dsoName, "===================")
    symbolDictionary = globalDictionary[dsoName]
    # print all symbols ordered by samples
    sortedSymbols = sorted(symbolDictionary.items(), key=lambda i: i[1], reverse=True)
    for symbol in sortedSymbols:
        print(" SYM\t {sampl:6d}\t{sym}".format(sampl=symbol[1], sym=symbol[0]))

def computeSumSamplesInSymbolDictionary(symbolDictionary):
    sum = 0
     # for every symbol in my dictionary
    for symbol in symbolDictionary:
        sum += symbolDictionary[symbol]
    return sum

# Function used to sort dsos based on total ticks in symbols
# item is tuple where item[0] is the name of the dso and item[1] is a dictionary with symbols and samples
def sortingDsoFunction(item):
    return computeSumSamplesInSymbolDictionary(item[1])

def printAllLibrariesContribution(globalDictionary):
    # I would like to sort the dsos in order of total samples
    sortedDsos = sorted(globalDictionary.items(), key=sortingDsoFunction, reverse=True)
    for item in sortedDsos:
        dso = item[0]
        symbolDictionary = item[1]
        sum = computeSumSamplesInSymbolDictionary(symbolDictionary)
        print("{dso:20s}\t{sampl:6d}".format(dso=dso, sampl=sum))
    for item in sortedDsos:
        dso = item[0]
        #printLibraryContribution(globalDictionary, dso)



def printDiffPerSymbol(symbolDictionary1, symbolDictionary2):
    numPrintedSymbols = 0
    totalSamples1 = 0
    totalSamples2 = 0
    symbolUnion = {}
    if symbolDictionary1 is not None:
        for symbol in symbolDictionary1:
            samples1 = symbolDictionary1[symbol]
            totalSamples1 += samples1
            symbolUnion[symbol] = {'samples1':samples1, 'samples2':0}
    if symbolDictionary2 is not None:
        for symbol in symbolDictionary2:
            samples2 = symbolDictionary2[symbol]
            totalSamples2 += samples2
            if symbol in symbolUnion:
                sampleMap = symbolUnion[symbol]['samples2'] =  samples2
            else:
                symbolUnion[symbol] = {'samples1':0, 'samples2':samples2}

    print("| {s1:6s}\t | {s2:6s} \t | {diff:7s} \t | {percent:6s} \t | {sym:20s} ".format(s1='samples', s2='samples', diff='   diff', percent=' diff %', sym='Symbol'))
    print("| {s1:6s}\t | {s2:6s} \t | {diff:7s} \t | {percent:6s} \t | {sym:20s} ".format(s1='-------', s2='-------', diff='-------', percent='-------', sym='------'))
    percent = (totalSamples2 - totalSamples1)*100.0/float(totalSamples1) if totalSamples1 != 0 else 0.0
    print("| {s1:6d}\t | {s2:6d} \t | {diff:7d} \t | {percent:6.1f}% \t | {symbol:20s} ".
        format(s1=totalSamples1, s2=totalSamples2, diff=totalSamples2-totalSamples1, percent=percent, symbol="TOTAL"))
    # Sort symbolUnion based on the absolute difference of samples
    sortedSymbols = sorted(symbolUnion.items(), key=lambda i: abs(i[1]['samples1']-i[1]['samples2']), reverse=True)
    for item in sortedSymbols:
        symbol = item[0]
        samples1 = item[1]['samples1']
        samples2 = item[1]['samples2']
        percent = (samples2 - samples1)*100.0/float(samples1)  if samples1 != 0 else 0.0
        print("| {s1:6d} \t | {s2:6d} \t | {diff:7d} \t | {percent:6.1f}% \t | {sym:20s} ".format(s1=samples1, s2=samples2, diff=samples2-samples1, percent=percent, sym=symbol))
        numPrintedSymbols += 1
        if numPrintedSymbols > 20: # don't print more than 20 symbols per dso
            print("...")
            break


def printDiffPerLibrary(globalDictionary1, globalDictionary2):
    totalSamples1 = 0
    totalSamples2 = 0
    unionDsos = {}
    for dso in globalDictionary1:
        samples1 = computeSumSamplesInSymbolDictionary(globalDictionary1[dso])
        totalSamples1 += samples1
        unionDsos[dso] = {'samples1':samples1, 'samples2':0}
    for dso in globalDictionary2:
        samples2 = computeSumSamplesInSymbolDictionary(globalDictionary2[dso])
        totalSamples2 += samples2
        if dso in unionDsos:
            unionDsos[dso]['samples2'] = samples2
        else:
            unionDsos[dso] = {'samples1':0, 'samples2':samples2}
    print("=== Samples grouped per shared library ===")
    print("| {s1:6s}\t | {s2:6s} \t | {diff:7s} \t | {percent:6s} \t | {dso:20s} |".format(s1='samples', s2='samples', diff='   diff', percent=' diff %', dso='Shared Library'))
    print("| {s1:6s}\t | {s2:6s} \t | {diff:7s} \t | {percent:6s} \t | {dso:20s} |".format(s1='-------', s2='-------', diff='-------', percent='-------', dso='--------------'))
    percent = (totalSamples2 - totalSamples1)*100.0/float(totalSamples1) if totalSamples1 != 0 else 0.0
    print("| {s1:6d}\t | {s2:6d} \t | {diff:7d} \t | {percent:6.1f}% \t | {symbol:20s} |".
        format(s1=totalSamples1, s2=totalSamples2, diff=totalSamples2-totalSamples1, percent=percent, symbol="TOTAL"))
    # Sort the dsos based on their absolute difference
    sortedDsos = sorted(unionDsos.items(), key=lambda i: abs(i[1]['samples1']-i[1]['samples2']), reverse=True)
    for item in sortedDsos:
        dso = item[0]
        samples1 = item[1]['samples1']
        samples2 = item[1]['samples2']
        percent = (samples2 - samples1)*100.0/float(samples1)  if samples1 != 0 else 0.0
        print("| {s1:6d} \t | {s2:6d} \t | {diff:7d} \t | {percent:6.1f}% \t | {dso:20s} |".format(s1=samples1, s2=samples2, diff=samples2-samples1, percent=percent, dso=dso))

    print("\n")
    # Print the symbol differences
    for item in sortedDsos:
        dso = item[0]
        samples1 = item[1]['samples1']
        samples2 = item[1]['samples2']
        # Do not print symbols for dsos that use very little CPU compared to the total (less than 1%)
        if samples1/float(totalSamples1) >= 0.01 or samples2/float(totalSamples2) >= 0.01:
            symbolDictionary1 = globalDictionary1[dso] if dso in globalDictionary1 else None
            symbolDictionary2 = globalDictionary2[dso] if dso in globalDictionary2 else None
            print("======== ", dso, " ===========")
            printDiffPerSymbol(symbolDictionary1, symbolDictionary2)



# Get the name of vlog
if  len(sys.argv) < 2:
    print ("Program must have 2N arguments: the perf profiles\n")
    sys.exit(-1)

numPerfProfiles = len(sys.argv) - 1
if numPerfProfiles % 2 != 0:
    print("Number of perf profiles must be even")
    sys.exit(-1)

# Process half the profiles
globalDictionary1 = {}
for i in range(numPerfProfiles//2):
    perfFileName = str(sys.argv[i+1])
    print(perfFileName)
    processPerfProfile(perfFileName, globalDictionary1)

# Process the other half
globalDictionary2 = {}
for i in range(numPerfProfiles//2, numPerfProfiles):
    perfFileName = str(sys.argv[i+1])
    print(perfFileName)
    processPerfProfile(perfFileName, globalDictionary2)

#printAllLibrariesContribution(globalDictionary1)
#printAllLibrariesContribution(globalDictionary2)
printDiffPerLibrary(globalDictionary1, globalDictionary2)

