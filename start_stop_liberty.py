#!/usr/bin/env python3

"""
Script to measure startup performance of Liberty server.
Converted from Perl to Python.
"""

import sys
import os
import re
import time
import subprocess
import glob
import math
from datetime import datetime
import platform

# Configuration variables
verbose = 5  # 1 or 2, for increasing verbosity
do_cold_run = True  # clear SCC and do a cold run first
use_vmmap = False
report_ws = True  # report working set size
report_cpu = True
report_cold_run_stats = True
wait_time_to_start = 10
cpu_affinity = "0x3"
liberty_dir = "c://tmp//OL-26.0.0.4//liberty"
app_name = "emptyserver"  # "EmptyProfile"
app_dir = f"{liberty_dir}/usr/servers/{app_name}"
log_dir = f"{app_dir}/logs"
log_file = f"{log_dir}/messages.log"
console_log_file = f"{log_dir}/console.log"
run_cmd = f"{liberty_dir}//bin//server.bat start {app_name}"
stop_cmd = f"{liberty_dir}//bin//server.bat stop {app_name}"
pid_file = f"{liberty_dir}/usr/servers/.pid/{app_name}.pid"
working_set_file = "WS.txt"
vmmap_executable = "C://Apps//Sysinternals//vmmap.exe"
vmmap_dir = "C://tmp//"
send_signal_executable = "C://IBM//sendCtrlBreak-64bit.exe"
get_working_set_executable = "C://tmp//GetWorkingSet.exe"

# Footprint
footprint_monitor = True
do_mem_analysis = False
dir_for_mem_analysis_files = "/tmp"
extra_args_for_mem_analysis = (
    f"-Xdump:none -Xdump:java:events=user,file={dir_for_mem_analysis_files}/javacore.%pid.%seq.txt "
    f"-Xdump:system:events=user,file={dir_for_mem_analysis_files}/core.%pid.%seq.dmp"
)

use_different_options_for_cold = False
options_for_cold = "-Xquickstart -Xjit:enableInterpreterProfiling -Xmx256m -Xshareclasses:enableBCI,name=liberty -Xscmx60M -Xscmaxaot4m"

jvm_options = [
    "-Xmx512m -Xscmx200m",
]

jdks = [
    "c://tmp//OpenJ9-JDK26-x86-64_windows-20260614-085147",
]


def is_windows():
    """Check if running on Windows."""
    return platform.system() == "Windows" or "cygwin" in platform.system().lower()


def update_stats(value, stats):
    """
    Update statistics array.
    stats = [samples, sum, max, min, sumsq]
    """
    stats[0] += 1  # samples
    stats[1] += value  # sum
    stats[4] += value * value  # sumsq
    if value > stats[2]:  # max
        stats[2] = value
    if value < stats[3]:  # min
        stats[3] = value


def print_stats(text, samples, sum_val, max_val, min_val, sumsq, median=0):
    """Print statistics for an array."""
    confidence_interval = 0
    if samples > 0:
        stddev = 0
        if samples > 1:
            variance = (sumsq - sum_val * sum_val / samples) / (samples - 1)
            stddev = math.sqrt(variance)
            avg = sum_val / samples
            confidence_interval = tdistribution(samples - 1) * stddev / math.sqrt(samples) * 100.0 / avg

        avg = sum_val / samples
        max_var = (100.0 * max_val / min_val - 100.0) if min_val > 0 else 0
        print(f"{text}\tavg={avg:4.0f}\tmin={min_val:4.0f}\tmax={max_val:4.0f}\t"
              f"stdDev={stddev:4.1f}\tmaxVar={max_var:3.1f}%\tconfInt={confidence_interval:.2f}%\t"
              f"samples={samples:3d}")


def tdistribution(degrees_of_freedom):
    """Return t-distribution value for given degrees of freedom."""
    table = [6.314, 2.92, 2.353, 2.132, 2.015, 1.943, 1.895, 1.860, 1.833, 1.812,
             1.796, 1.782, 1.771, 1.761, 1.753, 1.746, 1.740, 1.734, 1.729, 1.725]

    if degrees_of_freedom < 1:
        return -1
    elif degrees_of_freedom <= 20:
        return table[degrees_of_freedom - 1]
    else:
        if degrees_of_freedom < 30:
            return 1.697
        if degrees_of_freedom < 40:
            return 1.684
        if degrees_of_freedom < 50:
            return 1.676
        if degrees_of_freedom < 60:
            return 1.671
        if degrees_of_freedom < 70:
            return 1.667
        if degrees_of_freedom < 80:
            return 1.664
        if degrees_of_freedom < 90:
            return 1.662
        if degrees_of_freedom < 100:
            return 1.660
        return 1.65


