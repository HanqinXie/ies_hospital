function plot_supply_topology(outputDir)
% Native editable topology drawn from registered IDF service mapping.
% Run: plot_supply_topology('E:\hospital_ies_v21_20261023\figures\topology')
% Solid: H0 common plant. Dashed purple: separate H1/H2 extensions.
% Heat rejection arrows do not represent useful cooling delivery.

sourceDir = fileparts(mfilename('fullpath'));
repoRoot = fileparts(fileparts(fileparts(sourceDir)));
if nargin < 1
    outputDir = fullfile(sourceDir,'supply_topology');
end
if ~exist(outputDir,'dir'), mkdir(outputDir); end
mapPath = fullfile(repoRoot,'inputs','sci_engineering','IDF_SERVICE_MAPPING.csv');
mapping = readtable(mapPath,'TextType','string','VariableNamingRule','preserve');
cfg = jsondecode(fileread(fullfile(repoRoot,'configs','plant_configs.json')));
scale = cfg.study_cases.doe_capacity_matched_exploratory;
gt = cfg.source_equipment.gas_turbines.rated_electric_kW_each * scale.gas_turbine_scale;
dg = cfg.source_equipment.emergency_diesel_generators.rated_electric_kW_each * scale.diesel_scale;

colors.e = [0.12 0.29 0.48];
colors.h = [0.67 0.30 0.12];
colors.c = [0.06 0.48 0.47];
colors.r = [0.72 0.47 0.09];
colors.x = [0.49 0.29 0.58];
fig = figure('Color','w','Units','centimeters','Position',[2 2 25 16], ...
    'Visible','off','Renderer','painters');
ax = axes(fig,'Units','normalized','Position',[0 0 1 1], ...
    'XLim',[0 1],'YLim',[0 1]);
axis(ax,'off'); hold(ax,'on');
set(fig,'DefaultTextFontName','Times New Roman','DefaultTextInterpreter','none');
text(ax,.02,.965,'Hospital IES: supply paths and required auxiliaries', ...
    'FontSize',14,'FontWeight','bold','VerticalAlignment','top');
text(ax,.02,.925,'DOE-connected airloops; NMCSD-inspired plant; separate H1 and H2 configurations', ...
    'FontSize',10,'VerticalAlignment','top','Color',[.25 .25 .25]);

% Electrical sources and common bus.
edge(ax,[.15 .847;.23 .847],colors.e,'-');
edge(ax,[.085 .695;.085 .760;.295 .760;.295 .81],colors.e,'-');
edge(ax,[.15 .467;.18 .467;.18 .780;.250 .780;.250 .810],colors.e,'-');
edge(ax,[.36 .847;.64 .847],colors.e,'-');
edge(ax,[.36 .825;.600 .825;.600 .647;.64 .647],colors.e,'-');
edge(ax,[.36 .810;.585 .810;.585 .520;.65 .520;.65 .455],colors.e,'-');
edge(ax,[.585 .520;.55 .520;.55 .495],colors.e,'-');
edge(ax,[.265 .810;.205 .810;.205 .257;.23 .257],colors.e,'-');
% Useful heat and cooling delivery paths.
edge(ax,[.15 .657;.23 .657],colors.h,'-');
edge(ax,[.36 .657;.43 .657],colors.h,'-');
edge(ax,[.295 .525;.295 .565;.50 .565;.50 .620],colors.h,'-');
edge(ax,[.57 .657;.64 .657],colors.h,'-');
edge(ax,[.50 .620;.50 .495],colors.h,'-');
edge(ax,[.57 .457;.615 .457;.615 .417;.64 .417],colors.c,'-');
edge(ax,[.30 .295;.30 .345;.71 .345;.71 .380],colors.c,'-');
edge(ax,[.78 .417;.84 .417],colors.c,'-');
edge(ax,[.78 .647;.84 .647],colors.h,'-');
edge(ax,[.78 .837;.84 .837],colors.e,'-');
% Heat rejected to the shared tower is a separate capacity dependency.
edge(ax,[.50 .420;.50 .275],colors.r,'--');
edge(ax,[.37 .237;.43 .237],colors.r,'--');
% Finite hardware alternatives, each evaluated against H0 separately.
edge(ax,[.105 .145;.105 .370;.195 .370;.195 .795;.23 .795;.23 .810],colors.x,'--');
edge(ax,[.685 .170;.685 .380],colors.x,'--');
edge(ax,[.750 .380;.750 .170],colors.x,'--');

