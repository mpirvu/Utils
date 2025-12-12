"""
Python script to run Adobe Package Manager benchmark
# Prerequisites
1. **Python 3.7+** installed on your system
2. **Playwright** library (pip install playwright)
3. **Chromium browser library** (playwright install chromium)
"""

import datetime # for datetime.datetime.now()
import logging # https://www.machinelearningplus.com/python/python-logging-guide/
import math
import os # for environment variables
import re # for regular expressions
import shlex, subprocess
import statistics
import sys # for number of arguments
import time # for sleep
from collections import deque

from playwright.sync_api import sync_playwright


# Set level to level=logging.DEBUG, level=logging.INFO or level=WARNING reduced level of verbosity
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s :: %(levelname)s :: %(message)s',)


################### Benchmark configuration #################
doColdRun          = False # when True we clear the SCC before the first run. Set it to False for embedded SCC
doOnlyColdRuns     = False # when True we run only the cold runs (doColdRun flag is ignored)
AppServerHost      = "localhost" # the host where the app server is running from the point of view of the JMeter machine
AppServerPort      = 9080
AppServerLocation  = "/team/mpirvu/JackRabbit/wlp"
applicationName    = "defaultServer"
AppServerAffinity  = "numactl --physcpubind=0-7 --membind=0"
applicationLocation= f"{AppServerLocation}/usr/servers/{applicationName}"
logFile            = f"{applicationLocation}/logs/messages.log"
appServerStartCmd  = f"{AppServerAffinity} {AppServerLocation}/bin/server run {applicationName}"
appServerStopCmd   = f"{AppServerLocation}/bin/server stop {applicationName}"
startupWaitTime    = 180 # seconds to wait before checking to see if AppServer is up
URL = f"http://{AppServerHost}:{AppServerPort}/crx/packmgr/index.jsp"
USERNAME = "admin"
PASSWD = "admin"
PACKAGE_PATH = r"/team/mpirvu/JackRabbit/sample_package_performance.zip" # Package to install
# Debug options
ENABLE_SCREENSHOTS = False  # Set to False to disable all screenshots

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
sccDir  = f"/tmp" # Location of the shared class cache
sccDestroyParams = f"-Xshareclasses:cacheDir={sccDir},destroyall"


################ Load CONFIG ###############
numRepetitionsOneClient = 0
numRepetitions50Clients = 2
delayBetweenRepetitions = 10
numMeasurementTrials    = 1 # Last N trials are used in computation of throughput


################# JITServer CONFIG ###############
# JITServer is automatically launched if the JVM option include -XX:+UseJITServer
JITServerOpts="-XX:+JITServerLogConnections"
printJITServerOutput = False
JITServerOutputFile = "/tmp/jitserver.out" # This is where the stdout of the JITServer process is written
JITServerErrFile = "/tmp/jitserver.err"


############################# END CONFIG ####################################


# ENV VARS to use for all runs
TR_Options=""
TR_OptionsAOT=""


jvmOptions = [
        #f"-Xmx3G -Xms3G",
        f"-Xmx3G -Xms3G -Xshareclasses:none",
        #f"-Xmx3G -Xms3G -Xshareclasses:none -Xjit:dontDowngradeToCold",
        #f"-Xmx3G -Xms3G -Xshareclasses:none -Xjit:dontDowngradeToCold,disableSelectiveNoServer",
        #f"-Xmx3G -Xms3G -Xshareclasses:none -Xjit:disableDynamicLoopTransfer,disableInlinerFanIn,dontDowngradeToCold,disableSelectiveNoServer",
        #f"-Xmx3G -Xms3G -Xshareclasses:none -Xtune:throughput",
]

jdks = [
    "/team/mpirvu/sdks/OpenJ9-JDK17-x86-64_linux-20251129-000857",
]

def count_not_nan(myList):
    total = 0
    for elem in myList:
        if not math.isnan(elem):
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

'''
Given an input list of values, eliminate all NaNs and return a new list
'''
def eliminateNans(myList):
    return [value for value in myList if not math.isnan(value)]


