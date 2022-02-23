# Python script to run DaCapo benchmarks and collect performance statistics
# Usage:  python3 runDaCapo.py
# Note: dacapo-9.12-MR1-bach.jar  msut be present in current directory

# The script can be customized as follows:
# 1. Specify which benchmarks to run ==> change "benchmark" list below
# 2. Specify the number iterations for each benchmark (to warm up the JVM) ==> change "benchmarkOpts" below
# 3. Specify the number of runs for each benchmark ==> change "numIter" below
# 4. Specify JDK options ==> change "jvmOption" list below
# 5. Specify JDK to use ==> change "jdks" list below

import re # for regular expressions
import sys # for accessing parameters and exit
import shlex, subprocess
import logging
import numpy as np


numIter = 10 # number of iterations to use for each benchmark in each configuration

#level=logging.DEBUG,
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s :: %(levelname)s :: (%(threadName)-6s) :: %(message)s',
                    )

benchmarkOpts = "--iterations 20 -s default" # not all benchmarks can use size large. Better to use "default"

# List of benchmarks to run
# avrora batik eclipse fop h2 jython luindex lusearch lusearch-fix pmd sunflow tomcat tradebeans tradesoap xalan
benchmarks = [
    #"avrora",
    #"batik", Not working at all with OpenJDK
    #"eclipse", # only working with Java8
    #"fop",
    #"h2",
    #"jython",
    #"luindex",
    #"lusearch-fix",
    #"pmd",
    #"sunflow",
    #"tomcat", # does not work at all
    #"tradebeans", # does not work at all
    #"tradesoap", # does not work at all
    "xalan"
]

# List of JVM options to try
jvmOptions = [
    #"-Xmx1G",
    #"-XX:-EnableHCR -Xmx1G",
    "-XX:-OSRSafePoint -Xmx1G",
    #"-Xaggressive -Xmx1G",
    #"-Xms1G -Xmx1G",
    #"-Xtune:throughput -Xmx1G",
    #"-Xtune:throughput -Xmx1G -Xjit:acceptHugeMethods",
    #"-Xtune:throughput -Xmx1G -Xjit:inlineVeryLargeCompiledMethods",
    #"-Xtune:throughput -Xmx1G -Xjit:bigCalleeFreqCutoffAtHot=0",
    #"-Xtune:throughput -Xmx1G -Xjit:bigCalleeThresholdForColdCallsAtHot=600",
]

jdks = [
    #"/home/mpirvu/FullJava11/openj9-openjdk-jdk11/build/linux-x86_64-normal-server-release/images/jdk",
    #"/home/mpirvu/FullVM/openj9-openjdk-jdk8/build/linux-x86_64-normal-server-release/images/j2re-image",
    "/home/mpirvu/sdks/OpenJ9-JDK8-x86-64_linux-20220126-021513",
]

'''
Returns the execution time in milliseconds as a float
or Nan if the experiment fails
'''
def runBenchmarkOnce(benchmarkName, jvm, jvmOpts):
    cmd = f"{jvm}/bin/java {jvmOpts} -jar dacapo-9.12-MR1-bach.jar {benchmarkOpts} {benchmarkName}"
    logging.info("Starting: {cmd}".format(cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.STDOUT)
    # Parse the output and look for "PASSED in nnnn msec ====
    lines = output.splitlines()
    pattern = re.compile('^===== DaCapo .+ PASSED in (\d+) msec ====')
    for line in lines:
        m = pattern.match(line)
        if m:
            print(line)
            execTime = float(m.group(1))
            print(execTime)
            return execTime
    return np.nan
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

# multi-dimensional array of results
results = np.full((len(benchmarks), len(jdks), len(jvmOptions), numIter), fill_value=np.nan, dtype=np.float)
for bench in range(len(benchmarks)):
    for jdk in range(len(jdks)):
        for opt in range(len(jvmOptions)):
            for i in range(numIter):
                execTime = runBenchmarkOnce(benchmarks[bench], jdks[jdk], jvmOptions[opt])
                results[bench, jdk, opt, i] = execTime

# Stats ignoring Nan which are due to failed experiments
mean = np.nanmean(results, axis=3)
std  = np.nanstd(results, axis=3)
min  = np.nanmin(results, axis=3)
max  = np.nanmax(results, axis=3)

# Count valid experiments excluding Nan values
numValidExperiments = np.count_nonzero(~np.isnan(results), axis=3)
print(numValidExperiments)

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

