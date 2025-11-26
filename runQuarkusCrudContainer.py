# Python script to run restCrud with Quarkus in containers
# The script can run the same configuration multiple times and compute stats
# on performance metrics like: start-up time, throughput for first N minutes
# of load, peak throughput over a period of time, RSS, peak RSS, compCPU
# Each configuration is defined as a container image to run together with
# associated command line arguments or environment variables.
# If command line arguments indicates that the JVM is running in client mode,
# a JITServer will be launched automatically
# If using CRIU, we need to have a network defined with1
#          docker network create --subnet 192.168.200.0/24 myNetwork
import datetime # for datetime.datetime.now()
from collections import deque
import logging # https://www.machinelearningplus.com/python/python-logging-guide/
import math
import queue
import re # for regular expressions
import shlex, subprocess
import sys # for exit
import time # for sleep

############################### CONFIG ###############################################
#level=logging.DEBUG, logging.INFO, logging.WARNING
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s :: %(levelname)s :: (%(threadName)-6s) :: %(message)s',)

docker = "docker"         # Select between docker and podman
instantOnRestore = False  # Set to true to add --cap-add=CHECKPOINT_RESTORE (or --privileged) to docker run command
                          # Also may change the IP of the AppServer, postgres and wrk containers (they are attached to a network myNetwork which must exist)

################### Benchmark configuration #################
doColdRun = False
appServerMachine = "localhost" # This address is used by the load generator
username = "" # for connecting remotely to the SUT; leave empty to connect without ssh
containerName = "restcrud"
appServerPort   = "9090"
cpuLimit        = "--cpuset-cpus=1" # --cpuset-mems=0
memLimit        = "-m=256m"
delayToStart    = 5 # seconds; waiting for the AppServer to start before checking for valid startup
extraDockerOpts = "-v /tmp:/tmp" # extra options to pass to docker run
postRestoreOpts = "-XX:+UseJITServer -XX:+JITServerLogConnections" if instantOnRestore else "" # Options to add to the JVM for restore

getFirstResponseTime = True # Set to true to get the first response time
firstResponseHelperScript = "./loop_curl.sh" # Script to get the first response time
netOpts = "--network=slirp4netns" if docker == "podman" else "--network=host" # for podman we need to use slirp4netns if running as root.
if instantOnRestore:
    #netOpts = "--net myNetwork --ip 192.168.200.20"
    #appServerMachine = "192.168.200.20"
    netOpts = "--net=host"
    appServerMachine = "localhost"


############### SCC configuration #####################
useSCCVolume    = False  # set to true to have a SCC mounted in a volume (instead of the embedded SCC)
SCCVolumeName   = "scc_volume" # Name of the volume to use for the SCC
sccInstanceDir  = "/opt/java/.scc" # Location of the shared class cache in the instance
mountOpts       = f"--mount type=volume,src={SCCVolumeName},target={sccInstanceDir}" if useSCCVolume  else ""

############### Database configuration #########
dbMachine       = "localhost" # This address is also used by the AppServer to connect to the database
dbUsername      = "" # To connect to dbMachine remotely; leave empty to connect without ssh
dbImage         = "restcrud-db"
dbAffinity      = "--cpuset-cpus 2,3,6,7"
dbInstanceName  = "postgres"
dbNetOpts       = "--net=host" # "--net myNetwork --ip 192.168.200.10" if instantOnRestore else "--net=host"

################ Load CONFIG ###############
doApplyLoad      = True # Set to false to skip load generation
printRampup      = True # If True, print all JMeter throughput values to plot rampup curve; only applicable to JMeter
useJMeterForLoad = True # The alternative is to use wrk
loadGenMachine   = "localhost" if useJMeterForLoad else "localhost"
loadGenUsername  = "" if useJMeterForLoad else "" # To connect to load generator machine; leave empty to connect without ssh
loadgenImage     = "jmeter_simple:5.5" if useJMeterForLoad else "wrk"
loadGenContainerName = "jmeter" if useJMeterForLoad else "wrk"
loadGenNetOpts   = "--net=host" #"--net myNetwork --ip 192.168.200.60" if instantOnRestore else "--net=host"
loadGenAffinity  = "--cpuset-cpus 0,4" #"--net myNetwork --ip 192.168.200.60" if instantOnRestore else "--net=host"

numRepetitionsOneClient = 0
numRepetitions50Clients = 1 
durationOfOneClient     = 60 # seconds
durationOfOneRepetition = 180 # seconds
numClients              = 10 # Number of wrk threads
delayBetweenRepetitions = 10
numMeasurementTrials    = 1 # Last N trials are used in computation of throughput

################# JITServer CONFIG ###############
# JITServer is automatically launched if the JVM option include -XX:+UseJITServer
JITServerMachine = "192.168.1.9" # if applicable
JITServerUsername = "" # To connect to JITServerMachine; leave empty for connecting without ssh
JITServerImage   = "openj9:J17-20251108" # Leave empty to use the app container as JITServer
JITServerAffinity      = ""
JITServerContainerName = "jitserver"
JITServerOptions       = "-XX:+JITServerLogConnections -Xdump:directory=/tmp/vlogs" # Options to pass to the JITServer
JITServerExtraOptions  = "-v /tmp/vlogs:/tmp/vlogs"
JITServerUseEncryption = False
KeyAndCertificateDir   = "/team/mpirvu/secrets" # Only used if JITServerUseEncryption = True
SecretsDirInContainer  = "/tmp/secrets" # Only used if JITServerUseEncryption = True

