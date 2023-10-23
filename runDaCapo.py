# Python script to run DaCapo benchmarks and collect performance statistics
# Usage:  python3 runDaCapo.py
# Note: dacapo-9.12-MR1-bach.jar  msut be present in current directory

# The script can be customized as follows:
# 1. Specify which benchmarks to run ==> change "benchmark" list below
# 2. Specify the number iterations for each benchmark (to warm up the JVM) ==> change "benchmarkOpts" below
# 3. Specify the number of runs for each benchmark ==> change "numRuns" below
# 4. Specify JDK options ==> change "jvmOption" list below
# 5. Specify JDK to use ==> change "jdks" list below

import re # for regular expressions
import sys # for accessing parameters and exit
import shlex, subprocess
import logging
import numpy as np


numRuns = 100 # number of runs to use for each benchmark in each configuration
benchmarkOpts = "--iterations 1 -s default" # not all benchmarks can use size large. Better to use "default"
numlastIterForComputingAvg = 1 # number of last iterations for each JVM used for computing the average execution time
                                # Must be smaller that --iterations in benchmarkOpts
doColdRun = True # If True, destroy the SCC before each benchmark
affinity = "taskset 0x3"
#level=logging.DEBUG,
logging.basicConfig(level=logging.INFO, format='%(asctime)s :: %(levelname)s :: (%(threadName)-6s) :: %(message)s',)


# List of benchmarks to run
# avrora batik eclipse fop h2 jython luindex lusearch lusearch-fix pmd sunflow tomcat tradebeans tradesoap xalan
benchmarks = [
    #"avrora",
    #"batik", Not working at all with OpenJDK
    #"eclipse", # only working with Java8
    "fop",
    #"h2",
    #"jython", # Does not run Java17
    #"luindex",
    #"lusearch-fix",
    #"pmd",
    #"sunflow",
    #"tomcat", # does not work at all
    #"tradebeans", # does not work at all
    #"tradesoap", # does not work at all
    #"xalan"
]

# List of JVM options to try
jvmOptions = [
    #"",
    "-Xms1G -Xmx1G",
]

jdks = [
    #"/home/mpirvu/sdks/OpenJDK17U-jre_x64_linux_hotspot_17.0.8.1_1",
    "/home/mpirvu/FullJava17/openj9-openjdk-jdk17/build/linux-x86_64-server-release/images/jdk"
]

