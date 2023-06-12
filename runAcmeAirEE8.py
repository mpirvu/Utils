# Python script to run AcmeAirEE8 app in Liberty
# Liberty is not run in containers. The script should be run on the same machine as the app server
# Mongo and JMeter are ran in containers. Both docker and podman should work.

import datetime # for datetime.datetime.now()
import logging # https://www.machinelearningplus.com/python/python-logging-guide/
import math
import os # for environment variables
import re # for regular expressions
import shlex, subprocess
import sys # for number of arguments
import time # for sleep
from collections import deque


# Set level to level=logging.DEBUG, level=logging.INFO or level=WARNING reduced level of verbosity
logging.basicConfig(level=logging.INFO, format='%(asctime)s :: %(levelname)s :: %(message)s',)

docker = "podman" # Select between docker and podman
netOpts = "--network=slirp4netns" if docker == "podman" else "" # for podman we need to use slirp4netns if running as root. This will be added to Liberty. mongo uses host network.

################### Benchmark configuration #################
doColdRun          = False # when True we clear the SCC before the first run. Set it to False for embedded SCC
AppServerHost      = "localhost" # the host where the app server is running from the point of view of the JMeter machine
AppServerPort      = 9080
AppServerLocation  = "/opt/IBM/OL-23.0.0.3/liberty"
applicationName    = "acmeairee8"
AppServerAffinity  = "taskset 0x1"
applicationLocation= f"{AppServerLocation}/usr/servers/{applicationName}"
logFile            = f"{applicationLocation}/logs/messages.log"
appServerStartCmd  = f"{AppServerAffinity} {AppServerLocation}/bin/server run {applicationName}"
appServerStopCmd   = f"{AppServerLocation}/bin/server stop {applicationName}"
startupWaitTime    = 30 # seconds to wait before checking to see if AppServer is up
memAnalysis = False # Collect javacores and smaps for memory analysis
dirForMemAnalysisFiles = "/tmp"
extraArgsForMemAnalysis = f" -Dcom.ibm.dbgmalloc=true -Xdump:none -Xdump:system:events=user,file={dirForMemAnalysisFiles}/core.%pid.%seq.dmp -Xdump:java:events=user,file={dirForMemAnalysisFiles}/javacore.%pid.%seq.txt"


############### SCC configuration ###########################
sccDir  = f"{AppServerLocation}/usr/servers/.classCache" # Location of the shared class cache
sccDestroyParams = f"-Xshareclasses:cacheDir={sccDir},destroyall"

############### Database configuration #########
dbMachine          = "localhost"
dbUsername         = "" # To connect to mongoMachine remotely; leave empty to connect without ssh
dbImage            = "localhost/mongo-acmeair-ee8:5.0.15"
dbContainerName    = "mongodb"
startDbScript      = f"{docker} run --rm -d --name {dbContainerName} --network=host {dbImage} --nojournal"

############### JMeter CONFIG ###############
jmeterMachine       = "localhost"
jmeterUsername      = "" # To connect to JMeter machine; leave empty to connect without ssh
jmeterImage         = "localhost/jmeter-acmeair:5.3"
jmeterContainerName = "jmeter"
jmeterAffinity      = "2-3"
printRampup         = False # If True, print all JMeter throughput values to plot rampup curve

################ Load CONFIG ###############
numRepetitionsOneClient = 0
numRepetitions50Clients = 2
durationOfOneClient     = 60 # seconds
durationOfOneRepetition = 300 # seconds
numClients              = 10
delayBetweenRepetitions = 10
numMeasurementTrials    = 1 # Last N trials are used in computation of throughput
thinkTime               = 0 # ms
maxUsers                = 199 # Maximum number of simulated AcmeAir users

################# JITServer CONFIG ###############
# JITServer is automatically launched if the JVM option include -XX:+UseJITServer
#JITServerMachine = "9.42.142.177" # if applicable
#JITServerUsername = "" # To connect to JITServerMachine; leave empty for connecting without ssh
#JITServerImage   = "liberty-acmeair-ee8:J17"
#JITServerContainerName = "jitserver"
############################# END CONFIG ####################################


