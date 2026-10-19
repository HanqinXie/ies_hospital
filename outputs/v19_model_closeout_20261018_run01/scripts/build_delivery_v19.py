"""V19 compact ordinary-ZIP selection, dry sizing and independent extraction.

Default work is read-only dry sizing of scientific data. Build/verify require
explicit new D/E destinations. No old raw/archive is opened, no trajectory is
rescored, and no compression search or full-raw audit is performed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import time
import unicodedata
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[3]
RUN_NAME = "outputs/v19_model_closeout_20261018_run01"
RUN = ROOT / RUN_NAME
LIMIT = 200_000_000
CHUNK = 1024 * 1024
SKIP = frozenset(("__pycache__", ".pytest_cache", ".git", "source_snapshots", "delivery_builds"))
STORED = frozenset((".gz", ".zip", ".xz", ".png", ".jpg", ".pdf", ".xlsx", ".7z"))
RAW_ROLES = frozenset(("normal", "main_L1", "main_L2", "main_L3", "main_L4", "long000", "long005",
                       "propagation_ON", "propagation_OFF", "contact_0", "contact_0p25", "unknown_or_failure"))
SCHEMA = "hospital_ies.v19.compact_delivery.v1"


def utc():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + chr(10)).encode("utf-8")


def read(path):
    if Path(path).stat().st_size > 160_000_000:
        raise ValueError("Finite metadata read cap exceeded: " + str(path))
    return json.loads(Path(path).read_text(encoding="utf-8-sig"),
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON: " + value)))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(value if isinstance(value, bytes) else encoded(value))


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def safe_name(name):
    if (not isinstance(name, str) or not name or name.startswith(("/", "\\")) or "\\" in name
            or any(c in name for c in ':<>"|?*') or any(ord(c) < 32 for c in name)):
        raise ValueError("Unsafe ZIP member: " + str(name))
    for part in name.split("/"):
        if (part in ("", ".", "..") or part != part.rstrip(" .") or
                re.fullmatch(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", part.split(".")[0], re.I)):
            raise ValueError("Unsafe Windows ZIP component: " + name)
    return name


def unique_names(names):
    keys = set()
    for name in names:
        key = unicodedata.normalize("NFC", safe_name(name)).casefold()
        if key in keys:
            raise ValueError("Duplicate/case/Unicode ZIP alias: " + name)
        keys.add(key)
    for name in keys:
        parts = name.split("/")
        if any("/".join(parts[:i]) in keys for i in range(1, len(parts))):
            raise ValueError("ZIP file/ancestor collision: " + name)


def local(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
        raise ValueError("Selected source is outside workspace or missing: " + str(path))
    if path.is_symlink() or getattr(path.stat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        raise ValueError("Selected source is a link/reparse point: " + str(path))
    return path


def relative(path):
    return local(path).relative_to(ROOT.resolve()).as_posix()


def destination(path, *, archive=False, outside_workspace=False):
    original = Path(path)
    if not original.is_absolute():
        raise ValueError("An explicit absolute D/E destination is required")
    path = original.resolve()
    if os.name == "nt" and path.drive.lower() not in ("d:", "e:"):
        raise ValueError("Large delivery/stage output must use D/E, never C")
    if len(path.parts) < 2:
        raise ValueError("Drive root cannot be a delivery/stage destination")
    if archive and (path.suffix.lower() != ".zip" or path.is_relative_to(RUN.resolve())):
        raise ValueError("Use one ZIP outside the V19 evidence directory")
    if outside_workspace and (path.is_relative_to(ROOT.resolve()) or ROOT.resolve().is_relative_to(path)):
        raise ValueError("Standalone extraction must be outside the original workspace")
    if path.exists():
        raise FileExistsError("Destination must be new: " + str(path))
    return path


def raw_file(path):
    return path.name == "trajectory.json.gz" or ("raw" in path.parts and path.suffix == ".gz")


def live_binding():
    sys.path[:0] = [str(ROOT), str(ROOT / "src")]
    from tools.model_closeout.runner import source_binding
    return source_binding()


def freeze_binding(path, final):
    current = live_binding()
    if path is None:
        if final:
            raise ValueError("Final build requires --freeze")
        return current, None
    path = local(path)
    freeze = read(path)
    if final and freeze.get("kind") != "v19_final_shared_model_freeze":
        raise ValueError("Final delivery requires the final shared-model freeze, not a development freeze")
    if freeze.get("signature") != canonical({k:v for k,v in freeze.items() if k != "signature"}):
        raise ValueError("Final freeze signature mismatch")
    if freeze.get("model_id") != "M19_model_closeout" or freeze.get("source_sha256") != current:
        raise ValueError("Current source binding differs from the explicit M19 freeze")
    return current, dict(path=relative(path), sha256=sha(path), signature=freeze["signature"])


def raw_index():
    """Read compact producer receipts; never open raw payloads for a census."""
    index, states, manifests = {}, [], {}
    for path in sorted((RUN / "experiments").glob("*/pairs/*/RESULT.json")):
        try:
            record = read(path)
        except Exception as error:
            states.append(dict(result_path=relative(path), status="result_read_error", error=str(error)))
            continue
        row = {k:record.get(k) for k in ("scenario_id", "policy_id", "model_id", "status", "raw_sha256")}
        batch = path.parents[2]
        if batch not in manifests:
            manifest_path, plan_path = batch / "manifest.json", batch / "plan.json"
            manifest = read(manifest_path) if manifest_path.is_file() else {}
            batch_plan = read(plan_path) if plan_path.is_file() else {}
            manifests[batch] = ({s["id"]:s for s in manifest.get("scenarios", [])}, batch_plan)
        scenes, batch_plan = manifests[batch]
        scene = scenes.get(record.get("scenario_id"), {})
        row.update(batch=batch.name, canonical_class=scene.get("canonical_class"),
            enable_propagation=scene.get("enable_propagation"),
            registered_duration_minutes=float(scene.get("duration_steps", 0))*float(scene.get("step_minutes", 15.)),
            local_contact_restore_delay_minutes=batch_plan.get("model_config", {}).get("execution_recovery", {}).get("local_contact_restore_delay_minutes"))
        row.update(result_path=relative(path), result_sha256=sha(path),
            physical_error_present=record.get("error") is not None,
            analysis_error_present=record.get("analysis_error") is not None)
        raw = Path(record["raw_path"]) if record.get("raw_path") else path.parent / "trajectory.json.gz"
        if not raw.is_absolute():
            raw = ROOT / raw
        raw = raw.resolve()
        if raw.is_relative_to(RUN.resolve()):
            index[str(raw)] = row
        states.append(row)
    return index, states


def representative_plan(path, final):
    if path is None:
        if final:
            raise ValueError("Final build requires explicit --representatives")
        return dict(raw_entries=[], role_waivers={}, acknowledged_incomplete_batches={})
    plan = read(local(path))
    if final and plan.get("selection_basis") != "scenario_clock_and_failure_coverage_not_policy_winner":
        raise ValueError("Representative selection must state its coverage basis")
    for row in plan.get("raw_entries", []):
        if not row.get("reason") or not isinstance(row.get("roles"), list) or set(row["roles"]) - RAW_ROLES:
            raise ValueError("Every selected raw needs registered roles and an explicit coverage reason")
    if set(plan.get("role_waivers", {})) - RAW_ROLES:
        raise ValueError("Unknown raw coverage waiver")
    return plan


def historical_fixture_names():
    """Exact tiny inherited gate fixtures, never an old result/raw tree."""
    v17='outputs/v17_state_coordinated_20261018_run01'
    math_fixture=v17+'/review/ablation_interface_math_01'
    v18='outputs/v18_execution_recovery_20261018_run01/review/key01_c4_solver'
    return [math_fixture+'/selected_original_domain.npz',math_fixture+'/original_rows.json',
        v17+'/design/MAIN_PROPOSAL.json',
        *(math_fixture+'/layers/layer_'+str(i).zfill(2)+'.npz' for i in range(21)),
        *(v18+'/'+case+'/'+name for case in ('key01_000','key01_001')
            for name in ('domain.npz','rows_variables.json'))]


def runtime_metadata_names():
    """Original-byte lazy runtime inputs; supplement, never alter 66-file inventory."""
    return {
        'outputs/v13_executed_service_20261017_run01/design/DEVELOPMENT_POLICY_SPECS.json':
            'c89c50716465e8b0b3ee34e134e2a9ae7dcbf99f359472e69e3fe40885a15e9d',
        'outputs/v11_autonomous_dispatch_20261016_run01/new_data/standby_manifest.json':
            'ca01d42fd1e2cefdf5e9ead956d4237910eedcf382177695682a04e53088e1a3',
        'outputs/v15_engineering_operation_20261017_run01/source_data/p3_development_baseline/P3_SOURCE_RECEIPT.json':
            'c21a8f9302f38327fa3ec50122147e01e346c20253b859d1dc08602258cb2644',
        'outputs/v15_engineering_operation_20261017_run01/source_data/p3_frozen_baseline/P3_SOURCE_RECEIPT.json':
            '8ae27f4313e8a25e6f8e02b90b9be19c4509ea5994cf1c999baa02d8983709e9',
    }


def required_manifest_inventory(experiments):
    """Union signed registered byte inventories; do not prune runtime-idle inputs."""
    inventory = {}
    for manifest_path in sorted(Path(experiments).glob('*/manifest.json')):
        manifest = read(manifest_path)
        for row in manifest.get('files', []):
            name = safe_name(row['path'])
            if not valid_sha(row.get('sha256')):
                raise ValueError('Invalid registered inventory SHA: '+str(manifest_path)+' '+name)
            if name in inventory and inventory[name]['sha256'] != row['sha256']:
                raise ValueError('Conflicting registered input inventories: '+name)
            inventory.setdefault(name,dict(path=name,sha256=row['sha256'],
                registered_bytes=row.get('bytes'),manifests=[]))['manifests'].append(relative(manifest_path))
    return inventory


def assemble(representatives=None, freeze=None, *, final=False):
    binding, freeze_receipt = freeze_binding(freeze, final)
    plan = representative_plan(representatives, final)
    selections = {relative(ROOT / row["path"]): row for row in plan.get("raw_entries", [])}
    if len(selections) != len(plan.get("raw_entries", [])):
        raise ValueError("Repeated representative raw path")
    raw_meta, producer_states = raw_index()
    entries, omissions, raw_ledger, source_variants, variants_by_sha = {}, [], [], [], {}
    incomplete = []

    def add(path, reason, *, member=None, expected=None, raw=False):
        path = local(path)
        name = safe_name(member or path.relative_to(ROOT).as_posix())
        size = path.stat().st_size
        if name in entries:
            if entries[name]["source_path"] != str(path):
                raise ValueError("Two physical files target one ZIP path")
            if expected is not None:
                if entries[name]['sha256'] not in (None, expected):
                    raise ValueError('Conflicting source/inventory SHA for one ZIP member: '+name)
                entries[name]['sha256'] = expected
            return
        entries[name] = dict(member=name, source_path=str(path), bytes=size, sha256=expected,
            reason=reason, scientific_raw=raw, compression="stored" if raw or path.suffix.lower() in STORED else "deflate6")

    for base in ("src", "tools", "tests", "configs"):
        for path in sorted((ROOT / base).rglob("*")):
            if path.is_file() and not any(part in SKIP for part in path.parts) and path.name != "bootstrap_planners.py":
                if base in ("src", "tools") and path.suffix != ".py":
                    continue
                add(path, "M19 signed production closure" if base != "tests" else "original and M19 regression tests/fixtures")
    for base in ("loads", "energyplus", "reference", "optional"):
        for path in sorted((ROOT / "inputs" / base).rglob("*")):
            if path.is_file():
                add(path, "unchanged required runtime input and source provenance")
    for name in ("pyproject.toml", "setup.cfg", "requirements.txt", "README.md", "AGENTS.md", "V19_Hospital_IES_Model_Closeout_Task.txt"):
        path = ROOT / name
        if path.is_file():
            add(path, "runtime setup or explicit task boundary")
    for path in sorted((ROOT / "docs/v19").rglob("*")):
        if path.is_file():
            add(path, "M19 final methods/reproduction/report")
    for name, expected in binding.items():
        add(ROOT / name, "exact source_binding dependency", expected=expected)
    registered_inventory = required_manifest_inventory(RUN/'experiments')
    for name, row in registered_inventory.items():
        add(ROOT/name, 'complete registered manifest.files input/source inventory; no idle-input pruning',
            expected=row['sha256'])
    # Only small old design/freeze metadata needed by the unchanged inheritance.
    old = ROOT / "outputs/v18_execution_recovery_20261018_run01"
    for path in [old / "MODEL_FREEZE_RECOVERY01.json", *sorted((old / "design").glob("*.json"))]:
        if path.is_file():
            add(path, "read-only SOURCE05/design provenance; no old raw/archive")
    for name in historical_fixture_names():
        path=ROOT/name
        if not path.is_file():
            raise ValueError('Required pinned inherited regression fixture is missing: '+name)
        add(path,'exact inherited gate mathematical regression fixture/proposal; no old trajectory')
    for name, expected in runtime_metadata_names().items():
        path = ROOT/name
        if not path.is_file() or sha(path) != expected:
            raise ValueError('Pinned lazy runtime metadata absent or changed: '+name)
        add(path, 'original lazy policy/manifest or offline P3 source metadata; no old raw', expected=expected)
    for experiment in sorted((RUN / "experiments").glob("*")):
        if not experiment.is_dir():
            continue
        p = experiment / "plan.json"
        if p.is_file():
            registered = read(p)
            snapshot = Path(registered.get("source_snapshot", ""))
            if not snapshot.is_absolute():
                snapshot = ROOT / snapshot
            for name, expected in registered.get("source_sha256", {}).items():
                current = binding.get(name)
                if current == expected:
                    continue
                original = snapshot / name
                if not original.is_file():
                    raise ValueError("Unique registered source variant missing: " + str(original))
                object_name = "delivery_metadata/source_objects/" + expected + original.suffix
                if expected not in variants_by_sha:
                    add(original, "unique historical M19 source variant; deduplicated object", member=object_name, expected=expected)
                    variants_by_sha[expected] = object_name
                source_variants.append(dict(plan_path=relative(p), logical_source_path=name,
                    sha256=expected, source_object_member=object_name))
        if not (experiment / "BATCH_RESULT.json").is_file():
            incomplete.append(experiment.name)
    if final and set(incomplete) - set(plan.get("acknowledged_incomplete_batches", {})):
        raise ValueError("Unfinished registered batches need explicit unknown-status acknowledgment: " + str(incomplete))
    for path in sorted(RUN.rglob("*")):
        if not path.is_file() or any(part in SKIP for part in path.relative_to(RUN).parts):
            continue
        name = relative(path)
        if path.suffix.lower() == ".zip":
            omissions.append(dict(path=name, bytes=path.stat().st_size, reason="auxiliary archive/test container; never nest archives"))
            continue
        if raw_file(path):
            meta = raw_meta.get(str(path.resolve()), {})
            producer_sha = meta.get("raw_sha256") if valid_sha(meta.get("raw_sha256")) else None
            selected = name in selections
            orphan = not meta
            synthetic = "test_tmp" in path.relative_to(RUN).parts
            # Small test fixtures can be hashed; this is not a scientific raw audit.
            if synthetic and path.stat().st_size <= 2_000_000 and producer_sha is None:
                producer_sha = sha(path)
            row = dict(path=name, original_path=str(path.resolve()), bytes=path.stat().st_size,
                sha256=producer_sha, sha_basis="producer RESULT receipt" if meta else "small synthetic fixture" if producer_sha else "unknown",
                selected=selected, orphan_or_unindexed=orphan, synthetic_test_fixture=synthetic,
                status=meta.get("status", "unindexed_unknown"), **{k:v for k,v in meta.items() if k not in ("status", "raw_sha256")})
            if selected:
                if meta and meta.get("model_id") != "M19_model_closeout":
                    raise ValueError("Representative raw is not bound to M19")
                roles = selections[name]["roles"]
                for role in roles:
                    if role.startswith("main_L") and meta.get("canonical_class") != role.removeprefix("main_"):
                        raise ValueError("Representative canonical class does not match manifest: " + name)
                    if role in ("propagation_ON", "propagation_OFF") and meta.get("enable_propagation") is not (role == "propagation_ON"):
                        raise ValueError("Representative propagation label does not match manifest")
                    wanted_delay = {"contact_0": 0., "contact_0p25": .25}.get(role)
                    if wanted_delay is not None and meta.get("local_contact_restore_delay_minutes") != wanted_delay:
                        raise ValueError("Representative contact clock does not match registered model configuration")
                row.update(roles=selections[name]["roles"], selection_reason=selections[name]["reason"])
                add(path, "representative whole raw; " + selections[name]["reason"], expected=producer_sha, raw=True)
            elif producer_sha is None and final:
                raise ValueError("Omitted raw lacks producer SHA; include it as explicit unknown representative: " + name)
            else:
                omissions.append(dict(row, reason="whole raw omitted for compactness; all producer metadata/unknown states retained"))
            raw_ledger.append(row)
        else:
            add(path, "all new B19 metadata/CSV/log/failure/unknown evidence (excluding duplicate source trees)")
    missing = set(selections) - {r["path"] for r in raw_ledger}
    if missing:
        raise ValueError("Selected raw is not a new V19 whole trajectory: " + str(sorted(missing)))
    observed_roles = {role for row in raw_ledger if row["selected"] for role in row.get("roles", [])}
    waived = {role for role, reason in plan.get("role_waivers", {}).items() if isinstance(reason, str) and reason.strip()}
    missing_roles = sorted(RAW_ROLES - observed_roles - waived)
    if final and missing_roles:
        raise ValueError("Representative coverage is incomplete: " + str(missing_roles))
    groups = Counter()
    for row in entries.values():
        name = row["member"]
        groups[name.split("/")[0]] += row["bytes"]
    generated = {
        "delivery_metadata/NEW_RAW_LEDGER.json": encoded(dict(schema=SCHEMA+".raw_ledger", entries=raw_ledger,
            producer_states=producer_states, raw_payloads_reopened_for_census=0,
            old_raw_included=False, outcomes_not_filtered=True)),
        "delivery_metadata/OMITTED.json": encoded(dict(schema=SCHEMA+".omitted", entries=omissions,
            excluded_duplicate_tree_names=sorted(SKIP), old_archives_and_old_raw_never_copied=True)),
        "delivery_metadata/SOURCE_VARIANT_MAP.json": encoded(dict(entries=source_variants,
            full_source_snapshot_trees_copied=False, unique_objects=len(variants_by_sha))),
        "delivery_metadata/SOURCE_BINDING.json": encoded(dict(schema=SCHEMA+".source_binding", binding=binding,
            source_binding_sha256=canonical(binding), freeze=freeze_receipt)),
        "delivery_metadata/REPRESENTATIVE_SELECTION.json": encoded(plan),
        "delivery_metadata/REGISTERED_INPUT_INVENTORY.json": encoded(dict(
            entries=list(registered_inventory.values()),all_declared_paths_included=True,
            inventory_not_reduced_by_runtime_use=True)),
        "delivery_metadata/RUNTIME_METADATA_CLOSURE.json": encoded(dict(
            binding=runtime_metadata_names(), original_source_binding_unchanged=True,
            original_66_file_inventory_unchanged=True,
            role='lazy policy/manifest configuration and offline P3 provenance only; no old raw')),
    }
    unique_names([*entries, *generated])
    return dict(entries=entries, generated=generated, omissions=omissions, raw_ledger=raw_ledger,
        raw_roles_observed=sorted(observed_roles), raw_roles_missing=missing_roles,
        role_waivers=plan.get("role_waivers", {}), incomplete_batches=incomplete,
        source_variants=source_variants, freeze=freeze_receipt, source_binding=binding,
        uncompressed_bytes_by_top_level=dict(groups))


def profile_file(row):
    path = Path(row["source_path"])
    if row["scientific_raw"]:
        return row["bytes"], row["sha256"], "producer SHA; raw not reopened in dry-run"
    h = hashlib.sha256()
    compressor = zlib.compressobj(6, zlib.DEFLATED, -15) if row["compression"] == "deflate6" else None
    compressed = 0
    before = path.stat()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            h.update(block)
            compressed += len(compressor.compress(block)) if compressor else len(block)
    if compressor:
        compressed += len(compressor.flush())
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Metadata changed during dry sizing: " + str(path))
    actual = h.hexdigest()
    if row["sha256"] is not None and actual != row["sha256"]:
        raise ValueError("Source-bound file changed: " + str(path))
    return compressed, actual, "streamed metadata SHA/ordinary DEFLATE6 estimate"


def dry_run(label, representatives=None, freeze=None):
    if not label or not label.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Use a simple fresh dry label")
    out = RUN / "review" / ("delivery_dry_" + label)
    if out.exists():
        raise FileExistsError(out)
    value = assemble(representatives, freeze)
    selected = []
    estimated = 22
    for row in value["entries"].values():
        row = dict(row)
        size, actual_sha, basis = profile_file(row)
        row.update(sha256=actual_sha, estimated_zip_payload_bytes=size, sha_verification=basis)
        estimated += size + 130 + 2*len(row["member"].encode("utf-8"))
        selected.append(row)
    for name, data in value["generated"].items():
        comp = zlib.compressobj(6, zlib.DEFLATED, -15)
        estimated += len(comp.compress(data)+comp.flush()) + 130 + 2*len(name.encode("utf-8"))
    report = dict(schema=SCHEMA+".dry", utc=utc(), final_archive_built=False, actual_archive_bytes=None,
        hard_limit_bytes=LIMIT, strict_less_than_limit=True, estimated_ordinary_zip_bytes=estimated,
        estimated_headroom_bytes=LIMIT-estimated, estimate_is_not_final_size_verification=True,
        selected_file_count=len(selected), selected_uncompressed_bytes=sum(r["bytes"] for r in selected),
        selected_raw_files=sum(r["scientific_raw"] for r in selected),
        selected_raw_bytes=sum(r["bytes"] for r in selected if r["scientific_raw"]),
        new_raw_census_count=len(value["raw_ledger"]), omitted_raw_count=sum(not r["selected"] for r in value["raw_ledger"]),
        unresolved_raw_sha_count=sum(not valid_sha(r["sha256"]) for r in value["raw_ledger"]),
        raw_roles_observed=value["raw_roles_observed"], raw_roles_missing=value["raw_roles_missing"],
        uncompressed_bytes_by_top_level=value["uncompressed_bytes_by_top_level"],
        incomplete_batches=value["incomplete_batches"], physical_episodes_started=0,
        scientific_raw_payloads_read=0, old_archive_or_raw_reads=0, compression_algorithm="ordinary ZIP DEFLATE6; precompressed/raw stored",
        disk_free_bytes={d:shutil.disk_usage(d).free for d in ("D:/", "E:/") if Path(d).exists()})
    out.mkdir(parents=True)
    write_new(out / "DRY_ESTIMATE.json", report)
    write_new(out / "FILE_SELECTION.json", dict(schema=SCHEMA+".selection_draft", entries=selected))
    write_new(out / "OMITTED.json", dict(entries=value["omissions"], raw_ledger=value["raw_ledger"]))
    return report


def verify_zip(archive, entries, limit=LIMIT):
    archive = Path(archive)
    if archive.stat().st_size >= limit:
        raise ValueError("Single ZIP is not strictly below the decimal byte cap")
    expected = {r["member"]:r for r in entries}
    unique_names([r["member"] for r in entries])
    verified = []
    with zipfile.ZipFile(archive) as source:
        infos = source.infolist()
        unique_names([info.filename for info in infos])
        if {info.filename for info in infos} != set(expected):
            raise ValueError("ZIP exhaustive membership mismatch")
        for info in infos:
            kind = stat.S_IFMT(info.external_attr >> 16)
            wanted = expected[info.filename]
            if info.is_dir() or kind not in (0, stat.S_IFREG) or info.flag_bits & 1:
                raise ValueError("Nonregular/encrypted ZIP member")
            if info.file_size != wanted["bytes"]:
                raise ValueError("ZIP member header size mismatch")
            h, length, crc = hashlib.sha256(), 0, 0
            with source.open(info) as stream:
                for block in iter(lambda: stream.read(CHUNK), b""):
                    h.update(block); length += len(block); crc = zlib.crc32(block, crc)
            if length != wanted["bytes"] or h.hexdigest() != wanted["sha256"] or crc & 0xffffffff != info.CRC:
                raise ValueError("ZIP member CRC/SHA/size mismatch: " + info.filename)
            verified.append(dict(member=info.filename, bytes=length, sha256=h.hexdigest(), crc32=f"{crc & 0xffffffff:08x}"))
    return dict(schema=SCHEMA+".verification", archive=str(archive), archive_bytes=archive.stat().st_size,
        archive_sha256=sha(archive), maximum_archive_bytes=limit, under_limit=True,
        exact_membership_verified=True, all_member_crc_sha_size_verified=True, members=verified,
        physical_episodes_started=0, scientific_results_rescored=False)


def build(archive, representatives, freeze, repro_commands=None):
    archive = destination(archive, archive=True)
    building = archive.with_suffix(".building.zip")
    if building.exists():
        raise FileExistsError("Prior building/failure artifact is immutable")
    value = assemble(representatives, freeze, final=True)
    generated = dict(value["generated"])
    commands = read(local(repro_commands)) if repro_commands else dict(commands=[], actual_reproduction_not_yet_verified=True)
    generated["delivery_metadata/REPRO_COMMANDS.json"] = encoded(commands)
    chosen = []
    for row in value["entries"].values():
        row = dict(row)
        if not row["scientific_raw"]:
            _, row["sha256"], _ = profile_file(row)
        chosen.append(row)
    required_bytes = sum(r["bytes"] for r in chosen)
    if shutil.disk_usage(archive.parent if archive.parent.exists() else archive.parent.parent).free < LIMIT + 512*1024**2:
        raise OSError("Insufficient archive workspace; choose explicit E destination")
    archive.parent.mkdir(parents=True, exist_ok=True)
    started = utc()
    try:
        with zipfile.ZipFile(building, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as target:
            for row in chosen:
                path = Path(row["source_path"])
                h, length = hashlib.sha256(), 0
                before = path.stat()
                info = zipfile.ZipInfo(row["member"])
                info.compress_type = zipfile.ZIP_STORED if row["compression"] == "stored" else zipfile.ZIP_DEFLATED
                info._compresslevel = 6
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                with path.open("rb") as source, target.open(info, "w", force_zip64=True) as stream:
                    for block in iter(lambda: source.read(CHUNK), b""):
                        stream.write(block); h.update(block); length += len(block)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or length != row["bytes"]:
                    raise ValueError("Selected file changed during build: " + str(path))
                if row["sha256"] is not None and h.hexdigest() != row["sha256"]:
                    raise ValueError("Selected source/producer SHA mismatch: " + row["member"])
                row["sha256"] = h.hexdigest()
            # A selected orphan/unknown raw obtains its identity while streamed
            # into ZIP; never leave its in-archive raw ledger with a null SHA.
            packed = {row["member"]:row["sha256"] for row in chosen}
            raw_document = json.loads(generated["delivery_metadata/NEW_RAW_LEDGER.json"])
            for row in raw_document["entries"]:
                if row["selected"]:
                    row["sha256"] = packed[row["path"]]
                    row["sha_basis"] = "verified while streaming selected whole raw into ZIP"
            generated["delivery_metadata/NEW_RAW_LEDGER.json"] = encoded(raw_document)
            for name, data in generated.items():
                target.writestr(name, data)
                chosen.append(dict(member=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                    source_path=None, reason="generated exhaustive provenance manifest", scientific_raw=False))
        verification = verify_zip(building, chosen)
        selection = dict(schema=SCHEMA+".file_selection", utc=utc(), entries=chosen,
            selected_uncompressed_bytes=required_bytes, mandatory_metadata_outcomes_not_filtered=True,
            build_started_utc=started, archive_intended_path=str(archive))
        write_new(archive.with_suffix(".FILE_SELECTION.json"), selection)
        write_new(archive.with_suffix(".OMITTED.json"), dict(schema=SCHEMA+".omitted", entries=value["omissions"],
            raw_ledger=value["raw_ledger"], producer_raw_hashes_not_recomputed_for_omitted=True))
        verification.update(archive=str(archive), source_binding_sha256=canonical(value["source_binding"]),
            representative_roles=value["raw_roles_observed"], role_waivers=value["role_waivers"],
            standalone_verified=False, build_finished_utc=utc())
        write_new(archive.with_suffix(".VERIFICATION.json"), verification)
        building.rename(archive)
        write_new(archive.with_suffix(".sha256"), (verification["archive_sha256"] + "  " + archive.name + chr(10)).encode())
        return {k:v for k,v in verification.items() if k != "members"}
    except Exception as error:
        failure = dict(schema=SCHEMA+".build_failure", utc=utc(), error_type=type(error).__name__,
            message=str(error), intended_archive=str(archive), partial_archive=str(building),
            partial_bytes=building.stat().st_size if building.exists() else None,
            physical_episodes_started=0, failed_artifact_retained=True)
        failure_path = archive.with_suffix(".FAILED.json")
        if not failure_path.exists():
            write_new(failure_path, failure)
        for suffix, data in ((".FAILED_SELECTION.json", dict(entries=chosen, complete_archive_verified=False)),
                             (".FAILED_OMITTED.json", dict(entries=value["omissions"], raw_ledger=value["raw_ledger"]))):
            path = archive.with_suffix(suffix)
            if not path.exists():
                write_new(path, data)
        raise


def command_root(stage, command):
    """Commands may use only the extracted root or its fresh fixed science root."""
    stage=Path(stage).resolve()
    name=command.get('cwd','.')
    if name not in ('.','SCIENCE_ROOT'):
        raise ValueError('Command cwd must be . or the fixed SCIENCE_ROOT inside stage')
    root=stage if name=='.' else (stage/name).resolve()
    if not root.is_relative_to(stage) or not root.is_dir():
        raise ValueError('Command cwd is missing or escapes the independently extracted stage')
    return root


def _child(stage, argv, label, env, *, command=None):
    if not isinstance(argv, list) or not argv or argv[0] not in ("python", "python3"):
        raise ValueError("Reproduction commands must use argv with the existing Python runtime")
    if any(str(ROOT).casefold() in str(arg).casefold() for arg in argv[1:]):
        raise ValueError("Standalone command references original workspace")
    logs = stage / "delivery_verification_logs"
    logs.mkdir(exist_ok=True)
    started = time.perf_counter()
    root=command_root(stage,command or {})
    child_env=env.copy()
    temporary=root/'delivery_verification_temp';temporary.mkdir(exist_ok=True)
    child_env.update(PYTHONPATH=str(root/'src')+os.pathsep+str(root),TEMP=str(temporary),TMP=str(temporary))
    with (logs / (label+".stdout.log")).open("xb") as stdout, (logs / (label+".stderr.log")).open("xb") as stderr:
        result = subprocess.run([sys.executable, *argv[1:]], cwd=root, env=child_env,
            stdout=stdout, stderr=stderr, check=False)
    return dict(label=label, argv=argv, cwd=str(root), independent_stage=str(stage),exit_code=result.returncode,
        elapsed_seconds=time.perf_counter()-started,
        stdout_sha256=sha(logs / (label+".stdout.log")), stderr_sha256=sha(logs / (label+".stderr.log")))


def reproduction_proof_spec(stage, command):
    """A label/zero exit is insufficient: require a fresh M19 terminal artifact."""
    if command.get("kind") != "scientific_reproduction":
        return None
    argv = command.get("argv", [])
    if (not isinstance(argv, list) or "--help" in argv or "-h" in argv
            or "tools.model_closeout.runner" not in argv or "run" not in argv or "--directory" not in argv):
        raise ValueError("Scientific reproduction must execute the actual M19 runner, not a help/smoke command")
    index = argv.index("--directory") + 1
    if index >= len(argv):
        raise ValueError("Scientific reproduction directory is missing")
    directory = safe_name(str(argv[index]).replace("\\", "/"))
    root=command_root(stage,command)
    prefix='' if root==Path(stage).resolve() else 'SCIENCE_ROOT/'
    proof_name = prefix + directory + "/BATCH_RESULT.json"
    if command.get("proof_json") != proof_name or not command.get("expected_fields"):
        raise ValueError("Scientific reproduction needs its fresh BATCH_RESULT proof and preregistered expected fields")
    proof_path = Path(stage).joinpath(*PurePosixPath(proof_name).parts)
    if proof_path.exists():
        raise ValueError("Scientific reproduction proof already exists in stage; no old result may substitute for a fresh run")
    if not proof_path.resolve().is_relative_to(root):
        raise ValueError('Scientific proof escaped its registered runtime cwd')
    return dict(path=proof_path, directory=root.joinpath(*PurePosixPath(directory).parts),
                runtime_cwd=str(root),
                expected_fields=command["expected_fields"])


def verify_reproduction_proof(spec):
    proof = read(spec["path"])
    directory = spec["directory"]
    plan = read(directory / "plan.json")
    if (plan.get("model_id") != "M19_model_closeout" or proof.get("source_unchanged") is not True
            or not isinstance(proof.get("actual_attempts"), int) or proof["actual_attempts"] < 1
            or proof.get("plan_sha256") != sha(directory / "plan.json")
            or proof.get("manifest_sha256") != sha(directory / "manifest.json")):
        raise ValueError("Fresh scientific reproduction terminal proof has invalid model/source/attempt bindings")
    for key, expected in spec["expected_fields"].items():
        if proof.get(key) != expected:
            raise ValueError("Fresh scientific reproduction differs from registered expectation: " + key)
    return dict(proof_json=str(spec["path"]), proof_sha256=sha(spec["path"]),
        actual_attempts=proof["actual_attempts"], expected_fields_matched=True,
        scientific_reproduction_proof_verified=True)


def standalone(archive, stage):
    archive = Path(archive).resolve()
    stage = destination(stage, outside_workspace=True)
    selection = read(archive.with_suffix(".FILE_SELECTION.json"))
    expected = selection["entries"]
    verification = verify_zip(archive, expected)
    receipt = dict(schema=SCHEMA+".standalone", archive=str(archive), archive_sha256=verification["archive_sha256"],
        stage=str(stage), status="started", all_member_crc_sha_size_verified=True,
        original_workspace_used_for_import_or_reproduction=False)
    external_receipt = archive.with_suffix(".STANDALONE_VERIFICATION.json")
    if external_receipt.exists():
        raise FileExistsError("Standalone receipt is immutable; use a new archive/receipt attempt")
    needed = sum(r["bytes"] for r in expected) + 512*1024**2
    existing_parent = next(p for p in stage.parents if p.exists())
    if shutil.disk_usage(existing_parent).free < needed:
        raise OSError("Insufficient independent extraction space; explicitly choose E destination")
    stage.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive) as source:
            for row in expected:
                name = safe_name(row["member"])
                target = stage.joinpath(*PurePosixPath(name).parts)
                if not target.resolve().is_relative_to(stage):
                    raise ValueError("Extraction escaped explicit fresh stage")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(name) as inp, target.open("xb") as out:
                    shutil.copyfileobj(inp, out, CHUNK)
                if target.stat().st_size != row["bytes"] or sha(target) != row["sha256"]:
                    raise ValueError("Fresh extracted member SHA mismatch: " + name)
        env = os.environ.copy()
        env.update(PYTHONPATH=str(stage / "src") + os.pathsep + str(stage),
            PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        temporary = stage / "delivery_verification_temp"
        temporary.mkdir()
        env.update(TEMP=str(temporary), TMP=str(temporary))
        check = """from pathlib import Path
