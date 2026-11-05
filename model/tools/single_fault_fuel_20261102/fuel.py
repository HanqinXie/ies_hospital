"""Pure source-bound running fuel and exact held/linear-ramp integrals.

No startup, pilot/OFF or thermal-lag gas is supplied by the appendix. Those
unknown quantities are never inferred from the mathematical zero-power limit.
"""
from __future__ import annotations
from dataclasses import asdict,dataclass
import hashlib
import json
import math
from pathlib import Path
from numbers import Real
from hospital_ies.engineering_validation import active_validation_cache, immutable_token

CONTRACT='single_fault.pnnl_iso_running_fuel.sf2.v1'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def number(value, *, positive=False):
    if type(value) not in (int,float) or not math.isfinite(value) or value < 0. or positive and value == 0.:
        raise ValueError('Finite nonnegative physical fuel quantity required')
    return float(value)


@dataclass(frozen=True)
class FuelCurve:
    iso_original_kW: float
    iso_scaled_kW: float
    heat_rate_constant: float
    heat_rate_log_coefficient: float
    partload_multiplier: float
    partload_exponent: float
    source_pins: tuple
    contract: str = CONTRACT

    def __post_init__(self):
        for key in ('iso_original_kW','iso_scaled_kW','heat_rate_constant','heat_rate_log_coefficient','partload_multiplier'):
            number(getattr(self,key),positive=True)
        if (self.contract != CONTRACT or self.iso_original_kW != 800. or self.partload_exponent != -.327
                or self.partload_multiplier != .9851 or self.heat_rate_constant != 29477.
                or self.heat_rate_log_coefficient != 1954.9 or not self.source_pins):
            raise ValueError('Explicit frozen PNNL original ISO constants required')

    @classmethod
    def from_source(cls, root, plant):
        root=Path(root); path=root/'configs/plant_configs.json'
        cfg=json.loads(path.read_text('utf8'));p=cfg['historical_pnnl_performance']
        original=cfg['source_equipment']['gas_turbines']['rated_electric_kW_each']
        scaled=original*cfg['study_cases'][plant.case]['gas_turbine_scale']
        if plant.nominal_gt_capacity_used_for_heat_rate_kW != original or plant.gt_rated_kW != scaled:
            raise ValueError('Plant differs from source ISO rating/scale')
        files=('configs/plant_configs.json','src/hospital_ies/model.py')
        pins=tuple((name,hashlib.sha256((root/name).read_bytes()).hexdigest()) for name in files)
        return cls(float(original),float(scaled),p['gt_hhv_heat_rate_Btu_per_kWh_constant'],
            p['gt_hhv_heat_rate_Btu_per_kWh_ln_coeff'],p['gt_partload_correction_multiplier'],
            p['gt_partload_correction_exponent'],pins)

    @classmethod
    def from_config(cls, value):
        if type(value) is not dict or set(value) != set(cls.__dataclass_fields__):
            raise ValueError('Typed complete SF2 fuel configuration required')
        return cls(**dict(value,source_pins=tuple(tuple(v) for v in value['source_pins'])))

    def as_dict(self): return asdict(self)
    @property
    def identity(self):
        cache=active_validation_cache()
        if cache is None:return digest(self.as_dict())
        try:key=(_curve_parameter_token(self),digest)
        except TypeError:return digest(self.as_dict())
        return cache.get('fuel_curve.identity',key,lambda:digest(self.as_dict()))
    @property
    def exponent(self): return 1.+self.partload_exponent

    def coefficient(self, temperature_C):
        if type(temperature_C) not in (int,float) or not math.isfinite(temperature_C):
            raise ValueError('Finite current source temperature required')
        t=temperature_C*1.8+32.
        power=1.23836-.00404*t
        ambient=.965544+.000584*t if t<=59. else .910320+.00152*t
        if min(power,ambient)<=0.: raise ValueError('PNNL fitted ambient domain is nonpositive')
        h=self.heat_rate_constant-self.heat_rate_log_coefficient*math.log(self.iso_original_kW)
        return h*self.partload_multiplier*self.iso_scaled_kW**(-self.partload_exponent)*ambient/1e6

    def rate(self, power_kW, temperature_C):
        return self.coefficient(temperature_C)*number(power_kW)**self.exponent

    def integral(self, start_kW, end_kW, hours, temperature_C):
        a=number(start_kW);b=number(end_kW);h=number(hours)
        if a==b:return h*self.rate(a,temperature_C)
        mid=(a+b)/2.;eps=(b-a)/(b+a);alpha=self.exponent
        if abs(eps)<1e-5:
            factor=1.+alpha*(alpha-1.)*eps**2/6.+alpha*(alpha-1.)*(alpha-2.)*(alpha-3.)*eps**4/120.
            return h*self.rate(mid,temperature_C)*factor
        return self.coefficient(temperature_C)*h*(b**(alpha+1.)-a**(alpha+1.))/((alpha+1.)*(b-a))

    def qualification(self):
        return dict(scope='PNNL ISO-referenced mathematical running fuel only',
            startup_additional_fuel_MMBtu=None,OFF_pilot_fuel_MMBtu_h=None,
            OEM_transient_qualified=False,low_load_and_ramp_extension_is_not_OEM_qualification=True)


def _curve_parameter_token(curve):
    return tuple((name,immutable_token(getattr(curve,name))) for name in FuelCurve.__dataclass_fields__)


@dataclass(frozen=True)
class FuelPath:
    role: str
    start_minute: float
    end_minute: float
    temperature_C: float
    start_kW: tuple
    end_kW: tuple
    mean_kW: tuple
    curve_identity: str
    contract: str = CONTRACT

    def __post_init__(self):
        if self.role not in ('actual_interval','point_probe') or self.contract != CONTRACT:
            raise ValueError('Explicit SF2 actual interval or point probe role required')
        a=number(self.start_minute);b=number(self.end_minute)
        if b<=a:raise ValueError('Positive fuel path interval required')
        if type(self.temperature_C) not in (int,float) or not math.isfinite(self.temperature_C):
            raise ValueError('Finite source temperature required')
        for value in (self.start_kW,self.end_kW,self.mean_kW):
            if type(value) is not tuple or len(value)!=3:raise ValueError('Exactly three immutable GT powers required')
            for v in value:number(v)
        if self.mean_kW != tuple((a+b)/2. for a,b in zip(self.start_kW,self.end_kW)):
            raise ValueError('Path mean differs from source endpoints')
        if not isinstance(self.curve_identity,str) or len(self.curve_identity)!=64:
            raise ValueError('Source curve identity required')

    @classmethod
    def build(cls,curve,*,role,start_minute,end_minute,temperature_C,start_kW,end_kW):
        def typed(value):
            if isinstance(value,bool) or not isinstance(value,Real) or not math.isfinite(value):
                raise ValueError('Typed finite physical path value required')
            return float(value)
        a=tuple(typed(v) for v in start_kW);b=tuple(typed(v) for v in end_kW)
        return cls(role,typed(start_minute),typed(end_minute),typed(temperature_C),a,b,
            tuple((x+y)/2. for x,y in zip(a,b)),curve.identity)

    @property
    def hours(self):return (self.end_minute-self.start_minute)/60.
    @property
    def identity(self):return digest(asdict(self))
    def integrals(self,curve):
        if type(curve) is not FuelCurve or curve.identity != self.curve_identity:
            raise ValueError('Actual path source curve changed')
        return tuple(curve.integral(a,b,self.hours,self.temperature_C) for a,b in zip(self.start_kW,self.end_kW))