'''
Receives an array reference with results.
We compute the lower and and upper quartile, then compute the interquartile
range (IQR=Q3-Q1) and the lower (Q1 - 1.5*IQR) and upper (Q3 + 1.5*IQR) fences.
The two fences are returned to the caller in an array with 2 elements.
Needs at least 4 data points
'''
def computeOutlierFences(myList):
    Q1, Q2, Q3 = statistics.quantiles(myList, n = 4, method='inclusive')
    # Compute the interquartile range
    IQR = Q3 - Q1
    lowerFence = Q1 - 3 * IQR
    upperFence  = Q3 + 3 * IQR
    return (lowerFence, upperFence)

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
            return 2.042 - 0.0014 * (degreeOfFreedom - 30)
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


def computeStats(myList, eliminateOutliers=False):
    newList = eliminateNans(myList)
    if len(newList) < 1:
        return math.nan, math.nan, math.nan, math.nan, math.nan, 0, []
    # Eliminate outliers
    outlierList = []
    goodValuesList = []
    if eliminateOutliers and len(newList) > 3:
        lowerFence, upperFence = computeOutlierFences(newList)
        for val in newList:
            if val < lowerFence or val > upperFence:
                outlierList.append(val)
            else:
                goodValuesList.append(val)
    else:
        goodValuesList = newList
    avg = statistics.fmean(goodValuesList)
    stdDev = statistics.stdev(goodValuesList) if len(myList) > 1 else 0
    minVal = min(goodValuesList)
    maxVal = max(goodValuesList)
    numSamples = len(goodValuesList)
    tvalue = tDistributionValue95(numSamples-1)
    marginOfError = tvalue * stdDev / math.sqrt(numSamples)
    ci95 = 100.0*marginOfError/avg

    return avg, stdDev, minVal, maxVal, ci95, numSamples, outlierList


def meanLastValues(myList, numLastValues):
    assert numLastValues > 0
    if numLastValues > len(myList):
        numLastValues = len(myList)
    return nanmean(myList[-numLastValues:])


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
    cmd = f"{perfCmd} -o {outputFile} --pid {javaPID} --delay=10000 -- sleep {perfDuration}"
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
    #[5/3/23, 8:27:25:850 PDT] 0000002a com.ibm.ws.kernel.feature.internal.FeatureManager   A CWWKF0011I: The crudserver server is ready to run a smarter planet. The crudserver server started in 48.607 seconds.
    # Look for "server is ready to run a smarter planet" in messages.log
    errPattern = re.compile('.+\[ERROR')
    readyPattern = re.compile(".+is ready to run a smarter planet")
    for iter in range(20):
        try:
            with open(logFile) as f:
                for line in f:
                    print(line)
                    m = errPattern.match(line)
                    if m:
                        logging.warning("AppServer {applicationName} errored while starting:\n\t {line}").format(applicationName=applicationName,line=line)
                        return False
                    m1 = readyPattern.match(line)
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
    logging.info("Starting AppServer with command: {appServerStartCmd}".format(appServerStartCmd=appServerStartCmd))
    myEnv = os.environ.copy()
    myEnv["JAVA_HOME"] = jdk
    myEnv["JVM_ARGS"] = jvmArgs
    myEnv["TR_PrintCompTime"] = "1"
    #myEnv["TR_PrintCompStats"] = "1"
    myEnv["TR_Options"] = TR_Options
    myEnv["TR_OptionsAOT"] = TR_OptionsAOT

    # Fork a process and run in background
    childProcess = subprocess.Popen(shlex.split(appServerStartCmd), env=myEnv, universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
    if childProcess.poll() is None: # Still running
        output = subprocess.check_output(shlex.split(appServerStopCmd))
        logging.debug(output)
        time.sleep(1) # Allow some quiesce time
    else:
        logging.error("AppServer is not running")
    killAppServerIfRunning(childProcess)
    # Delete the log files
    cmd = f"rm -rf {AppServerLocation}/usr/servers/{applicationName}/crx-quickstart"
    logging.info("Deleting crx-quickstart files: {cmd}".format(cmd=cmd))
    try:
        output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.STDOUT)
        logging.info(f"Successfully deleted crx-quickstart files")
    except subprocess.CalledProcessError as e:
        logging.warning(f"Failed to delete crx-quickstart files (exit code {e.returncode}): {e.output}")
