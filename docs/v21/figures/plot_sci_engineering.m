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
        W=T(string(T.phase)==phase&T.window_minutes==w&string(T.configuration_id)=="H0",:);
        if ismember('interaction_group_id',W.Properties.VariableNames)
            W=W(~present(W.interaction_group_id),:);
        end
        % NORMAL uses existing backgrounds and is kept apart from disturbance
        % and other sensitivity cases even when phase/window are the same.
        normalMask=string(W.canonical_class)=="NORMAL";
        for normalGroup=[false true]
            S=W(normalMask==normalGroup,:);if isempty(S),continue;end
            S=balancedFive(S,fields);
            tag=regexprep(phase,'[^A-Za-z0-9_]','_')+"_w"+string(w);
            if normalGroup,tag=tag+"_NORMAL";end
            if isempty(S)
                receipt.skipped(end+1)="No complete matched five-policy H0 panel: "+tag;continue
            end
            f=fresh(18,14);L=tiledlayout(f,2,3,'TileSpacing','compact','Padding','compact');
            for k=1:6
                ax=nexttile(L);b=classBars(ax,S,fields{k},colors);ylabel(ax,labels{k},'FontName',chineseFont,'Interpreter','none');
                if k==1,policyLegend(b,chineseFont);end
            end
            classNames=join(unique(string(S.canonical_class)),', ');blocks=numel(unique(S.source_block_id));
            if normalGroup
                title(L,sprintf('正常工况关键服务（NORMAL；%g min；既有源背景 %d）',w,blocks), ...
                    'FontName',chineseFont,'FontSize',10);
                L.OuterPosition=[.015 .08 .97 .90];
                annotation(f,'textbox',[.025 .008 .95 .047], ...
                    'String','NORMAL 为既有源背景的无故障对照；五种策略不增加独立背景数。', ...
                    'FontName',chineseFont,'FontSize',8.5,'EdgeColor','none','HorizontalAlignment','center');
            else
                title(L,sprintf('同窗关键服务 %s；%g min；源背景 %d',classNames,w,blocks), ...
                    'FontName',chineseFont,'FontSize',10);
            end
            name="F1_critical_service_"+tag;savePanel(f,outputDir,name,dataDir);receipt.figures(end+1)=name;close(f);
            costFields={'P2_union_material_minutes','P3_union_material_minutes','energy_cost_USD','total_CO2_kg_conditional'};
            costLabels={'P2 服务中断 / min','P3 服务中断 / min','同窗条件费用 / USD','同窗条件 CO2 / kg'};
            if all(ismember(costFields,S.Properties.VariableNames))&&all(isfinite(S{:,costFields}),'all')
                f=fresh(18,13);L=tiledlayout(f,2,2,'TileSpacing','compact','Padding','compact');
                for k=1:4
                    ax=nexttile(L);b=classBars(ax,S,costFields{k},colors);ylabel(ax,costLabels{k},'FontName',chineseFont,'Interpreter','none');
                    if k==1,policyLegend(b,chineseFont);end
                end
                if normalGroup
                    title(L,sprintf('正常工况一般服务与条件费用/排放（NORMAL；%g min；既有源背景 %d）',w,blocks), ...
                        'FontName',chineseFont,'FontSize',10);
                    L.OuterPosition=[.015 .08 .97 .90];
                    annotation(f,'textbox',[.025 .008 .95 .047], ...
                        'String','NORMAL 为既有源背景的无故障对照；五种策略不增加独立背景数。', ...
                        'FontName',chineseFont,'FontSize',8.5,'EdgeColor','none','HorizontalAlignment','center');
                else
                    title(L,sprintf('一般服务代价、同窗费用与条件排放（%s；%g min）',classNames,w),'FontName',chineseFont,'FontSize',10);
                end
                name="F2_service_cost_"+tag;savePanel(f,outputDir,name,dataDir);receipt.figures(end+1)=name;close(f);
            else
                receipt.skipped(end+1)="Unknown economics in matched panel "+tag+"; no zero-filled figure";
            end
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
if any(string(T.phase)=="hardware")
    H=T(string(T.phase)=="hardware"&ismember(string(T.policy_alias),["C","A"]),:);
    if isempty(H),receipt.skipped(end+1)="Hardware CASE has no actual C/A rows; F4 not generated";end
    selected={'critical_initial_handoff_gap_minutes','critical_e_ENS_kWh','critical_c_ENS_kWh', ...
        'P3_e_ENS_kWh','energy_cost_USD','total_CO2_kg_conditional'};
    labels={'登记接续窗内关键中断变化 / min','P0+P1 电 ENS 变化 / kWh_e', ...
        'P0+P1 冷 ENS 变化 / kWh_c','P3 电 ENS 变化 / kWh_e', ...
        '条件费用变化 / USD','条件 CO2 变化 / kg'};
    required={'fault_target','window_minutes','policy_alias'};
    if ~all(ismember(required,H.Properties.VariableNames))
        receipt.skipped(end+1)="Hardware CASE metadata incomplete; no pooled or zero-filled comparison";
    else
        targets=unique(string(H.fault_target(present(H.fault_target))));
        qw=unique(H.window_minutes(isfinite(H.window_minutes)));
        if any(~present(H.fault_target))||any(~isfinite(H.window_minutes))
            receipt.skipped(end+1)="Hardware rows have unknown fault_target/window; not assigned to a plotted group";
        end
        for target=targets'
            for w=qw'
                S=H(string(H.fault_target)==target&H.window_minutes==w,:);
                if isempty(S),continue;end
                [D,unknown]=hardwareTripleDeltas(S,selected);
                tag=regexprep(target,'[^A-Za-z0-9_]','_')+"_w"+string(w);
                if ~isempty(unknown)||isempty(D)
                    receipt.skipped(end+1)="Unknown/incomplete hardware group "+tag+": "+join(unknown," | ");continue
                end
                aliases=["C","A"];Y=nan(2,2,6);
                for k=1:6
                    for c=1:2
                        for a=1:2
                            q=D(string(D.configuration_id)=="H"+string(c)&string(D.policy_alias)==aliases(a),:);
                            Y(c,a,k)=blockMean(q,selected{k});
                        end
                    end
                end
                if ~all(isfinite(Y),'all')
                    receipt.skipped(end+1)="Unknown paired hardware metric "+tag+"; no missing bar was replaced by zero";continue
                end
                blocks=unique(string(D.source_block_id));
                f=fresh(24,17);L=tiledlayout(f,2,3,'TileSpacing','compact','Padding','compact');
                L.OuterPosition=[.015 .15 .97 .81];
                for k=1:6
                    ax=nexttile(L);b=bar(ax,Y(:,:,k),'grouped');b(1).FaceColor=colors(4,:);b(2).FaceColor=colors(5,:);
                    yline(ax,0,'-','Color',[.35 .35 .35]);xticks(ax,1:2);xticklabels(ax,{'H1-H0','H2-H0'});styleAxes(ax);
                    ylabel(ax,labels{k},'FontName',chineseFont,'Interpreter','none');
                    ax.UserData=struct('source_metric',selected{k},'fault_target',target,'window_minutes',w, ...
                        'paired_delta_H1_H2_by_C_A',Y(:,:,k),'source_blocks',blocks, ...
                        'paired_scenario_ids',unique(string(D.scenario_id)),'independent_unit','source_block', ...
                        'only_complete_H0_H1_H2_triples',true,'missing_values_zero_filled',false);
                    if k==1,legend(ax,{'C','A'},'Box','off','FontName','Times New Roman','Location','best');end
                end
                title(L,"硬件配对差值 / fault_target="+target+" / "+string(w)+" min / b="+string(numel(blocks)), ...
                    'FontName',chineseFont,'Interpreter','none','FontSize',10);
                annotation(f,'textbox',[.025 .012 .95 .13], ...
                    'String',{'条形为 H1-H0 或 H2-H0；负值表示该指标下降。关键电/冷仅在各自载能内合并 P0+P1。', ...
                    '先在同场景同策略完整三元组内配对，再等来源块平均；b 为来源块数，策略或季度标签不是新增样本。', ...
                    '分 P0/P1 六载能指标及 M 完整结果仍在原 CSV；条件费用与 CO2 不代表投资回收期。'}, ...
                    'FontName',chineseFont,'FontSize',8.5,'EdgeColor','none','HorizontalAlignment','center');
                name="F4_hardware_"+tag;
                writetable(D,fullfile(outputDir,name+'_PAIRED_VALUES.csv'));
                savePanel(f,outputDir,name,dataDir);receipt.figures(end+1)=name;close(f);
            end
        end
    end
