# Python script that parses an OpenJ9 verbose log and prints all inlining desisions for a given method
# compilation statistics
# Usage: python3 parseVlog.py vlogFilename targetMethod
#
# Author: Marius Pirvu
import re # for regular expressions
import sys


def parseVlog(vlog, targetMethod) -> None:

    # #INL: 3 methods inlined into 8d213da5 java/util/ImmutableCollections$SetN.probe(Ljava/lang/Object;)I @ 00007FD02D0017C0
    # #INL: 28 methods inlined into a056a575 com/adobe/granite/workflow/core/WorkflowSessionImpl.<init>(Lcom/adobe/granite/workflow/core/WorkflowSessionFactory;Lcom/adobe/granite/workflow/core/event/EventPublishUtil;Ljavax/jcr/Session;Lcom/adobe/granite/workflow/core/advance/AdvanceHandlerManager;Lcom/adobe/granite/workflow/core/rule/admin/RuleEngineAdmin;Ljava/lang/ClassLoader;Lcom/adobe/granite/workflow/core/model/WorkflowModelCacheImpl;Lcom/adobe/granite/workflow/core/metadata/WorkflowUserMetaDataCache;Lorg/apache/sling/api/resource/ResourceResolverFactory;Lorg/apache/sling/api/adapter/AdapterManager;Lorg/apache/sling/event/jobs/JobManager;
    inliningStartPattern = re.compile(r'^#INL: (\d+) methods inlined into (\S+) (\S+)')

    # #INL: #28: 39d6d745 #10 inlined d4b5a828@76 -> 41b39055 bcsz=10 java/util/concurrent/atomic/Striped64$Cell.cas(JJ)Z
    methodInlinedPattern = re.compile(r'^#INL: #([+-]?\d+): (\S+) #([+-]?\d+) inlined (\S+)@(\d+) -> (\S+) bcsz=([+-]?\d+) (\S+)')
    # #INL: b9362404 coldCalled b9362404@60 -> bd4fa21c bcsz=25 java/lang/String.lengthInternal()I
    # #INL: b9362404 called b9362404@44 -> 80a178ea bcsz=47 java/lang/String.hashCodeImplCompressed([BII)I
    methodNotInlinedPattern = re.compile(r'^#INL: (\S+) (\S+) (\S+)@(\d+) -> (\S+) bcsz=([+-]?\d+) (\S+)')
    lineNum = 0
    insideInliningRegion = False
    crtCaller = ""
    # Parse the vlog
    for line in vlog:
        line = line.strip()
        lineNum += 1
        #print("Looking at line", lineNum, "insideInliningRegion=", insideInliningRegion, "crtCaller=", crtCaller, ":", line)

        if not line.startswith("#INL:"):
            # I am not interested at all in this line
            insideInliningRegion = False
            crtCaller = ""
            continue

        m = inliningStartPattern.match(line)
        if m:
            # A new inlining region starts
            methodName = m.group(3)
            insideInliningRegion = True
            crtCaller = methodName
            continue

        assert insideInliningRegion, "I must be inside an inlining region at line " + str(lineNum) + ": " + line

        # I am inside an inlining region and caller is crtCaller
        m = methodInlinedPattern.match(line)
        if m:
            callee = m.group(8)
            if callee == targetMethod:
                # Found the target method
                print("Found target method inlined in", crtCaller, "at line", lineNum)
            continue

        m = methodNotInlinedPattern.match(line)
        if m:
            callee = m.group(7) # the method that was not inlined
            if callee == targetMethod:
                # Found the target method
                print("Found target method not inlined in", crtCaller, "at line", lineNum)
            continue
        # There cannot be any other pattern
        assert False, "Unknown pattern at line " + str(lineNum) + ": " + line


# Get the name of vlog
if  len(sys.argv) < 3:
    print ("Program must have two argument: the name of the vlog and the full name of a method\n")
    sys.exit(-1)

# Open my file in read only mode with line buffering
vlogFileName = str(sys.argv[1])
Vlog = open(vlogFileName, 'r', 1)
targetMethod= str(sys.argv[2])
print(f"Searching for {targetMethod}")
parseVlog(vlog=Vlog, targetMethod=targetMethod)

