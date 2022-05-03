#!/usr/bin/perl
# Script to measure startup of jboss in a Docker environment
use strict;
use warnings;
use Time::HiRes qw(gettimeofday);
use Time::Local;
use threads;
use threads::shared;

my $verbose          = 5; # 1 or 2, for increasing verbosity
my $doColdRun        = 1; # clear SCC and do a cold run first
my $reportWS         = 1; # report working set size
my $reportCPU        = 1;
my $reportColdRunStats=1;
my $waitTimeToStart  = 10;
my $doOnlyColdRuns   = 0;
my $useDifferentOptionsForCold = 0;
my $optionsForCold = "-Xquickstart  -Xjit:enableInterpreterProfiling -Xmx256m -Xshareclasses:enableBCI,name=liberty -Xscmx60M -Xscmaxaot4m";

my $netHost          = ""; # add "--net=host" to docker run command line
my $containerMemLimit= "-m=2G";
my $useSCCVolume     = 1; # use docker volumes for persisting SCC
my $SCCVolumeName    = "sccvolume";

# Command for launching the JITServer remotely
my $useJITServer     = 0;
my $JITServerMachine = "192.168.11.8";
my $JITServerUserName= "root";
my $JITServerImage   = "jitaas-server:dev0506";
my $startJITServer   = "ssh ${JITServerUserName}\@${JITServerMachine} \"docker run -d -p 38400:38400 --rm --name server $JITServerImage\" ";
my $stopJITServer    = "ssh ${JITServerUserName}\@${JITServerMachine} \"docker stop server\" ";
# Note: passwordless login must be enabled

my $dbMachine        = "192.168.10.7"; # DB2 needs to be active
my $dbUserName       = "db2inst1";

# JBoss configuration
my $AppServerDir     = "/opt/jboss/jboss"; # This is the directory in the container instance
my $sccInstanceDir   = "$AppServerDir/.classCache";      # Location of the shared class cache in the instance
my $logsHostBaseDir  = "/tmp/vlogs";   # Each instance will add the ID of the instance; used for vlogs. e.g. /vlogs.1
my $logsInstanceDir  = "/tmp/vlogs"; # this is crt dir where vlogs appear; but need -Xjit:verbose={...},vlog=/tmp/vlogs -v /tmp/vlogs:/tmp/vlogs

my @WASServers =
   (  # define one port for each instance
    {
    instanceName      => "jboss",  # This is the container instance name
    port              => 8081,
    port1             => 8443,
    wasCPUAffinity    => "4-7",
    }
   );
#####################################
my $numWASInstances  = scalar(@WASServers); # all instances are collocated on the same machine

#These will be determined on demand
my $mountOpt = $useSCCVolume ? "--mount type=volume,src=$SCCVolumeName,target=$sccInstanceDir" : "";
# Note: do not use --rm in "docker run" because we want the container to exist after shutting down liberty to read stderr
my $wasStartupScript = "docker run $netHost -d -v /dev/urandom:/dev/random $mountOpt";
my $dockerStopScript = "docker stop";
my $wasStopScript    = "docker exec INSTANCE_PLACEHOLDER $AppServerDir/bin/jboss-cli.sh --connect command=:shutdown";

my $stderrFile       = "err.txt"; # stderr for the docker command

#Footprint
my $workingSetFile   = "/tmp/WS.txt";
my $doMemAnalysis    = 0; # copy smaps, generate javacore and coredump
my $dirForMemAnalysisFiles = "/tmp";
my $javacoreDir      = $logsInstanceDir; # use the usr/servers/.logs directory
my $argsForFootprintAnalysis = "-Dcom.ibm.dbgmalloc=true -Xdump:none -Xdump:none  -Xdump:directory=$javacoreDir -Xdump:java:events=user";
my $extraArgsForMemAnalysis  = "-Xdump:none -Xdump:java:events=user,file=${dirForMemAnalysisFiles}/javacore.\%pid.\%seq.txt -Xdump:system:events=user,file=${dirForMemAnalysisFiles}/core.\%pid.\%seq.dmp";
#-Xdump:java:events=user,file=$javacoreDir/javacore.\%pid.\%seq.txt -Xdump:system:events=user,file=$javacoreDir/core.\%pid.\%seq.dmp";
# use jdmpview -core core.dmp   and then !printallcallsites > callsites.txt
#my $extraArgsForMemAnalysis = "-Dcom.ibm.dbgmalloc=true -Xdump:none -Xdump:java:events=user,file=${dirForMemAnalysisFiles}/javacore.\%pid.\%seq.txt -Xdump:system:events=user,file=${dirForMemAnalysisFiles}/core.\%pid.\%seq.dmp";
my $javacoreCmd      = "docker exec INSTANCE_PLACEHOLDER kill -3 PID_PLACEHOLDER";
my $copySmapsCmd     = "docker exec INSTANCE_PLACEHOLDER cp /proc/PID_PLACEHOLDER/smaps $javacoreDir",

