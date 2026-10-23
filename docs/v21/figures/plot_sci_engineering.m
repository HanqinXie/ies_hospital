function receipt = plot_sci_engineering(dataDir,outputDir)
% Actual source-bound CSV/MAT only. No interpolation, mock or Python figures.
arguments
    dataDir (1,1) string
    outputDir (1,1) string
end
assert(isfile(fullfile(dataDir,'CASE_METRICS.csv')),'Actual CASE_METRICS.csv required.');
assert(~isfolder(outputDir),'Use a fresh immutable figure directory.');
T=readActual(dataDir,'CASE_METRICS');
assert(height(T)>0,'No actual results; no placeholder figures are generated.');
mkdir(outputDir);chineseFont=char([23435,20307]); % Installed font: 宋体.
set(groot,'defaultAxesFontName','Times New Roman','defaultTextFontName',chineseFont);
set(groot,'defaultAxesFontSize',9,'defaultTextFontSize',9);
colors=[.45 .45 .45;.10 .35 .62;.56 .37 .67;.13 .52 .39;.82 .37 .12];
receipt=struct('backend','MATLAB','release',version('-release'),'source_directory',dataDir, ...
    'scientific_data_only',true,'png_dpi',600,'chinese_font',chineseFont, ...
    'figures',strings(0,1),'skipped',strings(0,1));
fields={'P0_e_ENS_kWh','P0_h_ENS_kWh','P0_c_ENS_kWh','P1_e_ENS_kWh','P1_h_ENS_kWh','P1_c_ENS_kWh'};
labels={'P0 电 ENS / kWh_e','P0 热 ENS / kWh_h','P0 冷 ENS / kWh_c', ...
    'P1 电 ENS / kWh_e','P1 热 ENS / kWh_h','P1 冷 ENS / kWh_c'};
phases=unique(string(T.phase));windows=unique(T.window_minutes(isfinite(T.window_minutes)));
for phase=phases'
    if ismember(phase,["mechanism","boundary","hardware"]),continue;end
    for w=windows'
        S=T(string(T.phase)==phase&T.window_minutes==w&string(T.configuration_id)=="H0",:);
        if ismember('interaction_group_id',S.Properties.VariableNames)
            S=S(~present(S.interaction_group_id),:);
        end
        S=balancedFive(S,fields);
        tag=regexprep(phase,'[^A-Za-z0-9_]','_')+"_w"+string(w);
        if isempty(S)
            receipt.skipped(end+1)="No complete matched five-policy H0 panel: "+tag;continue
        end
        f=fresh(18,14);L=tiledlayout(f,2,3,'TileSpacing','compact','Padding','compact');
        for k=1:6
            ax=nexttile(L);classBars(ax,S,fields{k},colors);ylabel(ax,labels{k},'FontName',chineseFont,'Interpreter','none');
            if k==1,legend(ax,{'R','M','F','C','A'},'Location','northoutside','NumColumns',5,'Box','off');end
        end
        title(L,sprintf('同窗关键服务；%g min；独立源块 %d',w,numel(unique(S.source_block_id))), ...
            'FontName',chineseFont,'FontSize',10);
        name="F1_critical_service_"+tag;savePanel(f,outputDir,name,dataDir);receipt.figures(end+1)=name;close(f);
        costFields={'P2_union_material_minutes','P3_union_material_minutes','energy_cost_USD','total_CO2_kg_conditional'};
        costLabels={'P2 服务中断 / min','P3 服务中断 / min','同窗条件费用 / USD','同窗条件 CO2 / kg'};
        if all(ismember(costFields,S.Properties.VariableNames))&&all(isfinite(S{:,costFields}),'all')
            f=fresh(18,13);L=tiledlayout(f,2,2,'TileSpacing','compact','Padding','compact');
            for k=1:4,ax=nexttile(L);classBars(ax,S,costFields{k},colors);ylabel(ax,costLabels{k},'FontName',chineseFont,'Interpreter','none');end
            title(L,'一般服务代价、同窗费用与条件排放','FontName',chineseFont,'FontSize',10);
            name="F2_service_cost_"+tag;savePanel(f,outputDir,name,dataDir);receipt.figures(end+1)=name;close(f);
        else
            receipt.skipped(end+1)="Unknown economics in matched panel "+tag+"; no zero-filled figure";
        end
    end