else
    receipt.skipped(end+1)="No actual hardware CASE rows; F4 not generated";
end
if any(string(T.phase)=="boundary")
    B=T(string(T.phase)=="boundary"&eligible(T),:);
    assert(ismember('fault_target',B.Properties.VariableNames),'Boundary figures require the actual fault_target.');
    axesSpecs={ {'fault_duration_minutes','initial_reserve_kWh','故障持续时间 / min','初始轴功等价备用 / kWh_shaft','duration_reserve'}, ...
        {'demand_amplitude','rejection_capacity_kW','需求幅度 / 1','可用散热能力 / kW','demand_rejection'} };
    factors={'policy_id','configuration_id','window_minutes','fault_target','fault_duration_minutes', ...
        'initial_reserve_kWh','demand_amplitude','rejection_capacity_kW','response_delay_minutes'};
    metricFields={'critical_initial_handoff_gap_minutes','critical_post_handoff_gap_minutes', ...
        'critical_observed_continuous_minutes'};
    panelTitles={'登记接续窗内的关键中断','接续窗后至登记恢复前的关键中断', ...
        '接续后实际观察到的连续保障区间'};
    for spec=axesSpecs
        s=spec{1};xfield=s{1};yfield=s{2};
        if ~all(ismember({xfield,yfield},B.Properties.VariableNames)),continue;end
        keep=factors(~ismember(factors,{xfield,yfield}));keep=keep(ismember(keep,B.Properties.VariableNames));
        [group,keys]=boundaryGroups(B,keep);
        for g=1:height(keys)
            S=B(group==g,:);x=unique(S.(xfield));y=unique(S.(yfield));x=x(isfinite(x));y=y(isfinite(y));
            if numel(x)<2||numel(y)<2,continue;end
            Z=nan(numel(y),numel(x),3);rightCensored=false(numel(y),numel(x));
            for i=1:numel(y)
                for j=1:numel(x)
                    q=S(S.(xfield)==x(j)&S.(yfield)==y(i),:);
                    for k=1:2,Z(i,j,k)=boundaryCellValue(q,metricFields{k});end
                    value=boundaryCellValue(q,metricFields{3});
                    [known,flag]=boundaryCensorFlag(q);
                    if isfinite(value)&&known
                        Z(i,j,3)=value;rightCensored(i,j)=flag;
                    end
                end
            end
            if ~any(isfinite(Z),'all'),continue;end
            f=fresh(28,11);L=tiledlayout(f,1,3,'TileSpacing','compact','Padding','compact');
            L.OuterPosition=[.015 .17 .97 .80];
            for k=1:3
                ax=nexttile(L);
                flags=false(numel(y),numel(x));if k==3,flags=rightCensored;end
                boundaryPatchCells(ax,x,y,Z(:,:,k),flags,metricFields{k},xfield,yfield,chineseFont);
                xlabel(ax,s{3},'FontName',chineseFont,'Interpreter','none');
                ylabel(ax,s{4},'FontName',chineseFont,'Interpreter','none');
                title(ax,panelTitles{k},'FontName',chineseFont,'FontSize',9.5,'Interpreter','none');
            end
            title(L,string(S.policy_id(1))+" / "+string(S.configuration_id(1))+" / "+ ...
                string(S.window_minutes(1))+" min / fault_target="+string(S.fault_target(1)), ...
                'FontName',chineseFont,'Interpreter','none','FontSize',10);
            annotation(f,'textbox',[.025 .012 .95 .14], ...
                'String',{'每格为离散登记点；空白表示缺值或无观察齐备的连续区间，不补零、不插值。', ...
                '第三图从登记接续窗后首个实际完整接续点计时，最迟截止登记恢复起点或实际窗口末端。', ...
                '* 表示该格存在右删失记录：截至观察上限未再次中断，不是永久保障或已测得的资源寿命。'}, ...
                'FontName',chineseFont,'FontSize',8.5,'EdgeColor','none','HorizontalAlignment','center');
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
            '.','Color',colors(k+2,:),'MarkerSize',6); % Recorded endpoints only; no interpolated inventory curve.
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
function b=classBars(ax,S,field,colors)
order=["L1","L2","L3","L4","NORMAL"];classes=order(ismember(order,unique(string(S.canonical_class))));
aliases=["R","M","F","C","A"];Y=nan(numel(classes),5);ticks=strings(1,numel(classes));
for c=1:numel(classes)
    group=string(S.canonical_class)==classes(c);ticks(c)=sprintf('%s\nb=%d',classes(c),numel(unique(S.source_block_id(group))));
    for p=1:5,q=S(group&string(S.policy_alias)==aliases(p),:);Y(c,p)=blockMean(q,field);end