'''
Extract the start-up timestamp from logFile and compute start-up time of AppServer.
Parameters: appServerStartTimeMs - time in ms when AppServer was started (only minutes, seconds and millisec are used)
'''
def getStartupTime(appServerStartTimeMs):
    # [10/29/20, 23:18:49:468 UTC] 00000024 com.ibm.ws.kernel.feature.internal.FeatureManager            A CWWKF0011I: The defaultServer server is ready to run a smarter planet. The defaultServer server started in 2.885 seconds.
    readyPattern = re.compile('\[(.+)\] .+is ready to run a smarter planet')
    dateTimePattern = re.compile("(\d+)\/(\d+)\/(\d+),? (\d+):(\d+):(\d+):(\d+) (.+)") # [10/29/20, 17:53:03:894 EDT]
    try:
        with open(logFile) as f:
            for line in f:
                m = readyPattern.match(line)
                if m:
                    timestamp = m.group(1)
                    m1 = dateTimePattern.match(timestamp)
                    if m1:
                        # Ignore the hour to avoid time zone issues
                        endTime = (int(m1.group(5)) * 60 + int(m1.group(6)))*1000 + int(m1.group(7))
                        if endTime < appServerStartTimeMs:
                            endTime = endTime + 3600*1000 # add one hour
                        return float(endTime - appServerStartTimeMs)
                    else:
                        logging.error("AppServer timestamp is in the wrong format: {timestamp}".format(timestamp=timestamp))
                        return math.nan
    except FileNotFoundError:
        logging.error("Cannot find log file: {logFile}".format(logFile=logFile))
    except IOError as e:
        print("I/O error({num}): {msg}".format(num=e.errno, msg=e.strerror))
    logging.error("Cannot read start-up time. AppServer may not have started correctly")
    return math.nan


def getCompCPU(childProcess):
    outs, errs = childProcess.communicate()
    lines = errs.splitlines()
    threadTime = 0.0
    compTimePattern = re.compile("^Time spent in compilation thread =(\d+) ms")
    for line in lines:
        #print(line)
        m = compTimePattern.match(line)
        if m:
            threadTime += float(m.group(1))
    return threadTime/1000.0 if threadTime > 0 else math.nan


def startJITServer(jdk):
    jitServerCmd = f"{jdk}/bin/jitserver {JITServerOpts}"
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
    stdout = ""
    stderr = ""
    if jitServerProcess.poll() is None: # No return code means the process is still running
        jitServerProcess.terminate()
        try:
            stdout, stderr = jitServerProcess.communicate(timeout=15) # Using communicate instead of wait to avoid deadlock

        except subprocess.TimeoutExpired:
            logging.info("Stopping JITServer forcefully with sigkill")
            killAppServerIfRunning(jitServerProcess)
    else: # The JITServer process is not running
        stdout, stderr = jitServerProcess.communicate(timeout=15)
    if printJITServerOutput:
        # Write the stdout string to a file called JITServerOutputFile
        with open(JITServerOutputFile, "w") as stdoutFile:
            stdoutFile.write(stdout)
        # Write the stderr string to a file called JITServerErrFile
        with open(JITServerErrFile, "w") as stderrFile:
            stderrFile.write(stderr)