node(ax,[.02 .81 .13 .075],{'External grid','finite import port'},colors.e);
node(ax,[.02 .62 .13 .075],{sprintf('GT1-3: 3 x %.0f kW',gt),'actual start / protection'},colors.e);
node(ax,[.02 .43 .13 .075],{sprintf('DG: 3 x %.0f kW',dg),'finite shaft-work reserve'},colors.e);
node(ax,[.23 .81 .13 .075],{'Common electric bus','commands / contacts'},colors.e);
node(ax,[.23 .62 .13 .075],{'HRSG1-3','GT-dependent steam'},colors.h);
node(ax,[.23 .45 .13 .075],{'Boilers','gas -> steam'},colors.h);
node(ax,[.43 .62 .14 .075],{'Steam / heat path','building heat + ABS'},colors.h);
node(ax,[.43 .42 .14 .075],{'Absorption chiller','steam + electric aux.'},colors.c);
node(ax,[.23 .22 .14 .075],{'Electric chillers','electricity -> cooling'},colors.c);
node(ax,[.43 .20 .14 .075],{'Shared cooling tower','heat-rejection constraint'},colors.r);
node(ax,[.64 .80 .14 .075],{'Required airloop fans','OR / ICU / ER + loops'},colors.e);
node(ax,[.64 .61 .14 .075],{'Heat circulation pump','added electric demand'},colors.h);
node(ax,[.64 .38 .14 .075],{'CHW distribution pump','existing load reassigned'},colors.c);
node(ax,[.84 .38 .14 .495],{'AREA SERVICES', ...
    'P0: OR / ICU / trauma','P1: exam / IC rooms','and nurse station', ...
    'P2: radiology','P3: remaining services', ...
    'Original requests retained','e / h / c tracked separately'},[.32 .37 .42]);
node(ax,[.02 .06 .17 .085],{'H1: finite UPS bridge','power, SOC, losses, recharge'},colors.x,'--');
node(ax,[.64 .08 .16 .09],{'H2: finite cold storage','charge / discharge + pump','CHW / fan power needed'},colors.x,'--');

text(ax,.43,.31,'rejected heat','FontSize',8.5,'Color',colors.r);
text(ax,.25,.38,'useful cold','FontSize',8.5,'Color',colors.c);
text(ax,.20,.12,{'H1 and H2 are separate hardware comparisons.', ...
    'Dashed branches provide finite energy; they are not permanent sources.', ...
    'Fan power does not certify ventilation, pressure or infection control.'}, ...
    'FontSize',9,'VerticalAlignment','middle','Color',[.25 .25 .25]);
text(ax,.02,.025,sprintf('Source: IDF_SERVICE_MAPPING.csv (%d terminal-zone records); shared fan ownership uses recorded design airflow.',height(mapping)), ...
    'FontSize',8.5,'VerticalAlignment','bottom','Color',[.25 .25 .25]);

savefig(fig,fullfile(outputDir,'supply_topology.fig'));
print(fig,fullfile(outputDir,'supply_topology.svg'),'-dsvg');
exportgraphics(fig,fullfile(outputDir,'supply_topology.pdf'),'ContentType','vector','BackgroundColor','white');
exportgraphics(fig,fullfile(outputDir,'supply_topology.png'),'Resolution',600,'BackgroundColor','white');
receipt = struct('source_mapping',mapPath,'source_mapping_rows',height(mapping), ...
    'generator','MATLAB R2024b native line/patch/text objects','fig_editable',true, ...
    'line_objects',numel(findall(fig,'Type','line')), ...
    'patch_objects',numel(findall(fig,'Type','patch')), ...
    'text_objects',numel(findall(fig,'Type','text')), ...
    'configuration_scope','solid H0; dashed H1 UPS and H2 cold storage; capacities not asserted before freeze');
fid=fopen(fullfile(outputDir,'topology_receipt.json'),'w','n','UTF-8');
fwrite(fid,jsonencode(receipt),'char'); fclose(fid);
close(fig);
end

function node(ax,box,lines,color,style)
if nargin<5,style='-';end
rectangle(ax,'Position',box,'Curvature',.12,'EdgeColor',color, ...
    'FaceColor',[1 1 1]*.96+.04*color,'LineWidth',1.1,'LineStyle',style);
text(ax,box(1)+box(3)/2,box(2)+box(4)/2,lines,'HorizontalAlignment','center', ...
    'VerticalAlignment','middle','FontSize',8.0,'Color',[.08 .08 .08]);
end

function edge(ax,xy,color,style)
plot(ax,xy(:,1),xy(:,2),'Color',color,'LineWidth',1.25,'LineStyle',style);
v=xy(end,:)-xy(end-1,:);v=v/norm(v);n=[-v(2) v(1)];
tip=xy(end,:);base=tip-.009*v;
patch(ax,[tip(1),base(1)+.003*n(1),base(1)-.003*n(1)], ...
    [tip(2),base(2)+.003*n(2),base(2)-.003*n(2)],color,'EdgeColor',color);
end
