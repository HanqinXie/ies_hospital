"""Render new-run service comparisons, never legacy upper-only U data."""
from pathlib import Path
import argparse
import csv
import hashlib
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, FuncFormatter
import numpy as np

ROLES = ('normal', 'unattended', 'response')
COLORS = {'normal': '#2878B5', 'unattended': '#D64B40', 'response': '#25945D'}
LABELS = {'normal': 'Normal', 'unattended': 'Fault: no optimization', 'response': 'Fault: with dispatch'}
STYLES = {'normal': '-', 'unattended': (0, (5, 3)), 'response': (0, (4, 2, 1, 2))}
CARRIERS = (('cooling', 2, 'Cooling'), ('heating', 1, 'Heating'), ('electricity', 0, 'Electricity'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def align(topic):
    ss = {r: topic['conditions'][r]['segments'] for r in ROLES}
    edges = np.unique([v for a in ss.values() for s in a
                       for v in (s['start_minute'], s['end_minute'])])
    if len(edges) < 2 or abs(edges[0] - topic.get('start_minute', 0)) > 1e-7 or abs(edges[-1] - topic['end_minute']) > 1e-7:
        raise ValueError('Incomplete registered observation interval')
    result = {}
    for role, segments in ss.items():
        starts = np.asarray([s['start_minute'] for s in segments])
        ends = np.asarray([s['end_minute'] for s in segments])
        if (len(starts) == 0 or np.any(ends <= starts)
                or np.any(np.abs(starts[1:] - ends[:-1]) > 1e-7)):
            raise ValueError('Gap, overlap or nonpositive native segment')
        ii = np.searchsorted(starts, edges[:-1], side='right') - 1
        if np.any(ii < 0) or np.any(ends[ii] < edges[1:] - 1e-7):
            raise ValueError('Uncovered comparison interval')
        result[role] = np.asarray([s['delivered_kW'] for s in segments], dtype=float)[ii]
        demand = np.asarray([s['demand_kW'] for s in segments], dtype=float)[ii]
        if role == 'normal':
            result['demand'] = demand
        elif not np.allclose(demand, result['demand'], atol=1e-6, rtol=0):
            raise ValueError('Conditions use different original demands')
    if any(not np.isfinite(v).all() for v in result.values()):
        raise ValueError('Nonfinite input service or demand')
    return edges, result


def deviations(values, normal):
    out = np.full(normal.shape, np.nan)
    np.divide(100 * (values - normal), normal, out=out, where=normal != 0)
    return out


def format_axis(ax, x, onset_end):
    ax.set_xlim(x[0], x[-1])
    ax.axvspan(0, onset_end, color='#E5E5E5', alpha=.85, lw=0, zorder=0)
    for time in (0, onset_end):
        ax.axvline(time, color='#999999', lw=.65, linestyle=(0, (2, 2)))
    ax.grid(axis='y', color='#E9EBED', linewidth=.6)
    ax.set_axisbelow(True)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=7, integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=5))