end
if hasRows(fullfile(dataDir,'MECHANISM_EFFECTS.csv'))
    Q=readActual(dataDir,'MECHANISM_EFFECTS');
    selected={'P0_c_ENS_kWh','P1_c_ENS_kWh','P3_e_ENS_kWh','energy_cost_USD'};
    effect={'reserve_only_delta','preparation_only_delta','both_delta','interaction_delta'};
    labels={'P0 冷 ENS 变化 / kWh_c','P1 冷 ENS 变化 / kWh_c','P3 电 ENS 变化 / kWh_e','条件费用变化 / USD'};
    if all(ismember([{'window_minutes','configuration_id','metric','status'},effect],Q.Properties.VariableNames))
        configs=unique(string(Q.configuration_id));qw=unique(Q.window_minutes(isfinite(Q.window_minutes)));
        for config=configs'
            for w=qw'
                S=Q(string(Q.configuration_id)==config&Q.window_minutes==w&string(Q.status)=="complete_matched_four_cells",:);
                Y=nan(4,4);
                for k=1:4,for j=1:4,Y(k,j)=blockMean(S(string(S.metric)==selected{k},:),effect{j});end,end
                tag=config+"_w"+string(w);
                if ~all(isfinite(Y),'all'),receipt.skipped(end+1)="Incomplete mechanism effects: "+tag;continue;end
                f=fresh(18,13);L=tiledlayout(f,2,2,'TileSpacing','compact','Padding','compact');
                for k=1:4
                    ax=nexttile(L);bar(ax,Y(k,:),'FaceColor',colors(5,:));yline(ax,0,'-','Color',[.35 .35 .35]);
                    xticks(ax,1:4);xticklabels(ax,{'仅备用','仅准备','同时采用','交互项'});styleAxes(ax);ax.XAxis.FontName=chineseFont;
                    ylabel(ax,labels{k},'FontName',chineseFont,'Interpreter','none');
                end
                title(L,'匹配四组合的措施贡献；负值为该指标下降','FontName',chineseFont,'FontSize',10);
                name="F3_mechanism_"+tag;savePanel(f,outputDir,name,dataDir);receipt.figures(end+1)=name;close(f);
            end
        end
    end
end
if hasRows(fullfile(dataDir,'HARDWARE_DIFFERENCES.csv'))
    Q=readActual(dataDir,'HARDWARE_DIFFERENCES');
    selected={'critical_post_handoff_gap_minutes','P0_c_ENS_kWh','energy_cost_USD','total_CO2_kg_conditional'};
    labels={'接续窗后关键中断变化 / min','P0 冷 ENS 变化 / kWh_c','条件费用变化 / USD','条件 CO2 变化 / kg'};
    aliases=["C","A"];qw=unique(Q.window_minutes(isfinite(Q.window_minutes)));
    for w=qw'
        Y=nan(2,2,4);
        for k=1:4
            for c=1:2
                for a=1:2
                    q=Q(Q.window_minutes==w&string(Q.metric)==selected{k}&string(Q.configuration_id)=="H"+string(c) ...
                        &startsWith(string(Q.candidate_policy_id),aliases(a)),:);Y(c,a,k)=blockMean(q,'delta');
                end
            end
        end
        if ~all(isfinite(Y),'all'),receipt.skipped(end+1)="Incomplete paired hardware panel: w"+string(w);continue;end
        f=fresh(18,13);L=tiledlayout(f,2,2,'TileSpacing','compact','Padding','compact');
        for k=1:4
            ax=nexttile(L);b=bar(ax,Y(:,:,k),'grouped');b(1).FaceColor=colors(4,:);b(2).FaceColor=colors(5,:);
            yline(ax,0);xticks(ax,1:2);xticklabels(ax,{'H1-H0','H2-H0'});styleAxes(ax);
            ylabel(ax,labels{k},'FontName',chineseFont,'Interpreter','none');if k==1,legend(ax,{'C','A'},'Box','off');end
        end
        title(L,'各硬件扩展分别与基础配置比较','FontName',chineseFont,'FontSize',10);
        name="F4_hardware_w"+string(w);savePanel(f,outputDir,name,dataDir);receipt.figures(end+1)=name;close(f);
    end
