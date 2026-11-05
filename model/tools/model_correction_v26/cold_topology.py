"""Read the actual IDF branch graph; never invent unlisted loop connections."""
from __future__ import annotations

import csv
import io
import re

COLD_LOOPS=('CoolSys1_Supply','CoolSys1_Demand','Heat Recovery Chiller Bypass Loop')
ZONES=('OR1_FLR_2','OR2_MULT5_FLR_2','OR3_FLR_2','OR4_FLR_2')


def idf_objects(text):
    text=re.sub(r'!.*','',text)
    return [[f.strip() for f in next(csv.reader(io.StringIO(chunk.strip().replace('\n',' '))))]
            for chunk in text.split(';') if chunk.strip()]


def cold_topology(text):
    objects=idf_objects(text)
    index={(f[0].lower(),f[1].lower()):f for f in objects if len(f)>1}
    loops=[]
    components=[]
    for fields in objects:
        if fields[0].lower() not in ('plantloop','condenserloop'):
            continue
        sides=[]
        for side,bi,ci in [('Supply',13,14),('Demand',17,18)]:
            branchlist=index['branchlist',fields[bi].lower()]
            connectorlist=index['connectorlist',fields[ci].lower()]
            branches=[]
            for name in branchlist[2:]:
                b=index['branch',name.lower()]
                cs=[]
                for start in range(3,len(b),4):
                    kind,component,inlet,outlet=b[start:start+4]
                    record=dict(type=kind,name=component,inlet=inlet,outlet=outlet,loop=fields[1],side=side,branch=name,
                                fields=index[kind.lower(),component.lower()])
                    cs.append(record)
                    components.append(record)
                branches.append(dict(name=name,components=cs))
            connectors=[dict(type=connectorlist[i],name=connectorlist[i+1],fields=index[connectorlist[i].lower(),connectorlist[i+1].lower()])
                        for i in range(2,len(connectorlist),2)]
            sides.append(dict(side=side,inlet=fields[bi-2],outlet=fields[bi-1],branchlist=fields[bi],connectorlist=fields[ci],
                              branches=branches,connectors=connectors))
        loops.append(dict(type=fields[0],name=fields[1],fields=fields,sides=sides))
    cold=[c for c in components if c['loop'] in COLD_LOOPS]
    coils=[]
    for c in cold:
        if c['type'].lower().startswith('coil:cooling:'):
            coils.append(dict(c,air_inlet=c['fields'][12],air_outlet=c['fields'][13]))
    hxs=[]
    for fields in objects:
        if fields[0].lower()!='heatexchanger:fluidtofluid':
            continue
        demand=next(c for c in cold if c['name'].lower()==fields[1].lower() and c['inlet'].lower()==fields[3].lower())
        supply=next(c for c in cold if c['name'].lower()==fields[1].lower() and c['inlet'].lower()==fields[6].lower())
        hxs.append(dict(name=fields[1],fields=fields,api_demand_loop=demand['loop'],api_demand_side=demand['side'],
                        api_supply_loop=supply['loop'],api_supply_side=supply['side']))
    chillers=[c for c in cold if c['type'].lower().startswith('chiller:')]
    names={c['name'].lower() for c in chillers}
    cross=[c for c in components if c['loop'] not in COLD_LOOPS and c['name'].lower() in names]
    airbranch=index['branch','vav_or air loop main branch']
    air=[]
    for i in range(3,len(airbranch),4):
        kind,name,inlet,outlet=airbranch[i:i+4]
        air.append(dict(type=kind,name=name,inlet=inlet,outlet=outlet,fields=index[kind.lower(),name.lower()]))
    surface_objects=[f for f in objects if f[0].lower()=='buildingsurface:detailed']
    surface_by_name={f[1].lower():f for f in surface_objects}
    surfaces=[f for f in surface_objects if f[4].upper() in ZONES]
    parent_names={f[1].lower() for f in surfaces}
    surfaces.extend(f for f in objects if f[0].lower()=='fenestrationsurface:detailed' and f[4].lower() in parent_names)
    surfaces.extend(f for f in objects if f[0].lower()=='internalmass' and f[3].upper() in ZONES)
    adjacent=[]
    for f in surfaces:
        if f[0].lower()=='buildingsurface:detailed' and f[6].lower()=='surface':
            other=surface_by_name[f[7].lower()][4]
            if other.upper() not in ZONES:
                adjacent.append(other)
    return dict(cold_loops=[l for l in loops if l['name'] in COLD_LOOPS],all_loops=loops,
                cold_components=cold,cooling_coils=coils,pumps=[c for c in cold if 'pump' in c['type'].lower()],
                chillers=chillers,heat_exchangers=hxs,cross_boundary_components=cross,
                or_air_components=air,or_surfaces=surfaces,adjacent_zones=sorted(set(adjacent)),
                or_aux_objects=[f for f in objects if (len(f)>1 and (f[1].lower().startswith(('or1_','or2_','or3_','or4_'))
                               or f[1].lower() in ('vav_or_oamixing box','vav_or supply air splitter','vav_or return air mixer')))
                               and f[0].lower() in ('coil:heating:water','outdoorair:mixer','airloophvac:zonesplitter','airloophvac:zonemixer')])