############################################################

my @jvmOptions = (
	#"-server -Xms1303m -Xmx1303m -XX:MetaspaceSize=96M -XX:MaxMetaspaceSize=256m -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true  --add-exports=java.base/sun.nio.ch=ALL-UNNAMED --add-exports=jdk.unsupported/sun.misc=ALL-UNNAMED --add-exports=jdk.unsupported/sun.reflect=ALL-UNNAMED",
    #"-Xshareclasses:name=jboss,cacheDir=/opt/jboss/jboss/.classCache -Xmx1303m -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true  --add-exports=java.base/sun.nio.ch=ALL-UNNAMED --add-exports=jdk.unsupported/sun.misc=ALL-UNNAMED --add-exports=jdk.unsupported/sun.reflect=ALL-UNNAMED",
    #"-Xms1303m -Xmx1303m -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true  --add-exports=java.base/sun.nio.ch=ALL-UNNAMED --add-exports=jdk.unsupported/sun.misc=ALL-UNNAMED --add-exports=jdk.unsupported/sun.reflect=ALL-UNNAMED",
    #"-Xmx1303m -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true  --add-exports=java.base/sun.nio.ch=ALL-UNNAMED --add-exports=jdk.unsupported/sun.misc=ALL-UNNAMED --add-exports=jdk.unsupported/sun.reflect=ALL-UNNAMED",
 
    #"",
    #"-server -Xms1303m -Xmx1303m -XX:MetaspaceSize=96M -XX:MaxMetaspaceSize=256m -Dcom.acmeair.repository.type=mongo -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true",
    #"-server -Xmx1303m -XX:MetaspaceSize=96M -XX:MaxMetaspaceSize=256m -Dcom.acmeair.repository.type=mongo -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true",

    #"",
    #"-Xms1303m -Xmx1303m -Dcom.acmeair.repository.type=mongo -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true",
    #"-Xmx1303m -Dcom.acmeair.repository.type=mongo -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true",

    #"-Xshareclasses:name=jboss,cacheDir=/opt/jboss/jboss/.classCache",
    "-Xaot:forceaot -Xshareclasses:name=jboss,cacheDir=/opt/jboss/jboss/.classCache -Xms1303m -Xmx1303m -Dcom.acmeair.repository.type=mongo -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true",
    #"-Xshareclasses:name=jboss,cacheDir=/opt/jboss/jboss/.classCache -Xmx1303m -Dcom.acmeair.repository.type=mongo -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true",


    #"-server -Xmx1303m -XX:MetaspaceSize=96M -XX:MaxMetaspaceSize=256m -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true  --add-exports=java.base/sun.nio.ch=ALL-UNNAMED --add-exports=jdk.unsupported/sun.misc=ALL-UNNAMED --add-exports=jdk.unsupported/sun.reflect=ALL-UNNAMED",


    #"-Xjit:queueSizeThresholdToDowngradeOptLevelDuringStartup=0 -Xshareclasses:name=jboss,cacheDir=/opt/jboss/jboss/.classCache -Xms1303m -Xmx1303m -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true  --add-exports=java.base/sun.nio.ch=ALL-UNNAMED --add-exports=jdk.unsupported/sun.misc=ALL-UNNAMED --add-exports=jdk.unsupported/sun.reflect=ALL-UNNAMED",

    #"-Xshareclasses:name=jboss,cacheDir=/opt/jboss/jboss/.classCache -Xms1303m -Xmx1303m -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true  --add-exports=java.base/sun.nio.ch=ALL-UNNAMED --add-exports=jdk.unsupported/sun.misc=ALL-UNNAMED --add-exports=jdk.unsupported/sun.reflect=ALL-UNNAMED",
    #"-Xshareclasses:name=jboss,cacheDir=/opt/jboss/jboss/.classCache -Xmx1303m -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true  --add-exports=java.base/sun.nio.ch=ALL-UNNAMED --add-exports=jdk.unsupported/sun.misc=ALL-UNNAMED --add-exports=jdk.unsupported/sun.reflect=ALL-UNNAMED",
    #"-Xshareclasses:name=jboss,cacheDir=/opt/jboss/jboss/.classCache -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true  --add-exports=java.base/sun.nio.ch=ALL-UNNAMED --add-exports=jdk.unsupported/sun.misc=ALL-UNNAMED --add-exports=jdk.unsupported/sun.reflect=ALL-UNNAMED",
    #"-Xmx1303m -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true  --add-exports=java.base/sun.nio.ch=ALL-UNNAMED --add-exports=jdk.unsupported/sun.misc=ALL-UNNAMED --add-exports=jdk.unsupported/sun.reflect=ALL-UNNAMED",
    #"-Xms1303m -Xmx1303m -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true  --add-exports=java.base/sun.nio.ch=ALL-UNNAMED --add-exports=jdk.unsupported/sun.misc=ALL-UNNAMED --add-exports=jdk.unsupported/sun.reflect=ALL-UNNAMED",

    #""
);