end
if any(string(T.phase)=="boundary")
    B=T(string(T.phase)=="boundary"&eligible(T),:);
    axesSpecs={ {'fault_duration_minutes','initial_reserve_kWh','故障持续时间 / min','初始轴功等价备用 / kWh_shaft','duration_reserve'}, ...
        {'demand_amplitude','rejection_capacity_kW','需求幅度 / 1','可用散热能力 / kW','demand_rejection'} };
    factors={'policy_id','configuration_id','window_minutes','fault_target','fault_duration_minutes', ...
        'initial_reserve_kWh','demand_amplitude','rejection_capacity_kW','response_delay_minutes'};
    for spec=axesSpecs
        s=spec{1};xfield=s{1};yfield=s{2};
        if ~all(ismember({xfield,yfield},B.Properties.VariableNames)),continue;end
        keep=factors(~ismember(factors,{xfield,yfield}));keep=keep(ismember(keep,B.Properties.VariableNames));
        [group,keys]=boundaryGroups(B,keep);
        for g=1:height(keys)
            S=B(group==g,:);x=unique(S.(xfield));y=unique(S.(yfield));x=x(isfinite(x));y=y(isfinite(y));
            if numel(x)<2||numel(y)<2,continue;end
            gapFields={'critical_initial_handoff_gap_minutes','critical_post_handoff_gap_minutes'};
            Z=nan(numel(y),numel(x),2);
            for i=1:numel(y)
                for j=1:numel(x)
                    for k=1:2
                        q=S(S.(xfield)==x(j)&S.(yfield)==y(i),:);Z(i,j,k)=blockMean(q,gapFields{k});
                    end
                end
            end
            if ~any(isfinite(Z),'all'),continue;end
            f=fresh(19,8);L=tiledlayout(f,1,2,'TileSpacing','compact','Padding','compact');
            for k=1:2
                ax=nexttile(L);imagesc(ax,Z(:,:,k),'AlphaData',isfinite(Z(:,:,k)));set(ax,'YDir','normal');
                xticks(ax,1:numel(x));xticklabels(ax,string(x));yticks(ax,1:numel(y));yticklabels(ax,string(y));
                xlabel(ax,s{3},'FontName',chineseFont);ylabel(ax,s{4},'FontName',chineseFont);styleAxes(ax);
                cb=colorbar(ax);cb.Label.String='实际关键中断 / min';cb.Label.FontName=chineseFont;
                if k==1,title(ax,'登记接续窗内','FontName',chineseFont);else,title(ax,'接续窗后至登记恢复前','FontName',chineseFont);end
            end
            title(L,string(S.policy_id(1))+" / "+string(S.configuration_id(1))+" / "+string(S.window_minutes(1))+" min",'Interpreter','none','FontSize',10);
            name="F5_"+string(s{5})+"_slice"+string(g);savePanel(f,outputDir,name,dataDir);receipt.figures(end+1)=name;close(f);
        end
    end
