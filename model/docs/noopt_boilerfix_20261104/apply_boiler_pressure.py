from pathlib import Path
p=Path('tools/noopt_unattended_20261104/forward.py')
raw=p.read_bytes();bom=raw.startswith(b'\xef\xbb\xbf')
s=raw.decode('utf-8-sig')
start=s.index('    boiler=0.;boiler_e=0.;starter=0.;restart=0.')
end=s.index("    demand=float(final['consumption'])",start)
block=s[start:end]
block=block.replace('boiler=min(boiler_permission,','boiler=min(boiler_permission,boiler_cap,',1)
block=block.replace("    served=final['served'];q=final['q'];aux=final['aux']\n",'')
shifted=''.join(('    '+line if line.strip() else line) for line in block.splitlines(keepends=True))
new='''    # Settle every resource and service port again when the passive boiler
    # pressure/use regulator reduces firing. Keep the original cold reference
    # for each trial: a prior trial must not accumulate converter curtailment.
    base_desired=desired.copy()
    def settle(boiler_cap):
        desired=base_desired.copy()
'''+shifted+'''        return dict(final=final,boiler=boiler,boiler_e=boiler_e,starter=starter,
            restart=restart,station_e=station_e,total_steam=total_steam)

    def useful_steam(settled):
        flow=settled['final']
        return float(flow['served'][:,1].sum())/heat_factor+float(flow['q'][0])*abs_steam_per_kw

    settled=settle(boiler_permission)
    initial_boiler=float(settled['boiler']);initial_useful=useful_steam(settled)
    pressure_iterations=0
    if initial_boiler>initial_useful+1e-7:
        # Physical pressure/steam-demand closure, not candidate dispatch
        # scoring: retain the commanded upper target and find its passive
        # downward saturation boundary. OFF is the exact legal zero endpoint;
        # the existing model has no registered positive boiler minimum.
        low=0.;high=initial_boiler;accepted=settle(0.)
        for pressure_iterations in range(1,41):
            mid=(low+high)/2.;trial=settle(mid)
            if trial['boiler']<=useful_steam(trial):
                low=mid;accepted=trial
            else:high=mid
            # Root location only, in lb/h. The original useful-destination
            # inequality and energy-balance acceptance are not relaxed.
            if high-low<=1e-5:break
        settled=accepted
    boiler=settled['boiler'];boiler_e=settled['boiler_e']
    starter=settled['starter'];restart=settled['restart']
    station_e=settled['station_e'];total_steam=settled['total_steam']
    final=settled['final'];served=final['served'];q=final['q'];aux=final['aux']
    actual_useful=useful_steam(settled)
    if boiler>actual_useful+1e-7:
        raise ForwardBalanceError('Passive boiler pressure closure violates actual useful-steam destination')
    pressure_record=dict(method='passive_actual_steam_use_pressure_closure',
        initial_boiler_lbph=initial_boiler,initial_actual_useful_lbph=initial_useful,
        actual_boiler_lbph=boiler,actual_useful_lbph=actual_useful,
        pressure_iterations=pressure_iterations,dispatch_optimization=False,
        boiler_steam_dump_authorized=False,inventory_committed=False)
'''
s=s[:start]+new+s[end:]
old="service_dependencies=dependencies,m22_mode_selection=dict(method='deterministic_current_surplus_deficit',inventory_committed=False),"
assert s.count(old)==1
s=s.replace(old,"service_dependencies=dependencies,m22_mode_selection=dict(method='deterministic_current_surplus_deficit',\n            inventory_committed=False,boiler_pressure_regulation=pressure_record),",1)
p.write_bytes((b'\xef\xbb\xbf' if bom else b'')+s.encode('utf8'))
