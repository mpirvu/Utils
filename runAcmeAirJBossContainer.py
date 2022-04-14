import logging # https://www.machinelearningplus.com/python/python-logging-guide/
import shlex, subprocess
import time # for sleep
import re # for regular expressions
import numpy as np
import sys # for number of arguments
#import threading

# Set level to level=logging.DEBUG, level=logging.INFO or level=WARNING reduced level of verbosity
logging.basicConfig(level=logging.WARNING, format='%(asctime)s :: %(levelname)s :: %(message)s',)

################### Benchmark configuration #################
doColdRun       = True  # when True we clear the SCC before the first run
appServerHost   = "192.168.1.9"
username        = "mpirvu" # for connecting remotely to the SUT
instanceName    = "jboss"
appServerPort   = "8080"
appServerHttpsPort = "8443"
cpuAffinity     = "0-3"
memLimit        = "2G"
delayToStart    = 10 # seconds; waiting for the AppServer to start before checking for valid startup
############### JBoss configuration #####################
appServerDir    = "/opt/jboss/jboss" # This is the directory in the container instance
sccInstanceDir  = f"{appServerDir}/.classCache" # Location of the shared class cache in the instance
useSCCVolume    = True  # set to true to have a SCC mounted in a volume
SCCVolumeName   = "scc_volume"
mountOpts       = f"--mount type=volume,src={SCCVolumeName},target={sccInstanceDir}" if useSCCVolume  else ""
############# Mongo CONFIG ##############
mongoHost       = "192.168.1.7"
mongoUser       = "mpirvu"
mongoImage      = "mongo-acmeair"
mongoPropertiesFile = '' # not used for JBoss version of AcmeAir
loadDatabaseCmd = f"curl --ipv4 --silent --show-error http://{appServerHost}:{appServerPort}/acmeair/rest/info/loader/load?numCustomers=10000"
############### JMeter CONFIG ###############
numUsers        = 999
jmeterContainerName = "jmeter"
jmeterImage     = "jmeter-jboss-acmeair:4.0"
jmeterMachine   = "192.168.1.9"
jmeterUsername  = "mpirvu"
jmeterAffinity  = "0-7"
protocol        = "https" # http or https
################ Load CONFIG ###############
numRepetitionsOneClient = 0
numRepetitions50Clients = 2
durationOfOneClient     = 30 # seconds
durationOfOneRepetition = 240 # seconds
numClients              = 50
delayBetweenRepetitions = 10
numMeasurementTrials    = 1 # Last N trials are used in computation of throughput

jvmOptions = [
    "",
    #"-Xmx1G",
    #"-Xms1G -Xmx1G",
]

appImages = [
    "jboss-acmeair-openj9:11",
]

def stopContainersFromImage(host, username, imageName):
    # Find all running containers from image
    remoteCmd = f"docker ps --quiet --filter ancestor={imageName}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\""
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    for containerID in lines:
        remoteCmd = f"docker stop {containerID}"
        cmd = f"ssh {username}@{host} \"{remoteCmd}\""
        print("Stopping container: ", cmd)
        output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)

def removeContainersFromImage(host, username, imageName):
    # First stop running containers
    stopContainersFromImage(host, username, imageName)
    # Now remove stopped containes
    remoteCmd = f"docker ps -a --quiet --filter ancestor={imageName}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\""
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    for containerID in lines:
        remoteCmd = f"docker rm {containerID}"
        cmd = f"ssh {username}@{host} \"{remoteCmd}\""
        print("Removing container: ", cmd)
        output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)

# start mongo on a remote machine
def startMongo():
    remoteCmd = f"docker run --rm -d --net=host --name mongodb {mongoImage} --nojournal"
    cmd = f"ssh {mongoUser}@{mongoHost} \"{remoteCmd}\""
    logging.info("Starting mongo: {cmd}".format(cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.STDOUT)
    time.sleep(2)
    remoteCmd = "docker exec mongodb mongorestore --drop /AcmeAirDBBackup"
    cmd = f"ssh {mongoUser}@{mongoHost} \"{remoteCmd}\""
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.STDOUT)
    if logging.root.level <= logging.DEBUG:
        print(output)

