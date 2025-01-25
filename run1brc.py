# Python script for runing one billion row challenge
import logging
import math
import os
import re # for regular expressions
import shlex, subprocess
import sys # for accessing parameters and exit
from timeit import default_timer
import statistics


doColdRun = False # If True, destroy the SCC before each benchmark
doOnlyColdRuns = False
affinity = "" # Use numactl or taskset
#level=logging.DEBUG,
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s :: %(levelname)s :: (%(threadName)-6s) :: %(message)s',)

os.environ["CLASSPATH"] = "target/average-1.0.0-SNAPSHOT.jar"
appHome = "/team/mpirvu/Benchmarks/1brc"
appArgs = "dev.morling.onebrc.CalculateAverage_giovannicuccu"


# List of JVM options to try
jvmOptions = [
    #"--enable-preview --add-modules=jdk.incubator.vector -Xmx5g -Xms5g",
    #"--enable-preview --add-modules=jdk.incubator.vector -Xmx5g -Xms5g -Xjit:verbose={VectorAPI|compil*},vlog=vlog.log,AcceptHugeMethods,scratchSpaceLimit=2000000000,enableVectorAPIBoxing  -XX:-EnableHCR",
    "--enable-preview --add-modules=jdk.incubator.vector -Xmx5g -Xms5g -Xjit:enableVectorAPIBoxing  -XX:-EnableHCR",
]

jdks = [
    "/team/mpirvu/sdks/OpenJ9-JDK21-x86-64_linux-20241218-223824",
    #"/team/mpirvu/sdks/OpenJDK21U-jre_x64_linux_hotspot_21.0.5_11"
]

def eliminateNans(myList):
    return [value for value in myList if not math.isnan(value)]
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

def computeStats(myList):
    myList = eliminateNans(myList)
    numSamples = len(myList)
    if numSamples < 1:
        return math.nan, math.nan, math.nan, math.nan, math.nan, 0
    avg = statistics.fmean(myList)
    stdDev = statistics.stdev(myList)
    minVal = min(myList)
    maxVal = max(myList)

    tvalue = tDistributionValue95(numSamples-1)
    marginOfError = tvalue * stdDev / math.sqrt(numSamples)
    ci95 = 100.0*marginOfError/avg

    return avg, stdDev, minVal, maxVal, ci95, numSamples


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


def runBenchmarkOnce(jvm, jvmOpts):
    cmd = f"{affinity} {jvm}/bin/java {jvmOpts} {appArgs}"
    logging.info("Starting: {cmd}".format(cmd=cmd))

    startTime = default_timer()
    subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.STDOUT, cwd=appHome)
    endTime = default_timer()

    elapsedTime  = endTime - startTime # Time as a float in seconds
    logging.info("Program took {t:0.2f} seconds".format(t=elapsedTime))

    return elapsedTime

def runBenchmarkIteratively(numIter, jdk, javaOpts):
    thrResults = []
    # clear SCC if needed
    if doColdRun or doOnlyColdRuns:
        clearSCC(jdk, javaOpts)

    for iter in range(numIter):
        if doOnlyColdRuns:
            clearSCC(jdk, javaOpts)
        thr = runBenchmarkOnce(jdk, javaOpts)
        thrResults.append(thr)

   # print stats
    startIter = 0 if (doOnlyColdRuns or not doColdRun) else 1
    print(f"\nResults for jdk: {jdk} and opts: {javaOpts}")
    if startIter > 0:
        print("First run is a cold run and is not included in the stats")
    avg, stdDev, min, max, ci95, numSamples = computeStats(thrResults[startIter:])
    print("Runtime stats:  Avg={avg:7.1f}  StdDev={stdDev:7.1f}  Min={min:7.1f}  Max={max:7.1f}  Max/Min={maxmin:4.0f}% CI95={ci95:7.1f}% numSamples={numSamples:3d}".
                        format(avg=avg, stdDev=stdDev, min=min, max=max, maxmin=(max-min)*100.0/min, ci95=ci95, numSamples=numSamples))

############################ MAIN ##################################
if  len(sys.argv) < 2:
    print ("Program must have an argument: the number of iterations\n")
    sys.exit(-1)

if doOnlyColdRuns:
    print("Will do a cold run before each run")
elif doColdRun:
    print("Will do a cold run before each set")

for jvmOpts in jvmOptions:
    for jdk in jdks:
        runBenchmarkIteratively(numIter=int(sys.argv[1]), jdk=jdk, javaOpts=jvmOpts)
