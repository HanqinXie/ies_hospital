"""Explicit ECM2026 scenario inputs, never calibrated hospital reliability."""
from __future__ import annotations

import copy
import math
from numbers import Real
import warnings

SOURCE_SHA256 = 'afbcca5998bd5d32ee019cd0d4c7b913e07841c0169b71a0710815e80678d1ce'
MAIN_PORTS = ('BOILER_1', 'BOILER_2', 'BOILER_3', 'ABS')
GT_PORTS = ('GT_1', 'GT_2', 'GT_3')
SUPPORTED_PORTS = MAIN_PORTS + GT_PORTS
GT_WARNING = ('Incompatible-equipment GT proxy: Jiang2018 power units are gas engine '
              'plus fuel cell, not the hospital gas turbine; this is no hospital calibration.')

# Original Table 2 values and terminology. Percent units are retained verbatim.
_SOURCE_ROWS = (
    ('power_connection', 'Power connection', '0.0114', 2, 'author_assumption', '[43]', 'a'),
    ('combined_heat_and_power', 'Combined heat and power', '0.0335', 34, 'secondary_cited_observational_input', '[42]', None),
    ('emergency_generator', 'Emergency generator', '0.0335', 34, 'author_proxy_assumption', '[42]', 'b'),
    ('gas_boiler', 'Gas boiler', '0.0039', 24, 'secondary_cited_observational_input', '[42]', None),
    ('heat_pump', 'Heat pump', '0.0220', 48, 'author_proxy_assumption', '[42]', 'c'),
    ('absorption_chiller', 'Absorption chiller', '0.0220', 48, 'secondary_cited_observational_input', '[42]', None),
    ('compression_chiller', 'Compression chiller', '0.0220', 48, 'author_proxy_assumption', '[42]', 'c'),
    ('wind_turbine', 'Wind turbine', '0.0133', 13, 'secondary_cited_input', '[44]', None),
    ('photovoltaic', 'Photovoltaic', '0.0010', 72, 'secondary_cited_input_complete_outages_only', '[45]', 'd'),
    ('electrode_boiler', 'Electrode boiler', '0.0220', 48, 'author_proxy_assumption', '[42]', 'c'),
    ('electrolyzer', 'Electrolyzer', '0.0220', 48, 'author_proxy_assumption', '[42]', 'c'),
    ('fuel_cell', 'Fuel cell', '0.0220', 48, 'author_proxy_assumption', '[42]', 'c'),
)