end
processes=dir(fullfile(dataDir,'**','PROCESS.csv'));
for j=1:numel(processes)
    source=fullfile(processes(j).folder,processes(j).name);P=readProcess(processes(j).folder);
    if height(P)==0,continue;end
    storeFields={'pre_diesel_energy_remaining_kWh','ups_initial_kWh','cold_store_initial_kWh'};
    storeCount=sum(ismember(storeFields,P.Properties.VariableNames));
    f=fresh(18,19+3*storeCount);L=tiledlayout(f,4+storeCount,1,'TileSpacing','compact','Padding','compact');
    carriers={'e','h','c'};labels={'电功率 / kW_e','热功率 / kW_h','冷功率 / kW_c'};
    for c=1:3
        ax=nexttile(L);hold(ax,'on');request=P.(['P0_' carriers{c} '_requested_kW'])+P.(['P1_' carriers{c} '_requested_kW']);
        delivered=P.(['P0_' carriers{c} '_delivered_kW'])+P.(['P1_' carriers{c} '_delivered_kW']);
        nativeStairs(ax,P,request,[.2 .2 .2],'--');nativeStairs(ax,P,delivered,colors(4,:),'-');
        ylabel(ax,labels{c},'FontName',chineseFont,'Interpreter','none');styleAxes(ax);
        if c==1,legend(ax,{'P0+P1 请求','P0+P1 实际交付'},'FontName',chineseFont,'Box','off');end
    end
    ax=nexttile(L);hold(ax,'on');genKeys="gt_electric_kW_"+string(1:3);ports=strings(3,1);nport=0;
    if all(ismember(genKeys,P.Properties.VariableNames)),nativeStairs(ax,P,sum(P{:,cellstr(genKeys)},2),colors(1,:),'-');nport=nport+1;ports(nport)="GT";end
    if ismember('grid_import_kW',P.Properties.VariableNames),nativeStairs(ax,P,P.grid_import_kW,colors(2,:),'-');nport=nport+1;ports(nport)="GRID";end
    if ismember('diesel_kW',P.Properties.VariableNames),nativeStairs(ax,P,P.diesel_kW,colors(5,:),'-');nport=nport+1;ports(nport)="DG";end
    ylabel(ax,'实际电源 / kW_e','FontName',chineseFont,'Interpreter','none');if nport>0,legend(ax,ports(1:nport),'Box','off');end;styleAxes(ax);
    storeFinal={'post_diesel_energy_remaining_kWh','ups_final_kWh','cold_store_final_kWh'};
    storeLabels={'DG 轴功等价库存 / kWh_shaft','UPS 电储量 / kWh_e','独立冷储量 / kWh_c'};
    for k=1:3
        if ~ismember(storeFields{k},P.Properties.VariableNames),continue;end
        assert(ismember(storeFinal{k},P.Properties.VariableNames),'Missing actual terminal storage receipt.');
        ax=nexttile(L);last=P.(storeFinal{k});plot(ax,[P.start_minute;P.end_minute(end)]/60,[P.(storeFields{k});last(end)], ...
            '-','Color',colors(k+2,:),'LineWidth',1.1);
        ylabel(ax,storeLabels{k},'FontName',chineseFont,'Interpreter','none');styleAxes(ax);
    end
    xlabel(ax,'实际经过时间 / h','FontName',chineseFont);
    name="F6_process_"+string(j);savePanel(f,outputDir,name,source);receipt.figures(end+1)=name;close(f);
end
fid=fopen(fullfile(outputDir,'MATLAB_FIGURE_RECEIPT.json'),'w','n','UTF-8');assert(fid>=0);
cleaner=onCleanup(@()fclose(fid));fprintf(fid,'%s',jsonencode(receipt,'PrettyPrint',true));
end