def stopMongo():
    # find the ID of the container, if any
    remoteCmd = "docker ps --quiet --filter name=mongodb"
    cmd = f"ssh {mongoUser}@{mongoHost} \"{remoteCmd}\""
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    for containerID in lines:
        remoteCmd = f"docker stop {containerID}"
        cmd = f"ssh {mongoUser}@{mongoHost} \"{remoteCmd}\""
        logging.debug("Stopping mongo: {cmd}".format(cmd=cmd))
        output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)

def loadDatabase(host, username):
    cmd = f"ssh {username}@{host} \"{loadDatabaseCmd}\""
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.STDOUT)
    time.sleep(1)
    # Expect something like "Loaded flights and 10000 customers in 6.282 seconds"
    outputPattern = re.compile("^Loaded .+in (\d+.\d+) seconds")
    m = outputPattern.match(output)
    assert m, "Response from curl when loading the database is not in expected format: {line}".format(line=output)
    loadTime = float(m.group(1))
    assert loadTime > 0.0, "Loading the database was not successful"
    logging.debug(f"{output}")

# This works based on knowledge that there is a script which calls the java process
# so, we use docker inspect to find the PID of the script and then we find the child of this PID
def getMainPIDFromContainer(host, username, instanceID):
    remoteCmd = "docker inspect --format='{{.State.Pid}}' " + instanceID
    cmd = f"ssh {username}@{host} \"{remoteCmd}\""
    try:
        output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
        lines = output.splitlines()
        ppid = lines[0]
        remoteCmd = "ps -eo ppid,pid,cmd --no-headers"
        cmd = f"ssh {username}@{host} \"{remoteCmd}\""
        output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
        lines = output.splitlines()
        pattern = re.compile("(\d+)\s+(\d+)\s+(\S+)")
        for line in lines: # There should be two lines (maybe header too)
            m = pattern.match(line)
            if m:
                if ppid == m.group(1): # matching parent
                    return m.group(2)
        return 0
    except:
        return 0
    return 0


# Given a PID, return RSS and peakRSS for the process
def getRss(host, username, pid):
    _scale = {'kB': 1024, 'mB': 1024*1024, 'KB': 1024, 'MB': 1024*1024}
    # get pseudo file  /proc/<pid>/status
    filename = "/proc/" + pid + "/status"
    remoteCmd = f"cat {filename}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\""
    try:
        s = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
        #lines = s.splitlines()
    except IOError as ioe:
        logging.warning("Cannot open {filename}: {msg}".format(filename=filename,msg=str(ioe)))
        return [0, 0]  # wrong pid?
    i =  s.index("VmRSS:") # Find the position of the substring
    # Take everything from this position till the very end
    # Then split the string 3 times, taking first 3 "words" and putting them into a list
    tokens = s[i:].split(None, 3)
    if len(tokens) < 3:
        return [0, 0]  # invalid format
    rss = int(tokens[1]) * _scale[tokens[2]] // 1048576 # convert value to bytes and then to  MB

    # repeat for peak RSS
    i =  s.index("VmHWM:")
    tokens = s[i:].split(None, 3)
    if len(tokens) < 3:
        return [0, 0]  # invalid format
    peakRss = int(tokens[1]) * _scale[tokens[2]] // 1048576 # convert value to bytes and then to MB
    return rss, peakRss


def clearSCC(host, username):
    logging.info("Clearing SCC")
    remoteCmd = f"docker volume rm --force {SCCVolumeName}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\""
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    #lines = output.splitlines()


'''
return True if AppServer inside given container ID has started successfully; False otherwise
'''
def verifyAppServerInContainerIDStarted(instanceID, host, username):
    remoteCmd = f"docker ps --quiet --filter id={instanceID}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\""
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    if not lines:
        logging.warning("AppServer container {instanceID} is not running").format(instanceID=instanceID)
        return False

    remoteCmd = f"docker logs --tail=100 {instanceID}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\""
    errPattern = re.compile('^.+ ERROR ')
    #readyPattern = re.compile(".+is ready to run a smarter planet")
    # 19:10:46,911 INFO  [org.jboss.as] (Controller Boot Thread) WFLYSRV0025: JBoss EAP 7.3.0.GA (WildFly Core 10.1.2.Final-redhat-00001) started in 4703ms - Started 444 of 668 services (374 services are lazy, passive or on-demand)
    readyPattern = re.compile('^(.+) INFO .+ JBoss .+ started in')

    for iter in range(10):
        output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.STDOUT)
        liblines = output.splitlines()
        for line in liblines:
            m = errPattern.match(line)
            if m:
                logging.error("AppServer container {instanceID} errored while starting: {line}").format(instanceID=instanceID,line=line)
                return False
            m1 = readyPattern.match(line)
            if m1:
                if logging.root.level <= logging.INFO:
                    print(line)
                return True # True means success
        time.sleep(1) # wait 1 sec and try again
        logging.warning("Checking again")
    return False

