"""Read-only server facts and conservative execution policy; never admit science.

No scientific package, optimizer, network service, or subprocess is imported or
started by this module. The optional Windows probe observes topology and current
process affinity; it never sets affinity. A matching host is not an ADMISSION.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import struct
import sys


PACKAGE_KINDS = ('normal_year', 'busy_year')
THREAD_ENVIRONMENT = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
    'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'BLIS_NUM_THREADS')


class ProfileError(ValueError):
    """Required observed facts or the existing execution boundary are unmet."""


def target_profile():
    """Describe declared requirements without probing or granting admission."""
    return dict(schema='hospital_ies.transfer_server_profile.v1',
        target_system='Windows', target_cpu='AMD EPYC 9654', declared_ram_gb=256,
        minimum_ram_bytes=240_000_000_000, required_python_pointer_bits=64,
        producer_caps=None, concurrency_policy='explicit_positive_counts_with_live_resource_guards',
        registered_trajectories_per_package=3, threads_per_solver=1, threads_per_blas=1,
        concurrency_scope='independent_cases_only', affinity_policy='inherit_observed_no_setter',
        target_host_validation='not_executed', server_scientific_admission='not_executed',
        admission_granted=False, license_probe_performed=False,
        busy_historical_deployment_checks='skipped_by_user_request',
        physical_or_IES_execution_performed=False)


def validate_execution_policy(kind, *, max_producers, threads_per_producer=1,
                              trajectory_partitioning='independent_cases', requested_cpus=None):
    """Check candidate policy, without altering any process or runner limits."""
    if kind not in PACKAGE_KINDS:
        raise ProfileError('An explicit normal_year or busy_year package is required')
    if type(max_producers) is not int or max_producers<1:
        raise ProfileError('An explicit positive integer producer count is required')
    if type(threads_per_producer) is not int or threads_per_producer != 1:
        raise ProfileError('Each native solver and BLAS must remain single threaded')
    if trajectory_partitioning != 'independent_cases':
        raise ProfileError('Only independent complete trajectories may run concurrently')
    if requested_cpus is not None:
        if requested_cpus == list(range(24)) or requested_cpus == tuple(range(24)):
            raise ProfileError('Legacy hard-coded CPU0-23 affinity is forbidden on the target server')
        raise ProfileError('This profile inherits observed affinity; a new CPU pinning policy is not admitted')
    return dict(status='candidate_requires_server_scientific_admission', kind=kind,
        max_producers=max_producers, trajectory_partitioning=trajectory_partitioning,
        threads_per_producer=1, solver_parameters={'Threads': 1},
        blas_environment={name: '1' for name in THREAD_ENVIRONMENT},
        requested_cpus=None, affinity_policy='inherit_observed_no_setter', admission_granted=False)


def single_thread_environment(base_environment):
    """Return a child environment; Gurobi still needs its explicit Threads=1."""
    result = dict(base_environment)
    result.update({name: '1' for name in THREAD_ENVIRONMENT})
    return result


def _require_bytes(raw, offset, count):
    if offset < 0 or count < 0 or offset + count > len(raw):
        raise ProfileError('Truncated Windows logical processor information')


def parse_windows_topology(raw, *, pointer_bytes=8):
    """Decode GetLogicalProcessorInformationEx(RelationAll), including NUMAEx.

    Windows GROUP_RELATIONSHIP has a 24-byte fixed body. NUMA bodies have
    NodeNumber/Reserved[18]/GroupCount, followed by GROUP_AFFINITY entries.
    Pre-20H2 NUMA GroupCount is zero and carries one legacy GroupMask.
    Structure definitions are linked in SERVER_BEFORE_RUN_ZH.md.
    """
    if pointer_bytes not in (4, 8) or not isinstance(raw, bytes) or not raw:
        raise ProfileError('A nonempty native Windows topology buffer is required')
    mask_type = '<Q' if pointer_bytes == 8 else '<I'
    affinity_size, group_info_size = pointer_bytes + 8, 40 + pointer_bytes
    groups, nodes, cores, packages = [], {}, 0, 0
    cursor = 0
    while cursor < len(raw):
        _require_bytes(raw, cursor, 8)
        relation, size = struct.unpack_from('<II', raw, cursor)
        if size < 8:
            raise ProfileError('Invalid Windows topology record size')
        _require_bytes(raw, cursor, size)
        body = raw[cursor + 8:cursor + size]
        if relation == 4:  # RelationGroup
            _require_bytes(body, 0, 24)
            maximum, active = struct.unpack_from('<HH', body)
            if not 1 <= active <= maximum or groups:
                raise ProfileError('Invalid or repeated Windows processor-group record')
            _require_bytes(body, 24, active * group_info_size)
            for group in range(active):
                index = 24 + group * group_info_size
                maximum_cpus, active_cpus = struct.unpack_from('<BB', body, index)
                mask = struct.unpack_from(mask_type, body, index + 40)[0]
                if not 1 <= active_cpus <= maximum_cpus <= pointer_bytes * 8 or mask.bit_count() != active_cpus:
                    raise ProfileError('Windows processor-group count or mask is inconsistent')
                groups.append(dict(group=group, maximum_processors=maximum_cpus,
                    active_processors=active_cpus, mask_hex=hex(mask)))
        elif relation in (1, 6):  # RelationNumaNode / RelationNumaNodeEx
            _require_bytes(body, 0, 24)
            node = struct.unpack_from('<I', body)[0]
            group_count = struct.unpack_from('<H', body, 22)[0] or 1
            _require_bytes(body, 24, group_count * affinity_size)
            node_masks = nodes.setdefault(node, {})
            for index in range(group_count):
                offset = 24 + index * affinity_size
                mask = struct.unpack_from(mask_type, body, offset)[0]
                group = struct.unpack_from('<H', body, offset + pointer_bytes)[0]
                if not mask or group in node_masks and node_masks[group] != mask:
                    raise ProfileError('Invalid or conflicting NUMA group mask')
                node_masks[group] = mask
        elif relation in (0, 3):  # ProcessorCore / ProcessorPackage
            _require_bytes(body, 0, 24)
            count = struct.unpack_from('<H', body, 22)[0]
            if not count:
                raise ProfileError('Processor record contains no group affinity')
            _require_bytes(body, 24, count * affinity_size)
            cores += int(relation == 0)
            packages += int(relation == 3)
        cursor += size
    if not groups or not nodes:
        raise ProfileError('Processor groups or NUMA nodes were not observed')
    known_masks = {item['group']: int(item['mask_hex'], 16) for item in groups}
    for masks in nodes.values():
        if any(group not in known_masks or mask & ~known_masks[group] for group, mask in masks.items()):
            raise ProfileError('NUMA mask refers to an unobserved processor group')
    return dict(status='observed', source='GetLogicalProcessorInformationEx(RelationAll)',
        pointer_bits=pointer_bytes * 8, processor_groups=groups,
        logical_processors_in_groups=sum(item['active_processors'] for item in groups),
        numa_nodes=[dict(node=node, group_masks=[dict(group=group, mask_hex=hex(mask))
            for group, mask in sorted(masks.items())]) for node, masks in sorted(nodes.items())],
        physical_cores=cores if cores else None, processor_packages=packages if packages else None)


def _windows_topology():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    query = kernel.GetLogicalProcessorInformationEx
    query.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    query.restype = wintypes.BOOL
    length = wintypes.DWORD()
    query(0xffff, None, ctypes.byref(length))  # RelationAll; size query is read-only.
    if not length.value:
        raise OSError(ctypes.get_last_error(), 'Windows topology size query failed')
    buffer = ctypes.create_string_buffer(length.value)
    if not query(0xffff, buffer, ctypes.byref(length)):
        raise OSError(ctypes.get_last_error(), 'Windows topology query failed')
    return parse_windows_topology(buffer.raw[:length.value], pointer_bytes=struct.calcsize('P'))


def _collect_host_facts(work_root):
    facts = dict(hostname=socket.gethostname(), system=platform.system(), release=platform.release(),
        machine=platform.machine(), cpu_model=platform.processor(), pointer_bits=struct.calcsize('P') * 8,
        python_executable=str(Path(sys.executable).resolve()), python_version=platform.python_version(),
        visible_cpu_count=os.cpu_count(), cpu_affinity=None, ram_total_bytes=None, ram_available_bytes=None,
        topology=dict(status='not_observed', reason='Windows native topology has not been queried'), errors=[])
    if facts['system'] == 'Windows':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                    r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
                facts['cpu_model'] = winreg.QueryValueEx(key, 'ProcessorNameString')[0].strip()
        except OSError as error:
            facts['errors'].append(dict(stage='cpu_registry', type=type(error).__name__, message=str(error)))
        try:
            facts['topology'] = _windows_topology()
        except (OSError, AttributeError, ProfileError) as error:
            facts['topology'] = dict(status='unknown', type=type(error).__name__, reason=str(error))
    try:
        import psutil
        memory = psutil.virtual_memory()
        facts.update(ram_total_bytes=memory.total, ram_available_bytes=memory.available,
                     cpu_affinity=psutil.Process().cpu_affinity())
    except (ImportError, OSError, AttributeError) as error:
        facts['errors'].append(dict(stage='memory_affinity', type=type(error).__name__, message=str(error)))
    facts['affinity_scope'] = 'observed current process only; system groups are recorded separately'
    facts['affinity_modified'] = False
    if work_root is not None:
        path = Path(work_root).resolve()
        ancestor = path
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        facts.update(work_root=str(path), disk_probe_path=str(ancestor),
                     disk_free_bytes=shutil.disk_usage(ancestor).free)
    return facts


def probe_host(work_root=None):
    """Observe current facts only; unavailable facts remain explicit unknowns."""
    return dict(schema='hospital_ies.transfer_server_probe.v1', status='observed_only',
        created_utc=datetime.now(timezone.utc).isoformat(), host=_collect_host_facts(work_root),
        target_profile=target_profile(), target_host_validation='not_executed',
        server_scientific_admission='not_executed', solver_probe_performed=False,
        scientific_execution_performed=False, admission_granted=False)


def validate_target(facts):
    """Check provided facts, never create or replace a scientific admission."""
    if facts.get('system') != 'Windows':
        raise ProfileError('Target execution requires the declared Windows server')
    if not re.search(r'\bEPYC\s+9654\b', str(facts.get('cpu_model')), re.I):
        raise ProfileError('Target execution requires the declared AMD EPYC 9654')
    memory = facts.get('ram_total_bytes')
    if type(memory) is not int or memory < 240_000_000_000:
        raise ProfileError('At least 240 GB measured RAM is required for the declared 256 GB target')
    if facts.get('pointer_bits') != 64:
        raise ProfileError('A 64-bit Python is required to observe the full Windows processor groups')
    topology = facts.get('topology', {})
    if (topology.get('status') != 'observed' or not topology.get('processor_groups')
            or not topology.get('numa_nodes')):
        raise ProfileError('Current processor groups and NUMA topology must be observed, not assumed')
    affinity = facts.get('cpu_affinity')
    if not isinstance(affinity, list) or not affinity:
        raise ProfileError('Current process affinity could not be observed')
    if len(affinity) == 24 and set(affinity) == set(range(24)):
        raise ProfileError('Observed CPU0-23 restriction requires investigation; do not copy the old affinity')
    return dict(status='target_facts_match', admission_granted=False,
        scientific_execution_performed=False, server_scientific_admission='not_executed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('describe', help='Describe declared profile; no host probe')
    probe = commands.add_parser('probe', help='Read current host/topology only; no admission')
    probe.add_argument('--work-root', type=Path)
    check = commands.add_parser('check-target', help='Validate saved host facts; no admission')
    check.add_argument('--probe-file', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'describe':
        result = target_profile()
    elif args.command == 'probe':
        result = probe_host(args.work_root)
    else:
        receipt = json.loads(args.probe_file.read_text(encoding='utf-8-sig'))
        result = validate_target(receipt['host'])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
