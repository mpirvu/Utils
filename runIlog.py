# Python script for runing ilog
import logging
import math
import os
import re # for regular expressions
import shlex, subprocess
import sys # for accessing parameters and exit

ILOG_HOME="/opt/IBM/ILOGForMarius/ilog/odm881eGa/J2SE"
doColdRun = True # If True, destroy the SCC before each benchmark
doOnlyColdRuns = False
affinity = "taskset 0x3"
WARMUPTIME = 240
TIMEOUTTIME = 400
numThreads = 4
#level=logging.DEBUG,
logging.basicConfig(level=logging.INFO, format='%(asctime)s :: %(levelname)s :: (%(threadName)-6s) :: %(message)s',)
os.environ["CLASSPATH"] = "lib/asm-3.1.jar:lib/asm-analysis-3.1.jar:lib/asm-commons-3.1.jar:lib/asm-tree-3.1.jar:lib/asm-util-3.1.jar:lib/dom4j-1.6.1.jar:lib/j2ee_connector-1_5-fr.jar:lib/jrules-engine.jar:lib/jrules-res-execution.jar:lib/log4j-1.2.8.jar:lib/openxml4j-beta.jar:lib/sam.jar:lib/sizing-xom.jar:bin:bin/ra.xml"


# List of rule sets to run
ruleSet = [
    "F_JAVAXOM_Segmentation5_DE",
    "F_JAVAXOM_Segmentation300RulesSingleTask_DE"
]

# List of JVM options to try
jvmOptions = [
    #"",
    "-Xms1G -Xmx1G",
]

jdks = [
    "/home/mpirvu/FullJava17/openj9-openjdk-jdk17/build/linux-x86_64-server-release/images/jdk",
]

def count_not_nan(myList):
    total = 0
    for i in range(len(myList)):
        if not math.isnan(myList[i]):
            total += 1
    return total

def nanmean(myList):
    total = 0
    numValidElems = 0
    for i in range(len(myList)):
        if not math.isnan(myList[i]):
            total += myList[i]
            numValidElems += 1
    return total/numValidElems if numValidElems > 0 else math.nan

def nanstd(myList):
    total = 0
    numValidElems = 0
    for i in range(len(myList)):
        if not math.isnan(myList[i]):
            total += myList[i]
            numValidElems += 1

    if numValidElems == 0:
        return math.nan
    if numValidElems == 1:
        return 0
    else:
        mean = total/numValidElems
        total = 0
        for i in range(len(myList)):
            if not math.isnan(myList[i]):
                total += (myList[i] - mean)**2
        return math.sqrt(total/(numValidElems-1))

def nanmin(myList):
    min = math.inf
    for i in range(len(myList)):
        if not math.isnan(myList[i]) and myList[i] < min:
            min = myList[i]
    return min

def nanmax(myList):
    max = -math.inf
    for i in range(len(myList)):
        if not math.isnan(myList[i]) and myList[i] > max:
            max = myList[i]
    return max

def tDistributionValue95(degreeOfFreedom):
    if degreeOfFreedom < 1:
        return math.nan
        #import scipy.stats as stats
        #  stats.t.ppf(0.975, degreesOfFreedom))
    tValues = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
               2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
               2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,]
    if degreeOfFreedom <= 30:
        return tValues[degreeOfFreedom-1]
    else:
        if degreeOfFreedom <= 60:
            return 2.042 - 0.001 * (degreeOfFreedom - 30)
        else:
            return 1.96

# Confidence intervals tutorial
# mean +- t * std / sqrt(n)
# For 95% confidence interval, t = 1.96 if we have many samples
def meanConfidenceInterval95(myList):
    n = len(myList)
    if n <= 1:
        return math.nan
    tvalue = tDistributionValue95(n-1)
    avg, stdDev = nanmean(myList), nanstd(myList)
    marginOfError = tvalue * stdDev / math.sqrt(n)
    return 100.0*marginOfError/avg

def computeStats(myList):
    avg = nanmean(myList)
    stdDev = nanstd(myList)
    min = nanmin(myList)
    max = nanmax(myList)
    ci95 = meanConfidenceInterval95(myList)
    numValues = count_not_nan(myList)
    return avg, stdDev, min, max, ci95, numValues