def checkAppServerForErrors(instanceID, host, username):
    remoteCmd = f"docker ps --quiet --filter id={instanceID}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\""
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    if not lines:
        logging.warning("AppServer container {instanceID} is not running").format(instanceID=instanceID)
        return False

    remoteCmd = f"docker logs --tail=200 {instanceID}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\""
    errPattern = re.compile('^.+ERROR')
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True, stderr=subprocess.STDOUT)
    liblines = output.splitlines()
    for line in liblines:
        if errPattern.match(line):
            logging.error("AppServer errored: {line}".format(line=line))
            return False
    return True

def stopAppServerByID(host, username, containerID):
    remoteCmd = f"docker ps --quiet --filter id={containerID}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\""
    logging.debug("Stopping container {containerID}".format(containerID=containerID))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    if not lines:
        logging.warning("AppServer instance {containerID} does not exist. Might have crashed".format(containerID=containerID))
        return False
    remoteCmd = f"docker stop {containerID}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\""
    subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    return True

def startAppServerContainer(host, username, instanceName, image, port, httpsport, cpus, mem, jvmArgs, mongoHost, mongoPropertiesFile):
    remoteCmd = f"docker run -d --rm --cpuset-cpus={cpus} -m={mem} {mountOpts} -e JAVA_OPTS='{jvmArgs}' -v /tmp/vlogs:/tmp/vlogs -v {mongoPropertiesFile}:/config/mongo.properties -e MONGO_HOST={mongoHost} -e TR_PrintCompStats=1 -e TR_PrintCompTime=1  -p {port}:{port} -p {httpsport}:{httpsport} --name {instanceName} {image}"
    cmd = f"ssh {username}@{host} \"{remoteCmd}\""
    logging.info("Starting AppServer instance {instanceName}: {cmd}".format(instanceName=instanceName,cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    lines = output.splitlines()
    assert lines, "Error: docker run output is empty".format(l=lines)
    assert len(lines) == 1, f"Error: docker run output containes several lines: {lines}"
    if logging.root.level <= logging.DEBUG:
        print(lines)
    instanceID = lines[0] # ['2ccae49f3c03af57da27f5990af54df8a81c7ce7f7aace9a834e4c3dddbca97e']
    time.sleep(delayToStart) # delay to let JBoss start
    started = verifyAppServerInContainerIDStarted(instanceID, host, username)
    if not started:
        logging.error("AppServer failed to start")
        stopAppServerByID(host, username, instanceID)
        return None
    return instanceID


# Run jmeter remotely
def applyLoad(duration, clients):
    port = appServerHttpsPort if protocol == "https" else appServerPort
    remoteCmd = f"docker run -d --cpuset-cpus={jmeterAffinity} -e JTHREAD={clients} -e JDURATION={duration} -e JUSERBOTTOM=0 -e JUSER={numUsers} -e JPORT={port} -e JPROTOCOL={protocol} -e JHOST={appServerHost} -e JRAMP=0 --name {jmeterContainerName} {jmeterImage}"
    cmd = f"ssh {jmeterUsername}@{jmeterMachine} \"{remoteCmd}\""
    logging.info("Apply load: {cmd}".format(cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)
    logging.debug(f"{output}")

def stopJMeter():
    remoteCmd = f"docker rm {jmeterContainerName}"
    cmd = f"ssh {jmeterUsername}@{jmeterMachine} \"{remoteCmd}\""
    logging.debug("Removing jmeter: {cmd}".format(cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)

def getJMeterSummary():
    logging.debug("Getting throughput info...")
    remoteCmd = f"docker logs --tail=100 {jmeterContainerName}"
    cmd = f"ssh {jmeterUsername}@{jmeterMachine} \"{remoteCmd}\""
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
    queue = []
    pattern1 = re.compile('summary \+\s+(\d+) in\s+(\d+\.*\d*)s =\s+(\d+\.\d+)/s.+Finished: 0')
    pattern2 = re.compile('summary \+\s+(\d+) in\s+(\d\d):(\d\d):(\d\d) =\s+(\d+\.\d+)/s.+Finished: 0')
    for line in lines:
        # summary +  17050 in 00:00:06 = 2841.7/s Avg:     0 Min:     0 Max:    49 Err:     0 (0.00%) Active: 2 Started: 2 Finished: 0
        if line.startswith("summary +"):
            # Uncomment this line if we need to print rampup
            #print(line)
            m = pattern1.match(line)
            if m:
                thr = float(m.group(3))
                queue.append(thr)
            else:
                m = pattern2.match(line)
                if m:
                    thr = float(m.group(5))
                    queue.append(thr)

        if line.startswith("summary ="):
            lastSummaryLine = line

    pattern = re.compile('summary =\s+(\d+) in\s+(\d+\.*\d*)s =\s+(\d+\.\d+)/s.+Err:\s*(\d+)')
    m = pattern.match(lastSummaryLine)
    if m:
        # First group is the total number of transactions/pages that were processed
        totalTransactions = float(m.group(1))
        # Second group is the interval of time that passed
        elapsedTime = float(m.group(2))
        # Third group is the throughput value
        throughput = float(m.group(3))
        errs = int(m.group(4))
    else: # Check the second pattern
        pattern = re.compile('summary =\s+(\d+) in\s+(\d\d):(\d\d):(\d\d) =\s+(\d+\.\d+)/s.+Err:\s*(\d+)')
        m = pattern.match(lastSummaryLine)
        if m:
            # First group is the total number of transactions/pages that were processed
            totalTransactions = float(m.group(1))
            # Next 3 groups are the interval of time that passed
            elapsedTime = float(m.group(2))*3600 + float(m.group(3))*60 + float(m.group(4))
            # Fifth group is the throughput value
            throughput = float(m.group(5))
            errs = int(m.group(6))
    # Compute the peak throughput as the average of the last 3 throughput values
    peakThr = 0.0
    if len(queue) >= 3:
        queue = queue[-3:]
        peakThr = sum(queue)/3.0

    #print (str(elapsedTime), throughput, sep='\t')
    return throughput, elapsedTime, peakThr, errs


def runPhase(duration, clients):
    logging.debug("Sleeping for {n} sec".format(n=delayBetweenRepetitions))
    time.sleep(delayBetweenRepetitions)

    applyLoad(duration, clients)

    # Wait for load to finish
    remoteCmd = f"docker wait {jmeterContainerName}"
    cmd = f"ssh {jmeterUsername}@{jmeterMachine} \"{remoteCmd}\""
    logging.debug("Wait for {jmeter} to end: {cmd}".format(jmeter=jmeterContainerName, cmd=cmd))
    output = subprocess.check_output(shlex.split(cmd), universal_newlines=True)

    # Read throughput
    thr, elapsed, peakThr, errors = getJMeterSummary()

    stopJMeter()
    print("Throughput={thr:7.1f} duration={elapsed:6.1f} peak={peakThr:7.1f} errors={err:4d}".format(thr=thr,elapsed=elapsed,peakThr=peakThr,err=errors))
    if errors > 0:
        logging.error(f"JMeter encountered {errors} errors")
    return thr


def runBenchmarkOnce(image, javaOpts):
    instanceID = startAppServerContainer(host=appServerHost, username=username, instanceName=instanceName, image=image, port=appServerPort, httpsport=appServerHttpsPort, cpus=cpuAffinity, mem=memLimit, jvmArgs=javaOpts, mongoHost=mongoHost, mongoPropertiesFile=mongoPropertiesFile)
    if instanceID is None:
        return np.nan, np.nan

    # We know the app started successfuly
    loadDatabase(appServerHost, username)

    # Apply load in small bursts
    maxIterations = numRepetitionsOneClient + numRepetitions50Clients
    thrResults = np.full((maxIterations), fill_value=np.nan, dtype=np.float)
    for iter in range(maxIterations):
        thr = runPhase((durationOfOneRepetition if iter else durationOfOneClient), (numClients if iter else 1))
        thrResults[iter] = thr

    # Collect RSS at end of run
    serverPID = getMainPIDFromContainer(host=appServerHost, username=username, instanceID=instanceID)
    rss, peakRss = np.nan, np.nan
    if int(serverPID) > 0:
        rss, peakRss = getRss(host=appServerHost, username=username, pid=serverPID)
        logging.debug("Memory: RSS={rss} PeakRSS={peak}".format(rss=rss,peak=peakRss))
    else:
        logging.warning("Cannot get server PID. RSS will not be available")
    time.sleep(2)

    # If there were errors during the run, invalidate throughput results
    if not checkAppServerForErrors(instanceID, appServerHost, username):
        thrResults = np.full((maxIterations), fill_value=np.nan, dtype=np.float) # Reset any throughput values

    # stop container and read CompCPU
    rc = stopAppServerByID(appServerHost, username, instanceID)
    # container is already removed

    # return throughput as an numpy array of throughput values for each burst and also the RSS
    return thrResults, rss, peakRss


def runBenchmarkIteratively(numIter, image, javaOpts):
    # Initialize stats; 2D array of throughput results
    numPulses = numRepetitionsOneClient + numRepetitions50Clients
    thrResults = np.full((numIter, numPulses), fill_value=np.nan, dtype=np.float)
    rssResults = np.full((numIter), fill_value=np.nan, dtype=np.float)

    # clear SCC if needed (by destroying the SCC volume)
    if doColdRun:
        clearSCC(appServerHost, username)

    for iter in range(numIter):
        thr, rss, peakRss = runBenchmarkOnce(image, javaOpts)
        lastThr = np.nanmean(thr[-numMeasurementTrials:]) # average for last N pulses
        print(f"Run {iter}: Thr={lastThr} RSS={rss} MB  PeakRSS={peakRss} MB")
        thrResults[iter] = thr # copy all the pulses
        rssResults[iter] = rss

    # print stats
    print(f"\nResults for image: {image} and opts: {javaOpts}")
    thrAvgResults = np.full((numIter), fill_value=np.nan, dtype=np.float)
    for iter in range(numIter):
        print("Run", iter, end="")
        for pulse in range(numPulses):
            print("\t{thr:7.1f}".format(thr=thrResults[iter][pulse]), end="")
        thrAvgResults[iter] = np.nanmean(thrResults[iter][-numMeasurementTrials:])
        print("\tAvg={thr:7.1f}  RSS={rss:8.0} MB".format(thr=thrAvgResults[iter], rss=rssResults[iter]))

    verticalAverages = np.nanmean(thrResults, axis=0)
    print("Avg:", end="")
    for pulse in range(numPulses):
        print("\t{thr:7.1f}".format(thr=verticalAverages[pulse]), end="")
    print("\tAvg={avgThr:7.1f}  RSS={rss:7.1f} MB".format(avgThr=np.nanmean(thrAvgResults), rss=np.nanmean(rssResults)))
    # TODO: print stderr and CI

############################ MAIN ##################################
if  len(sys.argv) < 2:
    print ("Program must have an argument: the number of iterations\n")
    sys.exit(-1)

# Clean-up from a previous possible bad run
stopContainersFromImage(mongoHost, mongoUser, mongoImage)
#removeContainersFromImage(mongoHost, mongoUser, mongoImage)
for appServerImage in appImages:
    stopContainersFromImage(appServerHost, username, appServerImage)
stopContainersFromImage(jmeterMachine, jmeterUsername, jmeterImage)
removeContainersFromImage(jmeterMachine, jmeterUsername, jmeterImage)

time.sleep(1)
startMongo()
time.sleep(1)

if doColdRun:
    print("Will do a cold run before each set")

for jvmOpts in jvmOptions:
    for appServerImage in appImages:
        runBenchmarkIteratively(numIter=int(sys.argv[1]), image=appServerImage, javaOpts=jvmOpts)
stopMongo()