# List of configs to run
# Each entry is a dictionary with "image" and "args" as keys
# Note that openj9 containers also add: JAVA_TOOL_OPTIONS=-XX:+IgnoreUnrecognizedVMOptions -XX:+PortableSharedCache -XX:+IdleTuningGcOnIdle -Xshareclasses:name=openj9_system_scc,cacheDir=/opt/java/.scc,readonly,nonFatal
# Containers
configs = [
    #{"image":"openj9_restcrud:J17-20251020", "args":"-Xthr:parkPolicy=2,parkSleepCount=3,parkSleepMultiplier=0,parkSleepTime=200 -Xms128m -Xmx128m -Dquarkus.thread-pool.core-threads=8 -Dquarkus.thread-pool.max-threads=8"},
    #{"image":"openj9_restcrud:J17-20251020", "args":"-Xmx128m -Dquarkus.thread-pool.core-threads=8 -Dquarkus.thread-pool.max-threads=8"},
    #{"image":"openj9_restcrud_nopopulatescc:J17-20251020", "args":"-Xmx128m -Dquarkus.thread-pool.core-threads=8 -Dquarkus.thread-pool.max-threads=8"},

    #{"image":"temurin_restcrud:j17", "args":"-Xmx128m -Dquarkus.thread-pool.core-threads=8 -Dquarkus.thread-pool.max-threads=8"},
    #{"image":"restorerun-crud-256-1cpu-8threads:J17-20251108", "args":"-XX:+UseJITServer"}, # CRIU InstantON
    {"image":"restcrud-native", "args":"-Xmx128m"},

    #{"image":"openj9_restcrud:J17-20251108-nopopulate", "args":"-Xmx128m -Dquarkus.thread-pool.core-threads=8 -Dquarkus.thread-pool.max-threads=8"},
    #{"image":"openj9_restcrud:J17-20251108-nopopulate", "args":"-XX:+UseJITServer -Xmx128m -Dquarkus.thread-pool.core-threads=8 -Dquarkus.thread-pool.max-threads=8"},

    #{"image":"openj9_restcrud:J17-20251108-nopopulate", "args":"-Xmx128m"},
    #{"image":"openj9_restcrud:J17-20251108-nopopulate", "args":"-XX:+UseJITServer -Xmx128m"},
    #{"image":"openj9_restcrud:J17-20251108-nopopulate", "args":"-Xmx128m -Dquarkus.thread-pool.core-threads=8 -Dquarkus.thread-pool.max-threads=8"},
    #{"image":"openj9_restcrud:J17-20251108-nopopulate", "args":"-XX:+UseJITServer -Xmx128m -Dquarkus.thread-pool.core-threads=8 -Dquarkus.thread-pool.max-threads=8"},

    #{"image":"openj9_restcrud:J17-20251108-nopopulate", "args":"-Xnoaot -Xjit:initialOptLevel=warm,inhibitRecompilation,perfTool -Xmx128m -Dquarkus.thread-pool.core-threads=8 -Dquarkus.thread-pool.max-threads=8"},
    #{"image":"openj9_restcrud:J17-20251108-nopopulate", "args":"-XX:+UseJITServer -Xnoaot -Xjit:initialOptLevel=warm,inhibitRecompilation,perfTool -Xmx128m -Dquarkus.thread-pool.core-threads=8 -Dquarkus.thread-pool.max-threads=8"},


    #{"image":"restorerun-crud-256-1cpu:J17-20251108", "args":""}, # CRIU InstantON
    #{"image":"restorerun-crud-256-1cpu-jitserver:J17-20251108", "args":"-XX:+UseJITServer"}, # CRIU InstantON
    #{"image":"restorerun-crud-256-1cpu-8threads:J17-20251108", "args":""}, # CRIU InstantON
    #{"image":"restorerun-crud-256-1cpu-jitserver-8threads:J17-20251108", "args":"-XX:+UseJITServer"}, # CRIU InstantON
    #{"image":"restorerun-crud-256-1cpu-jitserver-8threads-noaot:J17-20251108", "args":"-XX:+UseJITServer"}, # CRIU InstantON

#    {"image":"", "args":""},
]

def nancount(myList):
    count = 0
    for i in range(len(myList)):
        if not math.isnan(myList[i]):
            count += 1
    return count

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
    cnt = nancount(myList)
    if cnt <= 1:
        return math.nan
    tvalue = tDistributionValue95(cnt-1)
    avg, stdDev = nanmean(myList), nanstd(myList)
    marginOfError = tvalue * stdDev / math.sqrt(cnt)
    return 100.0*marginOfError/avg

def computeStats(myList):
    avg = nanmean(myList)
    stdDev = nanstd(myList)
    min = nanmin(myList)
    max = nanmax(myList)
    ci95 = meanConfidenceInterval95(myList)
    samples = nancount(myList)
    return avg, stdDev, min, max, ci95, samples

