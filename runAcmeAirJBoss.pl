#!/usr/bin/perl
use strict;
use warnings;
use threads;
use threads::shared;


my $version = "1.0";

# DEFINE THESE VARIABLES PROPERLY PLEASE!!!
my $verbose          = 2; # 0-5 for increasing verbosity

# App server configuration
my $WASHost          = "192.168.11.9";
my $pathToWAS        = "/opt/IBM/jboss-eap-7.3";
$ENV{JBOSS_HOME}     =  "$pathToWAS";
my $logDir           = "${pathToWAS}/standalone/log";
my $logFile          = "$logDir/server.log";
my $errFile          = "$logDir/err.log"; # This is arbitrary chosen


my @WASServers =
   (  # define one port for each instance
      {
      instanceName      => "acmeair",
      port              => 8443,
      wasCPUAffinity    => "numactl -C 0,1,4,5",
      firstCustomerId   => 0,
      lastCustomerId    => 199,
      clientCPUAffinity => "numactl -C 0-7",
      },
   );
my $numWASInstances  = scalar(@WASServers); # all instances are collocated on the same machine

#These will be determined on demand
my $wasStartupScript = "$pathToWAS/bin/standalone.sh -c standalone_acmeair-mono.xml -b 0.0.0.0";
my $wasStopScript    = "$pathToWAS/bin/jboss-cli.sh --connect command=:shutdown";

# Name of the file containing the pid of the lanched process. Available only when launched in background
my $pidFileName      = "$pathToWAS/jboss-as-standalone.pid";
$ENV{JBOSS_PIDFILE}  = "$pidFileName";
$ENV{LAUNCH_JBOSS_IN_BACKGROUND} = 1;

my $sccDestroyParams = "-Xshareclasses:destroyall";
my $acmeairProperties= "$pathToWAS/usr/servers/acmeair/mongo.properties"; # Not used for JBoss


### Mongodb configuration ###
$ENV{ACMEAIR_PROPERTIES} = $acmeairProperties;
my $db2Machine    = "192.168.10.7";
my $db2User       = "mpirvu";
#my $db2RestoreCmd = "ssh $db2User\@$db2Machine 'docker exec mongodb mongorestore --drop /AcmeAirDBBackup'";
my $db2Start      = "ssh $db2User\@$db2Machine 'docker run --rm -d --net=host --name mongodb mongo-acmeair --nojournal'";
my $db2Stop       = "ssh $db2User\@$db2Machine 'docker stop mongodb'";
$ENV{MONGO_HOST} = "$db2Machine";
my $loadDatabaseCmd = "curl --ipv4 --silent --show-error http://$WASHost:8080/acmeair/rest/info/loader/load?numCustomers=10000";

### Client configuration ###
my $clientMachine    = "192.168.11.8";
my $clientUsername   = "root"; # only used when the client is remote
my $numUsers         = 999;
my $perfFileTemplate = "/tmp/daytrader.perf";
my $jmeterClientCmd  = "cd /opt/IBM/JMeter-4.0; AFFINITY_PLACEHOLDER bin/jmeter.sh -DusePureIDs=true -n -t AcmeAir-v5_context_acmair_withCancel.jmx -j /tmp/acmeair.stats.PLACEHOLDER_ID -JHOST=$WASHost -JPROTOCOL=https -JUSER=$numUsers -JRAMP=0";
my $localClientCmd   = $jmeterClientCmd;
my $remoteClientCmd  = "ssh $clientUsername\@$clientMachine \"$localClientCmd";
my $clientCmd        = $clientMachine eq "localhost" ? $localClientCmd : $remoteClientCmd;

#Profiling
my $doTprof          = 0;
my $doSCS            = 0;
my $doJLM            = 0;
my $tprofDataDir     = "/opt/IBM/AcmeAir/scripts";
my $tprofOpts        = "-agentlib:jprof=tprof,fnm=$tprofDataDir/log,pidx";
#my $scsOpts          = "-agentlib:jprof=sampling,scs_a2n,delay_start,logpath=$tprofDataDir";
my $scsOpts          = "-agentlib:jprof=callflow,delay_start,nometrics,logpath=$tprofDataDir";
#my $scsOpts          = "-agentlib:jprof=rtarcf,objectinfo,start,-ptt,logpath=$tprofDataDir";
my $jlmOpts          = "-agentlib:jprof=DELAY_START,logpath=$tprofDataDir";
my $tprofCmd         = "run.tprof  -r "; # profiling length is added automatically
                      #"run.tprof -M -m event -e NONHALTED_CYCLES -c 1000000 -s 1 -r "