def clearSCC(jvm, jvmOpts):
    print(jvm, jvmOpts)
    cmd = ""
    # Parse the options and try to figure out the location of the SCC
    # This should be something like -Xshareclasses:cacheDir=<name>
    # match alphanum and _ and . and / but not comma
    m = re.search('cacheDir=([a-zA-Z0-9_\./]+)', jvmOpts)
    if m:
        cacheDir = m.group(1)
        cmd = f"{jvm}/bin/java -Xshareclasses:cacheDir={cacheDir},destroyall"
    else:
        cmd = f"{jvm}/bin/java -Xshareclasses:destroyall"
    #logging.info("Destroying SCC with: {cmd}".format(cmd=cmd))
    try:
        output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        # If the SCC does not exist, we get a non-zero return code
        output = e.output
    except subprocess.SubprocessError as e:
        logging.warning("SubprocessError clearing SCC: {e}".format(e=e))
        output = str(e)
    logging.info("{output}".format(output=output))
    return 0

'''
Returns the throughput in TPS (as a float). Nan is experiment fails
or Nan if the experiment fails

Output looks like this:
Timeout: 400s
Warmup of 240s
421349 executions during Warmup
Executed 740386 times for each thread
Total execution time: 400000 ms
Nb. threads: 4
Size Input Parameter factor: 1
Sum Throughput (TPS) per thread for run = 7403.860000000001
Global Throughput (TPS) for run = 7403.86
Average response time for run = 0.5402587977860824 ms
Min Execution Duration: 0
Max Execution Duration: 4
Min TPS for one thread: 1849.9525
Max TPS for one thread: 1851.575
'''
def runBenchmarkOnce(rule, jvm, jvmOpts):
    ilogCmdLine = f"com.ibm.rules.bench.segmentation.RuleEngineRunner ruleset={rule} javaXOM sizeparam=1 warmup={WARMUPTIME} timeout={TIMEOUTTIME} stateful 100000000 reportPath=j2se-perf-report-87.csv  jrulesVersion=8.7 DT_or_Rules=DT multithread={numThreads}"
    cmd = f"{affinity} {jvm}/bin/java {jvmOpts} {ilogCmdLine}"
    logging.info("Starting: {cmd}".format(cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.STDOUT, cwd=ILOG_HOME)
    # Parse the output and look for "Global Throughput (TPS) for run = "
    lines = output.splitlines()
    pattern1 = re.compile('^Global Throughput \(TPS\) for run = (\d+\.\d+)')
    tps = math.nan
    for line in lines:
        #print(line)
        m = pattern1.match(line)
        if m:
            tps = float(m.group(1))
            break
    return tps

def runBenchmarkIteratively(numIter, rule, jdk, javaOpts):
    thrResults = []
    # clear SCC if needed
    if doColdRun or doOnlyColdRuns:
        clearSCC(jdk, javaOpts)

    for iter in range(numIter):
        thr = runBenchmarkOnce(rule, jdk, javaOpts)
        thrResults.append(thr)

   # print stats
    startIter = 0 if (doOnlyColdRuns or not doColdRun) else 1
    print(f"\nResults for jdk: {jdk} and opts: {javaOpts} and ruleSet: {rule}")
    if startIter > 0:
        print("First run is a cold run and is not included in the stats")
    avg, stdDev, min, max, ci95, numSamples = computeStats(thrResults[startIter:])
    print("Thr stats:  Avg={avg:7.1f}  StdDev={stdDev:7.1f}  Min={min:7.1f}  Max={max:7.1f}  Max/Min={maxmin:4.0f}% CI95={ci95:7.1f}% numSamples={numSamples:3d}".
                        format(avg=avg, stdDev=stdDev, min=min, max=max, maxmin=(max-min)*100.0/min, ci95=ci95, numSamples=numSamples))

############################ MAIN ##################################
if  len(sys.argv) < 2:
    print ("Program must have an argument: the number of iterations\n")
    sys.exit(-1)

if doOnlyColdRuns:
    print("Will do a cold run before each run")
elif doColdRun:
    print("Will do a cold run before each set")

for rule in ruleSet:
    for jvmOpts in jvmOptions:
        for jdk in jdks:
            runBenchmarkIteratively(numIter=int(sys.argv[1]), rule=rule, jdk=jdk, javaOpts=jvmOpts)
