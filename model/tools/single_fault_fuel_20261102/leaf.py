"""SF2-only fixed-path pricing and uncommitted native-primal meter receipts."""
from dataclasses import asdict
import math
import numpy as np
from .fuel import FuelCurve,FuelPath,digest


def prepare(config,path,role,dt_h,temperature_C):
    value=(config or {}).get('_sf2_fuel_curve')
    if value is None:
        if path is not None or role is not None:raise ValueError('SF2 path requires explicit fuel model')
        return None,None,None
    curve=FuelCurve.from_config(value)
    role=role or (config or {}).get('_sf2_nonactual_fuel_role')
    if role not in ('actual_interval','point_probe','free_reference_probe'):
        raise ValueError('SF2 leaf requires explicit actual or nonactual role')
    if role=='actual_interval':
        if (type(path) is not FuelPath or path.role!='actual_interval' or path.curve_identity!=curve.identity
                or abs(path.hours-dt_h)>1e-14 or path.temperature_C!=temperature_C):
            raise ValueError('SF2 actual path/source/role/duration/temperature mismatch')
    elif path is not None:
        raise ValueError('SF2 nonactual probes cannot consume an actual interval path')
    return curve,path,role


def fixed_coefficients(curve,path,role,lows,highs,temperature_C):
    if not np.array_equal(lows[:3],highs[:3]):raise ValueError('SF2 known fuel coefficients require fixed GT bounds')
    mean=np.asarray(lows[:3],float)
    if role=='actual_interval':
        if not np.allclose(mean,path.mean_kW,rtol=0.,atol=1e-9):
            raise ValueError('SF2 actual fixed GP differs from source path mean')
        rates=np.asarray(path.integrals(curve))/path.hours
    else:rates=np.asarray([curve.rate(float(p),temperature_C) for p in mean])
    return np.divide(rates,mean,out=np.zeros(3),where=mean>0.),rates


def receipt(curve,path,role,raw,dt_h,temperature_C,gas_price):
    values=np.asarray(raw,float);powers=values[:3]
    if role=='actual_interval':
        if not np.allclose(powers,path.mean_kW,rtol=0.,atol=1e-7):raise ValueError('SF2 accepted primal differs from actual mean')
        integrals=path.integrals(curve);rates=tuple(v/dt_h for v in integrals)
    else:
        integrals=None;rates=tuple(curve.rate(float(p),temperature_C) for p in powers)
    return dict(schema='single_fault.sf2.running_fuel_receipt.v1',role=role,curve=curve.as_dict(),
        curve_identity=curve.identity,path_context=asdict(path) if path is not None else None,
        path_identity=path.identity if path is not None else None,dt_h=dt_h,temperature_C=temperature_C,
        native_vector_dimension=len(values),accepted_raw_x=values.tolist(),accepted_raw_x_sha256=digest(values.tolist()),
        accepted_gp_kW=powers.tolist(),average_running_fuel_MMBtu_h=list(rates),
        running_fuel_by_unit_MMBtu=list(integrals) if integrals is not None else None,
        running_fuel_MMBtu=math.fsum(integrals) if integrals is not None else None,
        running_gas_fee_USD=math.fsum(integrals)*gas_price if integrals is not None else None,
        accounting_committed=False,qualification=curve.qualification())
