# Python script to run PingPerf app in Quarkus
# Liberty is not run in containers. The script should be run on the same machine as the app server
# Mongo and JMeter are ran in containers. Both docker and podman should work.

import datetime # for datetime.datetime.now()
import logging # https://www.machinelearningplus.com/python/python-logging-guide/
import math
import os # for environment variables
import re # for regular expressions
import shlex, subprocess
import signal
import sys # for number of arguments
import time # for sleep


# Set level to level=logging.DEBUG, level=logging.INFO or level=WARNING reduced level of verbosity
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s :: %(levelname)s :: %(message)s',)

docker = "podman" # Select between docker and podman
netOpts = "--network=slirp4netns" if docker == "podman" else "" # for podman we need to use slirp4netns if running as root. This will be added to Liberty. mongo uses host network.

################### Benchmark configuration #################
doColdRun          = False # when True we clear the SCC before the first run. Set it to False for embedded SCC
doOnlyColdRuns     = False # when True we run only the cold runs (doColdRun flag is ignored)
AppServerHost      = "localhost" # the host where the app server is running from the point of view of the JMeter/wrk machine
AppServerPort      = 9090
AppServerLocation  = "/team/mpirvu/QuarkusGary/quarkus-exp/pingPerf"
applicationName    = "target/quarkus-app/quarkus-run.jar"
QuarkusOpts        = f"-Dquarkus.thread-pool.core-threads=2 -Dquarkus.thread-pool.max-threads=2 -Dquarkus.http.port={AppServerPort} -Dquarkus.http.host=0.0.0.0 -Djava.util.logging.manager=org.jboss.logmanager.LogManager" # additional options for Quarkus
AppServerAffinity  = "numactl --physcpubind=2,3"
logFile            = f"{AppServerLocation}/quarkus.log"
startupWaitTime    = 5 # seconds to wait before checking to see if AppServer is up

memAnalysis = False # Collect javacores and smaps for memory analysis
dirForMemAnalysisFiles = "/tmp"
extraArgsForMemAnalysis = f" -Dcom.ibm.dbgmalloc=true -Xdump:none -Xdump:system:events=user,file={dirForMemAnalysisFiles}/core.%pid.%seq.dmp -Xdump:java:events=user,file={dirForMemAnalysisFiles}/javacore.%pid.%seq.txt"
# For collection of profiles, -Xjit:perfTool may need to be added to the OpenJ9 command line
# Also you must ensure that "perf" is installed and the user has the rights to collect such a profile   sudo sh -c " echo 0 >  /proc/sys/kernel/perf_event_paranoid"
collectPerfProfileForJIT = False # Collect perf profile of the "main" compilation thread
collectPerfProfileForJVM = False  # Collect perf profile for the entire JVM
perfProfileOutput = "/tmp/perf.data"
perfCmd= f"perf record -e cycles -c 200000"
perfDuration = 300 # seconds


############### SCC configuration ###########################
sccDir  = "/tmp" # Location of the shared class cache
sccDestroyParams = f"-Xshareclasses:cacheDir={sccDir},destroyall"

############### Database configuration #########
#dbMachine          = "localhost"
#dbUsername         = "" # To connect to mongoMachine remotely; leave empty to connect without ssh
#dbImage            = "localhost/mongo-acmeair-ee8:5.0.15"
#dbContainerName    = "mongodb"
#startDbScript      = f"{docker} run --rm -d --name {dbContainerName} --network=host {dbImage} --nojournal"

############### wrk CONFIG ###############
wrkMachine       = "localhost"
wrkUsername      = "" # To connect to JMeter machine; leave empty to connect without ssh
wrkExecutable    = "/team/mpirvu/wrk/wrk"
wrkAffinity      = "numactl --physcpubind 16-31"

################ Load CONFIG ###############
numRepetitionsOneClient = 0
numRepetitions50Clients = 2
durationOfOneClient     = 60 # seconds
durationOfOneRepetition = 180 # seconds
numClients              = 30
delayBetweenRepetitions = 10
numMeasurementTrials    = 1 # Last N trials are used in computation of throughput