def automate_aem_package_workflow_debug():


    installation_time = 0

    with sync_playwright() as p:
        # Launch browser in HEADED mode with slow motion for debugging
        print("Launching browser in DEBUG mode...")
        browser = p.chromium.launch(
            headless=True,      # FALSE = visible browser window
            slow_mo=1000,        # 1 second delay between actions
            devtools=False        # Opens DevTools automatically (no)
        )

        # Create context with viewport size
        context = browser.new_context(
            viewport={'width': 1280, 'height': 1024},
            #record_video_dir='./videos/'  # Records video of the session
        )

        page = context.new_page()

        # Enable console log capture
        page.on("console", lambda msg: print(f"[BROWSER CONSOLE] {msg.type}: {msg.text}"))

        # Enable request/response logging
        page.on("request", lambda request: print(f"[REQUEST] {request.method} {request.url}"))
        page.on("response", lambda response: print(f"[RESPONSE] {response.status} {response.url}"))

        try:
            print("\n" + "="*80)
            print("Step 1: Navigating to AEM Package Manager...")
            print("="*80)
            # Navigate and check for errors
            response = page.goto(URL, wait_until='domcontentloaded', timeout=30000)
            status_code = response.status if response else None
            print(f"Response status: {status_code}")
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step1_initial_load.png')
                print("✓ Screenshot saved: debug_step1_initial_load.png")

            # Check if we got an error status code
            if status_code and status_code >= 400:
                print(f"⚠️  Received error status {status_code}, will retry with refresh...")
                needs_refresh = True
            else:
                print(f"✓ Page loaded successfully (status {status_code})")
                needs_refresh = False

            time.sleep(2)

            print("\n" + "="*80)
            print("Step 2: Checking if refresh is needed...")
            print("="*80)

            if needs_refresh:
                print(f"Refreshing page due to previous error (status {status_code})...")
                time.sleep(5)
                #response = page.reload(wait_until='domcontentloaded', timeout=30000)
                response = page.goto(URL, wait_until='domcontentloaded', timeout=30000)
                new_status = response.status if response else None
                print(f"After refresh - Response status: {new_status}")

                if new_status and new_status >= 400:
                    print(f"✗ Still receiving error status {new_status} after refresh")
                    page.screenshot(path='debug_step2_error_after_refresh.png')
                    raise Exception(f"Page returned error status {new_status} even after refresh")
                else:
                    print(f"✓ Page loaded successfully after refresh (status {new_status})")
            else:
                print("✓ No refresh needed - page loaded successfully on first attempt")

            time.sleep(2)

            print("\n" + "="*80)
            print("Step 3: Logging in with credentials...")
            print("="*80)
            # Check if login form exists
            if page.locator('input[name="j_username"]').count() > 0:
                print("Login form found!")
                page.fill('input[name="j_username"]', USERNAME)
                page.fill('input[name="j_password"]', PASSWD)
                if ENABLE_SCREENSHOTS:
                    page.screenshot(path='debug_step3_before_login.png')
                    print("✓ Screenshot saved: debug_step3_before_login.png")

                page.click('button[type="submit"]')
                page.wait_for_load_state('networkidle', timeout=30000)
                if ENABLE_SCREENSHOTS:
                    page.screenshot(path='debug_step3_after_login.png')
                    print("✓ Screenshot saved: debug_step3_after_login.png")
            else:
                print("Already logged in or login form not found")
            time.sleep(2)

            print("\n" + "="*80)
            print("Step 4: Looking for 'Upload Package' button...")
            print("="*80)
            # Try multiple selectors for the upload button
            upload_selectors = [
                'button:has-text("Upload Package")',
                'button:text("Upload Package")',
                'a:has-text("Upload Package")',
                '[title="Upload Package"]'
            ]

            upload_button = None
            for selector in upload_selectors:
                if page.locator(selector).count() > 0:
                    print(f"✓ Found upload button with selector: {selector}")
                    upload_button = selector
                    break
            if upload_button:
                if ENABLE_SCREENSHOTS:
                    page.screenshot(path='debug_step4_before_upload_click.png')
                    print("✓ Screenshot saved: debug_step4_before_upload_click.png")
                page.click(upload_button)
                time.sleep(2)
                if ENABLE_SCREENSHOTS:
                    page.screenshot(path='debug_step4_after_upload_click.png')
                    print("✓ Screenshot saved: debug_step4_after_upload_click.png")
            else:
                print("✗ Upload button not found! Available buttons:")
                buttons = page.locator('button').all()
                for i, btn in enumerate(buttons[:10]):  # Show first 10 buttons
                    print(f"  Button {i}: {btn.text_content()}")
                if ENABLE_SCREENSHOTS:
                    page.screenshot(path='debug_step4_buttons_not_found.png')
                raise Exception("Upload Package button not found")

            print("\n" + "="*80)
            print("Step 5: Uploading package file...")
            print("="*80)
            page.set_input_files('input[type="file"]', PACKAGE_PATH)
            time.sleep(1)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step5_file_selected.png')
                print("✓ Screenshot saved: debug_step5_file_selected.png")

            print("\n" + "="*80)
            print("Step 6: Clicking OK to confirm upload...")
            print("="*80)
            page.click('button:has-text("OK")')
            page.wait_for_load_state('networkidle', timeout=60000)
            time.sleep(2)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step6_upload_complete.png')
                print("✓ Screenshot saved: debug_step6_upload_complete.png")

            print("\n" + "="*80)
            print("Step 7: Clicking 'Install' button...")
            print("="*80)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step7_before_install.png')
                print("✓ Screenshot saved: debug_step7_before_install.png")
            page.click('button:has-text("Install")')
            time.sleep(1)

            print("\n" + "="*80)
            print("Step 8: Confirming installation...")
            print("="*80)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step8_install_dialog.png')
                print("✓ Screenshot saved: debug_step8_install_dialog.png")

            # Buttons are on the bottom border/footer of the dialog
            # Try multiple selectors including footer-specific ones
            install_selectors = [
                # Footer/bottom border selectors
                '.coral-Dialog-footer button:has-text("Install")',
                '.coral-Dialog-footer .coral-Button:has-text("Install")',
                'footer button:has-text("Install")',
                '.modal-footer button:has-text("Install")',
                'div[class*="footer"] button:has-text("Install")',
                'div[class*="Footer"] button:has-text("Install")',
                # Generic selectors
                'button:has-text("Install")',
                'button:text-is("Install")',
                'div[role="dialog"] button:has-text("Install")',
                '.coral-Dialog button:has-text("Install")',
                '.coral-Button:has-text("Install")',
                'button.coral-Button--primary:has-text("Install")',
                'button[type="button"]:has-text("Install")'
            ]

            print("Looking for Install button in dialog footer/border...")
            install_clicked = False
            for selector in install_selectors:
                try:
                    count = page.locator(selector).count()
                    if count > 0:
                        print(f"Found {count} button(s) with selector: {selector}")
                        # Wait for button to be visible and enabled
                        page.wait_for_selector(selector, state='visible', timeout=5000)

                        # Get the button's bounding box to see its position
                        button = page.locator(selector).first
                        box = button.bounding_box()
                        if box:
                            print(f"  Button position: x={box['x']}, y={box['y']}, width={box['width']}, height={box['height']}")

                        # Try clicking with force=True to bypass any overlays
                        button.click(force=True, timeout=5000)
                        print(f"✓ Clicked Install button with selector: {selector}")
                        install_clicked = True
                        break
                except Exception as e:
                    print(f"  Selector {selector} failed: {str(e)}")
                    continue

            if not install_clicked:
                print("\n✗ Could not find Install button with any selector!")
                print("\nDEBUG: Analyzing all buttons on page...")
                buttons = page.locator('button').all()
                print(f"Total buttons found: {len(buttons)}")
                for i, btn in enumerate(buttons):
                    try:
                        text = btn.text_content()
                        visible = btn.is_visible()
                        enabled = not btn.is_disabled()
                        box = btn.bounding_box()
                        print(f"\nButton {i}:")
                        print(f"  Text: '{text}'")
                        print(f"  Visible: {visible}")
                        print(f"  Enabled: {enabled}")
                        if box:
                            print(f"  Position: x={box['x']}, y={box['y']}")
                    except:
                        pass

                # Try clicking by coordinates if we can find the Install button
                print("\nAttempting to click by coordinates...")
                for btn in buttons:
                    try:
                        if btn.text_content() and "Install" in btn.text_content():
                            box = btn.bounding_box()
                            if box:
                                # Click in the center of the button
                                x = box['x'] + box['width'] / 2
                                y = box['y'] + box['height'] / 2
                                print(f"Clicking Install button at coordinates: ({x}, {y})")
                                page.mouse.click(x, y)
                                install_clicked = True
                                print("✓ Clicked Install button by coordinates")
                                break
                    except:
                        pass

                if not install_clicked:
                    raise Exception("Install button not found in dialog - check debug_step8_install_dialog.png")

            time.sleep(2)

            print("\n" + "="*80)
            print("Step 9: Waiting for installation to complete...")
            print("="*80)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step9_install_dialog.png')
                print("✓ Screenshot saved: debug_step9_install_dialog.png")

            # It takes approximately 70 seconds for the window with progress bar indicator to disappear
            time.sleep(80)
            # The message appears in Activity Log frame at bottom - need to scroll it

            try:
                # Wait for the message
                message_locator = page.locator('text=/Package installed in \\d+ms/')
                message_locator.wait_for(timeout=120000)

                # Extract the installation time
                message_text = message_locator.first.text_content()
                print(f"✓ Found installation complete message: {message_text}")

                # Extract the time value using regex
                import re
                match = re.search(r'Package installed in (\d+)ms', message_text)
                if match:
                    installation_time = match.group(1)
                    print(f"\n{'='*80}")
                    print(f"⏱️  INSTALLATION TIME: {installation_time} ms")
                    print(f"{'='*80}\n")

            except Exception as e:
                print(f"Could not find message with standard selector, trying alternatives...")
                # Try alternative patterns
                alt_patterns = [
                    'text=/installed in \\d+ms/',
                    'text=/Package installed/',
                    'text=/installed/',
                    '*:has-text("Package installed")',
                    '*:has-text("installed in")'
                ]

                message_found = False
                for pattern in alt_patterns:
                    try:
                        alt_locator = page.locator(pattern)
                        alt_locator.wait_for(timeout=10000)
                        message_text = alt_locator.first.text_content()
                        print(f"✓ Found message with pattern: {pattern}")
                        print(f"   Message: {message_text}")

                        # Try to extract time
                        import re
                        match = re.search(r'(\d+)\s*ms', message_text)
                        if match:
                            installation_time = match.group(1)
                            print(f"\n{'='*80}")
                            print(f"⏱️  INSTALLATION TIME: {installation_time} ms")
                            print(f"{'='*80}\n")

                        message_found = True
                        break
                    except:
                        continue

                if not message_found:
                    print("✗ Could not find installation complete message")
                    if ENABLE_SCREENSHOTS:
                        print("Taking screenshot of current state...")
                        page.screenshot(path='debug_step9_message_not_found.png')
                    print("Check debug_step9_message_not_found.png")

                    # Print page text to see what's visible
                    print("\nSearching page text for 'installed'...")
                    page_text = page.content()
                    if 'installed' in page_text.lower():
                        print("✓ Found 'installed' in page content")
                    else:
                        print("✗ 'installed' not found in page content")


            print("\n" + "="*80)
            print("Step 10: Clicking 'More' button...")
            print("="*80)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step10_before_more.png')
                print("✓ Screenshot saved: debug_step10_before_more.png")
            page.click('button:has-text("More")')
            time.sleep(2)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step10_more_menu.png')
                print("✓ Screenshot saved: debug_step10_more_menu.png")

            print("\n" + "="*80)
            print("Step 11: Selecting 'Uninstall' option...")
            print("="*80)
            page.click('text="Uninstall"')
            time.sleep(2)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step11_uninstall_dialog.png')
                print("✓ Screenshot saved: debug_step11_uninstall_dialog.png")

            print("\n" + "="*80)
            print("Step 12: Confirming uninstall...")
            print("="*80)
            page.click('button:has-text("Uninstall")')
            page.wait_for_load_state('networkidle', timeout=60000)
            time.sleep(2)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step12_uninstall_complete.png')
                print("✓ Screenshot saved: debug_step12_uninstall_complete.png")

            print("\n" + "="*80)
            print("Step 13: Clicking 'More' button again...")
            print("="*80)
            page.click('button:has-text("More")')
            time.sleep(2)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step13_more_menu.png')
                print("✓ Screenshot saved: debug_step13_more_menu.png")

            print("\n" + "="*80)
            print("Step 14: Selecting 'Delete' option...")
            print("="*80)


            # Delete option appears at the top of the menu, separated by a line
            # Try multiple selectors to find it
            delete_selectors = [
                'text="Delete"',
                '*:has-text("Delete")',
                'a:has-text("Delete")',
                'button:has-text("Delete")',
                'li:has-text("Delete")',
                'div:has-text("Delete")',
                '[role="menuitem"]:has-text("Delete")',
                '.coral-Menu-item:has-text("Delete")',
                'coral-menu-item:has-text("Delete")'
            ]
            print("Looking for Delete option in menu (appears at top, separated by line)...")
            delete_clicked = False
            for selector in delete_selectors:
                try:
                    count = page.locator(selector).count()
                    if count > 0:
                        print(f"Found {count} element(s) with selector: {selector}")

                        # Get all matching elements
                        elements = page.locator(selector).all()
                        for i, elem in enumerate(elements):
                            try:
                                text = elem.text_content()
                                visible = elem.is_visible()
                                print(f"  Element {i}: text='{text}', visible={visible}")

                                # Click the visible Delete option
                                if visible and text and "Delete" in text:
                                    box = elem.bounding_box()
                                    if box:
                                        print(f"  Position: x={box['x']}, y={box['y']}")

                                    elem.click(force=True, timeout=5000)
                                    print(f"✓ Clicked Delete option with selector: {selector}")
                                    delete_clicked = True
                                    break
                            except Exception as e:
                                print(f"  Element {i} failed: {str(e)}")
                                continue

                        if delete_clicked:
                            break
                except Exception as e:
                    print(f"Selector {selector} failed: {str(e)}")
                    continue

            if not delete_clicked:
                print("\n✗ Could not find Delete option with any selector!")
                print("\nDEBUG: Analyzing all visible menu items...")

                # Try to find all menu items
                menu_selectors = ['li', '[role="menuitem"]', '.coral-Menu-item', 'a']
                for menu_sel in menu_selectors:
                    items = page.locator(menu_sel).all()
                    if len(items) > 0:
                        print(f"\nFound {len(items)} items with selector '{menu_sel}':")
                        for i, item in enumerate(items[:20]):  # Limit to first 20
                            try:
                                text = item.text_content()
                                visible = item.is_visible()
                                if visible and text:
                                    print(f"  Item {i}: '{text.strip()}'")
                                    if "Delete" in text:
                                        print(f"    ^ This is the Delete option! Clicking...")
                                        item.click(force=True)
                                        delete_clicked = True
                                        break
                            except:
                                pass
                        if delete_clicked:
                            break

                if not delete_clicked:
                    page.screenshot(path='debug_step14_delete_not_found.png')
                    raise Exception("Delete option not found in menu - check debug_step14_delete_not_found.png")

            time.sleep(1)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step14_delete_dialog.png')
                print("✓ Screenshot saved: debug_step14_delete_dialog.png")

            print("\n" + "="*80)
            print("Step 15: Confirming delete...")
            print("="*80)
            page.click('button:has-text("Delete")')
            page.wait_for_load_state('networkidle', timeout=30000)
            time.sleep(2)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step15_delete_complete.png')
                print("✓ Screenshot saved: debug_step15_delete_complete.png")

            print("\n" + "="*80)
            print("✓✓✓ ALL STEPS COMPLETED SUCCESSFULLY! ✓✓✓")
            print(f"⏱️  INSTALLATION TIME: {installation_time} ms")
            print("="*80)
            print("\nPackage has been uploaded, installed, uninstalled, and deleted.")
            if ENABLE_SCREENSHOTS:
                print("All screenshots saved in current directory.")
            #print("Video recording saved in ./videos/ directory.")

        except Exception as e:
            print("\n" + "="*80)
            print(f"✗✗✗ ERROR OCCURRED ✗✗✗")
            print("="*80)
            print(f"Error: {str(e)}")
            print(f"Error type: {type(e).__name__}")

            # Capture detailed error state
            page.screenshot(path='debug_error_screenshot.png')
            print("\n✓ Error screenshot saved: debug_error_screenshot.png")

            # Save page HTML for inspection
            with open('debug_error_page.html', 'w', encoding='utf-8') as f:
                f.write(page.content())
            print("✓ Page HTML saved: debug_error_page.html")

            # Print page title and URL
            print(f"\nPage Title: {page.title()}")
            print(f"Page URL: {page.url}")

            raise

        finally:
            # Keep browser open longer for inspection
            print("\nKeeping browser open for 10 seconds for inspection...")
            time.sleep(10)

            # Close context to save video
            context.close()
            browser.close()
            print("Browser closed.")
    return int(installation_time)