import importlib,json,sys
root=Path.cwd().resolve()
for name in ('tools.model_closeout.runner','tools.model_closeout.analysis','tools.model_closeout.environment','hospital_ies.model'):
    module=importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(root), (name,module.__file__)
from tools.model_closeout.runner import source_binding
expected=json.loads((root/'delivery_metadata/SOURCE_BINDING.json').read_text(encoding='utf-8'))['binding']
assert source_binding()==expected, 'staged exact source binding mismatch'
assert all((root/name).is_file() for name in ('inputs/loads/hospital_ies_demand_8760.csv','inputs/loads/critical_zones_8760.csv','inputs/energyplus/Hospital_SanDiego.idf','configs/plant_configs.json'))
print(json.dumps({'stage_root':str(root),'source_binding_exact':True,'runtime_imports_from_stage':True}))
"""
        commands = [dict(kind="source_and_import_check", argv=["python", "-c", check]),
            dict(kind="CLI_availability", argv=["python", "-m", "tools.model_closeout.runner", "--help"]),
            dict(kind="CLI_availability", argv=["python", "-m", "tools.model_closeout.analysis", "summarize", "--help"])]
        repro = read(stage / "delivery_metadata/REPRO_COMMANDS.json")
        commands.extend(repro.get("commands", []))
        results = []
        receipt["commands"] = results
        for index, command in enumerate(commands):
            proof_spec = reproduction_proof_spec(stage, command)
            row = _child(stage, command["argv"], f"command_{index:02d}", env,command=command)
            row["kind"] = command["kind"]
            results.append(row)
            if row["exit_code"] != 0:
                raise RuntimeError("Independent staged command failed: " + str(row))
            if proof_spec is not None:
                row.update(verify_reproduction_proof(proof_spec))
        actual_repro = any(row.get("scientific_reproduction_proof_verified") is True for row in results)
        receipt.update(status="passed" if actual_repro else "storage_and_interface_only_reproduction_unexecuted",
            extracted_member_count=len(expected), commands=results,
            actual_scientific_reproduction_verified=actual_repro,
            original_inputs_sha_verified=True, exact_source_binding_verified=True,
            stage_retained=True, finished_utc=utc())
        write_new(external_receipt, receipt)
        return receipt
    except Exception as error:
        receipt.update(status="failed_unknown", error_type=type(error).__name__, message=str(error),
            stage_retained=True, finished_utc=utc())
        write_new(external_receipt, receipt)
        raise


def self_test(label):
    """Small storage-security regressions only; no project data/raw opened."""
    out = RUN / "review" / ("delivery_selftest_" + label)
    out.mkdir(parents=True, exist_ok=False)
    for name in ("../escape", "C:/escape", "x/CON.txt", "a\\b", "a/./b"):
        try:
            safe_name(name)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe ZIP member accepted")
    try:
        unique_names(["A.txt", "a.txt"])
    except ValueError:
        pass
    else:
        raise AssertionError("case alias accepted")
    # A development freeze may not authorize a final delivery.
    development = dict(kind="v19_development_source_freeze", model_id="M19_model_closeout", source_sha256={})
    development["signature"] = canonical(development)
    development_path = out / "synthetic_development_freeze.json"
    write_new(development_path, development)
    actual_live_binding = globals()["live_binding"]
    globals()["live_binding"] = lambda: {}
    try:
        try:
            freeze_binding(development_path, True)
        except ValueError:
            pass
        else:
            raise AssertionError("Development freeze incorrectly accepted for final build")
    finally:
        globals()["live_binding"] = actual_live_binding
    try:
        reproduction_proof_spec(out, dict(kind="scientific_reproduction",
            argv=["python", "-m", "tools.model_closeout.runner", "--help"]))
    except ValueError:
        pass
    else:
        raise AssertionError("CLI help incorrectly qualifies as actual scientific reproduction")
    # Registered inventories and a fresh fixed-name scientific root are part of
    # the independent replay contract; these are tiny synthetic metadata only.
    assert 'required_manifest_inventory' in globals(), 'Registered manifest input closure is not implemented'
    manifests = out / 'fixture_inventory'
    write_new(manifests/'one/manifest.json', dict(files=[dict(path='inputs/literature/a.pdf',
        sha256='a'*64, bytes=12)]))
    inventory = required_manifest_inventory(manifests)
    assert inventory['inputs/literature/a.pdf']['sha256'] == 'a'*64
    write_new(manifests/'two/manifest.json', dict(files=[dict(path='inputs/literature/a.pdf',
        sha256='b'*64, bytes=12)]))
    try:
        required_manifest_inventory(manifests)
    except ValueError:
        pass
    else:
        raise AssertionError('Conflicting manifest inventory accepted')
    science = out / 'SCIENCE_ROOT'
    science.mkdir()
    directory_name = RUN_NAME+'/experiments/standalone_replay01'
    command = dict(kind='scientific_reproduction',cwd='SCIENCE_ROOT',
        argv=['python','-m','tools.model_closeout.runner','run','--directory',directory_name],
        proof_json='SCIENCE_ROOT/'+directory_name+'/BATCH_RESULT.json',expected_fields=dict(actual_attempts=1))
    proof = reproduction_proof_spec(out, command)
    assert proof['path'] == science/directory_name/'BATCH_RESULT.json', 'Scientific cwd/proof routing is not implemented'
    for invalid in ('../elsewhere','another_root','SCIENCE_ROOT/../elsewhere'):
        try:
            reproduction_proof_spec(out,dict(command,cwd=invalid))
        except ValueError:
            pass
        else:
            raise AssertionError('Unapproved command cwd accepted')
    child = _child(out,['python','-c',
        "import os;from pathlib import Path;r=Path.cwd();assert r.name=='SCIENCE_ROOT';assert os.environ['PYTHONPATH']==str(r/'src')+os.pathsep+str(r);assert Path(os.environ['TEMP']).parent==r;assert Path(os.environ['TMP']).parent==r"],
        'synthetic_cwd_environment',os.environ.copy(),command=dict(cwd='SCIENCE_ROOT'))
    assert child['exit_code']==0 and child['cwd']==str(science.resolve()), 'Child cwd/environment escaped SCIENCE_ROOT'
    assert 'historical_fixture_names' in globals(), 'Pinned historical regression fixture closure is missing'
    pinned=historical_fixture_names()
    assert len(pinned)==28 and len(set(pinned))==28
    assert sum('/layers/layer_' in name and name.endswith('.npz') for name in pinned)==21
    assert all('trajectory' not in name and not name.endswith('.gz') for name in pinned)
    assert any(name.endswith('/design/MAIN_PROPOSAL.json') for name in pinned)
    # Actual independent regression exposed lazy policy/manifest reads outside
    # both the frozen Python closure and the original 66-file input inventory.
    assert 'runtime_metadata_names' in globals(), 'Lazy historical runtime metadata closure is missing'
    supplementary = runtime_metadata_names()
    assert supplementary['outputs/v13_executed_service_20261017_run01/design/DEVELOPMENT_POLICY_SPECS.json'] == 'c89c50716465e8b0b3ee34e134e2a9ae7dcbf99f359472e69e3fe40885a15e9d'
    assert supplementary['outputs/v11_autonomous_dispatch_20261016_run01/new_data/standby_manifest.json'] == 'ca01d42fd1e2cefdf5e9ead956d4237910eedcf382177695682a04e53088e1a3'
    assert len(supplementary) == 4 and all(name.endswith('.json') for name in supplementary)
    archive = out / "synthetic.zip"
    data = b"independent ZIP synthetic identity check"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as target:
        target.writestr("metadata.txt", data)
    entries = [dict(member="metadata.txt", bytes=len(data), sha256=hashlib.sha256(data).hexdigest())]
    verified = verify_zip(archive, entries)
    for bad_entries, cap in (([dict(entries[0], sha256="0"*64)], LIMIT), (entries, archive.stat().st_size), ([], LIMIT)):
        try:
            verify_zip(archive, bad_entries, cap)
        except ValueError:
            pass
        else:
            raise AssertionError("changed SHA, exact-limit or membership accepted")
    damaged = bytearray(archive.read_bytes())
    with zipfile.ZipFile(archive) as source:
        header = source.infolist()[0].header_offset
    data_offset = header + 30 + int.from_bytes(damaged[header+26:header+28], "little") + int.from_bytes(damaged[header+28:header+30], "little")
    damaged[data_offset] ^= 0xff
    corrupt = out / "synthetic_corrupt.zip"
    write_new(corrupt, bytes(damaged))
    try:
        verify_zip(corrupt, entries)
    except (ValueError, zipfile.BadZipFile, zlib.error):
        pass
    else:
        raise AssertionError("Corrupted member was accepted")
    result = dict(status="passed", small_synthetic_archive_only=True,
        guards_checked=["path traversal", "Windows aliases", "Unicode/case duplicates", "exact membership",
                        "member SHA/CRC/size", "strict decimal cap including equality", "final freeze kind",
                        "CLI help cannot be scientific reproduction", "complete registered input inventory",
                        "conflicting inventory SHA rejected", "fixed SCIENCE_ROOT cwd and proof routing",
                        "unapproved command cwd rejected", "actual child cwd/PYTHONPATH/TEMP/TMP isolation",
                        "28 pinned inherited regression inputs including 21 objective layers; no old raw",
                        "four pinned lazy runtime policy/manifest and offline P3 provenance JSON supplements"],
        verified_member_count=len(verified["members"]), physical_episodes_started=0)
    write_new(out / "SELF_TEST.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    dry = sub.add_parser("dry-run")
    dry.add_argument("--label", required=True)
    dry.add_argument("--representatives", type=Path)
    dry.add_argument("--freeze", type=Path)
    build_cli = sub.add_parser("build")
    build_cli.add_argument("--destination", type=Path, required=True)
    build_cli.add_argument("--representatives", type=Path, required=True)
    build_cli.add_argument("--freeze", type=Path, required=True)
    build_cli.add_argument("--repro-commands", type=Path)
    verify = sub.add_parser("standalone-verify")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--destination", type=Path, required=True)
    test = sub.add_parser("self-test")
    test.add_argument("--label", required=True)
    args = parser.parse_args(argv)
    if args.command == "dry-run":
        result = dry_run(args.label, args.representatives, args.freeze)
    elif args.command == "build":
        result = build(args.destination, args.representatives, args.freeze, args.repro_commands)
    elif args.command == "standalone-verify":
        result = standalone(args.archive, args.destination)
    else:
        result = self_test(args.label)
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