def compute_outlier_fences(data_array):
    """
    Compute outlier fences using interquartile range.
    Returns (lower_fence, upper_fence, median).
    Needs at least 4 data points.
    """
    sorted_data = sorted(data_array)
    num_values = len(sorted_data)

    if num_values < 4:
        return (sorted_data[0], sorted_data[num_values - 1], sorted_data[num_values // 2])

    # Find the median
    mid_position = num_values // 2

    if num_values % 2:  # odd number
        q2 = sorted_data[mid_position]
        half_count = mid_position + 1
    else:  # even number
        q2 = (sorted_data[mid_position - 1] + sorted_data[mid_position]) / 2
        half_count = mid_position

    # Find the quartiles
    q1_position = half_count // 2
    q3_position = q1_position + mid_position

    if half_count % 2:  # odd number
        q1 = sorted_data[q1_position]
        q3 = sorted_data[q3_position]
    else:
        q1 = (sorted_data[q1_position - 1] + sorted_data[q1_position]) / 2
        q3 = (sorted_data[q3_position - 1] + sorted_data[q3_position]) / 2

    # Compute the interquartile range
    iqr = q3 - q1
    lower_fence = q1 - 3 * iqr
    upper_fence = q3 + 3 * iqr

    return (lower_fence, upper_fence, q2)


def print_statistics(text, data_array):
    """
    Eliminate outliers and print statistics.
    Also prints the values that were eliminated.
    """
    lower_fence, upper_fence, median = compute_outlier_fences(data_array)

    # Eliminate outliers
    outlier_array = []
    stats = [0, 0, 0, 10000000, 0]  # samples, sum, max, min, sumsq

    for val in data_array:
        if val < lower_fence or val > upper_fence:
            outlier_array.append(val)
        else:
            update_stats(val, stats)

    print_stats(text, stats[0], stats[1], stats[2], stats[3], stats[4], median)

    if outlier_array:
        print(f"\tOutlier values: {' '.join(str(x) for x in outlier_array)}")


def get_large_pages_footprint():
    """Get large pages footprint (Linux only)."""
    if is_windows():
        return 0  # large pages not tracked on windows

    try:
        with open("/proc/meminfo", "r") as f:
            total_huge_pages = 0
            free_huge_pages = 0
            huge_page_size = 0

            for line in f:
                if match := re.search(r'HugePages_Total:\s+(\d+)', line):
                    total_huge_pages = int(match.group(1))
                elif match := re.search(r'HugePages_Free:\s+(\d+)', line):
                    free_huge_pages = int(match.group(1))
                elif match := re.search(r'Hugepagesize:\s+(\d+) kB', line):
                    huge_page_size = int(match.group(1))

            return (total_huge_pages - free_huge_pages) * huge_page_size
    except FileNotFoundError:
        return 0


def kill_process(pid):
    """Kill a process by PID."""
    try:
        # Check if process exists
        os.kill(pid, 0)
        print(f"+ Trying to kill process with PID={pid}") if verbose >= 2 else None

        if is_windows():
            subprocess.run(["Taskkill", "/PID", str(pid), "/F"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.kill(pid, 9)
    except (OSError, ProcessLookupError):
        pass  # Process doesn't exist


def get_liberty_pid():
    """Get the PID of the Liberty server process."""
    java_pid = 0

    if is_windows():
        try:
            result = subprocess.run(
                ["c://Apps//SysInternals//pslist.exe", "javaw"],
                capture_output=True, text=True, stderr=subprocess.DEVNULL
            )
            lines = result.stdout.split('\n')

            if len(lines) < 2:
                raise Exception("No java process")

            for i, line in enumerate(lines):
                if re.search(r'Name\s+Pid', line):
                    for j in range(i + 1, len(lines)):
                        if match := re.search(r'java\s+(\d+)', lines[j]):
                            if java_pid == 0:
                                java_pid = int(match.group(1))
                            else:
                                raise Exception("Too many java processes")
        except Exception as e:
            print(f"Error getting PID: {e}")
            return 0
    else:  # Linux
        try:
            with open(pid_file, "r") as f:
                java_pid = int(f.read().strip())
        except FileNotFoundError:
            return 0

    if verbose >= 2:
        print(f"PID={java_pid}")
    return java_pid


def verify_liberty_started(trace_file):
    """Verify that Liberty server has started."""
    was_pid = 0
    success = False
    iterations = 0

    while iterations < 20:
        iterations += 1
        try:
            with open(trace_file, "r") as f:
                for line in f:
                    if match := re.search(r'process = (\d+)@', line):
                        was_pid = int(match.group(1))
                    if re.search(r'^\[(.+)\] .+is ready to run a smarter planet', line):
                        success = True
                        if verbose >= 3:
                            print("Found open for ebusiness")
                        break

            if success:
                break
        except FileNotFoundError:
            pass

        time.sleep(1)

    return was_pid if success else 0


def get_trace_filename():
    """Get the trace filename (messages.log)."""
    return log_file


def delete_trace_files():
    """Delete trace and error files."""
    # Try to delete log file, but don't fail if it's locked
    try:
        os.unlink(log_file)
    except (FileNotFoundError, PermissionError) as e:
        if isinstance(e, PermissionError):
            if verbose >= 2:
                print(f"Warning: Could not delete {log_file} (file in use)")
        pass

    # Delete FFDC files
    ffdc_files = glob.glob(f"{log_dir}/ffdc/*")
    for err_file in ffdc_files:
        try:
            os.unlink(err_file)
        except (FileNotFoundError, PermissionError):
            pass


def get_startup_timestamp_from_trace_file(trace_file):
    """Extract startup timestamp from trace file."""
    try:
        with open(trace_file, "r") as f:
            for line in f:
                if match := re.search(r'^\[(.+)\] .+is ready to run a smarter planet', line):
                    timestamp = match.group(1)
                    if verbose >= 2:
                        print(f"Found timestamp {timestamp}")

                    # Parse timestamp: [1/18/16, 19:47:06:222 EST]
                    if ts_match := re.search(r'(\d+)/(\d+)/(\d+),?\s+(\d+):(\d+):(\d+):(\d+)\s+(.+)', timestamp):
                        month = int(ts_match.group(1))
                        day = int(ts_match.group(2))
                        year = int(ts_match.group(3)) + 2000
                        hour = int(ts_match.group(4))
                        minute = int(ts_match.group(5))
                        second = int(ts_match.group(6))
                        millisecond = int(ts_match.group(7))

                        dt = datetime(year, month, day, hour, minute, second, millisecond * 1000)
                        epoch_seconds = int(dt.timestamp())
                        epoch_microseconds = millisecond * 1000

                        if verbose >= 2:
                            print(f"End time: sec={epoch_seconds} usec={epoch_microseconds}")

                        return (epoch_seconds, epoch_microseconds)
                    else:
                        print(f"Timestamp from tracefile {trace_file} is in wrong format:")
                        print(timestamp)
                        sys.exit(1)
                elif match := re.search(r'The kernel started after (\S+) seconds', line):
                    if verbose >= 2:
                        print(line.strip())
                elif match := re.search(r'Application (\S+) started in (\S+) seconds', line):
                    if verbose >= 2:
                        print(line.strip())
                elif match := re.search(r'Feature update completed in (\S+) seconds', line):
                    if verbose >= 2:
                        print(line.strip())
    except FileNotFoundError:
        pass

    return (0, 0)


def stop_liberty():
    """Stop the Liberty server."""
    if verbose >= 2:
        print(f"+ About to issue the stop command: {stop_cmd}")

    try:
        subprocess.run(stop_cmd, shell=True, stdout=subprocess.DEVNULL,
                      stderr=open("garbageStop.txt", "w"))
    except Exception as e:
        print(f"Error stopping Liberty: {e}")

    if verbose >= 2:
        print("+ Stop command ended")

    time.sleep(1)  # wait for server to shutdown

    # Make sure server is down
    pid = get_liberty_pid()
    if pid:
        kill_process(pid)


def get_cpu_time(java_pid):
    """Get CPU time for a process (Linux only)."""
    return 0  # Simplified - original uses 'top' command


def get_working_set(java_pid, large_page_footprint_start):
    """Get working set size for a process."""
    if is_windows():
        try:
            subprocess.run([get_working_set_executable, str(java_pid)],
                         stdout=open(working_set_file, "w"))

            if use_vmmap:
                vmmap_file = f"{vmmap_dir}vmmap{java_pid}.csv"
                subprocess.run([vmmap_executable, "-p", str(java_pid), vmmap_file])
                subprocess.run([send_signal_executable, str(java_pid)])
        except Exception as e:
            print(f"Error getting working set: {e}")
    else:
        try:
            subprocess.run(f"ps -orss,vsz,cputime --no-headers --pid {java_pid} > {working_set_file}",
                         shell=True)
            large_page_footprint_stop = get_large_pages_footprint()
            with open(working_set_file, "a") as f:
                f.write(f"HugePages {large_page_footprint_start} {large_page_footprint_stop}\n")

            if use_vmmap:
                os.kill(java_pid, 3)  # SIGQUIT
        except Exception as e:
            print(f"Error getting working set: {e}")


def clear_scc(java_home):
    """Clear the shared class cache."""
    if verbose >= 2:
        print(f"+ Clearing the SCC using {java_home}")

    try:
        result = subprocess.run(
            [f"{java_home}/bin/java", "-Xshareclasses:destroyall"],
            capture_output=True, text=True
        )
        if verbose >= 2:
            print(f"+ {result.stdout}")

        result = subprocess.run(
            [f"{java_home}/bin/java",
             f"-Xshareclasses:cacheDir={liberty_dir}/usr/servers/.classCache,destroyall"],
            capture_output=True, text=True
        )
        if verbose >= 2:
            print(f"+ {result.stdout}")
    except Exception as e:
        print(f"Error clearing SCC: {e}")


def run_benchmark_once(wait_time, java_home, jvm_opts, app_args,
                       collect_footprint_diagnostic):
    """Run the benchmark once and return startup time."""
    delete_trace_files()
    large_page_footprint_start = get_large_pages_footprint()

    if verbose >= 2:
        print(f"+ Will start benchmark with JAVA_HOME={java_home} and JVM_ARGS={jvm_opts}...")

    os.environ["JVM_ARGS"] = jvm_opts
    os.environ["JAVA_HOME"] = java_home

    if verbose >= 2:
        print(f"+ Will execute {run_cmd} {app_args}")

    start_time = time.time()

    try:
        if is_windows():
            with open("err.txt", "w") as err_file:
                subprocess.run(f"{run_cmd} {app_args}", shell=True,
                             stderr=err_file)
        else:
            subprocess.run(f"{run_cmd} {app_args} 2> err.txt", shell=True)
    except Exception as e:
        print(f"Error running benchmark: {e}")
        return (0, 0)

    if verbose >= 2:
        print("+ Start command finished")

    # Wait for server to start
    if is_windows():
        try:
            result = subprocess.run(
                ["c://Apps//SysInternals//pslist.exe", "-nobanner", "-m", "javaw"],
                capture_output=True, text=True
            )
            lines = result.stdout.split('\n')

            if len(lines) < 2:
                raise Exception("No java process")

            java_pid = 0
            for i, line in enumerate(lines):
                if re.search(r'Name\s+Pid', line):
                    for j in range(i + 1, len(lines)):
                        print(lines[j])
                        if match := re.search(r'javaw\s+(\d+)', lines[j]):
                            if java_pid == 0:
                                java_pid = int(match.group(1))
                            else:
                                raise Exception("Too many java processes")

            if java_pid == 0:
                raise Exception("No java process found")
        except Exception as e:
            print(f"Error finding java process: {e}")
            return (0, 0)
    else:
        try:
            result = subprocess.run(["pgrep", "java"], capture_output=True, text=True)
            pids = result.stdout.strip().split('\n')

            if len(pids) != 1:
                print(f"Error: expected to find exactly one java process, but found {len(pids)}")
                print(f"PIDs found: {pids}")
                if verbose >= 2:
                    print(f"Will sleep for {wait_time} seconds")
                time.sleep(wait_time)
            else:
                if verbose >= 2:
                    print(f"Found java process with PID={pids[0]}")

                if footprint_monitor:
                    java_pid = int(pids[0])
                    for i in range(wait_time):
                        result = subprocess.run(
                            ["ps", "--no-headers", "-o", "rss", "-p", str(java_pid)],
                            capture_output=True, text=True
                        )
                        rss = result.stdout.strip()
                        print(f"Working set for PID={java_pid} is {rss}")
                        time.sleep(1)
        except Exception as e:
            print(f"Error monitoring process: {e}")

    java_pid = verify_liberty_started(get_trace_filename())
    if java_pid == 0:
        return (0, 0)

    cpu_time = 0
    if report_ws:
        get_working_set(java_pid, large_page_footprint_start)

    cpu_time = get_cpu_time(java_pid)

    # Collect footprint diagnostic if needed
    if collect_footprint_diagnostic:
        if verbose >= 2:
            print("Collecting diagnostic files for footprint analysis")

        if not is_windows():
            try:
                subprocess.run(
                    f"cp /proc/{java_pid}/smaps {dir_for_mem_analysis_files}/smaps.{java_pid}.txt",
                    shell=True
                )
                os.kill(java_pid, 3)  # SIGQUIT
                if verbose >= 1:
                    print("Waiting 60 seconds to generate javacore/coredump")
                time.sleep(60)
            except Exception as e:
                print(f"Error collecting diagnostics: {e}")

    os.environ["JVM_ARGS"] = ""
    os.environ["JAVA_HOME"] = java_home
    stop_liberty()

    if verbose >= 2:
        print("+ Parent: Finished executing benchmark")

    # Compute startup time
    end_epoch_seconds, end_epoch_microseconds = get_startup_timestamp_from_trace_file(
        get_trace_filename()
    )

    if verbose >= 2:
        print(f"+ start_sec={start_time} end_sec={end_epoch_seconds}")

    startup_time = (end_epoch_seconds - start_time) * 1000 + (end_epoch_microseconds) / 1000

    return (startup_time, cpu_time)


def run_benchmark_iteratively(num_iter, java_home, jvm_opts, results_array):
    """Run benchmark multiple iterations."""
    if do_mem_analysis:
        jvm_opts = f"{jvm_opts} {extra_args_for_mem_analysis}"

    if verbose >= 1:
        print(f"Will do {num_iter} iterations with javaHome={java_home} jvmOpts={jvm_opts}")

    if do_cold_run:
        clear_scc(java_home)

    for i in range(num_iter):
        try:
            os.unlink("err.txt")
        except (FileNotFoundError, PermissionError):
            # File doesn't exist or is still in use - wait a moment and try again
            time.sleep(0.1)
            try:
                os.unlink("err.txt")
            except (FileNotFoundError, PermissionError):
                pass  # If still locked, proceed anyway

        # Determine arguments
        if do_cold_run and i == 0:
            app_args = "--clean"
            java_opts = options_for_cold if use_different_options_for_cold else jvm_opts
        else:
            app_args = ""
            java_opts = jvm_opts

        do_footprint_diagnostic = do_mem_analysis and (i == num_iter - 1)

        startup_time, cpu_time = run_benchmark_once(
            wait_time_to_start, java_home, java_opts, app_args, do_footprint_diagnostic
        )

        if verbose >= 1:
            print(f"+ Parent: startTime={startup_time} ms")

        if startup_time > 0:
            results_array[i]["startupTime"] = startup_time
            results_array[i]["processTime"] = cpu_time

            if verbose >= 1:
                print(f"ProcessCPU={cpu_time}")

            # Parse compilation thread time
            try:
                with open(console_log_file, "r") as f:
                    thread_time = 0
                    for line in f:
                        if match := re.search(r'Time spent in compilation thread =(\d+) ms', line):
                            thread_time += int(match.group(1))

                    if thread_time > 0:
                        results_array[i]["compCPUTime"] = thread_time
                        if verbose >= 1:
                            print(f"CompThreadTime={thread_time}")
            except FileNotFoundError:
                pass

            # Parse working set
            if report_ws:
                try:
                    with open(working_set_file, "r") as f:
                        working_set = 0

                        if is_windows():
                            for line in f:
                                if match := re.search(r'^(\d+) MB', line):
                                    working_set = int(match.group(1))
                                    if verbose >= 1:
                                        print(f"Working set={working_set}")
                        else:
                            for line in f:
                                if match := re.search(r'^(\d+)\s+(\d+)\s+(\d+):(\d+):(\d+)', line):
                                    working_set = int(match.group(1))
                                    if verbose >= 1:
                                        print(f"WorkingSet={working_set}")
                                elif match := re.search(r'HugePages\s+(\d+)\s+(\d+)', line):
                                    hp_working_set = int(match.group(2)) - int(match.group(1))
                                    working_set += hp_working_set
                                    if verbose >= 1:
                                        print(f"HPWorkingSet={hp_working_set}")

                        results_array[i]["footprint"] = working_set
                except FileNotFoundError:
                    pass


def print_all_performance_numbers(results, num_batches, num_iter):
    """Print all performance statistics."""
    for jdk_id in range(len(jdks)):
        for opt_id in range(len(jvm_options)):
            print(f"Results for JDK={jdks[jdk_id]} jvmOpts={jvm_options[opt_id]}")

            # Accumulate all valid scores
            startup_time = []
            comp_cpu_time = []
            footprint = []
            process_time = []

            for batch_id in range(num_batches):
                run_id = 2 if do_cold_run else 0
                for run_id in range(run_id, num_iter):
                    val = results[jdk_id][opt_id][batch_id][run_id].get("startupTime", 0)
                    if val > 0:
                        startup_time.append(val)

                    val = results[jdk_id][opt_id][batch_id][run_id].get("compCPUTime", 0)
                    if val > 0:
                        comp_cpu_time.append(val)

                    val = results[jdk_id][opt_id][batch_id][run_id].get("footprint", 0)
                    if val > 0:
                        footprint.append(val)

                    val = results[jdk_id][opt_id][batch_id][run_id].get("processTime", 0)
                    if val > 0:
                        process_time.append(val)

            # Print statistics
            if startup_time:
                print_statistics("StartupTime", startup_time)
            if footprint:
                print_statistics("Footprint", footprint)
            if comp_cpu_time:
                print_statistics("CThreadTime", comp_cpu_time)
            if process_time:
                print_statistics("ProcessTime", process_time)

            # Print cold run stats
            if do_cold_run and report_cold_run_stats:
                startup_time = []
                comp_cpu_time = []
                footprint = []
                process_time = []

                for batch_id in range(num_batches):
                    val = results[jdk_id][opt_id][batch_id][0].get("startupTime", 0)
                    if val > 0:
                        startup_time.append(val)

                    val = results[jdk_id][opt_id][batch_id][0].get("compCPUTime", 0)
                    if val > 0:
                        comp_cpu_time.append(val)

                    val = results[jdk_id][opt_id][batch_id][0].get("footprint", 0)
                    if val > 0:
                        footprint.append(val)

                    val = results[jdk_id][opt_id][batch_id][0].get("processTime", 0)
                    if val > 0:
                        process_time.append(val)

                print("Stats for cold run:")
                if startup_time:
                    print_statistics("StartupTime", startup_time)
                if footprint:
                    print_statistics("Footprint", footprint)
                if comp_cpu_time:
                    print_statistics("CThreadTime", comp_cpu_time)
                if process_time:
                    print_statistics("ProcessTime", process_time)


def main():
    """Main execution function."""
    if len(sys.argv) != 3:
        print("Usage: python start_stop_liberty.py <num_iterations> <num_batches>")
        sys.exit(1)

    num_iter = int(sys.argv[1])
    num_batches = int(sys.argv[2])

    print(f"doColdRun={do_cold_run} using server={app_name}")

    # Set environment variables
    os.environ["TR_PrintCompTime"] = "1"
    os.environ["TR_PrintCompStats"] = "1"
    os.environ["TR_PrintCompMem"] = "1"
    os.environ["ACMEAIR_PROPERTIES"] = f"{app_dir}/mongo.properties"

    # Initialize results array
    results = []
    for jdk_id in range(len(jdks)):
        results.append([])
        for opt_id in range(len(jvm_options)):
            results[jdk_id].append([])
            for batch_id in range(num_batches):
                results[jdk_id][opt_id].append([])
                for run_id in range(num_iter):
                    results[jdk_id][opt_id][batch_id].append({
                        "startupTime": 0,
                        "compCPUTime": 0,
                        "footprint": 0,
                        "processTime": 0,
                    })

    # Run benchmarks
    for global_batch_id in range(num_batches):
        print(f"batch {global_batch_id}")
        opt_id = 0
        for jvm_opts in jvm_options:
            jdk_id = 0
            for jdk in jdks:
                run_benchmark_iteratively(
                    num_iter, jdk, jvm_opts,
                    results[jdk_id][opt_id][global_batch_id]
                )
                jdk_id += 1
            opt_id += 1

    print_all_performance_numbers(results, num_batches, num_iter)


if __name__ == "__main__":
    main()

# Made with Bob
