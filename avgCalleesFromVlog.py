# Read a vlog and compute the average number of callees inlined

import re # for regular expressions
import sys # for number of arguments

def computeAverageInlined(filePath):
    totalInlined = 0
    count = 0

    with open(filePath, 'r') as file:
        pattern = re.compile("^#INL:\s+(\d+) methods inlined into")
        for line in file:
            line = line.strip()
            if line.startswith("#INL:"):
                m = pattern.match(line)
                if m:
                    numCallees = int(m.group(1))
                    if numCallees > 70:
                        print(line)
                    totalInlined += numCallees
                    count += 1

    if count == 0:
        return 0.0
    print("TotalInlined=", totalInlined, " count=", count)
    return totalInlined / count


if  len(sys.argv) < 2:
    print ("Program must have an argument: the vlog\n")
    sys.exit(-1)
vlog = sys.argv[1]
average = computeAverageInlined(vlog)
print(f"Average methods inlined: {average:.2f}")