# ENV VARS to use for all runs
#TR_Options=""
TR_Options="{com/ibm/ws/jaxrs20/injection/InjectionRuntimeContextHelper.findBeanCustomizer(Ljava/lang/Class;Lorg/apache/cxf/Bus;)Lcom/ibm/ws/jaxrs20/api/JaxRsFactoryBeanCustomizer;}(traceFull,traceInlining,log=/tmp/log.disableHCR.txt)"


jvmOptions = [
        #"-Xjit:disableNextGenHCR -Xshareclasses:none -XX:+EnableHCR -Xmx256m",
        #"-XX:+EnableHCR -Xshareclasses:none -Xjit:dontDowngradeToCold,disableSelectiveNoServer,verbose={compilePerformance},verbose={inlining},vlog=/tmp/vlog.default.txt -Xmx256m",
        "-XX:+EnableHCR -Xshareclasses:none -Xjit:disableNextGenHCR,dontDowngradeToCold,disableSelectiveNoServer -Xmx256m",
        #"-XX:+EnableHCR -Xshareclasses:none -Xjit:dontDowngradeToCold,disableSelectiveNoServer -Xmx256m ",
        #"-XX:-EnableHCR -Xshareclasses:none -Xjit:dontDowngradeToCold,disableSelectiveNoServer -Xmx256m "
        #"-XX:-TieredCompilation -Xmx256m"
]

jdks = [
    #"/home/mpirvu/sdks/OpenJDK8U-jre_x64_linux_hotspot_8u372b07",
    #"/home/mpirvu/sdks/pxa6480sr4fp1-20170215_01",
    #"/home/mpirvu/sdks/pxa6480sr9-20230606_01",
    #"/home/mpirvu/sdks/OpenJ9-JDK17-x86-64_linux-20230526-191133",
    "/home/mpirvu/FullJava17/openj9-openjdk-jdk17/build/linux-x86_64-server-release/images/jdk",
]

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
    return avg, stdDev, min, max, ci95


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

def stopContainersFromImage(host, username, imageName):
    # Find all running containers from image
    remoteCmd = f"{docker} ps --quiet --filter ancestor={imageName}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    for containerID in lines:
        remoteCmd = f"{docker} stop {containerID}"
        cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
        logging.debug(f"Stopping container: {cmd}")
        output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)