def printStats(myList, name):
    avg, stdDev, min, max, ci95, samples = computeStats(myList)
    print("{name:<17} Avg={avg:7.1f}  StdDev={stdDev:7.1f}  Min={min:7.1f}  Max={max:7.1f}  Max/Min={maxmin:7.1f} CI95={ci95:7.1f}%  samples={n}".
            format(name=name, avg=avg, stdDev=stdDev, min=min, max=max, maxmin=max/min, ci95=ci95, n=samples))

def meanLastValues(myList, numLastValues):
    assert numLastValues > 0
    if numLastValues > len(myList):
        numLastValues = len(myList)
    return nanmean(myList[-numLastValues:])

def getMainPIDFromContainer(host, username, instanceID):
    remoteCmd = f"{docker} inspect " + "--format='{{.State.Pid}}' " + instanceID
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    try:
        output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
        lines = output.splitlines()
        return lines[0]
    except:
        return 0
    return 0

# Given a container ID, find all the Java processes running in it
# If there is only one Java process, return its PID
def getJavaPIDFromContainer(host, username, instanceID):
    mainPID = getMainPIDFromContainer(host, username, instanceID)
    if int(mainPID) == 0:
        return 0 # Error
    logging.debug("Main PID from container is {mainPID}".format(mainPID=mainPID))
    # Find all PIDs running on host
    remoteCmd = "ps -eo ppid,pid,cmd --no-headers"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    pattern = re.compile("^\s*(\d+)\s+(\d+)\s+(\S+)")
    # Construct a dictionary with key=PPID and value a list of PIDs (for its children)
    ppid2pid = {}
    pid2cmd = {}
    for line in lines:
        m = pattern.match(line)
        if m:
            ppid = m.group(1)
            pid = m.group(2)
            cmd = m.group(3)
            if ppid in ppid2pid:
                ppid2pid[ppid].append(pid)
            else:
                ppid2pid[ppid] = [pid]
            pid2cmd[pid] = cmd
    # Do a breadth-first search to find all Java processes. Use a queue.
    javaPIDs = []
    pidQueue = queue.Queue()
    pidQueue.put(mainPID)
    while not pidQueue.empty():
        pid = pidQueue.get()
        # If this PID is a Java process, add it to the list
        if "/java" in pid2cmd[pid] or pid2cmd[pid].startswith("java"):
            javaPIDs.append(pid)
        if pid in ppid2pid: # If my PID has children
            for childPID in ppid2pid[pid]:
                pidQueue.put(childPID)
    if len(javaPIDs) == 0:
        logging.error("Could not find any Java process in container {instanceID}".format(instanceID=instanceID))
        return 0
    if len(javaPIDs) > 1:
        logging.error("Found more than one Java process in container {instanceID}".format(instanceID=instanceID))
        return 0
    return int(javaPIDs[0])

def removeForceContainer(host, username, instanceName):
    remoteCmd = f"{docker} rm -f {instanceName}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    logging.debug("Removing container instance {instanceName}: {cmd}".format(instanceName=instanceName,cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)

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

def stopAppServerByID(host, username, containerID):
    logging.debug("Stopping AppServer container {containerID}".format(containerID=containerID))
    # Check that the container is still running
    remoteCmd = f"{docker} ps --quiet --filter id={containerID}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    if not lines:
        logging.warning("AppServer instance {containerID} does not exist. Might have crashed".format(containerID=containerID))
        return False
    remoteCmd = f"{docker} stop {containerID}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    return True

def removeContainersFromImage(host, username, imageName):
    # First stop running containers
    stopContainersFromImage(host, username, imageName)
    # Now remove stopped containes
    remoteCmd = f"{docker} ps -a --quiet --filter ancestor={imageName}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    for containerID in lines:
        remoteCmd = f"{docker} rm {containerID}"
        cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
        logging.debug(f"Removing container: {cmd}")
        output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)

# Restore database from backup
def restoreDatabase(host, username, dbImage):
    return

# start postgress on a remote machine
def startDatabase(host, username, dbImage):
    remoteCmd = f"{docker} run --rm -d {dbNetOpts} {dbAffinity} --name {dbInstanceName} {dbImage}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    logging.info("Starting database: {cmd}".format(cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    time.sleep(3) # Give the database container some trime to start-up

# Given a PID, return RSS and peakRSS in MB for the process
def getRss(host, username, pid):
    _scale = {'kB': 1024, 'mB': 1024*1024, 'KB': 1024, 'MB': 1024*1024}
    # get pseudo file  /proc/<pid>/status
    filename = "/proc/" + str(pid) + "/status"
    remoteCmd = f"cat {filename}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
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
        return [math.nan, math.nan]  # invalid format
    rss = float(tokens[1]) * _scale[tokens[2]] / 1048576.0 # convert value to bytes and then to MB

    # repeat for peak RSS
    i =  s.index("VmHWM:")
    tokens = s[i:].split(None, 3)
    if len(tokens) < 3:
        return [math.nan, math.nan]  # invalid format
    peakRss = float(tokens[1]) * _scale[tokens[2]] / 1048576.0 # convert value to bytes and then to MB
    return [rss, peakRss]