# These are irrelevant because the JDK must be embedded in the container
my @images = (
	    #"jboss-dt8:11", #  This is the name of the docker image
            #"jboss-dt8-openj9:11",
	    #"localhost/jboss-acmeair:11",
	    #"localhost/jboss-acmeair-temurin:11"
	    "localhost/jboss-acmeair-openj9:11",
          );
#------------- main ---------------


# TODO: Verify docker daemon is running. /var/run/docker.pid  /usr/bin/docker
# Run docker --version  and I should have some output: Docker version 1.8.2, build 0a8c2e3
#
if (@ARGV != 2) {
    die "need number of iterations in a batch and number of batches\n";
}
my $numIter = shift(@ARGV);
my $numBatches = shift(@ARGV);
$| = 1; # auto-flush stdout
print "doColdRun=$doColdRun\n";
print "Use only cold runs\n" if $doOnlyColdRuns;
print "Use --net=host\n" if ($netHost eq "--net=host");

# set JIT options as environment variable
$ENV{TR_PrintCompTime} = 1;
$ENV{TR_PrintCompStats} = 1;


if ($useJITServer) {
    print("Starting JITServer with $startJITServer\n");
    `$startJITServer`;
    sleep 5;
}

my @results; # This is my multidimensional array with results
# Initialize my array of results
for (my $imageID=0; $imageID < scalar(@images); $imageID++) {
    for (my $optID=0; $optID < scalar(@jvmOptions); $optID++) {
        for (my $batchID=0; $batchID < $numBatches; $batchID++) {
            for (my $runID=0; $runID < $numIter; $runID++) {
                # each entry is a hash with various values, like startup time, CPU time, footprint etc
                $results[$imageID][$optID][$batchID][$runID] = {
                                                               startupTime => 0,
                                                               compCPUTime => 0,
                                                               footprint   => 0,
                                                               processTime => 0,
                                                               };
            }
        }
    }
}

my $globalBatchID = 0;
for ($globalBatchID=0; $globalBatchID < $numBatches; $globalBatchID++) {
    print "batch $globalBatchID\n";
    my $optID = 0;
    foreach my $jvmOpts (@jvmOptions) {
        my $imageID = 0;
        foreach my $imageName (@images) {
            runBenchmarkIteratively($numIter, $imageName, $jvmOpts, $results[$imageID][$optID][$globalBatchID]);
            $imageID++;
        }
        $optID++;
    }
}
PrintAllPerformanceNumbers(\@results);


if ($useJITServer) {
    print("Stopping JITServer\n");
    `$stopJITServer`;
}


# How to use   &UpdateStats($value, @array);
# First param is the value of the sample; the second array is a param with
# the following configuration:   my @arrayName= (0,0,0,10000000,0); # samples, sum, max, min, sumsq
sub UpdateStats {
    $_[1] += 1;           # update samples
    $_[2] += $_[0];       # update sum
    $_[5] += $_[0]*$_[0]; # update sumsq
    if ($_[0] > $_[3]) {  #update MAX
        $_[3] = $_[0];
    }
    if ($_[0] < $_[4]) {  #update MIN
        $_[4] = $_[0];
    }
}

# subroutine to print the stats for an array
sub PrintStats {
    my($text, $samples, $sum, $maxVal, $minVal, $sumsq, $median) = @_;
    my $confidenceInterval = 0;
    if ($samples > 0) {
        my $stddev = 0;
        if ($samples > 1) {
           my $variance = (($sumsq-$sum*$sum/$samples)/($samples-1));
           $stddev = sqrt($variance);
           $confidenceInterval = tdistribution($samples-1)*$stddev/sqrt($samples)*100.0/($sum/$samples);
        }
        printf "$text\tavg=%4.0f\tmin=%4.0f\tmax=%4.0f\tstdDev=%4.1f\tmaxVar=%3.1f%%\tconfInt=%.2f%%\tsamples=%3d\n", $sum/$samples, $minVal, $maxVal, $stddev, (100.0*$maxVal/$minVal-100.0), $confidenceInterval, $samples;
    }
}

sub tdistribution {
    my $degreesOfFreedom = shift;
    my @table = (6.314, 2.92, 2.353, 2.132, 2.015, 1.943, 1.895, 1.860, 1.833, 1.812, 1.796, 1.782, 1.771, 1.761, 1.753, 1.746, 1.740, 1.734, 1.729, 1.725);

    if ($degreesOfFreedom < 1) { return -1;}
    elsif ($degreesOfFreedom <= 20) { return $table[$degreesOfFreedom-1]; }
    else {
        if($degreesOfFreedom < 30)   { return 1.697; }
        if($degreesOfFreedom < 40)   { return 1.684; }
        if($degreesOfFreedom < 50)   { return 1.676; }
        if($degreesOfFreedom < 60)   { return 1.671; }
        if($degreesOfFreedom < 70)   { return 1.667; }
        if($degreesOfFreedom < 80)   { return 1.664; }
        if($degreesOfFreedom < 90)   { return 1.662; }
        if($degreesOfFreedom < 100)  { return 1.660; }
        return 1.65;
    }
}