def startDatabase(dbMachine, dbUsername, startDbScript):
    remoteCmd = f"{startDbScript}"
    cmd = f"ssh {dbUsername}@{dbMachine} \"{remoteCmd}\"" if dbUsername else remoteCmd
    logging.info("Starting database: {cmd}".format(cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    logging.debug(output)

def restoreDatabase(mongoMachine, dbUsername):
    remoteCmd = f"{docker} exec {dbContainerName} mongorestore --drop /AcmeAirDBBackup"
    cmd = f"ssh {dbUsername}@{mongoMachine} \"{remoteCmd}\"" if dbUsername else remoteCmd
    logging.debug(f"Restoring database: {cmd}")
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.STDOUT)
    logging.debug(output)

def stopDatabase(dbMachine, dbUsername):
    remoteCmd = f"{docker} stop {dbContainerName}"
    cmd = f"ssh {dbUsername}@{dbMachine} \"{remoteCmd}\"" if dbUsername else remoteCmd
    logging.info("Stopping database: {cmd}".format(cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    logging.debug(output)

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
    cmd = f"cp /proc/{javaPID}/smaps /tmp/smaps.{javaPID}"
    try:
        subprocess.run(shlex.split(cmd), universal_newlines=True)
    except:
        logging.error("Cannot get smaps file for javaPID {javaPID}".format(javaPID=javaPID))

def collectJavacoreAndSmaps(javaPID):
    collectJavacore(javaPID)
    collectSmaps(javaPID)



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
        with open(logFile) as f:
            for line in f:
                m = errPattern.match(line)
                if m:
                    logging.warning("AppServer {applicationName} errored while starting:\n\t {line}").format(applicationName=applicationName,line=line)
                    return False
                m1 = readyPattern.match(line)
                if m1:
                    return True # True means success
        logging.warning("sleeping 1 sec and trying again")
        time.sleep(1) # wait 1 sec and try again
    return False # False means failure


def killAppServerIfRunning(childProcess):
    if childProcess.poll() is None: # Still running
        logging.error("Killing AppServer")
        childProcess.kill()
        childProcess.wait()


def startAppServer(jdk, jvmArgs):
    logging.info("Starting AppServer with command: {appServerStartCmd}".format(appServerStartCmd=appServerStartCmd))
    myEnv = os.environ.copy()
    myEnv["JAVA_HOME"] = jdk
    myEnv["JVM_ARGS"] = jvmArgs
    myEnv["TR_PrintCompTime"] = "1"
    myEnv["TR_Options"] = TR_Options
    # Fork a process and run in background
    childProcess = subprocess.Popen(shlex.split(appServerStartCmd), env=myEnv, universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logging.debug(f"Waiting for {startupWaitTime} sec for the AppServer to start")
    time.sleep(startupWaitTime)
    if childProcess.poll() is None: # It's running
        logging.debug("AppServer started with pid {pid}".format(pid=childProcess.pid))
    # Verify that server started correctly
    startOK = verifyAppserverStarted()
    if not startOK:
        logging.error("AppServer did not start correctly")
        killAppServerIfRunning(childProcess)
        return None
    logging.debug("AppServer started OK")
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
                        logging.error("Liberty timestamp is in the wrong format: {timestamp}".format(timestamp=timestamp))
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
        m = compTimePattern.match(line)
        if m:
            threadTime += float(m.group(1))
    return threadTime/1000.0 if threadTime > 0 else math.nan


def applyLoad(duration, numClients):
    # Run jmeter remotely
    remoteCmd = f"{docker} run -d --network=host -e JTHREAD={numClients} -e JDURATION={duration} -e JUSER={maxUsers} -e JHOST={AppServerHost} -e JPORT={AppServerPort} --name {jmeterContainerName} {jmeterImage}"
    cmd = f"ssh {jmeterUsername}@{jmeterMachine} \"{remoteCmd}\"" if jmeterUsername else remoteCmd
    logging.info("Apply load: {cmd}".format(cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)

def getThroughput():
    logging.debug("Getting throughput info...")
    remoteCmd = f"{docker} logs --tail=200 {jmeterContainerName}"
    cmd = f"ssh {jmeterUsername}@{jmeterMachine} \"{remoteCmd}\"" if jmeterUsername else remoteCmd
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.DEVNULL)
    lines = output.splitlines()

    # Find the last line that contains
    # summary = 110757 in    30s = 3688.6/s Avg:    12 Min:     0 Max:   894 Err:     0 (0.00%)
    # or
    # summary = 233722 in 00:02:00 = 1947.4/s Avg:     0 Min:     0 Max:   582 Err:     0 (0.00%)
    elapsedTime = 0
    throughput = 0
    errs = 0
    totalTransactions = 0
    lastSummaryLine = ""
    slidingWindow = deque([0.0, 0.0, 0.0])
    pattern1 = re.compile('summary \+\s+(\d+) in\s+(\d+\.*\d*)s =\s+(\d+\.\d+)/s.+Finished: 0')
    pattern2 = re.compile('summary \+\s+(\d+) in\s+(\d\d):(\d\d):(\d\d) =\s+(\d+\.\d+)/s.+Finished: 0')
    for line in lines:
        # summary +  17050 in 00:00:06 = 2841.7/s Avg:     0 Min:     0 Max:    49 Err:     0 (0.00%) Active: 2 Started: 2 Finished: 0
        if line.startswith("summary +"):
            if printRampup:
                print(line)
            m = pattern1.match(line)
            if m:
                thr = float(m.group(3))
                slidingWindow.pop()
                slidingWindow.appendleft(thr)
            else:
                m = pattern2.match(line)
                if m:
                    thr = float(m.group(5))
                    slidingWindow.pop()
                    slidingWindow.appendleft(thr)

        if line.startswith("summary ="):
            lastSummaryLine = line

    pattern = re.compile('summary =\s+(\d+) in\s+(\d+\.*\d*)s =\s+(\d+\.\d+)/s.+Err:\s*(\d+)')
    m = pattern.match(lastSummaryLine)
    if m:
        totalTransactions = float(m.group(1)) # First group is the total number of transactions/pages that were processed
        elapsedTime = float(m.group(2)) # Second group is the interval of time that passed
        throughput = float(m.group(3)) # Third group is the throughput value
        errs = int(m.group(4))  # Fourth group is the number of errors
    else: # Check the second pattern
        pattern = re.compile('summary =\s+(\d+) in\s+(\d\d):(\d\d):(\d\d) =\s+(\d+\.\d+)/s.+Err:\s*(\d+)')
        m = pattern.match(lastSummaryLine)
        if m:
            totalTransactions = float(m.group(1))  # First group is the total number of transactions/pages that were processed
            # Next 3 groups are the interval of time that passed
            elapsedTime = float(m.group(2))*3600 + float(m.group(3))*60 + float(m.group(4))
            throughput = float(m.group(5))  # Fifth group is the throughput value
            errs = int(m.group(6)) # Sixth group is the number of errors
    # Compute the peak throughput as a sliding window of 3 consecutive entries
    peakThr = sum(slidingWindow)/len(slidingWindow)

    #print (str(elapsedTime), throughput, sep='\t')
    if errs > 0:
        logging.error("JMeter Errors: {n}".format(n=errs))
    return throughput, elapsedTime, peakThr, errs

def stopJMeter():
    remoteCmd = f"{docker} rm {jmeterContainerName}"
    cmd = f"ssh {jmeterUsername}@{jmeterMachine} \"{remoteCmd}\"" if jmeterUsername else remoteCmd
    logging.debug("Removing jmeter: {cmd}".format(cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)


def runPhase(duration, numClients):
    logging.debug("Sleeping for {n} sec before applying load".format(n=delayBetweenRepetitions))
    time.sleep(delayBetweenRepetitions)

    applyLoad(duration, numClients)

    # Wait for load to finish
    remoteCmd = f"{docker} wait {jmeterContainerName}"
    cmd = f"ssh {jmeterUsername}@{jmeterMachine} \"{remoteCmd}\"" if jmeterUsername else remoteCmd
    logging.debug("Wait for {jmeter} to end: {cmd}".format(jmeter=jmeterContainerName, cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)

    # Read throughput
    thr, elapsed, peakThr, errors = getThroughput()

    stopJMeter()
    if logging.root.level <= logging.DEBUG:
        print("Throughput={thr:7.1f} duration={elapsed:6.1f} peak={peakThr:7.1f} errors={err:4d}".format(thr=thr,elapsed=elapsed,peakThr=peakThr,err=errors))
    if errors > 0:
        logging.error(f"JMeter encountered {errors} errors")
    return thr, elapsed, peakThr, errors


def runBenchmarkOnce(jdk, jvmArgs, doMemAnalysis):
    # must remove the logFile before starting the AppServer
    if os.path.exists(logFile):
        os.remove(logFile)

    # Will apply load in small bursts
    maxPulses = numRepetitionsOneClient + numRepetitions50Clients
    thrResults = [math.nan for i in range(maxPulses)] # np.full((maxPulses), fill_value=np.nan, dtype=np.float)
    rss, peakRss, cpu, startupTime = math.nan, math.nan, math.nan, math.nan

    restoreDatabase(dbMachine, dbUsername)

    crtTime = datetime.datetime.now()
    startTimeMs = (crtTime.minute * 60 + crtTime.second)*1000 + crtTime.microsecond//1000

    childProcess = startAppServer(jdk=jdk, jvmArgs=jvmArgs)
    if childProcess is None: # Failed to start properly
        return thrResults, rss, peakRss, cpu, startupTime

    # Compute AppServer start-up time
    startupTime = getStartupTime(startTimeMs)

    peakThroughput = 0
    for pulse in range(maxPulses):
        if pulse >= numRepetitionsOneClient:
            cli = numClients
            duration = durationOfOneRepetition
        else:
            cli = 1
            duration = durationOfOneClient
        thrResults[pulse], elapsed, peakThr, errors = runPhase(duration, cli)
        if errors == 0:
            peakThroughput = max(peakThroughput, peakThr)
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

    # Must compute the CPU after stopping the AppServer
    cpu = getCompCPU(childProcess)

    # return throughput as an array of throughput values for each burst and also the RSS, PeakRSS and CPU
    return thrResults, peakThroughput, rss, peakRss, cpu, startupTime


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

    for iter in range(numIter):
        # if memAnalysis is True, add the options required for memory analysis, but only for the last iteration
        doMemAnalysis = memAnalysis and iter == numIter - 1
        if doMemAnalysis:
            javaOpts = javaOpts + extraArgsForMemAnalysis
        thrList, peakThr, rss, peakRss, cpu, startupTime = runBenchmarkOnce(jdk, javaOpts, doMemAnalysis)
        lastThr = meanLastValues(thrList, numMeasurementTrials) # average for last N pulses
        print(f"Run {iter}: Thr={lastThr:6.1f} RSS={rss:6.1f} MB  PeakRSS={peakRss:6.1f} MB  CPU={cpu:4.1f} sec  Startup={startupTime:5.0f} PeakThr={peakThr:6.1f}".
              format(lastThr=lastThr, rss=rss, peakRss=peakRss, cpu=cpu, startupTime=startupTime, peakThr=peakThr))
        thrResults.append(thrList) # copy all the pulses
        rssResults.append(rss)
        cpuResults.append(cpu)
        startupResults.append(startupTime)

    # print stats
    print(f"\nResults for jdk: {jdk} and opts: {javaOpts}")
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
        for iter in range(numIter):
            if not math.isnan(thrResults[iter][pulse]):
                total += thrResults[iter][pulse]
                numValidEntries += 1
        verticalAverages.append(total/numValidEntries if numValidEntries > 0 else math.nan)

    print("Avg:", end="")
    for pulse in range(numPulses):
        print("\t{thr:7.1f}".format(thr=verticalAverages[pulse]), end="")
    print("\tThr={avgThr:7.1f}  RSS={rss:7.0f} MB  CompCPU={cpu:5.1f} sec  Startup={startup:5.0f} ms".
          format(avgThr=nanmean(thrAvgResults), rss=nanmean(rssResults), cpu=nanmean(cpuResults), startup=nanmean(startupResults)))
    # Throughput stats
    avg, stdDev, min, max, ci95 = computeStats(thrAvgResults)
    print("Throughput stats: Avg={avg:7.1f}  StdDev={stdDev:7.1f}  Min={min:7.1f}  Max={max:7.1f}  Max/Min={maxmin:7.1f} CI95={ci95:7.1f}%".
                        format(avg=avg, stdDev=stdDev, min=min, max=max, maxmin=max/min, ci95=ci95))

def cleanup():
    stopContainersFromImage(dbMachine, dbUsername, dbImage)
    # CWWKE0029E: An instance of server crudserver is already running.
    getJavaProcesses()


############################ MAIN ##################################
if  len(sys.argv) < 2:
    print ("Program must have an argument: the number of iterations\n")
    sys.exit(-1)

# Clean-up from a previous possible bad run
cleanup()

# Database needs to be started only once
startDatabase(dbMachine, dbUsername, startDbScript)

if doColdRun:
    print("Will do a cold run before each set")

for jvmOpts in jvmOptions:
    for jdk in jdks:
        runBenchmarkIteratively(numIter=int(sys.argv[1]), jdk=jdk, javaOpts=jvmOpts)

# Stop the database
stopDatabase(dbMachine, dbUsername)