def runPhase():
    logging.debug("Sleeping for {n} sec before next phase".format(n=delayBetweenRepetitions))
    time.sleep(delayBetweenRepetitions)
    installationTime = automate_aem_package_workflow_debug()
    return installationTime

def runBenchmarkOnce(jdk, jvmArgs, doMemAnalysis):
    # must remove the logFile before starting the AppServer
    if os.path.exists(logFile):
        os.remove(logFile)

    # Will apply load in small bursts
    maxPulses = numRepetitionsOneClient + numRepetitions50Clients
    thrResults = [math.nan for i in range(maxPulses)] # np.full((maxPulses), fill_value=np.nan, dtype=np.float)
    rss, peakRss, cpu, startupTime = math.nan, math.nan, math.nan, math.nan

    crtTime = datetime.datetime.now()
    startTimeMs = (crtTime.minute * 60 + crtTime.second)*1000 + crtTime.microsecond//1000

    childProcess = startAppServer(jdk=jdk, jvmArgs=jvmArgs)
    if childProcess is None: # Failed to start properly
        return thrResults, rss, peakRss, cpu, startupTime

    # Compute AppServer start-up time
    startupTime = getStartupTime(startTimeMs)

    if collectPerfProfileForJIT:
        if childProcess.poll() is None: # Still running:
            collectJITPerfProfile(childProcess.pid)
        else:
            logging.error("Failed to start JIT perf profiling because Java process has terminated")

    for pulse in range(maxPulses):
        # If enabled, start the JVM profiling thread in the background
        if collectPerfProfileForJVM and pulse == maxPulses-1:
            if childProcess.poll() is None: # Still running:
                collectJVMPerfProfile(childProcess.pid)
            else:
                logging.error("Failed to start JVM perf profiling because Java process has terminated")

        thrResults[pulse] = runPhase()
        logging.info("InstTime={thr}".format(thr=thrResults[pulse]))

    # Collect RSS at end of run
    if childProcess.poll() is None: # Still running
        rss, peakRss = getRss(pid=childProcess.pid)

        if doMemAnalysis:
            logging.info("Generating javacore, core and smaps for process {pid}".format(pid=childProcess.pid))
            collectJavacoreAndSmaps(childProcess.pid)
            time.sleep(20)

    # Stop the AppServer
    stopAppServer(childProcess)

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
    if doColdRun:
        clearSCC(jdk, sccDestroyParams)

    # Start JITServer if needed
    jitServerHandle = None
    if "-XX:+UseJITServer" in javaOpts:
        jitServerHandle = startJITServer(jdk)
        if jitServerHandle == None:
            sys.exit(-1)

    for iter in range(numIter):
        if doOnlyColdRuns:
            clearSCC(jdk, sccDestroyParams)
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
    avg, stdDev, min, max, ci95, numSamples, outliers = computeStats(thrAvgResults[startIter:], eliminateOutliers=True)
    print("Throughput stats: Avg={avg:7.1f}  StdDev={stdDev:7.1f}  Min={min:7.1f}  Max={max:7.1f}  Max/Min={maxmin:4.0f}% CI95={ci95:7.1f}% numSamples={numSamples:3d}".
                        format(avg=avg, stdDev=stdDev, min=min, max=max, maxmin=(max-min)*100.0/min, ci95=ci95, numSamples=numSamples))
    # Footprint stats
    avg, stdDev, min, max, ci95, numSamples, outliers = computeStats(rssResults[startIter:], eliminateOutliers=True)
    print("Footprint stats:  Avg={avg:7.1f}  StdDev={stdDev:7.1f}  Min={min:7.1f}  Max={max:7.1f}  Max/Min={maxmin:4.0f}% CI95={ci95:7.1f}% numSamples={numSamples:3d}".
                        format(avg=avg, stdDev=stdDev, min=min, max=max, maxmin=(max-min)*100.0/min, ci95=ci95, numSamples=numSamples))
    # CompCPU stats
    avg, stdDev, min, max, ci95, numSamples, outliers = computeStats(cpuResults[startIter:], eliminateOutliers=True)
    print("Comp CPU stats:   Avg={avg:7.1f}  StdDev={stdDev:7.1f}  Min={min:7.1f}  Max={max:7.1f}  Max/Min={maxmin:4.0f}% CI95={ci95:7.1f}% numSamples={numSamples:3d}".
                        format(avg=avg, stdDev=stdDev, min=min, max=max, maxmin=(max-min)*100.0/min, ci95=ci95, numSamples=numSamples))
    # Start-up stats
    avg, stdDev, min, max, ci95, numSamples, outliers = computeStats(startupResults[startIter:], eliminateOutliers=True)
    print("StartupTime stats:Avg={avg:7.1f}  StdDev={stdDev:7.1f}  Min={min:7.1f}  Max={max:7.1f}  Max/Min={maxmin:4.0f}% CI95={ci95:7.1f}% numSamples={numSamples:3d}".
                        format(avg=avg, stdDev=stdDev, min=min, max=max, maxmin=(max-min)*100.0/min, ci95=ci95, numSamples=numSamples))

    if jitServerHandle:
        stopJITServer(jitServerHandle)


############################ MAIN ##################################
if  len(sys.argv) < 2:
    print ("Program must have an argument: the number of iterations\n")
    sys.exit(-1)

if doOnlyColdRuns:
    print("Will do a cold run before each tun")
elif doColdRun:
    print("Will do a cold run before each set")

for jvmOpts in jvmOptions:
    for jdk in jdks:
        runBenchmarkIteratively(numIter=int(sys.argv[1]), jdk=jdk, javaOpts=jvmOpts)

