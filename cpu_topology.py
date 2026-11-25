"""Discover the logical CPUs and sibling topology visible inside the Docker guest."""
import re


# This probe runs in the benchmark image with its Java entry point bypassed.
# CPU IDs come from the process affinity mask; sysfs can include CPUs outside it.
_PROBE = r"""
set -eu
awk '/^Cpus_allowed_list:/ {printf "allowed\t%s\n", $2}' /proc/self/status
printf 'quota\t'
cat /sys/fs/cgroup/cpu.max
for dir in /sys/devices/system/cpu/cpu[0-9]*; do
    for file in "$dir/topology/core_cpus_list" "$dir/topology/thread_siblings_list"; do
        if [ -r "$file" ] && IFS= read -r siblings < "$file"; then
            printf 'siblings\t%s\t%s\n' "${dir##*/cpu}" "$siblings"
            break
        fi
    done
done
"""


def parse_cpu_list(text):
    """Parse Linux CPU sets such as '0-2,5,8-9'."""
    cpus = set()
    for field in text.strip().split(","):
        match = re.fullmatch(r"([0-9]+)(?:-([0-9]+))?", field)
        if not match:
            raise ValueError(f"Invalid CPU list: {text!r}")
        first = int(match[1])
        last = int(match[2]) if match[2] is not None else first
        if last < first:
            raise ValueError(f"Reversed range in CPU list: {text!r}")
        cpus.update(range(first, last + 1))
    return cpus


def _sibling_groups(allowed, lines):
    masks = {}
    for line in lines:
        try:
            key, cpu_id, cpu_list = line.split("\t")
            cpu_id = int(cpu_id)
            if key != "siblings" or cpu_id not in allowed:
                continue
            siblings = frozenset(parse_cpu_list(cpu_list))
        except ValueError:
            return None
        if cpu_id not in siblings or cpu_id in masks:
            return None
        masks[cpu_id] = siblings
    if masks.keys() != allowed:
        return None

    # Masks for the same guest core must agree, including masked-out siblings.
    groups = []
    seen = set()
    for siblings in set(masks.values()):
        if seen.intersection(siblings):
            return None
        seen.update(siblings)
        groups.append(sorted(siblings & allowed))
    return sorted(groups)


def discover_topology(docker, image_id):
    """Choose a stable CPU order, using each guest core once before its SMT siblings."""
    output = docker("run", "--rm", "--read-only", "--network", "none", "--entrypoint", "sh",
                    image_id, "-c", _PROBE)
    lines = output.splitlines()
    if len(lines) < 2 or not lines[0].startswith("allowed\t") or not lines[1].startswith("quota\t"):
        raise RuntimeError("Missing process affinity or cpu.max in the Docker topology probe")
    try:
        allowed = parse_cpu_list(lines[0].split("\t", 1)[1])
    except ValueError as error:
        raise RuntimeError("Invalid Docker process affinity mask") from error
    cpu_max = lines[1].split("\t", 1)[1].strip()
    if not re.fullmatch(r"max\s+[1-9][0-9]*", cpu_max):
        raise RuntimeError(f"Affinity experiment requires an unlimited visible CPU quota; cpu.max={cpu_max!r}")

    groups = _sibling_groups(allowed, lines[2:])
    order = sorted(allowed)
    if groups:
        order = [group[layer] for layer in range(max(map(len, groups)))
                 for group in groups if layer < len(group)]
    return {"allowed_cpus": sorted(allowed), "sibling_groups": groups, "cpu_order": order,
            "cpu_max": cpu_max}