my $scsCmd           = "rtdriver -l -c start 1 -c flush 160 -c reset "; # connect, wait 10 sec and profile for 120 sec
#my $scsCmd           = "rtdriver -l -c end 180";
my $jlmCmd           = "rtdriver -l -c jlmstartlite 10 -c jlmdump 60 -c jlmstop ";

my $doProfile        = $doTprof || $doSCS || $doJLM;
my $profileOpts      = $doTprof ? $tprofOpts : ($doSCS ? $scsOpts : ($doJLM ? $jlmOpts : ""));
my $profileCmd       = $doTprof ? $tprofCmd : ($doSCS ? $scsCmd : ($doJLM ? $jlmCmd : ""));

#Footprint
my $reportWS         = 1;
my $workingSetFile   = "/tmp/WS.txt";

my $doMemAnalysis    = 0;
my $dirForMemAnalysisFiles = "/tmp";
#my $extraArgsForMemAnalysis = "-Dcom.ibm.dbgmalloc=true -Xdump:none -Xdump:heap:events=user -Xdump:java:events=user,file=${dirForMemAnalysisFiles}/javacore.\%pid.\%seq.txt";
my $extraArgsForMemAnalysis = "-Dcom.ibm.dbgmalloc=true -Xdump:none -Xdump:system:events=user,file=${dirForMemAnalysisFiles}/core.\%pid.\%seq.dmp -Xdump:java:events=user,file=${dirForMemAnalysisFiles}/javacore.\%pid.\%seq.txt";


my $startupWaitTime  = 10; # in seconds
# use 1, 7, 60, 10 for the variables below for Hursley style (or 1 4 120 10 for something better)
# use 0, 1, 480, 0 for Torolab style
my $numRepetitionsOneClient = 0;
my $numRepetitions50Clients = 2;
my $durationOfOneClient     = 60; # seconds
my $durationOfOneRepetition = 180; # seconds
my $numClients              = 50;
my $delayBetweenRepetitions = 10;
my $numMeasurementTrials    = 1; # Last N trials are used in computation of throughput

my $doColdRun        = 0;
my $doOnlyColdRuns   = 0;
my $useSeparateOptionsForColdRun = 0;
my $optionsForColdRun= ""; # only takes effect if useSeparateOptionsForColdRun is defined above



#-agentlib:jprof=tprof,fnm=/opt/Dpiperf/bin/log,pidx
my @jitOptions = (
       "-Xms1303m -Xmx1303m -XX:MetaspaceSize=96M -XX:MaxMetaspaceSize=256m -Dcom.acmeair.repository.type=mongo -Djava.net.preferIPv4Stack=true -Djboss.modules.system.pkgs=org.jboss.byteman -Djava.awt.headless=true",
    );

my @jdks=(
      "/home/mpirvu/sdks/OpenJDK11U-jre_x64_linux_hotspot_11.0.14.1_1",
      #"/home/mpirvu/sdks/ibm-semeru-open-jre_x64_linux_11.0.14.1_1_openj9-0.30.1",
    );

use Config;
$Config{useithreads} or die('Recompile Perl with threads to run this program.');

if (@ARGV != 1) {
    die "need number of iterations\n";}
my $numIter = shift(@ARGV);

$| = 1; # auto-flush stdout

# define a shared variable that acts as a flag to stop the monitorint thread
my  $stopMonitoringThread :shared;
$stopMonitoringThread = 0;

