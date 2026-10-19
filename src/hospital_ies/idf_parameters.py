"""Extract simple geometrical/mass inputs from the **original** DOE San Diego IDF.

No EnergyPlus simulation is performed here; these are inputs to an explicit
LOW-ORDER thermal surrogate, not validated dynamic parameter identification.
"""
from __future__ import annotations

from pathlib import Path
import math


def parse_idf_objects(path: Path) -> dict[str, list[list[str]]]:
    """Minimal comment-aware EnergyPlus IDF object reader (no macro/IDF schema support)."""
    text = Path(path).read_text(encoding='utf-8-sig', errors='replace')
    clean = '\n'.join(line.split('!',1)[0] for line in text.splitlines())
    objs: dict[str, list[list[str]]] = {}
    for block in clean.split(';'):
        fields = [part.strip() for part in block.split(',')]
        if len(fields) < 2 or not fields[0]:
            continue
        kind = fields.pop(0).upper()
        objs.setdefault(kind, []).append(fields)
    return objs


def zone_parameter_table(path: Path, mass_participation: float = 0.5,
                         fabric_U_W_m2K: float = 1.5) -> list[dict]:
    """Derive effective 1R1C coefficients from IDF volume and internal furniture mass.

    U is an assumed effective W/m2K * floor area (NOT actual envelope U).
    C = zone air + chosen participation fraction of IDF furnishings thermal mass.
    Materials in *all* constructions, envelope transfer, ventilation and thermal
    bridges are not identified. These values MUST be calibrated before validation.
    Zone multipliers are *not* applied: per-zone temperature physics is for one
    representative zone, since multiplier replicates identical zones.
    """
    if not 0.0 <= mass_participation <= 1.0 or fabric_U_W_m2K <= 0:
        raise ValueError('Invalid mass participation or effective U')
    objs = parse_idf_objects(path)
    materials = {}
    for f in objs.get('MATERIAL',[]):
        if len(f) >= 6:
            try:
                materials[f[0].upper()] = (float(f[2]),float(f[4]),float(f[5]))
            except ValueError:
                pass
    constructions = {f[0].upper(): f[1].upper() for f in objs.get('CONSTRUCTION',[]) if len(f)>=2}
    furniture_cap = {}
    for f in objs.get('INTERNALMASS',[]):
        if len(f) < 5: continue
        z=f[2].upper()
        layer=constructions.get(f[1].upper())
        if layer not in materials:
            continue
        thickness,density,cp=materials[layer]
        try: area=float(f[4]) if len(f)>=5 else float(f[3])
        except ValueError: continue
        furniture_cap[z]=furniture_cap.get(z,0.0)+area*thickness*density*cp/3_600_000.0
    records=[]
    for f in objs.get('ZONE',[]):
        if len(f)<9: continue
        try:
            name=f[0].upper()
            multiplier=float(f[6]) if f[6] else 1.0
            height=float(f[7]); volume=float(f[8])
            if height<=0 or volume<=0:continue
            floor=volume/height
            air_cap=volume*1.2*1005.0/3_600_000.0 # kWh/K
            mass_cap=furniture_cap.get(name,0.0)
            effective_cap=air_cap+mass_participation*mass_cap
            ua=floor*fabric_U_W_m2K/1000.0   # kW/K, illustrative
            records.append(dict(zone_name=name,zone_multiplier=multiplier,volume_m3=volume,
                floor_area_from_volume_m2=floor,air_capacity_kWh_K=air_cap,
                IDF_furniture_capacity_kWh_K=mass_cap,
                effective_C_kWh_K=effective_cap,assumed_UA_kW_K=ua,
                nominal_time_constant_h=effective_cap/ua))
        except (ValueError, ZeroDivisionError):
            continue
    if not records: raise ValueError('No valid zone geometry found in IDF')
    return records