def _profile_value(name):
    entries = [dict(parameter_id=key, original_technology_term=term,
                    raw_percent_per_hour=percent, raw_probability_unit='%/h',
                    probability_per_hour=float(percent) / 100,
                    duration_hours=duration, raw_duration_unit='h',
                    duration_kind='fixed_failure_duration', repair_distribution=None,
                    provenance_class=provenance, cited_reference=reference,
                    table_footnote=footnote, complete_outages_only=(key == 'photovoltaic'),
                    hospital_calibrated=False)
               for key, term, percent, duration, provenance, reference, footnote in _SOURCE_ROWS]
    by_id = {entry['parameter_id']: entry for entry in entries}
    ports = MAIN_PORTS + GT_PORTS if name == 'gt_proxy' else MAIN_PORTS
    assets = []
    for port in ports:
        source_id = ('combined_heat_and_power' if port in GT_PORTS else
                     'gas_boiler' if port.startswith('BOILER_') else 'absorption_chiller')
        source = by_id[source_id]
        assets.append(dict(port=port, source_parameter_id=source_id,
                           probability_per_hour=source['probability_per_hour'],
                           duration_hours=source['duration_hours'],
                           evidence_group=('incompatible_equipment_proxy' if port in GT_PORTS
                                           else 'matching_technology_literature_input'),
                           hospital_calibrated=False))
    return dict(
        schema_version='literature_basis_profile_v1', name=name,
        assets=assets, source_entries=entries, gt_proxy_opt_in=(name == 'gt_proxy'),
        warnings=[GT_WARNING] if name == 'gt_proxy' else [],
        source=dict(
            id='R14', doi='10.1016/j.enconman.2025.120946', year=2026,
            pdf_sha256=SOURCE_SHA256, table='Table 2', page=8,
            algorithm='Algorithm 1', algorithm_page=15,
            probability_term='failure probability', duration_term='failure duration',
            source_chain='ECM2026 Table2 -> Jiang2018 [42] for CHP, GB and ABS; author proxies remain separate',
            jiang_doi='10.3390/en11061519',
            jiang_equipment_limit='gas engine plus fuel cell; not the hospital gas turbine',
            jiang_original_table11_independently_verified=False,
            jiang_official_followup='Parent checked MDPI section5.1: Kitakyushu, Japan, April2002-March2011; independent single failures; grid outage excluded. Original Table11 numbers not independently verified.',
            transfer_limit='Secondary industrial-site literature inputs, not hospital observations, OEM qualification or local utility calibration',
            author_proxy_limit='Emergency generator copied from CHP; HP/compression chiller/electrode boiler/electrolyzer/fuel cell copied from ABS',
            grid_limit='European grid assumption, not local utility data',
        ),
        unknown_probabilities=dict(hospital_common_cause=None, storage=None,
                                   gas_connection=None, other_unspecified_mechanisms=None),
        independence='Source assumes constant component probabilities independent of other components; no measured hospital CCF zero is inferred',
        hospital_reliability_estimate=False,
        physical_parameters_changed=False,
    )


def profile(name, allow_gt_proxy=False):
    """Return a fresh main profile, or an explicitly permitted GT proxy profile.

    ``main`` contains BOILER_1/2/3 and ABS. ``gt_proxy`` adds GT_1/2/3;
    the caller must pass ``allow_gt_proxy=True`` and receives a warning.
    Permission alone never adds GT assets to ``main``.
    """
    if not isinstance(name, str) or name not in ('main', 'gt_proxy'):
        raise ValueError('Unknown literature profile name')
    if type(allow_gt_proxy) is not bool:
        raise ValueError('GT proxy permission must be boolean')
    if name == 'gt_proxy' and not allow_gt_proxy:
        raise ValueError('GT proxy requires explicit allow_gt_proxy=True')
    if name == 'gt_proxy':
        warnings.warn(GT_WARNING, UserWarning, stacklevel=2)
    return _profile_value(name)


def _validated_profile(value):
    """Reject caller mutation of the registered source/profile; allow reordering."""
    if not isinstance(value, dict) or value.get('name') not in ('main', 'gt_proxy'):
        raise ValueError('Expected a registered literature profile')
    assets = value.get('assets')
    if not isinstance(assets, list) or not all(isinstance(asset, dict) for asset in assets):
        raise ValueError('Profile assets must be distinct supported ports')
    ports = [asset.get('port') for asset in assets]
    if any(not isinstance(port, str) or port not in SUPPORTED_PORTS for port in ports) or len(ports) != len(set(ports)):
        raise ValueError('Profile assets must be distinct supported ports')
    for asset in assets:
        for key in ('probability_per_hour', 'duration_hours'):
            number = asset.get(key)
            if isinstance(number, bool) or not isinstance(number, Real) or not math.isfinite(number):
                raise ValueError('Unknown or nonfinite profile parameter')
    expected = _profile_value(value['name'])
    supplied = copy.deepcopy(value)
    supplied['assets'] = sorted(assets, key=lambda asset: asset['port'])
    canonical = copy.deepcopy(expected)
    canonical['assets'] = sorted(canonical['assets'], key=lambda asset: asset['port'])
    if type(value.get('gt_proxy_opt_in')) is not bool or supplied != canonical:
        raise ValueError('Altered source parameters or missing explicit GT proxy permission')
    return expected