#---------------------------------- eliminateOutliers ----------------------------
# Receives an array reference with results.
# We compute the lower and and upper quartile, then compute the interquartile
# range (IQR=Q3-Q1) and the lower (Q1 - 1.5*IQR) and upper (Q3 + 1.5*IQR) fences.
# The two fences are returned to the caller in an array with 2 elements. The third element is the median.
# Needs at least 4 data points
#---------------------------------------------------------------------------------
sub computeOutlierFences {
    my $arrayRef = shift;
    # Sort the array
    my @sortedData = sort  { $a <=> $b } @{$arrayRef};  # convert the array reference into an array and sort it
    my $numValues = scalar(@sortedData);
    # If less than 4 values, include all data points
    if ($numValues < 4) {
        return ($sortedData[0], $sortedData[$numValues-1]);
    }
    # Find the median
    my $midPosition = int $numValues/2;
    my $halfCount;
    my $Q1;
    my $Q2;
    my $Q3;
    if ($numValues % 2) { # odd number
        $Q2 = $sortedData[$midPosition];
        # include median (Q2) in the calculation of Q1 and Q3
        $halfCount = $midPosition+1; # round up
    } else { # even number
        $Q2 = ($sortedData[$midPosition-1] + $sortedData[$midPosition])/2;
        $halfCount = $midPosition;
    }
    # Find the quartiles:
    my $Q1position = int $halfCount/2;
    my $Q3position = $Q1position + $midPosition;
    if ($halfCount % 2) { # odd number
        $Q1 = $sortedData[$Q1position];
        $Q3 = $sortedData[$Q3position];
    } else {
        $Q1 = ($sortedData[$Q1position-1] + $sortedData[$Q1position])/2;
        $Q3 = ($sortedData[$Q3position-1] + $sortedData[$Q3position])/2;
    }
    # Compute the interquartile range
    my $IQR = $Q3 - $Q1;
    my $lowerFence = $Q1 - 3 * $IQR;
    my $upperFence  = $Q3 + 3 * $IQR;
    return ($lowerFence, $upperFence, $Q2);
}


#---------------------------------- PrintStatistics --------------------------------
# Parameters: (1) A text header to be printed;
#             (2) An array reference with valid results.
# Eliminates outliers and prints stats. Also prints the values that were eliminated
#-----------------------------------------------------------------------------------
sub PrintStatistics {
    # Extract parameters
    my $text = shift;
    my $dataArrayRef = shift;
    # Compute outlier values
    my ($lowerFence, $upperFence, $median) = computeOutlierFences($dataArrayRef);
    # Eliminate outliers
    my @outlierArray = ();
    my @stats  = (0,0,0,10000000,0);
    for (my $i=0; $i < scalar(@{$dataArrayRef}); $i++) {
        my $val = $dataArrayRef->[$i];
        if ($val < $lowerFence || $val > $upperFence) {
            push @outlierArray, $val;
        } else { # good value
            UpdateStats($val, @stats);
        }
    }
    PrintStats($text, @stats, $median);
    my $numOutliers = scalar(@outlierArray);
    if ($numOutliers > 0) {
        print "\tOutlier values: ";
        for (my $i=0; $i < $numOutliers; $i++) {
            print " " . $outlierArray[$i];
        }
        print "\n";
    }
}