end
if isscalar(classes)
    b=gobjects(1,5);hold(ax,'on');
    for p=1:5,b(p)=bar(ax,p,Y(1,p),.65,'FaceColor',colors(p,:));end
    hold(ax,'off');xticks(ax,1:5);xticklabels(ax,aliases);xlim(ax,[.4 5.6]);
else
    b=bar(ax,Y,'grouped');for p=1:5,b(p).FaceColor=colors(p,:);end
    xticks(ax,1:numel(classes));xticklabels(ax,ticks);
end
ax.UserData=struct('source_metric',field,'plotted_classes',classes,'source_block_mean_values',Y, ...
    'policy_aliases',aliases,'independent_unit','source_block');
styleAxes(ax);ax.XTickLabelRotation=0;
end
function policyLegend(b,chineseFont)
labels={'R 常规规则','M 当前需求优先','F 固定备用','C 现有 C19','A 状态协调'};
legendHandle=legend(b,labels,'NumColumns',5,'Box','off','FontName',chineseFont,'FontSize',8,'Interpreter','none');
legendHandle.Layout.Tile='north';
end
function [group,keys]=boundaryGroups(T,fields)
K=table;for field=fields,s=string(T.(field{1}));s(ismissing(s))="<missing>";K.(field{1})=s;end
[group,keys]=findgroups(K);
end
function [D,unknown]=hardwareTripleDeltas(S,metrics)
% Complete, source-matched H0/H1/H2 triples only. Never select a default A.
D=table;unknown=strings(0,1);
identity={'source_block_id','model_id','source_binding_sha256','scenario_sha256','policy_id','policy_sha256','fault_target'};
rawFields={'critical_initial_handoff_gap_minutes','P0_e_ENS_kWh','P1_e_ENS_kWh', ...
    'P0_c_ENS_kWh','P1_c_ENS_kWh','P3_e_ENS_kWh','energy_cost_USD','total_CO2_kg_conditional'};
