"""V27 explicit units; immutable V26 records retain their original bytes."""
from __future__ import annotations

LEGACY_STAGE_ENERGY_FIELDS=('native_storage_total_W','coil_total_W','pump_total_W','evaporator_total_W')

def corrected_cold_stage_units(frame):
    missing=set(LEGACY_STAGE_ENERGY_FIELDS)-set(frame.columns)
    if missing:raise ValueError('Incomplete legacy phase integral contract; missing '+str(sorted(missing)))
    mapping=[dict(original_field=k,exported_field=k[:-2]+'_kWh',unit='kWh',
        operation='rename only; historical value is already the phase time integral') for k in LEGACY_STAGE_ENERGY_FIELDS]
    return frame.rename(columns={r['original_field']:r['exported_field'] for r in mapping}).copy(deep=True),mapping