#----------------------- PrintAllPerformanceNumbers ------------------------
sub PrintAllPerformanceNumbers {
    my $resultsRef = shift;

    for (my $imageID=0; $imageID < scalar(@images); $imageID++) {
        for (my $optID=0; $optID < scalar(@jvmOptions); $optID++) {
            print "Results for JDK=" . $images[$imageID] . " jvmOpts=" . $jvmOptions[$optID] . "\n";

            # Accumulate all valid scores in separate arrays
            my @startupTime = ();
            my @compCPUTime = ();
            my @footprint = ();
            my @processTime = ();
            for (my $batchID=0; $batchID < $numBatches; $batchID++) {
                my $runID = $doColdRun ? 1 : 0; # skip the cold runs
                for (; $runID < $numIter; $runID++) {
                    my $val;
                    $val = $resultsRef->[$imageID][$optID][$batchID][$runID]->{startupTime};
                    push @startupTime, $val  unless $val <= 0;
                    $val = $resultsRef->[$imageID][$optID][$batchID][$runID]->{compCPUTime};
                    push @compCPUTime, $val  unless $val <= 0;
                    $val = $resultsRef->[$imageID][$optID][$batchID][$runID]->{footprint};
                    push @footprint, $val  unless $val <= 0;
                    $val = $resultsRef->[$imageID][$optID][$batchID][$runID]->{processTime};
                    push @processTime,  $val  unless $val <= 0;
                }
            }
            # Process the arrays of valid results by eliminating outliers and printing stats on remaining data
            PrintStatistics("StartupTime", \@startupTime);
            PrintStatistics("Footprint",   \@footprint);
            PrintStatistics("CThreadTime", \@compCPUTime);
            PrintStatistics("ProcessTime", \@processTime);

            # Do the same for cold runs
            if ($doColdRun && $reportColdRunStats) {
                # Accumulate all valid scores in separate arrays
                my @startupTime = ();
                my @compCPUTime = ();
                my @footprint = ();
                my @processTime = ();
                for (my $batchID=0; $batchID < $numBatches; $batchID++) {
                    my $val = $resultsRef->[$imageID][$optID][$batchID][0]->{startupTime};
                    push @startupTime, $val  unless $val <= 0;
                    $val = $resultsRef->[$imageID][$optID][$batchID][0]->{compCPUTime};
                    push @compCPUTime, $val  unless $val <= 0;
                    $val = $resultsRef->[$imageID][$optID][$batchID][0]->{footprint};
                    push @footprint, $val  unless $val <= 0;
                    $val = $resultsRef->[$imageID][$optID][$batchID][0]->{processTime};
                    push @processTime,  $val  unless $val <= 0;
                }
                # Process the arrays of valid results by eliminating outliers and printing stats on remaining data
                print "Stats for cold run:\n";
                PrintStatistics("StartupTime", \@startupTime);
                PrintStatistics("Footprint",   \@footprint);
                PrintStatistics("CThreadTime", \@compCPUTime);
                PrintStatistics("ProcessTime", \@processTime);
            }
        }
    }
}

sub clearSCC {
    print "+ Clearing the SCC\n" if $verbose >= 2;
    my $answer = `docker volume rm --force $SCCVolumeName`;
    print "+ $answer\n" if $verbose >= 2;
}

##########################################################################
sub deleteContainer {
    my $WAS_ID = shift;
    my $instanceName = $WASServers[$WAS_ID]{instanceName};

    # The stop command is not needed, but we issue it just in case
    my $dockerStopCommand = "$dockerStopScript $instanceName";
    print "+ About to issue the docker stop command: $dockerStopCommand\n" if $verbose >= 2;
    my $answer = `$dockerStopCommand`;
    print "$answer\n" if $verbose >= 3;
    $answer = `docker rm $instanceName`;
    print $answer if $verbose >= 3;
}

##################################################################################
# This works with Docker proper, but not with podman
sub getJavaPIDOnHostForDockerOnly {
    my $WAS_ID = shift;
    my $instanceName = $WASServers[$WAS_ID]{instanceName};
    my $javaPid = 0;

    # docker top will show 2 processes; we are looking for the one with bin/java
    #UID                 PID                 PPID                C                   STIME               TTY                 TIME                CMD
    #mpirvu              12004               11985               0                   20:47               pts/0               00:00:00            /bin/sh /opt/jboss/jboss/bin/standalone.sh -b 0.0.0.0
    #mpirvu              12186               12004               75                  20:47               pts/0               00:00:33            /usr/lib/jvm/java/bin/java -D[Standalone]
    my @lines = `docker top $instanceName`;
    foreach my $line (@lines) {
        if ($line =~ /\S+\s+(\d+)\s+(\d+)\s+.+bin\/java /) {
            $javaPid = $1;
            last;
        }
    }
    return $javaPid;
}

# This works based on knowledge that there is a scrript which calls the java process
# so, we use docker inspect to find the PID of the script and then we find the child of this PID
sub getJavaPIDOnHost {
    my $WAS_ID = shift;
    my $instanceName = $WASServers[$WAS_ID]{instanceName};
    my $javaPid = 0;
    my $ppid = `docker inspect --format '{{.State.Pid}}' $instanceName`;
    my @lines = `ps -eo ppid,pid,cmd --no-headers`;
    foreach my $line (@lines) {
        if ($line =~ /(\d+)\s+(\d+)\s+(\S+)/) {
	    if ($1 == $ppid) {
		return $2;
            }
        }
    }
    return 0;
}



########################## getStartupTimestampFromLogFile ################
sub getStartupTimestampFromLogFile {
    my $WAS_ID = shift;
    my $instanceName = $WASServers[$WAS_ID]{instanceName};

    # Check the log with   docker logs --tail=20  lib1
    my @lines = `docker logs --tail=100 $instanceName`;
    my $line = "";
    foreach $line (@lines) {
        print $line if $verbose >= 5;
        # 00:47:38,854 INFO  [org.jboss.as] (Controller Boot Thread) WFLYSRV0025: JBoss EAP 7.3.0.GA (WildFly Core 10.1.2.Final-redhat-00001) started in 6652ms - Started 1286 of 1473 services (360 services are lazy, passive
        if ($line =~ /(\d\d):(\d\d):(\d\d)\,(\d\d\d).+JBoss.+started in/) {
            my $min = $2;
            my $sec = $3;
            my $millis = $4;
            return ($min*60 + $sec)*1000 + $millis;
        }
    }
    print "Timestamp is in wrong format:$line\n";
    return 0;
}