function T=readActual(directory,name)
name=string(name);
T=readtable(fullfile(directory,name+'.csv'),'TextType','string','VariableNamingRule','preserve');
path=fullfile(directory,'SCI_TABLES.mat');assert(isfile(path),'Matching numeric MAT table is required.');
M=load(path,name+'_numeric',name+'_numeric_columns');columns=string(M.(name+'_numeric_columns'));values=M.(name+'_numeric');
assert(height(T)==size(values,1)&&numel(columns)==size(values,2),'CSV/MAT dimensions differ.');
for k=1:numel(columns),T.(columns(k))=values(:,k);end
end
function T=readProcess(directory)
T=readtable(fullfile(directory,'PROCESS.csv'),'VariableNamingRule','preserve');
M=load(fullfile(directory,'PROCESS.mat'));assert(height(T)==size(M.values,1),'Process CSV/MAT dimensions differ.');
columns=string(M.columns);for k=1:numel(columns),T.(columns(k))=M.values(:,k);end
end
function S=balancedFive(S,fields)
if isempty(S),return;end
if ~all(ismember(fields,S.Properties.VariableNames)),S=S([],:);return;end
keys=unique(string(S.scenario_id));keep=false(height(S),1);
for key=keys'
    index=string(S.scenario_id)==key;aliases=string(S.policy_alias(index));
    if sum(index)==5&&numel(unique(aliases))==5&&all(ismember(["R","M","F","C","A"],aliases)) ...
            &&all(eligible(S(index,:)))&&all(isfinite(S{index,fields}),'all'),keep=keep|index;end
end
S=S(keep,:);
end
function value=eligible(T)
value=ismember(lower(string(T.eligible)),["true","1"]);
end
function value=present(values)
s=string(values);value=~ismissing(s)&strlength(s)>0&s~="NaN";
end
function y=blockMean(T,field)
y=nan;if isempty(T)||~ismember(field,T.Properties.VariableNames),return;end
values=T.(field);assert(isnumeric(values),'Actual numeric MAT column required.');
ok=isfinite(values);T=T(ok,:);values=values(ok);if isempty(T),return;end
blocks=unique(string(T.source_block_id));means=nan(numel(blocks),1);
for i=1:numel(blocks),means(i)=mean(values(string(T.source_block_id)==blocks(i)));end
y=mean(means);
end
function classBars(ax,S,field,colors)
classes=["L1","L2","L3","L4"];aliases=["R","M","F","C","A"];Y=nan(4,5);ticks=strings(1,4);
for c=1:4
    group=string(S.canonical_class)==classes(c);ticks(c)=classes(c)+" (b="+string(numel(unique(S.source_block_id(group))))+")";
    for p=1:5,q=S(group&string(S.policy_alias)==aliases(p),:);Y(c,p)=blockMean(q,field);end
end
b=bar(ax,Y,'grouped');for p=1:5,b(p).FaceColor=colors(p,:);end
xticks(ax,1:4);xticklabels(ax,ticks);styleAxes(ax);
end
function [group,keys]=boundaryGroups(T,fields)
K=table;for field=fields,s=string(T.(field{1}));s(ismissing(s))="<missing>";K.(field{1})=s;end
[group,keys]=findgroups(K);
end
function f=fresh(width,height)
f=figure('Visible','off','Color','w','Units','centimeters','Position',[1 1 width height]);
end
function styleAxes(ax)
ax.FontSize=9;ax.FontName='Times New Roman';ax.Box='off';ax.TickDir='out';ax.LineWidth=.7;
grid(ax,'on');ax.GridAlpha=.12;ax.GridLineStyle=':';
end
function nativeStairs(ax,P,values,color,style)
stairs(ax,[P.start_minute;P.end_minute(end)]/60,[values;values(end)],style,'Color',color,'LineWidth',1.1);
end
function value=hasRows(path)
value=false;if ~isfile(path),return;end
info=dir(path);if info.bytes<=5,return;end
T=readtable(path,'TextType','string','VariableNamingRule','preserve');value=height(T)>0;
end
function savePanel(f,directory,name,source)
f.UserData=struct('actual_source',source,'no_synthetic_data',true,'native_intervals_preserved',true);
drawnow;savefig(f,fullfile(directory,name+'.fig'));
exportgraphics(f,fullfile(directory,name+'.pdf'),'ContentType','vector','BackgroundColor','white');
print(f,fullfile(directory,name+'.svg'),'-dsvg');exportgraphics(f,fullfile(directory,name+'.png'),'Resolution',600,'BackgroundColor','white');
proof=openfig(fullfile(directory,name+'.fig'),'invisible');assert(~isempty(findall(proof,'Type','axes')),'Reopened FIG has no axes.');close(proof);
end