def draw(topic, carrier, name, j, edges, series, kind):
    fig = plt.figure(figsize=(183/25.4, 145/25.4))
    if kind == 'normalized':
        ax = fig.add_axes([.12, .36, .85, .36])
        demand_ax = fig.add_axes([.12, .17, .85, .14], sharex=ax)
        plt.setp(ax.get_xticklabels(), visible=False)
    else:
        ax = fig.add_axes([.12, .22, .85, .50])
        demand_ax = None
    x = edges - topic['fault_start_minute']
    repair = topic['fault_end_minute'] - topic['fault_start_minute']
    normal = series['normal'][:, j]
    plotted = {}
    for role in ROLES:
        values = deviations(series[role][:, j], normal) if kind == 'normalized' else series[role][:, j]
        plotted[role] = values
        ax.stairs(values, x, baseline=None, color=COLORS[role],
                  linestyle=STYLES[role], lw=1.8 if role == 'normal' else 1.5,
                  label=LABELS[role], zorder=3)
    format_axis(ax, x, repair)
    if kind == 'normalized':
        finite = np.concatenate([v[np.isfinite(v)] for v in plotted.values()])
        lo = min(0., float(finite.min())) if len(finite) else 0.
        hi = max(0., float(finite.max())) if len(finite) else 0.
        if hi-lo < 1.:
            ax.set_ylim(-1., 1.)
        else:
            pad = max(.15, (hi-lo)*.08)
            ax.set_ylim(lo-pad, hi+pad)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: '0' if v == 0 else f'{v:+g}'))
        ax.set_ylabel(f'{name} delivery deviation (%)')
        demand_ax.stairs(series['demand'][:, j], x, baseline=None, color='#252525',
                         lw=1.1, linestyle=(0, (4, 3)), label='Original total demand (P0-P3)')
        format_axis(demand_ax, x, repair)
        demand_ax.set_ylim(bottom=0)
        demand_ax.set_ylabel('Total demand (kW)')
        demand_ax.set_xlabel('Time relative to fault onset (min)')
        subtitle = 'Signed deviation from normal delivery and original total demand'
    else:
        ax.stairs(series['demand'][:, j], x, baseline=None, color='#252525',
                  lw=1.1, linestyle=(0, (4, 3)), label='Original total demand (P0-P3)', zorder=2)
        ax.set_ylim(bottom=0)
        ax.set_ylabel(f'{name} service (kW)')
        ax.set_xlabel('Time relative to fault onset (min)')
        subtitle = 'Actual delivered power and original total demand'
    for t, label in ((0, 'Fault onset'), (repair, 'External fault cleared')):
        ax.text(t, 1.04, label, transform=ax.get_xaxis_transform(),
                ha='center', va='bottom', fontsize=7.5, color='#666666')
    fig.text(.12, .95, f"{topic['id']} | {topic['title']}", fontsize=10.5, fontweight='bold', va='top')
    fig.text(.12, .90, f'{name} | {subtitle}', fontsize=8.5, color='#454545', va='top')
    handles, labels = ax.get_legend_handles_labels()
    if demand_ax is not None:
        h, lab = demand_ax.get_legend_handles_labels()
        handles += h; labels += lab
    fig.legend(handles, labels, bbox_to_anchor=(.115, .84), loc='upper left', ncol=2,
               fontsize=7.8, handlelength=3, columnspacing=1.8, borderaxespad=0)
    if kind == 'normalized':
        fig.text(.12, .075, 'Deviation = (scenario delivery - normal delivery) / normal delivery x 100%', fontsize=7, color='#555555')
        note = 'Normal = 0. Zero normal delivery is undefined. Native held intervals; no interpolation.'
    else:
        note = 'Native actual service delivery; coincident traces overlap. No offsets or interpolation.'
    fig.text(.12, .045, note, fontsize=7, color='#555555')
    if topic['id'] == 'T08':
        fig.text(.12, .017, 'Same continuous event as T05; natural recovery view.', fontsize=7, color='#555555')
    return fig