required=[identity,rawFields,{'scenario_id','configuration_id','configuration_sha256','window_minutes', ...
    'eligible','status','unknown_tail_minutes','policy_alias'}];
if ~all(ismember(required,S.Properties.VariableNames))
    unknown="Required CASE identities or hardware metrics absent";return
end
for field={'model_id','source_binding_sha256'}
    if any(~present(S.(field{1})))||numel(unique(string(S.(field{1}))))~=1
        unknown="Mixed/unknown hardware model or source identity";return
    end
end
for alias=["C","A"]
    q=S(string(S.policy_alias)==alias,:);
    if isempty(q)||any(~present(q.policy_id))||any(~present(q.policy_sha256)) ...
            ||numel(unique(string(q.policy_id)))~=1||numel(unique(string(q.policy_sha256)))~=1
        unknown="Missing or mixed actual policy specification for alias "+alias;return
    end
end
for config=["H0","H1","H2"]
    q=S(string(S.configuration_id)==config,:);
    if isempty(q)||any(~present(q.configuration_sha256))||numel(unique(string(q.configuration_sha256)))~=1
        unknown="Missing or mixed frozen hardware configuration "+config;return
    end
end
scenes=unique(string(S.scenario_id));records=cell(height(S),1);used=0;
for scene=scenes'
    for alias=["C","A"]
        V=S(string(S.scenario_id)==scene&string(S.policy_alias)==alias,:);
        configs=string(V.configuration_id);
        reason="";
        if height(V)~=3||numel(unique(configs))~=3||~all(ismember(["H0","H1","H2"],configs))
            reason="Missing or duplicate H0/H1/H2 member";
        elseif ~all(eligible(V))||any(string(V.status)~="completed") ...
                ||any(~isfinite(V.unknown_tail_minutes))||any(V.unknown_tail_minutes~=0)
            reason="Failed, ineligible, incomplete or unknown-tail member";
        elseif any(~isfinite(V.window_minutes))||numel(unique(V.window_minutes))~=1
            reason="Evaluation windows differ or are unknown";
        else
            for field=identity
                if any(~present(V.(field{1})))||numel(unique(string(V.(field{1}))))~=1
                    reason="Unmatched "+string(field{1});break
                end
            end
        end
        if strlength(reason)>0
            unknown(end+1)=scene+" / "+alias+": "+reason;continue %#ok<AGROW>
        end
        values=V{:,rawFields};assert(isnumeric(values),'Actual numeric hardware MAT columns required.');
        if any(~isfinite(values),'all')
            unknown(end+1)=scene+" / "+alias+": Unknown service, cost or conditional CO2 metric";continue %#ok<AGROW>
        end
        % Only same-carrier sums across P0 and P1; heat/electricity/cold are
        % never combined, and initial_handoff is the existing union metric.
        values=[values(:,1),values(:,2)+values(:,3),values(:,4)+values(:,5),values(:,6:8)];
        baseline=values(configs=="H0",:);
        for config=["H1","H2"]
            delta=values(configs==config,:)-baseline;
            row=struct('source_block_id',string(V.source_block_id(1)),'scenario_id',scene, ...
                'policy_id',string(V.policy_id(1)),'policy_alias',alias,'policy_sha256',string(V.policy_sha256(1)), ...
                'configuration_id',config,'window_minutes',V.window_minutes(1), ...
                'fault_target',string(V.fault_target(1)),'source_binding_sha256',string(V.source_binding_sha256(1)), ...
                'scenario_sha256',string(V.scenario_sha256(1)));
            for k=1:numel(metrics),row.(metrics{k})=delta(k);end
            used=used+1;records{used}=row;
        end
    end