#######################################################################
sub stopAppServer {
    my $WAS_ID = shift;
    my $instanceName = $WASServers[$WAS_ID]{instanceName};
    my $serverStopScript = $wasStopScript;
    $serverStopScript =~ s/INSTANCE_PLACEHOLDER/$instanceName/;
    print "+ About to issue the server stop command: $serverStopScript\n" if $verbose >= 2;
    my $answer = `$serverStopScript 2>&1`;
    print $answer if $verbose >= 3;
}

##########################################################################
sub getCompilationTimeFromContainer {
    my $WAS_ID = shift;
    my $instanceName = $WASServers[$WAS_ID]{instanceName};

    print "+ Computing time in compilation threads for $instanceName\n" if $verbose >= 2;
    my @lines = `docker logs --tail=100 $instanceName 2>&1`; # Info we are looking for is in stderr of this command
    my $threadTime = 0;
    foreach my $line (@lines) {
        if ($line =~ /Time spent in compilation thread =(\d+) ms/) {  #stats about compilation thread time
            $threadTime += $1; # add time from all compilation threads
            print $line if $verbose >= 5;
        }
    }
    return $threadTime;
}

###
sub getCpuTime {
    my $javaPid = shift;
    my @output = `top -n1 -b -p $javaPid`;
    # read last two lines
    my $numLines = scalar(@output);
    my $headers = $output[$numLines-2];
    my $values = $output[$numLines-1];
    # Make sure the 11-th filed is what we want
    if ($headers =~ /PID\s+USER\s+PR\s+NI\s+VIRT\s+RES\s+SHR\s+S\s+%CPU\s+%MEM\s+TIME\+/) {
       if ($values =~ /\d+\s+\w+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\w+\s+\S+\s+\S+\s+(\d+):(\d+)\.(\d+)/) {
          return $1*60*1000 + $2*1000 + $3*10;
       }
       else {
          return 0;
       }
    }
    else {
       print "Warning: top output is not in expected format\n";
       print "Found: $headers\n";
       return 0;
    }
}

#####################################################################
sub getLargePagesFootprint {
    # read the /proc/meminfo file
    open MEMINFO, "/proc/meminfo" or die "Cannot open /proc/meminfo\n";
    my $totalHugePages = 0;
    my $freeHugePages = 0;
    my $hugePageSize = 0;
    while (<MEMINFO>) {
            if (/HugePages_Total:\s+(\d+)/) {
                $totalHugePages = $1;
            }elsif (/HugePages_Free:\s+(\d+)/) {
                $freeHugePages = $1;
            }elsif (/Hugepagesize:\s+(\d+) kB/) {
                $hugePageSize = $1;
            }
    }
    close MEMINFO;
    return ($totalHugePages - $freeHugePages)*$hugePageSize;
}

######################################################################
sub computeWorkingSet {
    my $javaPid = shift; # This is the pid on the host

    # docker stats --no-stream lib1    prints something like this
    #CONTAINER           CPU %               MEM USAGE / LIMIT     MEM %               NET I/O               BLOCK I/O
    #lib1                0.16%               445.4 MB / 16.72 GB   2.66%               10.39 kB / 9.454 kB   50.63 MB / 16.62 MB

    my $ws = `ps  -orss,vsz,cputime --no-headers --pid $javaPid`;
    if ($ws =~ /^(\d+)\s+(\d+)\s+(\d+):(\d+):(\d+)/) {
        return $1;
    } else {
        print "Warning: WS for PID ${javaPid} is 0\n";
        return 0;
    }
}

###########################################################################
sub startAppServerInstance {
    my $imageName = shift;
    my $jvmOpts  = shift;
    my $extraArgs= shift;
    my $WAS_ID   = shift;

    my $instanceName = $WASServers[$WAS_ID]{instanceName};
    my $vlogDir      = $logsHostBaseDir . "$WAS_ID";

    my $port  = $WASServers[$WAS_ID]{port};
    my $port1 = $WASServers[$WAS_ID]{port1};
    # TODO: how do I add extra args? Currently I have EXTRA_ARGS env var, but nobody looks at them

    #--- start Liberty ---

    #docker run -d -p 9080:9080 -p 9443:9443 -v /home/mpirvu/sdks/pxa6480sr2fp10-20151224_02:/opt/ibm/wlp/JRE  -e JVM_ARGS='-Xshareclasses:none -Xmx1024m -Xms1024m' --name lib1 libertytest1
    my $affinity = $WASServers[$WAS_ID]{wasCPUAffinity};
    my $cmd = "${wasStartupScript} $containerMemLimit --cpuset-cpus='${affinity}' -p ${port}:${port} -p ${port1}:${port1} -e  JAVA_OPTS='${jvmOpts}' -e TR_PrintCompTime=1 --name $instanceName ${imageName}";
    print "+ Executing script $cmd\n" if $verbose >= 2;
    my $errFile = "$stderrFile.${WAS_ID}";

    my $output = `$cmd 2> $errFile`;
    print $output if $verbose > 3;
    # We get the prompt immediately
}


