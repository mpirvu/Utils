# Python script that parses an OpenJ9 javacore and displays
# the number of classes in SCC and outside SCC.
# Depending on the configuration it will also print
# all classes that were shared (in SCC) or not shared
# If `displayClassLoaderHierarchy` is `True` we also print all the unique (by name)
# class hierarchies that lead to a class being non shared (or shared)
# The goal is to understand why some methods are not AOT compiled, despite -Xaot:forceaot
# Usage: python3 findNonSCCClassesFromJavacore.py javacore
#
# Author: Marius Pirvu

import re # for regular expressions
import sys # for accessing parameters and exit

displaySharedClasses = False
displayNonSharedClasss = True
displayClassLoaderHierarchy = True


'''
1CLTEXTCLLSS            12345678: 1=primordial,2=extension,3=shareable,4=middleware,5=system,6=trusted,7=application,8=delegating
2CLTEXTCLLOADER         -----t-- Loader org/jboss/modules/ModuleClassLoader(0x00000000B1701190), Parent jdk/internal/loader/ClassLoaders$AppClassLoader(0x00000000AED4F1C0)
3CLNMBRLOADEDLIB                Number of loaded libraries 0
3CLNMBRLOADEDCL                 Number of loaded classes 15
2CLTEXTCLLOADER         -x--st-- Loader jdk/internal/loader/ClassLoaders$PlatformClassLoader(0x00000000AED3DEF0), Parent *none*(0x0000000000000000)
3CLNMBRLOADEDLIB                Number of loaded libraries 1
3CLNMBRLOADEDCL                 Number of loaded classes 77
3CLNMBRSHAREDCL                 Number of shared classes 72
2CLTEXTCLLOADER         -----t-- Loader org/eclipse/osgi/internal/loader/EquinoxClassLoader(0x00000000F16DD068), Parent com/ibm/ws/kernel/internal/classloader/BootstrapChildFirstJarClassloader(0x00000000F014E948)
3CLNMBRLOADEDLIB                Number of loaded libraries 0
3CLNMBRLOADEDCL                 Number of loaded classes 21
3CLNMBRSHAREDCL                 Number of shared classes 19

...
2CLTEXTCLLOAD           Loader org/jboss/modules/ModuleClassLoader(0x00000000B1701190)
3CLTEXTCLASS                    com/fasterxml/jackson/databind/exc/UnrecognizedPropertyException(0x00000000030DFB00)
3CLTEXTCLASS                    com/fasterxml/jackson/databind/exc/PropertyBindingException(0x00000000030DF400)
3CLTEXTCLASS                    com/fasterxml/jackson/databind/exc/MismatchedInputException(0x00000000030DEE00)
3CLTEXTCLASS                    com/fasterxml/jackson/databind/JsonMappingException(0x00000000030DEA00)
3CLTEXTCLASS                    com/fasterxml/jackson/databind/SerializationFeature(0x00000000030DDD00)
3CLTEXTCLASS                    com/fasterxml/jackson/databind/DeserializationFeature(0x00000000030DD500)
...
2CLTEXTCLLOAD           Loader jdk/internal/reflect/DelegatingClassLoader(0x00000000EBABE088)
3CLTEXTCLASS                    jdk/internal/reflect/GeneratedMethodAccessor3(0x0000000002B16400)
...
2CLTEXTCLLOAD           Loader org/eclipse/osgi/internal/loader/EquinoxClassLoader(0x00000000F16DD068)
3CLTEXTCLASS                    com/ibm/ws/sib/jfapchannel/server/impl/JFapDiscriminator(0x0000000000AD5800 shared)
3CLTEXTCLASS                    com/ibm/ws/sib/jfapchannel/server/impl/JFapChannelInbound(0x0000000000AD5000 shared)
3CLTEXTCLASS                    com/ibm/ws/jfap/inbound/channel/JFAPInboundServiceContext(0x0000000000AD4A00 shared)
3CLTEXTCLASS                    com/ibm/ws/jfap/inbound/channel/CommsInboundChain$ChainConfiguration(0x0000000000AD4500 shared)
3CLTEXTCLASS                    com/ibm/ws/jfap/inbound/channel/JFAPServerInboundChannelFactory(0x0000000000AD3F00 shared)
3CLTEXTCLASS                    [Lcom/ibm/ws/sib/jfapchannel/server/impl/ServerConnectionManagerImpl$State;(0x0000000000AD3C00)
'''