end
if used>0,D=struct2table([records{1:used}]);end
end
function value=boundaryCellValue(T,field)
% A missing observation remains a blank cell; never replace None/NaN by zero.
value=nan;if isempty(T)||~ismember(field,T.Properties.VariableNames),return;end
values=T.(field);assert(isnumeric(values),'Actual numeric boundary MAT column required.');
if any(~isfinite(values)),return;end
value=blockMean(T,field);
end
function [known,flag]=boundaryCensorFlag(T)
known=false;flag=false;field='critical_continuity_right_censored';
if isempty(T)||~ismember(field,T.Properties.VariableNames),return;end
values=T.(field);
if ~(isnumeric(values)||islogical(values))||any(~isfinite(values))||any(~ismember(values,[0 1])),return;end
known=true;flag=any(values==1);
end
function boundaryPatchCells(ax,x,y,Z,censored,metric,xfield,yfield,chineseFont)
% One native vector rectangle per finite registered grid value. No imagesc,
% image objects, resampling, interpolation or invented points are used.
assert(isequal(size(Z),[numel(y),numel(x)])&&isequal(size(censored),size(Z)),'Boundary grid dimensions differ.');
assert(all(Z(isfinite(Z))>=0),'Boundary time metric must be nonnegative.');
hold(ax,'on');set(ax,'YDir','normal','Color','w');
xlim(ax,[.5 numel(x)+.5]);ylim(ax,[.5 numel(y)+.5]);
xticks(ax,1:numel(x));xticklabels(ax,compose('%.5g',x));
yticks(ax,1:numel(y));yticklabels(ax,compose('%.5g',y));
styleAxes(ax);grid(ax,'off');ax.Box='on';ax.Layer='top';
ax.UserData=struct('schema','hospital_ies.boundary_patch_grid.v1','source_metric',metric, ...
    'x_field',xfield,'y_field',yfield,'native_x_values',x,'native_y_values',y, ...
    'native_grid_values',Z,'right_censored_cells',censored,'missing_cells',~isfinite(Z), ...
    'discrete_registered_grid',true,'interpolated',false,'missing_values_zero_filled',false);