def destroySCC(jvm, jvmOpts):
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
Returns the execution time in milliseconds as a float
or Nan if the experiment fails
'''
def runBenchmarkOnce(benchmarkName, jvm, jvmOpts, benchIter):
    cmd = f"{affinity} {jvm}/bin/java {jvmOpts} -jar dacapo-9.12-MR1-bach.jar {benchmarkOpts} {benchmarkName}"
    logging.info("Starting: {cmd}".format(cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.STDOUT)
    # Parse the output and look for "PASSED in nnnn msec" or "completed warmup nnn in nnnn msec" ====
    #===== DaCapo 9.12-MR1 fop starting warmup 1 =====
    #===== DaCapo 9.12-MR1 fop completed warmup 1 in 1672 msec =====
    #...
    #===== DaCapo 9.12-MR1 fop starting =====
    #===== DaCapo 9.12-MR1 fop PASSED in 242 msec =====


    lines = output.splitlines()
    pattern1 = re.compile('^===== DaCapo .+ PASSED in (\d+) msec ====')
    pattern2 = re.compile('^===== DaCapo .+ completed warmup \d+ in (\d+) msec ====')
    foundPassed = False
    runTimes = np.full(benchIter, fill_value=np.nan, dtype=float)
    i = 0
    for line in lines:
        #print(line)
        m = pattern1.match(line)
        if m:
            runTimes[i] = float(m.group(1))
            foundPassed = True
            i = i + 1
        else:
            m = pattern2.match(line)
            if m:
                runTimes[i] = float(m.group(1))
                i = i + 1
    # Compute the average time of the last N iterations
    avgTime = np.nanmean(runTimes[-numlastIterForComputingAvg:])
    print(avgTime)

    return avgTime if foundPassed else np.nan
    #print(output)

def tdistribution(degreesOfFreedom):
    table = [6.314, 2.92, 2.353, 2.132, 2.015, 1.943, 1.895, 1.860, 1.833, 1.812, 1.796, 1.782, 1.771, 1.761, 1.753, 1.746, 1.740, 1.734, 1.729, 1.725]
    if degreesOfFreedom < 1:
        return -1.0
    if degreesOfFreedom <= 20:
        return table[degreesOfFreedom-1]
    if degreesOfFreedom < 30:
        return 1.697
    if degreesOfFreedom < 40:
        return 1.684
    if degreesOfFreedom < 50:
        return 1.676
    if degreesOfFreedom < 60:
        return 1.671
    if degreesOfFreedom < 70:
        return 1.667
    if degreesOfFreedom < 80:
        return 1.664
    if degreesOfFreedom < 90:
        return 1.662
    if degreesOfFreedom < 100:
        return 1.660
    return 1.65

#import scipy.stats as st
#def computeCI95(a):
#    results = st.t.interval(0.95, len(a)-1, loc=0, scale=st.sem(a))
#    return 100.0 * results[1] / st.tmean(a)

# Determine the number of iterations to use for each benchmark
m = re.compile('--iterations (\d+)').match(benchmarkOpts)
benchIter = int(m.group(1)) if m else sys.exit('Cannot determine number of iterations from benchmarkOpts')
if benchIter < numlastIterForComputingAvg:
    sys.exit('Number of iterations for computing the average must be smaller than the total number of iterations')

if doColdRun:
    print("Will do cold")

# multi-dimensional array of results
results = np.full((len(benchmarks), len(jdks), len(jvmOptions), numRuns), fill_value=np.nan, dtype=float)
for bench in range(len(benchmarks)):
    for jdk in range(len(jdks)):
        for opt in range(len(jvmOptions)):
            if doColdRun:
                destroySCC(jdks[jdk],jvmOptions[opt])
                runBenchmarkOnce(benchmarks[bench], jdks[jdk], jvmOptions[opt], benchIter) # discard the cold run
            for i in range(numRuns):
                execTime = runBenchmarkOnce(benchmarks[bench], jdks[jdk], jvmOptions[opt], benchIter)
                results[bench, jdk, opt, i] = execTime

# Stats ignoring Nan which are due to failed experiments
mean = np.nanmean(results, axis=3)
std  = np.nanstd(results, axis=3)
min  = np.nanmin(results, axis=3)
max  = np.nanmax(results, axis=3)

# Count valid experiments excluding Nan values
numValidExperiments = np.count_nonzero(~np.isnan(results), axis=3)
#print(numValidExperiments)

# Create my function that will apply "tdistribution" to all elements in an ndarray
tdist_vec = np.vectorize(tdistribution)
# Compute 95% confidence intervals as percentages of the mean value
ci95 = tdist_vec(numValidExperiments-1) * std / np.sqrt(numValidExperiments) / mean *100.0


# np.percentile(s1, [25, 50, 75], interpolation='midpoint')
# Count how many non-NaN values are in the array
#  np.count_nonzero(~np.isnan(data))

for bench in range(len(benchmarks)):
    for jdk in range(len(jdks)):
        for opt in range(len(jvmOptions)):
            print("Bench =", benchmarks[bench], "JDK =", jdks[jdk], "Opt =", jvmOptions[opt])
            print("mean = {m:5.0f} \tCI95 = {ci:4.2}% \tStdDev = {s:3.1f} \tMin = {mi:5.0f} \tMax = {ma:5.0f} \tNum = {n:2d}".
                format(m=mean[bench, jdk, opt], ci=ci95[bench, jdk, opt], s=std[bench, jdk, opt], mi= min[bench, jdk, opt], ma=max[bench, jdk, opt], n=numValidExperiments[bench, jdk, opt]))