########################### verifyAppServerStarted(was_id, imageName) ######################
# Searches for "is ready to run a smarter planet"
# If Liberty started we return the PID on host
sub verifyAppServerStarted {
    my $WAS_ID = shift;
    my $imageName = shift;
    print("Verify Appserver started...\n") if $verbose>=4;
    my $instanceName = $WASServers[$WAS_ID]{instanceName};
    # we should make sure the container is running and the log shows "is ready to run a smarter planet"
    my @runingContainers = `docker ps`;
    #CONTAINER ID        IMAGE               COMMAND                  CREATED             STATUS              PORTS                                            NAMES
    #c79065d97271        libertytest1        "/opt/ibm/wlp/bin/ser"   15 minutes ago      Up 15 minutes       0.0.0.0:9080->9080/tcp, 0.0.0.0:9443->9443/tcp   lib1
    # search for a line showing "Up and instanceName"
    my $running = 0;
    foreach my $line (@runingContainers) {
        if ($line =~ /$imageName.+Up.+$instanceName/) {
            $running = 1;
            last;
        }
    }
    if ($running == 0) {
        print "ERROR: Container $instanceName is not running\n";
        if ($verbose >= 5) {
            print "Running containers:\n";
            foreach my $line (@runingContainers) {
               print "$line\n";
            }
        }
        return 0; # return failure
    }

    # Check the log with   docker logs --tail=20  lib1
    # Also check that "[ERROR   ] CWWKO0221E"  does not appear
    my $success = 0;
    my $iterations = 0;
    while ($iterations < 20) {
        $iterations++;
        my @lines = `docker logs --tail=100 $instanceName`;
        foreach my $line (@lines) {
            print $line if $verbose >= 5;
            if ($line =~ /ERROR/) {
                print "ERROR during starting JBoss for instance $instanceName";
                print $line;
                return 0;
            }
            #JBoss EAP 7.3.0.GA (WildFly Core 10.1.2.Final-redhat-00001) started in 5333ms
            if ($line =~ /JBoss.+ started in (\d+)ms/) {
                $success = 1;
            }
        }
        if ($success) {
            last;
        }
        sleep 1;
    }
    print("JBoss has finished starting\n") if $verbose >=5;
    my $wasPid = getJavaPIDOnHost($WAS_ID) unless !$success;
    print("JBoss PID on host = $wasPid\n") if $verbose >=4;

    return $wasPid;
}