def parseJavacore(javacore):
    classLoaderHash = {}
    classHash = {}
    foundLegend = False
    activeClassLoader = None
    totalClasses = 0
    totalSharedClasses  = 0
    uniqueClassLoaderHierarchies = {}

    def printClassLoaderHierarchyForClass(classAddr, uniqueClassLoaderHierarchies):
        indentLevel = 1
        CLAddr = classHash[classAddr]['classLoaderAddr']
        print("Class loader hierarchy:")
        CLName = classLoaderHash[CLAddr]['classLoaderName']
        print("\t{name} {addr:016X}".format(name=CLName, addr=CLAddr))
        classLoaderHierarchy = [CLName] # Use name instead of CLAddr to get uniqueness by name (to many loaders with same name but different address)
        parentCL = classLoaderHash[CLAddr]['parentCLAddr']
        while parentCL != 0:
            indentLevel += 1
            for i in range(indentLevel):
                print("\t", end='')
            parentName = classLoaderHash[parentCL]['classLoaderName']
            print("{name} {addr:016X}".format(name=parentName, addr=parentCL))
            classLoaderHierarchy.append(parentName)
            parentCL = classLoaderHash[parentCL]['parentCLAddr']
        # Convert the list into a tuple so that we can use as a key in a dictionary
        q = tuple(classLoaderHierarchy)
        uniqueClassLoaderHierarchies[q] = uniqueClassLoaderHierarchies.get(q, 0) + 1


    def printUniqueClassLoaderHierarchies(uniqueClassLoaderHierarchies):
        print("Unique class loader hierarchies:")
        for key, value in uniqueClassLoaderHierarchies.items(): # key is a tuple
            print("\nNum classes loaded by this hierarchy:", value)
            indentLevel = 0;
            for classLoader in key:
                for i in range(indentLevel):
                    print("\t", end='')
                #print(classLoaderHash[classLoader]['classLoaderName'])
                print(classLoader)
                indentLevel += 1


    legendPattern = re.compile('1CLTEXTCLLSS\s+12345678: 1=primordial,2=extension,3=shareable,4=middleware,5=system,6=trusted,7=application,8=delegating')
    for line in javacore:
        if legendPattern.match(line):
            foundLegend = True
            break
    if not foundLegend:
        print("Cannot find classloader legend in the javacore")
        exit(-1)

    classLoaderPattern = re.compile('^2CLTEXTCLLOADER\s+([-\w]{8}) Loader (\S+)\(0x([0-9A-F]+)\), Parent (\S+)\(0x([0-9A-F]+)\)')
    numLibsLoadedPattern = re.compile('^3CLNMBRLOADEDLIB\s+Number of loaded libraries\s+(\d+)')
    numClassesLoadedPattern = re.compile('^3CLNMBRLOADEDCL\s+Number of loaded classes\s+(\d+)')
    numClassesSharedPattern = re.compile('^3CLNMBRSHAREDCL\s+Number of shared classes\s+(\d+)')
    classLoaderHeader = re.compile('^2CLTEXTCLLOAD\s+Loader (\S+)\(0x([0-9A-F]+)\)')
    classPattern = re.compile('^3CLTEXTCLASS\s+(\S+)\(0x([0-9A-F]+)(?: shared)?\)') # " shared" is optional

    for line in javacore:
        if line.startswith("2CLTEXTCLLOADER"):
            flags = ""
            m = classLoaderPattern.match(line)
            if m:
                flags = m.group(1)
                classLoaderName = m.group(2)
                classLoaderAddr = int(m.group(3), base=16)
                parentCLName = m.group(4)
                parentCLAddr = int(m.group(5), base=16)
                activeClassLoader = classLoaderAddr
                classLoaderHash[classLoaderAddr] = {"classLoaderName":classLoaderName, "flags":flags, "parentCLName":parentCLName, "parentCLAddr":parentCLAddr}
            else:
                # The system class loader has no parent specified
                clp = re.compile('^2CLTEXTCLLOADER\s+([-\w]{8}) Loader \*System\*\(0x([0-9A-F]+)\)')
                m = clp.match(line)
                if m:
                    flags = m.group(1)
                    classLoaderName = "System"
                    classLoaderAddr = int(m.group(2), base=16)
                    activeClassLoader = classLoaderAddr
                    classLoaderHash[classLoaderAddr] = {"classLoaderName":classLoaderName, "flags":flags, "parentCLName":"*none*", "parentCLAddr":0}
                else:
                    print("Unrecognized classLoaderPattern:", line)
                    exit(-1)
        elif line.startswith("3CLNMBRLOADEDLIB"):
            m = numLibsLoadedPattern.match(line)
            assert m, "Wrong line with 3CLNMBRLOADEDLIB heading: {l}".format(l=line)
            numLibs = int(m.group(1))
            classLoaderHash[activeClassLoader]['numLibsLoaded'] = numLibs
        elif line.startswith("3CLNMBRLOADEDCL"):
            m = numClassesLoadedPattern.match(line)
            assert m, "Wrong line with 3CLNMBRLOADEDCL heading: {l}".format(l=line)
            numClasses = int(m.group(1))
            classLoaderHash[activeClassLoader]['numClassesLoaded'] = numClasses
            totalClasses += numClasses
        elif line.startswith("3CLNMBRSHAREDCL"):
            m = numClassesSharedPattern.match(line)
            assert m, "Wrong line with 3CLNMBRSHAREDCL heading: {l}".format(l=line)
            numShared = int(m.group(1))
            classLoaderHash[activeClassLoader]['numClassesShared'] = numShared
            totalSharedClasses += numShared
        elif line.startswith("2CLTEXTCLLOAD"):
            m = classLoaderHeader.match(line)
            assert m, "Wrong line with 2CLTEXTCLLOAD heading: {l}".format(l=line)
            classLoaderName = m.group(1)
            classLoaderAddr = int(m.group(2), base=16)
            assert classLoaderAddr in classLoaderHash, "Class loader must have been seen before"
            activeClassLoader = classLoaderAddr
        elif line.startswith("3CLTEXTCLASS"):
            m = classPattern.match(line)
            if m:
                className = m.group(1)
                classAddr = int(m.group(2), base=16)
                shared = True if " shared" in line else False
                classHash[classAddr] = {'className':className, 'shared':shared, 'classLoaderAddr':activeClassLoader}

    print("Total classes:", totalClasses)
    print("Total shared classes", totalSharedClasses)

    if displaySharedClasses:
        print("Displaying classes in SCC")
        numShared = 0
        for classAddr, attribs in classHash.items():
            if attribs['shared'] == True:
                print(attribs['className'])
                numShared += 1
                if displayClassLoaderHierarchy:
                    printClassLoaderHierarchyForClass(classAddr, uniqueClassLoaderHierarchies)
        print("Num shared classes in dictionary:", numShared)
        if displayClassLoaderHierarchy:
            printUniqueClassLoaderHierarchies(uniqueClassLoaderHierarchies)

    if displayNonSharedClasss:
        print("Displaying classes not in SCC")
        numNonShared = 0
        for classAddr, attribs in classHash.items():
            if attribs['shared'] == False:
                print(attribs['className'])
                numNonShared += 1
                if displayClassLoaderHierarchy:
                    printClassLoaderHierarchyForClass(classAddr, uniqueClassLoaderHierarchies)
        print("Num non shared classes in dictionary:", numNonShared)
        if displayClassLoaderHierarchy:
            printUniqueClassLoaderHierarchies(uniqueClassLoaderHierarchies)

# Get the name of vlog
if  len(sys.argv) < 2:
    print ("Program must have an argument: the name of the javacore\n")
    sys.exit(-1)

# Open my file in read only mode with line buffering
javacoreFileName = str(sys.argv[1])
javacore = open(javacoreFileName, 'r', 1)

parseJavacore(javacore)