sub printTimeAndString {
    my $msg = shift;
    my ($sec, $min, $hr, $day, $mon, $year) = localtime;
    printf("+ %02d:%02d:%02d %s\n", $hr, $min, $sec, $msg);
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


# subroutine to print the stats for an array
sub PrintStats {
    my($text, $samples, $sum, $maxVal, $minVal, $sumsq) = @_;
    my $confidenceInterval = 0;
    if ($samples > 0) {
        my $stddev = 0;
        if ($samples > 1) {
            my $variance = (($sumsq-$sum*$sum/$samples)/($samples-1));
            $stddev = sqrt($variance);
            $confidenceInterval = tdistribution($samples-1)*$stddev/sqrt($samples)*100.0/($sum/$samples);
        }
        printf "$text\tavg=%.2f\tmin=%.2f\tmax=%.2f\tstdDev=%.1f\tmaxVar=%.2f%%\tconfInt=%.2f%%\tsamples=%2d\n", $sum/$samples, $minVal, $maxVal, $stddev, (100.0*$maxVal/$minVal-100.0), $confidenceInterval, $samples;
    }
}

############################# Barrier implementation ##################################
my @arrived :shared;
my $direction :shared;
my $numParticipants :shared;

sub barrier_init {
   my $participants = shift;
   $numParticipants = $participants;
   $direction = 0;
   $arrived[0] = $participants;
   $arrived[1] = 0;
}
sub barrier {
    lock(@arrived);
    #decrement the number of threads that arrived at the barrier
    $arrived[$direction]--;
    # test if I am the last one
    if ($arrived[$direction] == 0) {
       # prepare for next barrier
       $direction = 1 - $direction;
       $arrived[$direction] = $numParticipants;
       # signall all the other threads to go
       cond_broadcast(@arrived);
    }
    else {
       cond_wait(@arrived);
    }
}

##################################################################
sub convertAffinityToNumactlFormat {
    my $tasksetAffinity = shift;
    # for each set bit, compute the CPU number
    my $cpuID = 0;
    my $addComma = 0;
    my $numactlAffinity = "";
    while ($tasksetAffinity) {
        if ($tasksetAffinity & 0x1) {
            if ($addComma) {
	        $numactlAffinity = $numactlAffinity . "," . $cpuID;
	    } else {
                $numactlAffinity = $numactlAffinity . $cpuID;
                $addComma = 1;
	    }
	}
	$cpuID++;
	$tasksetAffinity >>= 1;
    }
    return $numactlAffinity;
}

###################################################################
sub clearSCC {
    my $javaHome = shift;
    print "+ Clearing the SCC using $javaHome \n" if $verbose >= 2;
    my $answer = `$javaHome/bin/java $sccDestroyParams 2>&1`;
    print "+ $answer\n" if $verbose >= 2;
}


sub getLocalPerfFilename {
    my $WAS_ID =  shift;
    return "daytrader.perf.${WAS_ID}";
}

sub getPerfFilename {
    my $WAS_ID =  shift;
    return "${perfFileTemplate}.${WAS_ID}";
}

sub deleteResultsFile {
    my $WAS_ID =  shift;
    my $perfFile = getPerfFilename($WAS_ID);
    if ($clientMachine eq "localhost") {
        `rm $perfFile`;
    } else {
        `ssh $clientUsername\@$clientMachine "rm $perfFile"`;
    }
}

sub copyResultsFile {
    my $WAS_ID =  shift;
    my $perfFile = getPerfFilename($WAS_ID);
    my $localPerfFile = getLocalPerfFilename($WAS_ID);
    if ($clientMachine eq "localhost") {
        `cp $perfFile $localPerfFile`;
    } else {
	print("+ Copy  $clientUsername\@$clientMachine:$perfFile  to  $localPerfFile") if $verbose >= 5;
        my $res = `scp $clientUsername\@$clientMachine:$perfFile $localPerfFile`;
	print $res if $verbose >= 5;
    }
}

sub getPidFilename {
    return $pidFileName;
}

sub getLogFilename {
    return $logFile;
}

sub getErrFilename {
    return $errFile;
}

######################################################
sub getPIDFromPIDFile {
    my $pidFilename = shift;
    my $javaPid = 0;
    open PIDFILE, $pidFilename or return 0;
    $javaPid = <PIDFILE>;
    chomp($javaPid);
    close(PIDFILE);
    print "getPIDFromPIDFile returned PID=$javaPid\n" if $verbose >= 2;
    return $javaPid;
}


########################################################################
sub getWASPID {
   return getPIDFromPIDFile(getPidFilename());
}


##############################################################################
# verifyAppServerStarted(was_id) searches for some keywords in the log, like "is ready to run a smarter planet"
sub verifyAppServerStarted {
   my $wasPid = 0;
   my $success = 0;
   my $iterations = 0;
   my $logFile = shift;

   while ($iterations < 20) {
      $iterations++;
      open LOGFILE, $logFile or die "Cannot open log file $logFile\n";
      while (<LOGFILE>)  {
         # 2019-10-24 20:20:36,890 INFO  [org.jboss.as] (Controller Boot Thread) WFLYSRV0025: JBoss EAP 7.2.0.GA (WildFly Core 6.0.11.Final-redhat-00001) started in 10951ms - Started 1620 of 1805 services
         if (/^(.+) INFO .+ JBoss .+ started in/) {
            $success = 1;
            print "Found open for ebusiness\n" if $verbose>= 3;
            last;
         }
      }
      close LOGFILE;
      if ($success) {
         last;
      }
      sleep 1;
   }
   $wasPid = getWASPID() unless !$success;
   return $wasPid;
}



####################################################################
sub killProcess {
    my $pid = shift;
    # Is the process still around?
    my $exists = kill(0, $pid);
    if ($exists) {
        my $killCmd = ("$^O" =~ /MSWin/) ? "Taskkill /PID $pid /F" : "kill -9 $pid";
        print "+ Trying to kill process with cmd: $killCmd\n" if $verbose >= 2 ;
        my $answer = `$killCmd`;
        print $answer if $verbose >= 2;
        if (kill(0, $pid)) {
            print "Killing process $pid failed\n";
            kill "SIGKILL", $pid;
        }
    }
}




#######################################################################
sub stopAppServer {
    my $WAS_ID = shift;
    print "+ Parent: About to issue the stop command: $wasStopScript\n" if $verbose >= 2;
    `$wasStopScript 2>garbageStop.txt`;
    # when we return from this call, WAS should be down already
    sleep 1; # wait some more for the server to shutdown
    # make sure that stopServer finished correctly and that the server indeed is shutdown
    my $pid = getWASPID();
    if ($pid) {
       killProcess($pid);
    }
}

#####################################################################
sub getLargePagesFootprint {
    if ("$^O" =~ /MSWin/) {
        return 0; # large pages not tracked on windows
    }
    else {
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
}


######################################################################
sub computeWorkingSet {
    my $javaPid = shift;

    if ("$^O" =~ /MSWin/) {
        print "THIS IS NOT TESTED";
        my $ws = `GetWorkingSet.exe $javaPid`;
        if ($ws =~ /^WorkingSetSize:\s+(\d+) KB/) {
            return $1;
        } else {
            print "Warning: WS for PID ${javaPid} is 0\n";
            return 0;
        }
    }
    else {
        my $ws = `ps  -orss,vsz,cputime --no-headers --pid $javaPid`;
        if ($ws =~ /^(\d+)\s+(\d+)\s+(\d+):(\d+):(\d+)/) {
            return $1;
        } else {
            print "Warning: WS for PID ${javaPid} is 0\n";
            return 0;
        }
    }
}

######################################################################
sub getCompilationTimeFromFile {
    my $errFile = shift;
    print "Will open $errFile\n" if $verbose > 2;
    open(ERR, $errFile) || die "Cannot open ${errFile}: $!\n";
    my $threadTime = 0;
    while(<ERR>) {
        if (/Time spent in compilation thread =(\d+) ms/) {  #stats about compilation thread time
            $threadTime += $1; # add time from all compilation threads
        }
    }
    close(ERR);
    return $threadTime;
}

########################################################################
sub printCompilationStatsFromStderrFile {
    my $errFile = shift;
    print "Will open $errFile\n" if $verbose >= 5;
    open(ERR, $errFile) || die "Cannot open ${errFile}: $!\n";
    print "Compilation levels:\n";
    # Level=1 numComp=210
    while(<ERR>) {
        if (/Level=(\d)\s+numComp=(\d+)/) {
            print "Level=$1 numComp=$2\n";
        }
    }
    close(ERR);
}



####################################################################
sub getThroughput {
    my $WAS_ID = shift;
    my $localPerfFile = getLocalPerfFilename($WAS_ID);
    #my $refNumErrors = shift; # reference to number of errors
    my $runThroughput = 0;
    my $numErrors = 0;
    copyResultsFile($WAS_ID);

    #Page element throughput = 4936.399 /s   ==> for JIBE
    #Throughput        = 201.3 pages/sec     ==> for TradeClient
    open RESULTSFILE, $localPerfFile or die "Cannot open file  $localPerfFile\n";

    #2022-04-07 19:10:11,000 INFO o.a.j.r.Summariser: summary = 627085 in 00:01:36 = 6533.9/s Avg:     6 Min:     0 Max:  2765 Err:     0 (0.00%)
    #2022-04-07 19:10:17,000 INFO o.a.j.r.Summariser: summary +  56131 in 00:00:06 = 9355.2/s Avg:     5 Min:     0 Max:   139 Err:     0 (0.00%) Active: 50 Started: 50 Finished: 0
    #2022-04-07 19:13:45,104 INFO o.a.j.r.Summariser: summary = 2630855 in 00:05:10 = 8484.5/s Avg:     5 Min:     0 Max:  2765 Err:     0 (0.00%)


    # I am looking for the last like that has summary =
    while (<RESULTSFILE>)  {
        if (/summary =.+=\s+(\d+.\d+)\/s.+Err:\s*(\d+)\s+/) {
            $runThroughput = "$1";
            $numErrors = $2;
        }
    }
    close RESULTSFILE;

   #--- check validity of the result ---
   if ($runThroughput == 0) {
      print "Cannot get information from $localPerfFile\n";
      return -1;
   }
   return ($runThroughput, $numErrors);
}



####################################################################
sub composeClientStimulusCommand {
    my $WAS_ID = shift;
    my $runLength = shift;
    my $numClients = shift;
    # must replace PORT_PLACEHOLDER, AFFINITY_PLACEHOLDER, FIRST_CUST_PLACEHOLDER, LAST_CUST_PLACEHOLDER
    my $stimulusCommand = $clientCmd;
    my $port     = $WASServers[$WAS_ID]{port};
    my $affinity = $WASServers[$WAS_ID]{clientCPUAffinity};
    $stimulusCommand =~ s/PORT_PLACEHOLDER/$port/;
    $stimulusCommand =~ s/AFFINITY_PLACEHOLDER/$affinity/;
    $stimulusCommand =~ s/PLACEHOLDER_ID/$WAS_ID/;
    $stimulusCommand =~ s/PLACEHOLDER_ID/$WAS_ID/;

    my $perfFile = getPerfFilename($WAS_ID);

    my $redirect = ($clientMachine eq "localhost") ? " > $perfFile": " > $perfFile\"";
    $stimulusCommand = "$stimulusCommand -JDURATION=$runLength -JTHREAD=$numClients -JPORT=$port $redirect";

    return $stimulusCommand;
}

#---------------------- monotoring routine ---------------------
sub monitorFootprintCPU {
    my $wasPids = shift;
    my $largePageFootprintStart = shift;
    print "Started the monitoring thread\n" if $verbose >= 5;

    my @footprintStats = (0,0,0,10000000,0);
    # collect information every 10 seconds
    while ($stopMonitoringThread == 0) {
        my $sumWS = 0;
        for (my $instanceID=0; $instanceID < $numWASInstances; $instanceID++) {
            $sumWS += computeWorkingSet($wasPids->[$instanceID]);
        }
        # Now get the large pages working set
        if (!("$^O" =~ /MSWin/)) {
            $sumWS += (getLargePagesFootprint() - $largePageFootprintStart);
        }
        print "Instant footprint = $sumWS\n" if $verbose >= 5;
        UpdateStats($sumWS, @footprintStats);
        sleep 3;
    }

    # write the average into the output object
    my %monitoringObject = ();
    $monitoringObject{footprintMin} = $footprintStats[3];
    $monitoringObject{footprintMax} = $footprintStats[2];
    if ($footprintStats[0] != 0) {
        $monitoringObject{footprintAvg} = $footprintStats[1]/$footprintStats[0];
        print "Average instant footprint = " . $monitoringObject{footprintAvg} . "\n" if $verbose >=5;
    }
    print "Ending the monitoring thread\n" if $verbose >= 5;
    return \%monitoringObject; # return a reference to the hash
}


#------------------------ runPhase(wasId, numSecons, numClients, doProfile) ------------
sub runPhase {
    my $WAS_ID    = shift;
    my $runLength = shift;
    my $numClients= shift;
    my $doProfile = shift;

    unlink(getLocalPerfFilename($WAS_ID));
    deleteResultsFile($WAS_ID);
    my $sleepTime = $delayBetweenRepetitions;
    print "+ Sleeping for $sleepTime seconds\n" if $verbose >= 3;
    sleep($sleepTime);

    my $cmd = composeClientStimulusCommand($WAS_ID, $runLength, $numClients);
    print "WAS_ID=${WAS_ID}: Will execute: $cmd\n" if $verbose >= 3;
    printTimeAndString("Start load driver") if $verbose >= 2;
    # if profiling we need to do it in background
    if ($doProfile) {
        $ENV{IBMPERF_DATA_PATH} = $tprofDataDir; # setup the directory for the data
       if ($doTprof) {
            print "Please start profiling now";
            #print "Will profile and put data in $tprofDataDir\n" if $verbose >= 1;
            #print "$profileCmd $runLength\n" if $verbose >= 2;
            #system("$profileCmd $runLength &");
        } else { # SCS or JLM
            print "Will do scs/jlm and put data in $tprofDataDir\n" if $verbose >= 1;
            print "$profileCmd\n" if $verbose >= 2;
            system("$profileCmd &");
        }
    }
    `$cmd`;

    return getThroughput($WAS_ID);
}

##########################################################################
sub runPhaseConcurrently {
    my $runLength = shift;
    my $numClients= shift;
    my $doProfile = shift;
    my $sumThr = 0;
    my $sumErr = 0;
    if ($numWASInstances == 1) {
       ($sumThr, $sumErr) = runPhase(0, $runLength, $numClients, $doProfile);
    } else {
        my @myThreads;
        # Need to create multiple threads to run in parallel
        for (my $instanceID=0; $instanceID < $numWASInstances; $instanceID++) {
            # The context for the thread specify that we will return a list and not a scalar when we join
            $myThreads[$instanceID] = threads->create({'context' => 'list'}, \&runPhase, $instanceID, $runLength, $numClients, $doProfile);
        }
        # Wait for the threads to finish
        for (my $instanceID=0; $instanceID < $numWASInstances; $instanceID++) {
            my ($thr, $numErr) = $myThreads[$instanceID]->join();
            print "runPhaseConcurrently: Throughput for instance ${instanceID} = ${thr}\n" if $verbose >= 5;
            $sumThr += $thr;
            $sumErr += $numErr;
        }
    }
    print "runPhaseConcurrently: cummulative throughput = ${sumThr}  NumErrors = ${sumErr}\n" if $verbose >= 5;
    return ($sumThr, $sumErr);
}

###########################################################################
sub startWASInstance {
    my $waitTime = shift; # extract parameter
    my $javaHome = shift;
    my $jvmOpts  = shift;
    my $extraArgs= shift;
    my $WAS_ID   = shift;

    my $logFile      = getLogFilename($WAS_ID);
    my $errFile      = getErrFilename($WAS_ID); # no such thing for WAS
    my $pidFile      = getPidFilename($WAS_ID); # available only when launched in background

    #--- start application server ---
    my $affinity = $WASServers[$WAS_ID]{wasCPUAffinity};
    my $port     = $WASServers[$WAS_ID]{port};
    my $cmd;

    $cmd = "${affinity} ${wasStartupScript} ${extraArgs}";
    print "+ Executing script $cmd with JDK=${javaHome} and opts=$jvmOpts\n" if $verbose >= 2;

    my $childPid = 0;
    if ($childPid = fork) {
        # parent code here
        print "+ Parent: Waiting for $waitTime seconds to generate the log file...\n" if $verbose >= 2;
        sleep $waitTime; # Just a delay to finish startup...
        #--- Verify that the server has started ---
        my $was_pid = verifyAppServerStarted($logFile);
        if (! $was_pid) {
            print "cannot find PID file for Application Server. Aborting now...";
            exit 1;
        }
        print "+ PID for WAS is $was_pid\n" if $verbose >= 2;
        return $was_pid;
    }
    elsif (defined $childPid) {
        $ENV{JAVA_HOME} = $javaHome;
        $ENV{JAVA_OPTS} = $jvmOpts;
        $ENV{TR_PrintCompTime} = 1;
        $ENV{TR_PrintCompStats} = 1;
        $ENV{TR_PrintJaasMsgStats} = 1;
        $ENV{TR_PrintJITaaSMsgStats} = 1;
        $ENV{TR_PrintJITServerMsgStats} = 1;
        $ENV{TR_PrintJITServerAOTCacheStats} = 1;

        # Child process will start JBoss
        my $output = `$cmd 2> $errFile`;
        # we will block here until stop is called
        print $output if $verbose > 5;
        print "Child exiting\n" if $verbose > 2;
        exit 0;
    }
    else {
        print "fork failed. Exiting\n";
        exit 1;
    }
    return 0;
}

############################################################################
sub runBenchmarkOnce {
    my $waitTime = shift; # extract parameter
    my $javaHome = shift;
    my $jvmOpts  = shift;
    my $extraArgs= shift;
    my $doFootprintDiagnostic = shift;
    my $ref_resultsArray = shift;

    my $runThroughput = 0;
    #--- restore database ---
    #print "+ Restoring database\n" if $verbose >= 2;
    #my $answer = `$db2RestoreCmd`;
    #print "$answer\n" if $verbose >= 2;

    #--- possibly clear SCC ---
    if ($doOnlyColdRuns) {
        clearSCC($javaHome);
    }

    #--- read the number of large pages in use for footprint purposes ---
    my $largePageFootprintStart = 0;
    if ($reportWS) {
        $largePageFootprintStart = getLargePagesFootprint();
    }

    #--- start N instances of the application server ---
    my @wasPids;
    for (my $instanceID=0; $instanceID < $numWASInstances; $instanceID++) {
        $wasPids[$instanceID] = startWASInstance($waitTime, $javaHome, $jvmOpts, $extraArgs, $instanceID);
        my $res = `$loadDatabaseCmd`;
        print "$res\n" if $verbose > 3;
    }
    sleep $waitTime;
    #--- for each phase, run N instances of clients ---
    my $numSamples = 0;
    my $sumThr = 0;
    my $sumErr = 0;
    my $sumWS = 0;
    my $compTime = 0;
    print "+ Running TradeClient remotely\n" if $verbose >= 2;
    my $thr = 0;
    my $numErr = 0;
    #TODO: configure the number of clients for warmup and measurement
    my $maxIterations = $numRepetitionsOneClient+$numRepetitions50Clients;

    # Create the monitoring thread that collects footprint and CPU values
    $stopMonitoringThread = 0;
    my $monitoringThread = threads->create({'context' => 'scalar'}, \&monitorFootprintCPU, \@wasPids, $largePageFootprintStart);

    for (my $iter=0; $iter < $maxIterations; $iter++) {
        my $profile = ($doProfile && ($iter == ($maxIterations-1))) ? 1 : 0; # profile last iteration

        if ($iter < $numRepetitionsOneClient) {
            ($thr, $numErr) = runPhaseConcurrently($durationOfOneClient, 1, $profile);
        } else {
            ($thr, $numErr) = runPhaseConcurrently($durationOfOneRepetition, $numClients, $profile); # run for 60 seconds with 50 clients
        }
        print "$iter--> thr=$thr\n" if $verbose >= 1;
        $sumErr += $numErr;
        if ($iter >= $numRepetitionsOneClient+$numRepetitions50Clients-$numMeasurementTrials) {
            $sumThr += $thr;
            $numSamples++;
        }
        # cache the throughput
        push @{$ref_resultsArray}, $thr;
        if ($thr <= 0) {
            $numSamples = 0; # force a result of 0
            last; # don't continue in case of error
        }
    }
    $runThroughput = $sumThr/$numSamples unless $numSamples == 0;

    print "${sumErr} errors encountered\n" unless ($verbose < 5 &&$sumErr == 0);

    #--- Collect working set and write it to file ---
    if ($reportWS) {
        for (my $instanceID=0; $instanceID < $numWASInstances; $instanceID++) {
            my $ws = computeWorkingSet($wasPids[$instanceID]);
            $sumWS += $ws;
        }
        # Now get the large pages working set
        if (!("$^O" =~ /MSWin/)) {
            my $largePageFootprintStop = getLargePagesFootprint();
            my $lpWS = ($largePageFootprintStop - $largePageFootprintStart);
            $sumWS += $lpWS;
            print "LP WorkingSet=$sumWS\n" if ($verbose >= 2 && $lpWS > 0);
        }
        print "Total WorkingSet=$sumWS\n" if $verbose >= 2;
    }

    # Join the monitoring thread and collect history about footprint and CPU for this run
    # We are interested in average footprint and CPU comsumption, but also min/max values
    $stopMonitoringThread = 1;
    my $monitoringObject = $monitoringThread->join();
    print "Average footprint from continuous monitoring = " . $monitoringObject->{footprintAvg} . " min=" . $monitoringObject->{footprintMin} . " max=" . $monitoringObject->{footprintMax} ."\n";


    if ($doFootprintDiagnostic) {
        print "Waiting 60 seconds for manual data collection. Please copy the smap and javacore\n";

        sleep 60;
    }

    #--- stop all application server instances ---
    print "+ Shutting down Application Server instances\n" if $verbose >= 2;
    $ENV{JAVA_HOME} = $javaHome;

    for (my $instanceID=0; $instanceID < $numWASInstances; $instanceID++) {
        stopAppServer();
    }
    delete $ENV{JAVA_HOME};

    # Determine compilation times and possibly print opt levels
    sleep 1;
    for (my $instanceID=0; $instanceID < $numWASInstances; $instanceID++) {
        # parse file with process time and compilation thread time
        my $errFile = getErrFilename($instanceID);
        my $threadTime = getCompilationTimeFromFile($errFile);
        print "Time spent in compilation threads = $threadTime\n" if $verbose >= 2;
        $compTime += $threadTime;

        # Print compilation levels
        if ($verbose >= 2) {
          printCompilationStatsFromStderrFile($errFile);
        }
    }
    return ($runThroughput, $sumWS, $compTime, $sumErr);
}

##############################################################################
sub runBenchmarkIteratively {
    my $javaHome   = shift;
    my $jvmOptions = shift;
    my @perfStats  = (0,0,0,10000000,0);
    my @compTimeStats = (0,0,0,10000000,0);
    my @wsStats    = (0,0,0,10000000,0);

    # Define an array of references to other arrays to hold all results
    my @resultsCollection = ();
    my @cpuTimes = ();  # This is my array with CPU times; one value for each run
    my @footprintValues = (); # One footprint value for each run

    # If there is any PID file, delete it now; may need to kill proccess
    if (-e  getPidFilename()) {
        my $pid = getPIDFromPIDFile(getPidFilename());
        killProcess($pid);
        unlink(getPidFilename());
    }

    my $jvmOpts = "";

    # clear SCC if needed
    clearSCC($javaHome) if ($doColdRun);

    # iterate n times
    for (my $i=0; $i < $numIter; $i++) {
        my @resultsArray = ();
        my $extraArgs = "";
        if ($doOnlyColdRuns || ($doColdRun && $i == 0)) {
            #$extraArgs = "--clean"; # cold runs in Liberty need --clean
        }
        $jvmOpts = ($doColdRun && $i==0 && $useSeparateOptionsForColdRun) ? $optionsForColdRun : $jvmOptions;
        if ($doProfile) {
           $jvmOpts = $jvmOpts . " $profileOpts";
        }

        my $doFootprintDiagnostic = $doMemAnalysis && ($i == $numIter-1);
        if ($doFootprintDiagnostic) {
             $jvmOpts = $jvmOpts . " $extraArgsForMemAnalysis";
        }
        my ($throughput, $workingSet, $threadTime, $sumErr) = runBenchmarkOnce($startupWaitTime, $javaHome, $jvmOpts, $extraArgs, $doFootprintDiagnostic, \@resultsArray); # run application
        print "Run $i Throughput=$throughput WS=$workingSet CPU=$threadTime Errors=$sumErr\n";
        if ($throughput <= 0) {
            next;
        }
        # collect throughput stats
        UpdateStats($throughput, @perfStats);
        # store all partial results
        push @resultsCollection, \@resultsArray;

        UpdateStats($threadTime, @compTimeStats) unless $threadTime <= 0;
        push @cpuTimes, $threadTime; # store CPU for each run

        # collect working set stats
        UpdateStats($workingSet, @wsStats) unless (!$reportWS || $workingSet==0);
        push @footprintValues, $workingSet; # store footprint for each run
    }

    # print stats
    print "Results for JDK=$javaHome jvmOpts=$jvmOpts\n";
    &PrintStats("Throughput", @perfStats);
    print "Intermediate results:\n";
    for (my $i=0; $i < scalar @resultsCollection; $i++) { # for each run
        my $refArray = $resultsCollection[$i];
        my $numResults = scalar @{$refArray};
        print "Run $i\t";
        my $sum = 0;
        for (my $j=0; $j < $numResults; $j++) {
            print "$refArray->[$j]\t";
            $sum += $refArray->[$j] unless $numResults - $j > $numMeasurementTrials; # add it to the average
        }
        printf("Avg=%.0f", $sum/$numMeasurementTrials);
        # Also print the CPU and footprint for these runs
        printf("\tCPU=%d ms  Footprint=%d KB\n", $cpuTimes[$i], $footprintValues[$i]);
    }
    &PrintStats("CompTime", @compTimeStats);
    &PrintStats("Footprint", @wsStats) unless !$reportWS;
}

#------------- main ---------------

print "Use cold run\n" if $doColdRun;
print "Use only cold runs\n" if $doOnlyColdRuns;
print "Options for cold run: $optionsForColdRun\n" if ($doColdRun && $useSeparateOptionsForColdRun);

print "Starting database\n" if $verbose >= 1;
my $dbOutput = `$db2Start`;
print $dbOutput if $verbose >= 2;

sleep 2;

foreach my $jvmOpts (@jitOptions) {
    # run the benchmark n times
    foreach my $jdk (@jdks) {
        runBenchmarkIteratively($jdk, $jvmOpts);
    }
}
`$db2Stop`;