def clearSCC(host, username):
    logging.info("Clearing SCC")
    remoteCmd = f"{docker} volume rm --force {SCCVolumeName}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    output = subprocess.run(shlex.split(cmd), universal_newlines=True, stderr=subprocess.DEVNULL)
    # TODO: make sure volume does not exist

def verifyAppServerInContainerIDStarted(instanceID, host, username):
    remoteCmd = f"{docker} ps --quiet --filter id={instanceID}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    if not lines:
        logging.warning("AppServer container {instanceID} is not running").format(instanceID=instanceID)
        return False

    # When instantOnRestore is enabled, there is no output after the restore
    # so we can assume that if the process is till running, it has started correctly.
    if instantOnRestore:
        return True

    remoteCmd = f"{docker} logs --tail=100 {instanceID}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    errPattern = re.compile('.+\[ERROR')
    # 2025-10-17 23:30:25,616 INFO  [io.quarkus] (main) rest-crud 1.0 on JVM (powered by Quarkus 3.20.3) started in 3.462s. Listening on: http://0.0.0.0:9090
    readyPattern = re.compile("rest-crud .+ started in \d+\.\d+s. Listening on")
    for iter in range(15):
        output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
        liblines = output.splitlines()
        for line in liblines:
            m = errPattern.match(line)
            if m:
                logging.warning("AppServer container {instanceID} errored while starting: {line}").format(instanceID=instanceID,line=line)
                return False
            m1 = readyPattern.search(line)
            if m1:
                if logging.root.level <= logging.DEBUG:
                   print(lines)
                return True # True means success
        if logging.root.level <= logging.DEBUG:
            print(liblines)
        time.sleep(1) # wait 1 sec and try again
        logging.warning("Checking again")
    return False

def startAppServerContainer(host, username, instanceName, image, port, jvmArgs, mountOpts, dbMachine):
    # vlogs can be created in /tmp/vlogs -v /tmp/vlogs:/tmp/vlogs
    #JITOPTS = "\"verbose={compilePerformance},verbose={JITServer},vlog=/tmp/vlog.client.txt\""
    JITOPTS = ""
    # If using JITServer post restore, add its address to the JVM options
    instantONOpts = ""
    otherOpts = ""
    if instantOnRestore:
        restoreOpts = postRestoreOpts
        if ("-XX:+UseJITServer" in postRestoreOpts):
            restoreOpts = restoreOpts + f" -XX:JITServerAddress={JITServerMachine} "
            if JITServerUseEncryption:
                restoreOpts = restoreOpts + f" -XX:JITServerSSLRootCerts={SecretsDirInContainer}/cert.pem "
                otherOpts = f"--mount type=bind,source={KeyAndCertificateDir},destination={SecretsDirInContainer}"
        instantONOpts = f"-e OPENJ9_RESTORE_JAVA_OPTIONS='{restoreOpts}' --privileged" # or --cap-add=CHECKPOINT_RESTORE --security-opt seccomp=unconfined
    else:
        if ("-XX:+UseJITServer" in jvmArgs):
            jvmArgs = jvmArgs + f" -XX:JITServerAddress={JITServerMachine} "
            if JITServerUseEncryption:
                jvmArgs = jvmArgs + f" -XX:JITServerSSLRootCerts={SecretsDirInContainer}/cert.pem "
                otherOpts = f"--mount type=bind,source={KeyAndCertificateDir},destination={SecretsDirInContainer}"

    remoteCmd = f"{docker} run -d {extraDockerOpts} {cpuLimit} {memLimit} {mountOpts} {instantONOpts} {netOpts} {otherOpts} -e QUARKUS_DATASOURCE_JDBC_URL=jdbc:postgresql://{dbMachine}:5432/rest-crud -e TR_Options={JITOPTS} -e _JAVA_OPTIONS='{jvmArgs}' -e TR_PrintCompStats=1 -e TR_PrintCompTime=1 -p {port}:9090 --name {instanceName} {image}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    logging.info("Starting AppServer instance {instanceName}: {cmd}".format(instanceName=instanceName,cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    assert lines, "Error: docker run output is empty".format(l=lines)
    assert len(lines) == 1, f"Error: docker run output containes several lines: {lines}"
    if logging.root.level <= logging.DEBUG:
        print(lines)
    instanceID = lines[0] # ['2ccae49f3c03af57da27f5990af54df8a81c7ce7f7aace9a834e4c3dddbca97e']
    time.sleep(delayToStart)
    started = verifyAppServerInContainerIDStarted(instanceID, host, username)
    if not started:
        logging.error(f"AppServer instance {instanceName} from {image} cannot start in the alloted time")
        removeForceContainer(host=host, username=username, instanceName=instanceName)
        return None
    return instanceID

def checkAppServerForErrors(instanceID, host, username):
    remoteCmd = f"{docker} ps --quiet --filter id={instanceID}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    if not lines:
        logging.warning("AppServer container {instanceID} is not running").format(instanceID=instanceID)
        return False

    remoteCmd = f"{docker} logs --tail=200 {instanceID}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    errPattern = re.compile('^.+ERROR')
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.STDOUT)
    liblines = output.splitlines()
    for line in liblines:
        if errPattern.match(line):
            logging.error("AppServer errored: {line}".format(line=line))
            return False
    return True

