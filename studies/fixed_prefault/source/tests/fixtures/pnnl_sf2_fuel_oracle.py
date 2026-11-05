"""PNNL appendix algebra oracle only. No OEM transient or solver qualification."""
import math

ISO_ORIGINAL_KW=800.
DEFAULT_SCALE=.4
EXPONENT=.673

def _finite(x,nonnegative=False,positive=False):
 if type(x)not in(int,float)or not math.isfinite(x)or nonnegative and x<0 or positive and x<=0:raise ValueError('finite physical algebra input required')
 return float(x)
def ambient(temp_C):
 t=1.8*_finite(temp_C)+32.
 fp=1.23836-.00404*t
 ff=.965544+.000584*t if t<=59. else .910320+.00152*t
 if min(fp,ff)<=0:raise ValueError('source fitted ambient factors nonpositive: no extrapolation admitted')
 return dict(temperature_C=temp_C,temperature_F=t,power_factor=fp,heat_rate_factor=ff)
def running_fuel_rate(actual_P_kW,temp_C,*,scale=DEFAULT_SCALE,iso_original_kW=ISO_ORIGINAL_KW):
 """MMBtu/h HHV. P=0 is mathematical continuous limit, not measured OFF fuel."""
 p=_finite(actual_P_kW,nonnegative=True);sc=_finite(scale,positive=True);iso=_finite(iso_original_kW,positive=True);a=ambient(temp_C)
 if p==0:return 0.
 h=29477.-1954.9*math.log(iso)
 if h<=0:raise ValueError('source fitted ISO heat rate nonpositive')
 return p*h*.9851*(p/(iso*sc))**(-.327)*a['heat_rate_factor']/1e6
def ramp_fuel_integral(P0_kW,P1_kW,hours,temp_C,*,scale=DEFAULT_SCALE,iso_original_kW=ISO_ORIGINAL_KW):
 """Exact integral of mathematical running curve along one linear P path.

 Does not invent startup fuel or certify below-minimum operation.
 """
 p0=_finite(P0_kW,nonnegative=True);p1=_finite(P1_kW,nonnegative=True);h=_finite(hours,nonnegative=True)
 if h==0:return 0.
 if p0==p1:return h*running_fuel_rate(p0,temp_C,scale=scale,iso_original_kW=iso_original_kW)
 # Stable symmetric midpoint expansion for near-equal nonzero endpoints.
 mid=(p0+p1)/2.;eps=(p1-p0)/(p1+p0)
 if abs(eps)<1e-5:
  alpha=EXPONENT
  average_factor=1.+alpha*(alpha-1.)*eps**2/6.+alpha*(alpha-1.)*(alpha-2.)*(alpha-3.)*eps**4/120.
  return h*running_fuel_rate(mid,temp_C,scale=scale,iso_original_kW=iso_original_kW)*average_factor
 coefficient=running_fuel_rate(1.,temp_C,scale=scale,iso_original_kW=iso_original_kW)
 return coefficient*h*(p1**(EXPONENT+1)-p0**(EXPONENT+1))/((EXPONENT+1)*(p1-p0))
def assess_operating_scope(actual_P_kW,temp_C,state,*,scale=DEFAULT_SCALE):
 if state not in ['OFF','startup','steady','ramp']:raise ValueError('explicit operating state required')
 p=_finite(actual_P_kW,nonnegative=True);a=ambient(temp_C);iso_scaled=ISO_ORIGINAL_KW*_finite(scale,positive=True);maximum=iso_scaled*a['power_factor']
 return dict(state=state,ISO_original_kW=ISO_ORIGINAL_KW,scale=scale,ISO_scaled_kW=iso_scaled,ambient_maximum_kW=maximum,actual_power_kW=p,actual_over_ISO_scaled=p/iso_scaled,actual_over_ambient_maximum=p/maximum,
  mathematical_running_curve_MMBtu_h=running_fuel_rate(p,temp_C,scale=scale),inside_current_research_steady_range=.4*maximum<=p<=maximum,
  minimum_steady_fraction_source='current .4 of ambient maximum is research operating constraint, not PNNL OEM minimum',
  PNNL_curve_fitted_load_validity_range=None,OEM_or_transient_fuel_qualified=False,OFF_pilot_no_load_fuel_MMBtu_h=None,startup_additional_fuel_MMBtu=None,transient_thermal_lag_fuel_MMBtu_h=None,
  qualification='Steady appendix curve is sourced; load validity/startup/OFF pilot/transient fuel are not supplied. Mathematical zero/low-P/ramp extension is not OEM evidence.')