def render(data_path, destination):
    data_path, destination = Path(data_path), Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Use a fresh figure candidate directory')
    data = json.loads(data_path.read_text(encoding='utf-8'))
    if data.get('schema') != 'hospital_ies.cooling_dynamics.plot_data.v1':
        raise ValueError('Only new cooling-dynamics campaign data is accepted; legacy U is ineligible')
    if [t['id'] for t in data['topics']] != [f'T{i:02d}' for i in range(1, 9)]:
        raise ValueError('Eight registered ordered topics required')
    plt.rcParams.update({'font.family': 'Arial', 'font.size': 8,
                         'svg.fonttype': 'none', 'svg.hashsalt': 'cooling-dynamics-20261104',
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'axes.edgecolor': '#858585', 'axes.linewidth': .8, 'legend.frameon': False})
    destination.mkdir(parents=True, exist_ok=True)
    (destination/'data').mkdir()
    (destination/'scripts').mkdir()
    source_sha = sha(data_path)
    (destination/'data'/'PLOT_DATA.json').write_bytes(data_path.read_bytes())
    (destination/'scripts'/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    manifest = {'schema': 'hospital_ies.cooling_dynamics.figure_exports.v1',
                'source_data_sha256': source_sha, 'renderer_sha256': sha(__file__),
                'backend': 'Python/matplotlib', 'PNG_dpi': 600, 'editable_SVG_text': True,
                'policy': 'Native held intervals; no smoothing, interpolation or artificial offsets',
                'qualification': 'Literature-supported low-order engineering proxies, not OEM calibration',
                'figures': []}
    for kind in ('normalized', 'power'):
        for fmt in ('png', 'svg'):
            (destination/kind/fmt).mkdir(parents=True)
    for topic in data['topics']:
        edges, series = align(topic)
        for carrier, j, name in CARRIERS:
            stem = f"{topic['id']}_{carrier}"
            csv_path = destination/'data'/(stem+'.csv')
            with csv_path.open('w', encoding='utf-8', newline='') as f:
                wr = csv.writer(f)
                wr.writerow(['start_relative_min', 'end_relative_min', 'demand_kW',
                             'normal_kW', 'unattended_kW', 'response_kW',
                             'normal_percent', 'unattended_percent', 'response_percent'])
                ratios = {r: deviations(series[r][:,j], series['normal'][:,j]) for r in ROLES}
                for i in range(len(edges)-1):
                    wr.writerow([edges[i]-60, edges[i+1]-60, series['demand'][i,j],
                                 *[series[r][i,j] for r in ROLES], *[ratios[r][i] for r in ROLES]])
            for kind in ('normalized', 'power'):
                fig = draw(topic, carrier, name, j, edges, series, kind)
                png = destination/kind/'png'/(stem+'.png')
                svg = destination/kind/'svg'/(stem+'.svg')
                fig.savefig(png, dpi=600, facecolor='white')
                fig.savefig(svg, facecolor='white', metadata={'Date': None})
                plt.close(fig)
                text = svg.read_text(encoding='utf-8')
                if '<image' in text or '<text' not in text:
                    raise ValueError('Expected native vector marks and editable text')
                manifest['figures'].append({'topic': topic['id'], 'carrier': carrier, 'kind': kind,
                                            'png': str(png.relative_to(destination)), 'svg': str(svg.relative_to(destination)),
                                            'png_sha256': sha(png), 'svg_sha256': sha(svg), 'csv_sha256': sha(csv_path)})
            print('Exported normalized + actual power: '+stem, flush=True)
    if sha(data_path) != source_sha or len(manifest['figures']) != 48:
        raise ValueError('Unexpected source mutation or incomplete export')
    (destination/'MANIFEST.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    (destination/'README_ZH.md').write_text(
        '# 八专题冷却动态工程代理结果\n\n'
        'normalized/：24张归一化差值图，各含原始总需求kW小面板。\n\n'
        'power/：24张实际交付功率图，黑色虚线为原始总需求。每图均有600dpi PNG和可编辑SVG，无PDF。\n\n'
        '蓝色正常、红色严格无优化未响应、绿色有调度响应；灰色为外部故障区间。'
        '归一化采用100×(本工况交付−正常交付)/正常交付；正常交付为0时留作不可计算。\n\n'
        'T08沿用T05同一连续事件的自然恢复，不算新的独立设备故障。'
        '采用已登记的文献低阶工程代理，不能当作本院厂家动态参数或实机标定。\n\n'
        'data/保留源数据及逐原生区间CSV；scripts/保留渲染脚本。实际运行、参数假设与零优化核对见新运行根目录。\n',
        encoding='utf-8')
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--data', required=True, type=Path)
    p.add_argument('--destination', required=True, type=Path)
    a = p.parse_args()
    render(a.data, a.destination)
