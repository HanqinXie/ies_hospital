"""Configured independent-case process counts, not a hardware benchmark."""
SCHEMA='hospital_ies.transfer.configurable_concurrency.v2'
FIELDS=('max_producers','max_accounts','max_total_processes')

def validate_counts(limits):
    for key in FIELDS:
        if type(limits.get(key)) is not int or limits[key]<1:
            raise ValueError('Positive integer process count required: '+key)
    if any(limits[key]>limits['max_total_processes'] for key in ('max_producers','max_accounts')):
        raise ValueError('Producer/account allowance exceeds total process allowance')
    return {key:limits[key] for key in FIELDS}