#############################################################################
sub runBenchmarkOnce {
    my $waitTime = shift; # extract parameter
    my $imageName = shift;
    my $jvmOpts  = shift;
    my $appArgs  = shift;
    my $collectFootprintDiagnostic = shift;

    #--- possibly clear SCC ---
    if ($doOnlyColdRuns) {
        clearSCC();
    }

    #--- read the number of large pages in use for footprint purposes ---
    my $largePageFootprintStart = 0;
    my $usedMemoryStart = 0;
    if ($reportWS) {
        $largePageFootprintStart = getLargePagesFootprint();
        #$usedMemoryStart = getUsedMemory();   ---> Revisit this
    }

    # read start time
    my $startTime = 0.0;
    my $startTimeString = `date +"%H:%M:%S:%N"`;

    if ($startTimeString =~ /^(\d\d):(\d\d):(\d\d):(\d\d\d)/) {
        $startTime = ($2*60.0 + $3)*1000 + $4;
    } else {
        print("Cannot parse current time");
        exit;
    }

    #--- start N instances of Liberty in parallel ---
    for (my $instanceID=0; $instanceID < $numWASInstances; $instanceID++) {
        startAppServerInstance($imageName, $jvmOpts, $appArgs, $instanceID);
    }
    print "Will sleep for $waitTime seconds\n" if $verbose >= 2;
    sleep $waitTime; # leave some time to allow instances to start

    #--- verify all of them started successfully ---
    my @libertyPids;
    my $allStarted = 1; # optimistic assumption
    for (my $instanceID=0; $instanceID < $numWASInstances; $instanceID++) {
        $libertyPids[$instanceID] = verifyAppServerStarted($instanceID, $imageName);
        if ($libertyPids[$instanceID] == 0) { # failed to start
            $allStarted = 0;
	    print("Instance $instanceID failed to start\n");
        }
    }
    if ($allStarted == 0) {
        # I have to abandon this run.
        # Shutdown the instances that started correctly
        for (my $instanceID=0; $instanceID < $numWASInstances; $instanceID++) {
            if ($libertyPids[$instanceID]) {
            # TODO: shutdown
            }
        }
        return (0, 0, 0, 0, 0);
    }

    #--- Collect working set ---
    my $sumWS = 0;
    my $usedPhysicalMemory = 0;
    if ($reportWS) {
        for (my $instanceID=0; $instanceID < $numWASInstances; $instanceID++) {
            my $ws = computeWorkingSet($libertyPids[$instanceID]);
            $sumWS += $ws;
        }
        # getUsedMemory() - $usedMemoryStart; #TODO
        # Now get the large pages working set
        my $largePageFootprintStop = getLargePagesFootprint();
        my $lpWS = ($largePageFootprintStop - $largePageFootprintStart);
        $sumWS += $lpWS;
        print "LP WorkingSet=$lpWS\n" if ($verbose >= 2 && $lpWS > 0);
        print "Total WorkingSet=$sumWS\n" if $verbose >= 2;
        #print "Used physical memory=$usedPhysicalMemory\n" if $verbose >= 1;
    }

    #--- Collect startup times by looking at the largest timestamp of an instance ---
    my $startupTime = 0;
    for (my $instanceID=0; $instanceID < $numWASInstances; $instanceID++) {
        my $endTime = getStartupTimestampFromLogFile($instanceID);
        if ($endTime == 0) { # failure
           next;
        }
        if ($endTime < $startTime) {
            $endTime += 3600 * 1000; # add an extra hour (in millis)
        }
        my $sTime = $endTime - $startTime;

        $startupTime = $sTime unless ($startupTime >= $sTime);
    }

    #--- Collect process CPU ---
    my $cpuTime = 0;
    for (my $instanceID=0; $instanceID < $numWASInstances; $instanceID++) {
         $cpuTime += getCpuTime($libertyPids[$instanceID]);
    }


    #--- stop all appserver instances ---
    print "+ Shutting down AppServer instances\n" if $verbose >= 2;
    my $compTime = 0;
    for (my $instanceID=0; $instanceID < $numWASInstances; $instanceID++) {
        stopAppServer($instanceID); # Note that this will change the state of the container to exited
        my $threadTime = getCompilationTimeFromContainer($instanceID);
        print "Total Time spent in compilation threads for instance $instanceID = $threadTime\n" if $verbose >= 2;
        $compTime += $threadTime;
        deleteContainer($instanceID);
    }
    print "+ Finished executing benchmark\n" if $verbose >= 2;
    return ($startupTime, $sumWS, $compTime, $cpuTime, $usedPhysicalMemory);
}

#--------------------------------------------------
sub runBenchmarkIteratively {
    my $numIter = shift; # first param is the number of iterations
    my $imageName = shift;
    my $jvmOpts = shift;
    my $resultsArrayRef = shift;

    if ($doMemAnalysis) {
	   $jvmOpts = $jvmOpts . " $extraArgsForMemAnalysis";
    }

    print "Will do $numIter iterations with imageName=$imageName jvmOpts=$jvmOpts\n" if $verbose >= 1;

    # clear SCC if needed
    clearSCC() if ($doColdRun);

    my $isColdRun = 0;

    # iterate n times
    for (my $i=0; $i < $numIter; $i++) {
        unlink("err.txt");

        # execute one iteration
        my $appArgs;
        my $javaOpts;
        # Add extra options for cold runs
        if ($doOnlyColdRuns || ($doColdRun && $i == 0)) {
           $appArgs = "--clean";
           $javaOpts = $useDifferentOptionsForCold ? $optionsForCold : $jvmOpts;
           $isColdRun = 1;
        }
        else {
           $appArgs = "";
           $javaOpts = $jvmOpts;
        }
        my $doFootprintDiagnostic = $doMemAnalysis && ($i == $numIter-1);
        my ($startTime, $workingSet, $threadTime, $cpuTime, $usedPhysicalMemory)
           = runBenchmarkOnce($isColdRun?$waitTimeToStart*3:$waitTimeToStart, $imageName, $javaOpts, $appArgs, $doFootprintDiagnostic); # run application
        print "+ startTime=$startTime ms\n" if $verbose >= 1;
        if ($startTime > 0) {
           $resultsArrayRef->[$i]->{startupTime} = $startTime;
           if ($cpuTime > 0) {
               $resultsArrayRef->[$i]->{processTime} = $cpuTime;
	           print "ProcessCPU=$cpuTime\n" if $verbose >= 1;
            }
	        if ($threadTime > 0) {
                $resultsArrayRef->[$i]->{compCPUTime} = $threadTime;
                print "CompThreadTime=$threadTime\n" if $verbose >= 1;
            }
            if ($workingSet > 0) {
                $resultsArrayRef->[$i]->{footprint} = $workingSet;
                print "WorkingSet=$workingSet\n" if $verbose >= 1;
            }
        }
    }
}