valid=isfinite(Z);
if ~any(valid,'all')
    text(ax,.5,.5,'无可用连续区间观测','Units','normalized','HorizontalAlignment','center', ...
        'FontName',chineseFont,'FontSize',9,'Color',[.4 .4 .4]);
    return
end
cmap=parula(256);colormap(ax,cmap);top=max(Z(valid));
if top==0,clim(ax,[0 1]);else,clim(ax,[0 top]);end
for i=1:numel(y)
    for j=1:numel(x)
        if ~valid(i,j),continue;end
        p=patch(ax,[j-.5 j+.5 j+.5 j-.5],[i-.5 i-.5 i+.5 i+.5],Z(i,j), ...
            'FaceColor','flat','CDataMapping','scaled','EdgeColor','w','LineWidth',.6);
        p.Tag='native_boundary_cell';
        p.UserData=struct('source_metric',metric,'x_field',xfield,'y_field',yfield, ...
            'x_value',x(j),'y_value',y(i),'value',Z(i,j),'right_censored',censored(i,j));
        index=1;if top>0,index=1+floor((size(cmap,1)-1)*Z(i,j)/top);end
        labelColor=[0 0 0];if dot(cmap(index,:),[.2126 .7152 .0722])<.47,labelColor=[1 1 1];end
        suffix='';if censored(i,j),suffix='*';end
        label=text(ax,j,i,sprintf('%.1f%s',Z(i,j),suffix),'HorizontalAlignment','center', ...
            'VerticalAlignment','middle','FontName','Times New Roman','FontSize',8,'Color',labelColor);
        label.Tag='native_boundary_value_label';label.UserData=p.UserData;
    end
end
cb=colorbar(ax);cb.FontName='Times New Roman';cb.FontSize=8;
cb.Label.String='实际观察时长 / min';cb.Label.FontName=chineseFont;
if top==0,cb.Ticks=0;cb.TickLabels={'0'};end
end
function f=fresh(width,height)
f=figure('Visible','off','Color','w','Units','centimeters','Position',[1 1 width height]);
end
function styleAxes(ax)
labels=[ax.XLabel,ax.YLabel,ax.Title];fonts={labels.FontName};
ax.FontSize=9;ax.FontName='Times New Roman';ax.Box='off';ax.TickDir='out';ax.LineWidth=.7;
for k=1:numel(labels),labels(k).FontName=fonts{k};end
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