def getCompCPUFromContainer(host, username, instanceID):
    logging.debug("Computing CompCPU for AppServer instance {instanceID}".format(instanceID=instanceID))
    # Check that the indicated container still exists
    remoteCmd = f"{docker} ps -a --quiet --filter id={instanceID}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    if not lines:
        logging.warning("AppServer instance {instanceID} does not exist.".format(instanceID=instanceID))
        return math.nan

    threadTime = 0.0
    remoteCmd = f"{docker} logs --tail=200 {instanceID}" # I need to capture stderr as well
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.STDOUT)
    liblines = output.splitlines()
    compTimePattern = re.compile("^Time spent in compilation thread =(\d+) ms")
    for line in liblines:
        #print(line)
        m = compTimePattern.match(line)
        if m:
            threadTime += float(m.group(1))
    return threadTime if threadTime > 0 else math.nan

def getAppServerStartupTime(host, username, containerID, userStartTimeMs, curlProcess):
    logging.debug("Computing startup time for AppServer instance {instanceID}".format(instanceID=containerID))
    # userStartTimeMs is the time when the user launched the container
    startupTime = math.nan
    firstResponseTime = math.nan
    # Check that the indicated container still exists
    remoteCmd = f"{docker} ps -a --quiet --filter id={containerID}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    if not lines:
        logging.warning("AppServer instance {instanceID} does not exist.".format(instanceID=containerID))
        return startupTime, firstResponseTime

    # Use "docker inspect" to get the time when container was initialized
    remoteCmd = f"{docker} inspect {containerID}" # I need to capture stderr as well
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.STDOUT)
    lines = output.splitlines()
     # "StartedAt": "2025-10-23T15:01:39.342994166Z",
    startatPattern = re.compile("StartedAt.+T(\d+):(\d+):(\d+)\.(\d\d\d)")
    containerStartAtTime = 0
    for line in lines:
        m = startatPattern.search(line)
        if m:
            containerStartAtTime = (int(m.group(2)) * 60 + int(m.group(3)))*1000 + int(m.group(4))
            if containerStartAtTime < userStartTimeMs:
                containerStartAtTime = containerStartAtTime + 3600*1000 # add one hour
            break
    if containerStartAtTime == 0:
        logging.warning("Container startAt time could not be retrieved")

    remoteCmd = f"{docker} logs --tail=200 {containerID}" # I need to capture stderr as well
    cmd = f"ssh {username}@{host} \"{remoteCmd}\"" if username else remoteCmd
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.STDOUT)
    liblines = output.splitlines()

    # The very first line may include a timestamp in the form of "15:01:39.472491267" representing the time when the appServer started
    appServerStart = 0
    if len(liblines) > 0:
        firstLine = liblines[0]
        timePattern = re.compile("(\d+):(\d+):(\d+)\.(\d\d\d)")
        m = timePattern.match(firstLine)
        if m:
            appServerStart = (int(m.group(2)) * 60 + int(m.group(3)))*1000 + int(m.group(4))
            if appServerStart < userStartTimeMs:
                appServerStart = appServerStart + 3600*1000 # add one hour
        if appServerStart == 0:
            logging.warning("AppServer start time could not be retrieved. First line is: " + firstLine)

    # 2025-10-17 23:30:25,616 INFO  [io.quarkus] (main) rest-crud 1.0 on JVM (powered by Quarkus 3.20.3) started in 3.462s. Listening on: http://0.0.0.0:9090
    readyPattern = re.compile("([\d\- :,]*) INFO .+ rest-crud .+ started in \d+\.\d+s. Listening on")
    for line in liblines:
        m = readyPattern.match(line)
        if m:
            timestamp = m.group(1)
            # 2025-10-17 23:30:25,616
            pattern1 = re.compile("(\d+)-(\d+)-(\d+),? (\d+):(\d+):(\d+),(\d\d\d)")
            m1 = pattern1.match(timestamp)
            if m1:
                # Ignore the hour to avoid time zone issues
                endTime = (int(m1.group(5)) * 60 + int(m1.group(6)))*1000 + int(m1.group(7))
                if endTime < userStartTimeMs:
                    endTime = endTime + 3600*1000 # add one hour
                startupTime = float(endTime - userStartTimeMs)
                break
            else:
                logging.warning("Quarkus timestamp is in the wrong format: {timestamp}".format(timestamp=timestamp))
                break
    if startupTime == math.nan:
        logging.warning("AppServer instance {containerID} did not start correctly".format(containerID=containerID))

    endTimeCurlMs = 0
    if curlProcess: # We intend to get the firstResponse time
        outs = None
        errs = None
        try:
            outs, errs = curlProcess.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            curlProcess.kill()
            outs, errs = curlProcess.communicate()

        if curlProcess.returncode == 0:
            firstResponseTimeString = outs #curlProcess.stdout
            # my output coming from `date +"%H:%M:%S.%N"` is in the format HH:MM:SS.NNNNNNNNN
            logging.debug(f"Computing first response time. End time is {firstResponseTimeString}")
            tpattern = re.compile("(\d+):(\d+):(\d+)\.(\d\d\d)")
            m2 = tpattern.match(firstResponseTimeString)
            if m2:
                # Ignore the hour to avoid time zone issues
                endTimeCurlMs = (int(m2.group(2))*60 + int(m2.group(3)))*1000 + int(m2.group(4))
                if endTimeCurlMs < userStartTimeMs:
                    endTimeCurlMs = endTimeCurlMs + 3600*1000 # add one hour
            else:
                logging.warning("First response time is in the wrong format: {firstResponseTimeString}".format(firstResponseTimeString=firstResponseTimeString))

    responseTime1 = 0
    responseTime2 = 0
    responseTime3 = 0
    if endTimeCurlMs > 0:
        responseTime1 = endTimeCurlMs - userStartTimeMs
        if containerStartAtTime > 0:
            responseTime2 = endTimeCurlMs - containerStartAtTime
        if appServerStart > 0:
            responseTime3 = endTimeCurlMs - appServerStart

    return startupTime, responseTime1, responseTime2, responseTime3