# ENV VARS to use for all runs
TR_Options=""

jvmOptions = [
    #"-Xmx128m -Xshareclasses:cacheDir=/tmp,name=quarkus-scc -Xscmx80m"
    "-Xmx512m"
]

jdks = [
    #"/team/mpirvu/sdks/OpenJ9-JDK17-x86-64_linux-20241014-232255",
    "/team/mpirvu/sdks/OpenJDK17U-jre_x64_linux_hotspot_17.0.11_9",
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


def meanLastValues(myList, numLastValues):
    assert numLastValues > 0
    if numLastValues > len(myList):
        numLastValues = len(myList)
    return nanmean(myList[-numLastValues:])


def getJavaProcesses():
    cmd = "ps -eo pid,cmd --no-headers"
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    pattern = re.compile("^\s*(\d+)\s+(\S+)")
    for line in lines:
        m = pattern.match(line)
        if m:
            pid = m.group(1)
            cmd = m.group(2)
            if "/bin/java" in cmd:
                print("WARNING: Java process still running: {pid} {cmd}".format(pid=pid,cmd=cmd))


# Given a PID, return RSS and peakRSS in MB for the process
def getRss(pid):
    _scale = {'kB': 1024, 'mB': 1024*1024, 'KB': 1024, 'MB': 1024*1024}
    # get pseudo file  /proc/<pid>/status
    filename = f"/proc/{pid}/status"
    cmd = f"cat {filename}"
    try:
        s = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
        #lines = s.splitlines()
    except IOError as ioe:
        logging.warning("Cannot open {filename}: {msg}".format(filename=filename,msg=str(ioe)))
        return [math.nan, math.nan]  # wrong pid?
    i =  s.index("VmRSS:") # Find the position of the substring
    # Take everything from this position till the very end
    # Then split the string 3 times, taking first 3 "words" and putting them into a list
    tokens = s[i:].split(None, 3)
    if len(tokens) < 3:
        return [0, 0]  # invalid format
    rss = float(tokens[1]) * _scale[tokens[2]] / 1048576.0 # convert value to bytes and then to MB

    # repeat for peak RSS
    i =  s.index("VmHWM:")
    tokens = s[i:].split(None, 3)
    if len(tokens) < 3:
        return [0, 0]  # invalid format
    peakRss = float(tokens[1]) * _scale[tokens[2]] / 1048576.0 # convert value to bytes and then to MB
    return [rss, peakRss]


def collectJavacore(javaPID):
    # Produce javacore file
    cmd = f"kill -3 {javaPID}" # Send SIGQUIT to the Java process
    logging.info("Generating javacore by sending SIGQUIT with {cmd}".format(cmd=cmd))
    subprocess.run(shlex.split(cmd), universal_newlines=True)


def collectSmaps(javaPID):
    # Get smaps file
    cmd = f"cp /proc/{javaPID}/smaps {dirForMemAnalysisFiles}/smaps.{javaPID}"
    try:
        subprocess.run(shlex.split(cmd), universal_newlines=True)
    except:
        logging.error("Cannot get smaps file for javaPID {javaPID}".format(javaPID=javaPID))

def collectJavacoreAndSmaps(javaPID):
    collectJavacore(javaPID)
    collectSmaps(javaPID)


"""
Find the main compilation thread ID of an OpenJ9 JVM process
The JVM can have multiple compilation threads; in this case we will
return the TID for the compilation thread that used most of the CPU
"""
def findMainCompThreadID(javaPID):
    logging.debug("Determine the threads of PID={pid}".format(pid=javaPID))
    # Exec an external command to get the threads of the Java process
    cmd = f"ps -T -p {javaPID}"
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    """
       PID    SPID TTY          TIME CMD
    157603  157603 pts/0    00:00:00 java
    157603  157624 pts/0    00:00:01 main
    157603  157625 pts/0    00:00:00 Signal Reporter
    157603  157626 pts/0    00:00:12 JIT Compilation
    157603  157627 pts/0    00:00:00 JIT Compilation
    157603  157635 pts/0    00:00:00 JIT IProfiler
    """
    lines = output.splitlines()
    # Verify that the header is as expected
    psHeaderPattern = re.compile('^\s*PID\s+SPID\s+TTY\s+TIME\s+CMD\s*$')
    if not psHeaderPattern.match(lines[0]):
        raise Exception("Unexpected output from ps command when determining thread IDs: {header}".format(header=lines[0]))
    psOutputPattern = re.compile('^\s*(\d+)\s+(\d+)\s+(\S+)\s+(\d\d):(\d\d):(\d\d)\s+JIT Compilation')
    compThreadId = None
    compCPU = 0
    # Skip the first line and search for the JIT Compilation TID with most CPU consumed
    for line in lines[1:]:
        m = psOutputPattern.match(line)
        if m:
            cpu = int(m.group(4)) * 3600 + int(m.group(5)) * 60 + int(m.group(6))
            if cpu > compCPU:
                compCPU = cpu
                compThreadId = m.group(2)
    return compThreadId

'''
Collect Linux perf profile for the main JIT compilation thread which is determined automatically from JVM process.
"main" JIT compilation thread is the JIT compilation thread that consumed the most amount of CPU.
'''
def collectJITPerfProfile(javaPID):
    compThreadTID = findMainCompThreadID(javaPID)
    if not compThreadTID:
        logging.error("Cannot find main compilation thread ID for PID={pid}".format(pid=javaPID))
        return
    # Get the JIT perf profile in the background
    perfProcess = None
    outputFile = f"{perfProfileOutput}.{javaPID}"
    cmd = f"{perfCmd} -o {outputFile} --tid {compThreadTID} -- sleep {perfDuration}"
    try:
        # Fork a process and run in background
        perfProcess = subprocess.Popen(shlex.split(cmd), universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # This process will run for the specified amount of time (perfDuration) and then end
    except subprocess.CalledProcessError as e:
        logging.error("CalledProcessError calling perf record: {e}".format(e=e))
        #output = str(e)
    except subprocess.SubprocessError as e:
        logging.error("SubprocessError calling perf record: {e}".format(e=e))
    logging.info("Collecting JIT perf profile in the background for TID={tid} with cmd={cmd}".format(tid=compThreadTID, cmd=cmd))
    return perfProcess

def collectJVMPerfProfile(javaPID):
    perfProcess = None
    outputFile = f"{perfProfileOutput}.{javaPID}"
    cmd = f"{perfCmd} -o {outputFile} --pid {javaPID} --delay=5000 -- sleep {perfDuration}"
    try:
        # Fork a process and run in background
        perfProcess = subprocess.Popen(shlex.split(cmd), universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # This process will run for the specified amount of time (perfDuration) and then end
    except subprocess.CalledProcessError as e:
        logging.error("CalledProcessError calling perf record: {e}".format(e=e))
        #output = str(e)
    except subprocess.SubprocessError as e:
        logging.error("SubprocessError calling perf record: {e}".format(e=e))
    logging.info("Collecting JVM perf profile in the background for PID={pid} with cmd={cmd}".format(pid=javaPID, cmd=cmd))
    return perfProcess

def clearSCC(jdk, sccDestroyParams):
    cmd = f"{jdk}/bin/java {sccDestroyParams}"
    logging.info("Clearing SCC with cmd: {cmd}".format(cmd=cmd))
    try:
        output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        # If the SCC does not exist, we get a non-zero return code
        output = e.output
    except subprocess.SubprocessError as e:
        logging.warning("SubprocessError clearing SCC: {e}".format(e=e))
        output = str(e)
    logging.info(output)
    # TODO: make sure the SCC does not exist anymore


def verifyAppserverStarted():
    logging.info("Verify app server started")
    #[5/3/23, 8:27:25:850 PDT] 0000002a com.ibm.ws.kernel.feature.internal.FeatureManager   A CWWKF0011I: The crudserver server is ready to run a smarter planet. The crudserver server started in 48.607 seconds.
    # Look for "server is ready to run a smarter planet" in messages.log
    errPattern = re.compile('.+\[ERROR')
    readyPattern = re.compile("pingperf .+ started in \d+\.\d+s. Listening on")
    for iter in range(20):
        try:
            with open(logFile) as f:
                for line in f:
                    print(line)
                    m = errPattern.search(line)
                    if m:
                        logging.warning("AppServer {applicationName} errored while starting:\n\t {line}").format(applicationName=applicationName,line=line)
                        return False
                    m1 = readyPattern.search(line)
                    if m1:
                        return True # True means success
        except IOError:
            print("Could not open file", logFile)
            return False
        logging.warning("sleeping 1 sec and trying again")
        time.sleep(1) # wait 1 sec and try again
    return False # False means failure


def killAppServerIfRunning(childProcess):
    if childProcess.poll() is None: # Still running
        logging.error("Killing server")
        childProcess.kill()
        childProcess.wait()


def startAppServer(jdk, jvmArgs):
    cmd = f"{AppServerAffinity} {jdk}/bin/java {QuarkusOpts} {jvmArgs} -jar {applicationName}"
    logging.info("Starting AppServer with command: {cmd}".format(cmd=cmd))
    myEnv = os.environ.copy()
    myEnv["JAVA_HOME"] = jdk
    myEnv["JVM_ARGS"] = jvmArgs
    myEnv["TR_PrintCompTime"] = "1"
    #myEnv["TR_PrintCompStats"] = "1"
    myEnv["TR_Options"] = TR_Options
    myEnv["QUARKUS_LOG_FILE_ENABLE"] = "true"
    myEnv["QUARKUS_LOG_FILE_FORMAT"] = "%d{yyyy-MM-dd HH:mm:ss,SSS} %-5p [%c{3.}] (%t) %s%e%n"
    # Also useful:  QUARKUS_LOG_FILE_PATH, otherwise use ./quarkus.log
    # Fork a process and run in background
    childProcess = subprocess.Popen(shlex.split(cmd), env=myEnv, universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logging.debug(f"Waiting for {startupWaitTime} sec for the AppServer to start")
    time.sleep(startupWaitTime)
    startOK = False
    if childProcess.poll() is None: # It's running
        logging.debug("AppServer started with pid {pid}".format(pid=childProcess.pid))
        # Verify that server started correctly
        startOK = verifyAppserverStarted()
        if not startOK:
            logging.error("AppServer did not start correctly")
            outs, errs = childProcess.communicate(timeout=15)
            print(outs)
            killAppServerIfRunning(childProcess)
            return None
        else:
            logging.debug("AppServer started OK")
    else:
        logging.error("AppServer did not start")
        return None
    return childProcess


def stopAppServer(childProcess):
    # Stop the AppServer
    logging.info("Stopping AppServer")
    childProcess.terminate()
    killAppServerIfRunning(childProcess)

'''
Extract the start-up timestamp from logFile and compute start-up time of AppServer.
Parameters: appServerStartTimeMs - time in ms when AppServer was started (only minutes, seconds and millisec are used)
'''
def getStartupTime(appServerStartTimeMs):
    # 2024-10-16 16:27:06,902 INFO  [io.quarkus] (main) pingperf 1.0 on JVM (powered by Quarkus 3.13.2) started in 2.090s. Listening on: http://0.0.0.0:9090
    readyPattern = re.compile('(\d+)-(\d+)-(\d+) (\d+):(\d+):(\d+),(\d+) .+ pingperf .+ started in \d+\.\d+s\. Listening on')
    try:
        with open(logFile) as f:
            for line in f:
                m = readyPattern.match(line)
                if m:
                    # Ignore the hour to avoid time zone issues
                    endTime = (int(m.group(5)) * 60 + int(m.group(6)))*1000 + int(m.group(7))
                    if endTime < appServerStartTimeMs:
                        endTime = endTime + 3600*1000 # add one hour
                    return float(endTime - appServerStartTimeMs)
    except FileNotFoundError:
        logging.error("Cannot find log file: {logFile}".format(logFile=logFile))
    except IOError as e:
        print("I/O error({num}): {msg}".format(num=e.errno, msg=e.strerror))
    logging.error("Cannot read start-up time. AppServer may not have started correctly")
    return math.nan


def getCompCPU(childProcess):
    logging.info("Getting CPU")
    outs, errs = childProcess.communicate()
    print(errs)
    lines = errs.splitlines()
    threadTime = 0.0
    compTimePattern = re.compile("^Time spent in compilation thread =(\d+) ms")
    for line in lines:
        print(line)
        m = compTimePattern.match(line)
        if m:
            threadTime += float(m.group(1))
    return threadTime/1000.0 if threadTime > 0 else math.nan


def startJITServer(jdk):
    jitServerCmd = f"{jdk}/bin/jitserver"
    logging.info("Starting JITServer with command: {jitServerCmd}".format(jitServerCmd=jitServerCmd))
    myEnv = os.environ.copy()
    # Fork a process and run in background
    childProcess = subprocess.Popen(shlex.split(jitServerCmd), env=myEnv, universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(1) # Give server some time to start
    # Check that server started correctly
    if childProcess.poll() is None: # No return code means the process is still running
        return childProcess
    else:
        logging.error("JITServer did not start correctly")
        return None

def stopJITServer(jitServerProcess):
    logging.info("Stopping JITServer...")
    if jitServerProcess.poll() is None: # No return code means the process is still running
        jitServerProcess.terminate()
        try:
            jitServerProcess.communicate(timeout=15) # Using communicate instead of wait to avoid deadlock
        except subprocess.TimeoutExpired:
            logging.info("Stopping JITServer forcefully with sigkill")
            killAppServerIfRunning(jitServerProcess)

def applyLoad(duration, numClients):
    # Run jmeter remotely
    remoteCmd = f"{wrkAffinity} {wrkExecutable} -t{numClients} -c{numClients} -d{duration} http://{AppServerHost}:{AppServerPort}/ping/greeting"
    cmd = f"ssh {wrkUsername}@{wrkMachine} \"{remoteCmd}\"" if wrkUsername else remoteCmd
    logging.info("Apply load: {cmd}".format(cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    return output

def getThroughput(output):
    logging.debug("Getting throughput info...")
    lines = output.splitlines()

#Running 6m test @ http://9.42.142.176:9090/ping/greeting
#  25 threads and 25 connections
#  Thread Stats   Avg      Stdev     Max   +/- Stdev
#    Latency     2.55ms    3.79ms  24.81ms   83.61%
#    Req/Sec     1.22k   263.42     3.51k    73.94%
#  10899233 requests in 6.00m, 0.93GB read
#Requests/sec:  30267.47
#Transfer/sec:      2.66MB
    pattern = re.compile('Requests/sec:\s+(\d+\.\d+)')
    thr = math.nan
    for line in lines:
        m = pattern.match(line)
        if m:
            thr = float(m.group(1))
            break
    return thr

def runPhase(duration, numClients):
    logging.debug("Sleeping for {n} sec before applying load".format(n=delayBetweenRepetitions))
    time.sleep(delayBetweenRepetitions)

    output = applyLoad(duration, numClients)

    # Read throughput
    thr = getThroughput(output)

    if logging.root.level <= logging.DEBUG:
        print("Throughput={thr:7.1f}".format(thr=thr))

    return thr


def runBenchmarkOnce(jdk, jvmArgs, doMemAnalysis):
    # must remove the logFile before starting the AppServer
    if os.path.exists(logFile):
        os.remove(logFile)

    # Will apply load in small bursts
    maxPulses = numRepetitionsOneClient + numRepetitions50Clients
    thrResults = [math.nan for i in range(maxPulses)] # np.full((maxPulses), fill_value=np.nan, dtype=np.float)
    rss, peakRss, cpu, startupTime = math.nan, math.nan, math.nan, math.nan
    peakThroughput = math.nan

    crtTime = datetime.datetime.now()
    startTimeMs = (crtTime.minute * 60 + crtTime.second)*1000 + crtTime.microsecond//1000

    childProcess = startAppServer(jdk=jdk, jvmArgs=jvmArgs)
    if childProcess is None: # Failed to start properly
        return thrResults, peakThroughput, rss, peakRss, cpu, startupTime

    # Compute AppServer start-up time
    startupTime = getStartupTime(startTimeMs)

    if collectPerfProfileForJIT:
        if childProcess.poll() is None: # Still running:
            collectJITPerfProfile(childProcess.pid)
        else:
            logging.error("Failed to start JIT perf profiling because Java process has terminated")

    peakThroughput = 0
    for pulse in range(maxPulses):
        # Determine run characteristics
        if pulse >= numRepetitionsOneClient:
            cli = numClients
            duration = durationOfOneRepetition
        else:
            cli = 1
            duration = durationOfOneClient
        # If enabled, start the JVM profiling thread in the background
        if collectPerfProfileForJVM and pulse == maxPulses-1:
            if childProcess.poll() is None: # Still running:
                collectJVMPerfProfile(childProcess.pid)
            else:
                logging.error("Failed to start JVM perf profiling because Java process has terminated")

        thrResults[pulse] = runPhase(duration, cli)

        logging.info("Throughput={thr}".format(thr=thrResults[pulse]))

    # Collect RSS at end of run
    if childProcess.poll() is None: # Still running
        rss, peakRss = getRss(pid=childProcess.pid)

        if doMemAnalysis:
            logging.info("Generating javacore, core and smaps for process {pid}".format(pid=childProcess.pid))
            collectJavacoreAndSmaps(childProcess.pid)
            time.sleep(20)

    # Stop the AppServer
    stopAppServer(childProcess)
    time.sleep(1)

    # Must compute the CPU after stopping the AppServer
    cpu = getCompCPU(childProcess)

    # return throughput as an array of throughput values for each burst and also the RSS, PeakRSS and CPU
    return thrResults, rss, peakRss, cpu, startupTime


def runBenchmarkIteratively(numIter, jdk, javaOpts):
    # Initialize stats; 2D array of throughput results
    numPulses = numRepetitionsOneClient + numRepetitions50Clients
    thrResults = [] # List of lists
    rssResults = [] # Just a list
    cpuResults = []
    startupResults = []

    # clear SCC if needed (by destroying the SCC volume)
    if doColdRun or doOnlyColdRuns:
        clearSCC(jdk, sccDestroyParams)

    # Start JITServer if needed
    jitServerHandle = startJITServer(jdk) if "-XX:+UseJITServer" in javaOpts else None

    for iter in range(numIter):
        # if memAnalysis is True, add the options required for memory analysis, but only for the last iteration
        doMemAnalysis = memAnalysis and iter == numIter - 1
        if doMemAnalysis:
            javaOpts = javaOpts + extraArgsForMemAnalysis
        thrList, rss, peakRss, cpu, startupTime = runBenchmarkOnce(jdk, javaOpts, doMemAnalysis)
        lastThr = meanLastValues(thrList, numMeasurementTrials) # average for last N pulses
        print(f"Run {iter}: Thr={lastThr:6.1f} RSS={rss:6.1f} MB  PeakRSS={peakRss:6.1f} MB  CPU={cpu:4.1f} sec  Startup={startupTime:5.0f}".
              format(lastThr=lastThr, rss=rss, peakRss=peakRss, cpu=cpu, startupTime=startupTime), flush=True)
        thrResults.append(thrList) # copy all the pulses
        rssResults.append(rss)
        cpuResults.append(cpu)
        startupResults.append(startupTime)

    startIter = 0 if (doOnlyColdRuns or not doColdRun) else 1

    # print stats
    print(f"\nResults for jdk: {jdk} and opts: {javaOpts}")
    if startIter > 0:
        print("First run is a cold run and is not included in the stats")
    thrAvgResults = [math.nan for i in range(numIter)] # np.full((numIter), fill_value=np.nan, dtype=np.float)
    for iter in range(numIter):
        print("Run", iter, end="")
        for pulse in range(numPulses):
            print("\t{thr:7.1f}".format(thr=thrResults[iter][pulse]), end="")
        thrAvgResults[iter] = meanLastValues(thrResults[iter], numMeasurementTrials) #np.nanmean(thrResults[iter][-numMeasurementTrials:])
        print("\tAvg={thr:7.1f}  RSS={rss:7.0f} MB  CompCPU={cpu:5.1f} sec  Startup={startup:5.0f} ms".
              format(thr=thrAvgResults[iter], rss=rssResults[iter], cpu=cpuResults[iter], startup=startupResults[iter]))

    verticalAverages = []  #verticalAverages = np.nanmean(thrResults, axis=0)
    for pulse in range(numPulses):
        total = 0
        numValidEntries = 0
        for iter in range(startIter, numIter):
            if not math.isnan(thrResults[iter][pulse]):
                total += thrResults[iter][pulse]
                numValidEntries += 1
        verticalAverages.append(total/numValidEntries if numValidEntries > 0 else math.nan)

    print("Avg:", end="")
    for pulse in range(numPulses):
        print("\t{thr:7.1f}".format(thr=verticalAverages[pulse]), end="")
    print("\tThr={avgThr:7.1f}  RSS={rss:7.0f} MB  CompCPU={cpu:5.1f} sec  Startup={startup:5.0f} ms".
          format(avgThr=nanmean(thrAvgResults[startIter:]), rss=nanmean(rssResults[startIter:]), cpu=nanmean(cpuResults[startIter:]), startup=nanmean(startupResults[startIter:])))
    # Throughput stats
    avg, stdDev, min, max, ci95, numSamples = computeStats(thrAvgResults[startIter:])
    print("Throughput stats: Avg={avg:7.1f}  StdDev={stdDev:7.1f}  Min={min:7.1f}  Max={max:7.1f}  Max/Min={maxmin:4.0f}% CI95={ci95:7.1f}% numSamples={numSamples:3d}".
                        format(avg=avg, stdDev=stdDev, min=min, max=max, maxmin=(max-min)*100.0/min, ci95=ci95, numSamples=numSamples))
    # Footprint stats
    avg, stdDev, min, max, ci95, numSamples = computeStats(rssResults[startIter:])
    print("Footprint stats:  Avg={avg:7.1f}  StdDev={stdDev:7.1f}  Min={min:7.1f}  Max={max:7.1f}  Max/Min={maxmin:4.0f}% CI95={ci95:7.1f}% numSamples={numSamples:3d}".
                        format(avg=avg, stdDev=stdDev, min=min, max=max, maxmin=(max-min)*100.0/min, ci95=ci95, numSamples=numSamples))
    # CompCPU stats
    avg, stdDev, min, max, ci95, numSamples = computeStats(cpuResults[startIter:])
    print("Comp CPU stats:   Avg={avg:7.1f}  StdDev={stdDev:7.1f}  Min={min:7.1f}  Max={max:7.1f}  Max/Min={maxmin:4.0f}% CI95={ci95:7.1f}% numSamples={numSamples:3d}".
                        format(avg=avg, stdDev=stdDev, min=min, max=max, maxmin=(max-min)*100.0/min, ci95=ci95, numSamples=numSamples))
    # Start-up stats
    avg, stdDev, min, max, ci95, numSamples = computeStats(startupResults[startIter:])
    print("StartupTime stats:Avg={avg:7.1f}  StdDev={stdDev:7.1f}  Min={min:7.1f}  Max={max:7.1f}  Max/Min={maxmin:4.0f}% CI95={ci95:7.1f}% numSamples={numSamples:3d}".
                        format(avg=avg, stdDev=stdDev, min=min, max=max, maxmin=(max-min)*100.0/min, ci95=ci95, numSamples=numSamples))

    if jitServerHandle:
        stopJITServer(jitServerHandle)

def cleanup():
    getJavaProcesses()


############################ MAIN ##################################
if  len(sys.argv) < 2:
    print ("Program must have an argument: the number of iterations\n")
    sys.exit(-1)

# Clean-up from a previous possible bad run
cleanup()

if doOnlyColdRuns:
    print("Will do a cold run before each tun")
elif doColdRun:
    print("Will do a cold run before each set")

for jvmOpts in jvmOptions:
    for jdk in jdks:
        runBenchmarkIteratively(numIter=int(sys.argv[1]), jdk=jdk, javaOpts=jvmOpts)