def startJITServer(serverImage):
    # -v /tmp/vlogs:/tmp/JITServer_vlog -e TR_Options=\"statisticsFrequency=10000,vlog=/tmp/vlogs/vlog.txt\"
    #JITOptions = "\"statisticsFrequency=10000,verbose={compilePerformance},verbose={JITServer},vlog=/tmp/vlogs/vlog.txt\""
    JITOptions = ""
    serverOpts = JITServerOptions
    otherOpts = ""
    if JITServerUseEncryption:
        serverOpts = serverOpts + f" -XX:JITServerSSLKey={SecretsDirInContainer}/key.pem -XX:JITServerSSLCert={SecretsDirInContainer}/cert.pem "
        otherOpts = f"--mount type=bind,source={KeyAndCertificateDir},destination={SecretsDirInContainer}"

    remoteCmd = f"{docker} run -d -p 38400:38400 -p 38500:38500 --rm --memory=4G {JITServerAffinity} {netOpts} {otherOpts} {JITServerExtraOptions} -e TR_PrintCompMem=1 -e TR_PrintCompStats=1 -e TR_PrintCompTime=1 -e TR_PrintCodeCacheUsage=1 -e TR_PrintJITServerCacheStats=1 -e TR_PrintJITServerIPMsgStats=1 -e _JAVA_OPTIONS='{serverOpts}' -e TR_Options={JITOptions} --name {JITServerContainerName} {serverImage} jitserver"
    cmd = f"ssh {JITServerUsername}@{JITServerMachine} \"{remoteCmd}\"" if JITServerUsername else remoteCmd
    logging.info(f"Start JITServer: {cmd}")
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)

def stopJITServer():
    # find the ID of the container, if any
    remoteCmd = f"{docker} ps --quiet --filter name={JITServerContainerName}"
    cmd = f"ssh {JITServerUsername}@{JITServerMachine} \"{remoteCmd}\"" if JITServerUsername else remoteCmd
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    for containerID in lines:
        remoteCmd = f"{docker} stop {containerID}"
        cmd = f"ssh {JITServerUsername}@{JITServerMachine} \"{remoteCmd}\"" if JITServerUsername else remoteCmd
        logging.debug(f"Stop JITServer: {cmd}")
        output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)

################################ cleanup ######################################
def cleanup():
    removeContainersFromImage(loadGenMachine, loadGenUsername, loadgenImage)
    for config in configs:
        removeContainersFromImage(appServerMachine, username, config["image"])
    stopContainersFromImage(dbMachine, dbUsername, dbImage)
    stopContainersFromImage(JITServerMachine, JITServerUsername, JITServerImage)

def applyLoad(duration, numClients):
    remoteCmd = ""
    if useJMeterForLoad:
        # Run jmeter remotely
        remoteCmd = f"{docker} run -d {loadGenNetOpts} {loadGenAffinity} -e JTHREAD={numClients} -e JDURATION={duration} -e JHOST={appServerMachine} -e JPORT={appServerPort} --name {loadGenContainerName} {loadgenImage}"
    else: # use wrk
        remoteCmd = f"{docker} run -d {loadGenNetOpts} {loadGenAffinity} -e THREADS={numClients} -e CONNECTIONS={numClients} -e DURATION={duration} -e SUT_IP={appServerMachine} -e SUT_PORT={appServerPort} --name {loadGenContainerName} {loadgenImage}"

    cmd = f"ssh {loadGenUsername}@{loadGenMachine} \"{remoteCmd}\"" if loadGenUsername else remoteCmd
    logging.info("Apply load: {cmd}".format(cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    return output

def getThroughputWrk(lines):
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
    return thr, 0

def getThroughputJMeter(lines):
    # Find the last line that contains
    # summary = 110757 in    30s = 3688.6/s Avg:    12 Min:     0 Max:   894 Err:     0 (0.00%)
    # or
    # summary = 233722 in 00:02:00 = 1947.4/s Avg:     0 Min:     0 Max:   582 Err:     0 (0.00%)
    elapsedTime = 0
    throughput = 0
    errs = 0
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
        #totalTransactions = float(m.group(1)) # First group is the total number of transactions/pages that were processed
        throughput = float(m.group(3)) # Third group is the throughput value
        errs = int(m.group(4))  # Fourth group is the number of errors
    else: # Check the second pattern
        pattern = re.compile('summary =\s+(\d+) in\s+(\d\d):(\d\d):(\d\d) =\s+(\d+\.\d+)/s.+Err:\s*(\d+)')
        m = pattern.match(lastSummaryLine)
        if m:
            #totalTransactions = float(m.group(1))  # First group is the total number of transactions/pages that were processed
            # Next 3 groups are the interval of time that passed
            throughput = float(m.group(5))  # Fifth group is the throughput value
            errs = int(m.group(6)) # Sixth group is the number of errors
    # Compute the peak throughput as a sliding window of 3 consecutive entries
    #peakThr = sum(slidingWindow)/len(slidingWindow)

    if errs > 0:
        logging.error("JMeter Errors: {n}".format(n=errs))
    return throughput, errs

def getThroughput():
    logging.debug("Getting throughput info...")
    remoteCmd = f"{docker} logs --tail=200 {loadGenContainerName}"
    cmd = f"ssh {loadGenUsername}@{loadGenMachine} \"{remoteCmd}\"" if loadGenUsername else remoteCmd
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.DEVNULL)
    lines = output.splitlines()
    thr, errs = getThroughputJMeter(lines) if useJMeterForLoad else getThroughputWrk(lines)
    return thr, errs


def stopLoad():
    remoteCmd = f"{docker} rm {loadGenContainerName}"
    cmd = f"ssh {loadGenUsername}@{loadGenMachine} \"{remoteCmd}\"" if loadGenUsername else remoteCmd
    logging.debug("Removing load generator: {cmd}".format(cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)

def runPhase(duration, numClients):
    logging.debug("Sleeping for {n} sec".format(n=delayBetweenRepetitions))
    time.sleep(delayBetweenRepetitions)

    output = applyLoad(duration, numClients)
    # Wait for load to finish
    remoteCmd = f"{docker} wait {loadGenContainerName}"
    cmd = f"ssh {loadGenUsername}@{loadGenMachine} \"{remoteCmd}\"" if loadGenUsername else remoteCmd
    logging.debug("Wait for {jmeter} to end: {cmd}".format(jmeter=loadGenContainerName, cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)

    # Read throughput
    thr, errs = getThroughput()

    stopLoad()
    if logging.root.level <= logging.DEBUG:
        print("Throughput={thr:7.1f}".format(thr=thr))
    if errs > 0:
        logging.error(f"JMeter encountered {errs} errors")

    return thr

def runBenchmarkOnce(image, javaOpts):
    # Will apply load in small bursts
    maxPulses = numRepetitionsOneClient + numRepetitions50Clients
    thrResults = [math.nan for i in range(maxPulses)] # np.full((maxPulses), fill_value=np.nan, dtype=np.float)
    rss, peakRss, cpu = math.nan, math.nan, math.nan
    curlProcess = None

    restoreDatabase(dbMachine, dbUsername, dbImage)

    # Start an external program called curl_loop in background
    # This program will keep sending requests to the app server until it responds with 200 once
    if getFirstResponseTime:
        curlCmd = f"{firstResponseHelperScript}"
        curlProcess = subprocess.Popen(shlex.split(curlCmd), universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    crtTime = datetime.datetime.now()
    userStartTimeMs = (crtTime.minute * 60 + crtTime.second)*1000 + crtTime.microsecond//1000

    instanceID = startAppServerContainer(host=appServerMachine, username=username, instanceName=containerName, image=image, port=appServerPort, jvmArgs=javaOpts, mountOpts=mountOpts, dbMachine=dbMachine)
    if instanceID is None:
        return thrResults, rss, peakRss, cpu

    # We know the app started successfuly

    if doApplyLoad:
        for pulse in range(maxPulses):
            if pulse >= numRepetitionsOneClient:
                cli = numClients
                duration = durationOfOneRepetition
            else:
                cli = 1
                duration = durationOfOneClient
            thrResults[pulse] = runPhase(duration, cli)

    # Collect RSS at end of run
    # When running the native image there is no java application
    serverPID = 0
    if "native" in image:
        serverPID = int(getMainPIDFromContainer(host=appServerMachine, username=username, instanceID=instanceID))
    else:
        serverPID = getJavaPIDFromContainer(host=appServerMachine, username=username, instanceID=instanceID)

    if serverPID > 0:
        rss, peakRss = getRss(host=appServerMachine, username=username, pid=serverPID)
        logging.debug("Memory: RSS={rss} MB  PeakRSS={peak} MB".format(rss=rss,peak=peakRss))
    else:
        logging.warning("Cannot get server PID. RSS will not be available")
    time.sleep(2)

    # If there were errors during the run, invalidate throughput results
    if doApplyLoad:
        if not checkAppServerForErrors(instanceID, appServerMachine, username):
            thrResults = [math.nan for i in range(maxPulses)] #np.full((maxPulses), fill_value=np.nan, dtype=np.float) # Reset any throughput values

    # stop container and read CompCPU
    rc = stopAppServerByID(appServerMachine, username, instanceID)
    cpu = getCompCPUFromContainer(appServerMachine, username, instanceID)
    # There are 3 types of first response time depending on how we decide on the starting point
    startTimeMillis, frt1, frt2, frt3 = getAppServerStartupTime(appServerMachine, username, instanceID, userStartTimeMs, curlProcess)
    removeForceContainer(host=appServerMachine, username=username, instanceName=containerName)

    # return throughput as an array of throughput values for each burst and also the RSS
    return thrResults, float(rss), float(peakRss), float(cpu/1000.0), startTimeMillis, frt1, frt2, frt3

####################### runBenchmarksIteratively ##############################
def runBenchmarkIteratively(numIter, image, javaOpts):
    # Initialize stats; 2D array of throughput results
    numPulses = numRepetitionsOneClient + numRepetitions50Clients
    thrResults = [] # List of lists
    rssResults = [] # Just a list
    peakRssResults = []
    cpuResults = []
    startupResults = []
    firstResponseResults1 = []
    firstResponseResults2 = []
    firstResponseResults3 = []

    # clear SCC if needed (by destroying the SCC volume)
    if doColdRun:
        clearSCC(appServerMachine, username)

    useJITServer = ("-XX:+UseJITServer" in javaOpts) or (instantOnRestore and ("-XX:+UseJITServer" in postRestoreOpts))
    if useJITServer:
        # If the JITServer image has been specifically provided, use that; otherwise use the image for test
        serverImage = JITServerImage if JITServerImage else image
        startJITServer(serverImage)
        time.sleep(2) # Give JITServer some time to start

    for iter in range(numIter):
        thrList, rss, peakRss, cpu, startupTime, frt1, frt2, frt3 = runBenchmarkOnce(image, javaOpts)
        lastThr = meanLastValues(thrList, numMeasurementTrials) # average for last N pulses
        print("Run {iter}: Thr={lastThr:6.1f} RSS={rss:6.0f} MB  PeakRSS={peakRss:6.0f} MB  CPU={cpu:6.1f} sec".
              format(iter=iter, lastThr=lastThr, rss=rss, peakRss=peakRss, cpu=cpu))
        thrResults.append(thrList) # copy all the pulses
        rssResults.append(rss)
        peakRssResults.append(peakRss)
        cpuResults.append(cpu)
        startupResults.append(startupTime)
        if frt1 > 0:
            firstResponseResults1.append(frt1)
        if frt2 > 0:
            firstResponseResults2.append(frt2)
        if frt3 > 0:
            firstResponseResults3.append(frt3)

    # print stats
    print(f"\nResults for image: {image} and opts: {javaOpts}")
    thrAvgResults = [math.nan for i in range(numIter)] # np.full((numIter), fill_value=np.nan, dtype=np.float)
    for iter in range(numIter):
        print("Run", iter, end="")
        for pulse in range(numPulses):
            print("\t{thr:7.1f}".format(thr=thrResults[iter][pulse]), end="")
        thrAvgResults[iter] = meanLastValues(thrResults[iter], numMeasurementTrials) #np.nanmean(thrResults[iter][-numMeasurementTrials:])
        print("\tAvg={thr:7.1f}  RSS={rss:7.0f} MB  PeakRSS={peakRss:7.0f} MB  CPU={cpu:7.1f} sec".
             format(thr=thrAvgResults[iter], rss=rssResults[iter], peakRss=peakRssResults[iter], cpu=cpuResults[iter]))

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
    print("\tAvg={avgThr:7.1f}  RSS={rss:7.0f} MB  PeakRSS={peakRss:7.0f} MB  CPU={cpu:7.1f} sec".
          format(avgThr=nanmean(thrAvgResults), rss=nanmean(rssResults), peakRss=nanmean(peakRssResults), cpu=nanmean(cpuResults)))

    printStats(thrAvgResults, "Thr stats:")
    printStats(rssResults, "RSS stats:")
    printStats(peakRssResults, "Peak RSS stats:")
    printStats(cpuResults, "CompCPU stats:")
    printStats(startupResults, "StartTime stats:")
    if getFirstResponseTime:
        printStats(firstResponseResults1, "FirstResp1 stats:")
        printStats(firstResponseResults2, "FirstResp2 stats:")
        printStats(firstResponseResults3, "FirstResp3 stats:")

    if useJITServer:
        stopJITServer()

############################ MAIN ##################################
def mainRoutine():
    if  len(sys.argv) < 2:
        print ("Program must have an argument: the number of iterations\n")
        sys.exit(-1)

    cleanup() # Clean-up from a previous possible bad run

    startDatabase(dbMachine, dbUsername, dbImage)

    if doColdRun:
        logging.warning("Will do a cold run before each set")

    for config in configs:
        runBenchmarkIteratively(numIter=int(sys.argv[1]), image=config["image"], javaOpts=config["args"])

    # Execute final clean-up step
    cleanup()

mainRoutine()
