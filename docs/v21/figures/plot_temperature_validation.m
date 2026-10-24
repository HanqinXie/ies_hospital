function plot_temperature_validation(outputDir, sourceRoot)
% Native recorded 10-minute held-out EnergyPlus temperatures and old 1R1C.
% No smoothing, interpolation, curve fabrication or production parameter edit.
% Default source_data is a compact copy of analysis03_clock_corrected CSVs.
here = fileparts(mfilename('fullpath'));
if nargin<1, outputDir=fullfile(here,'temperature_validation'); end
if nargin<2, sourceRoot=fullfile(here,'temperature_validation','source_data'); end
if ~exist(outputDir,'dir'),mkdir(outputDir);end
cases = {'val_winter_2h','val_summer_4h'};
names = {'Held-out winter: 2 h cooling loss','Held-out summer: 4 h cooling loss'};
summary=readtable(fullfile(sourceRoot,'TEMPERATURE_VALIDATION_RESULTS.csv'), ...
    'TextType','string','VariableNamingRule','preserve');
fig=figure('Color','w','Units','centimeters','Position',[2 2 24 16], ...
    'Visible','off','Renderer','painters');
set(fig,'DefaultAxesFontName','Times New Roman','DefaultTextFontName','Times New Roman');
layout=tiledlayout(fig,2,2,'TileSpacing','compact','Padding','compact');
layout.OuterPosition=[.015 .09 .97 .86];
sgtitle(layout,'Independent OR1 temperature validation: warming and recovery', ...
    'FontName','Times New Roman','FontSize',12,'FontWeight','bold');
colors=[.10 .29 .45;.76 .31 .16;.45 .45 .45;.48 .30 .59];
allSamples=table(); firstHandles=[];
for k=1:2
    path=fullfile(sourceRoot,[cases{k} '_unchanged_production_proxy.csv']);
    data=readtable(path,'TextType','string','VariableNamingRule','preserve');
    stamps=datetime(data.timestamp,'InputFormat','yyyy-MM-dd HH:mm:ss');
    active=strcmpi(string(data.outage_active),'True') | strcmp(string(data.outage_active),'1');
    nativeMinutes=minutes(diff(stamps));
    assert(all(abs(nativeMinutes-10)<1e-8),'Source must retain native 10-minute intervals');
    start=stamps(find(active,1))-minutes(10);
    outageHours=hours(stamps(find(active,1,'last'))-start);
    relativeHours=hours(stamps-start);
    selected=relativeHours>=-1 & relativeHours<=12;
    x=relativeHours(selected);
    actual=data.independent_temperature_C(selected);
    surrogate=data.surrogate_temperature_C(selected);
    baseline=data.baseline_temperature_C(selected);
    criterion=data.cooling_setpoint_C(selected)+2;
    error=surrogate-actual;
    rows=summary.case_id==string(cases{k}) & summary.parameter_label=="unchanged_production_proxy";
    wholeRMSE=summary.rms_temperature_error_C(rows);
    maxAbs=summary.maximum_absolute_temperature_error_C(rows);
    assert(numel(wholeRMSE)==1,'Exact held-out unchanged-proxy summary required');

    ax=nexttile(layout,k);hold(ax,'on');box(ax,'on');
    ymax=ceil(max([actual;surrogate;criterion])+1);
    yl=[20 ymax];
    patch(ax,[0 outageHours outageHours 0],[yl(1) yl(1) yl(2) yl(2)], ...
        [.94 .94 .94],'EdgeColor','none','HandleVisibility','off');
    h1=plot(ax,x,actual,'Color',colors(1,:),'LineWidth',1.6);
    h2=plot(ax,x,surrogate,'Color',colors(2,:),'LineWidth',1.45);
    h3=plot(ax,x,baseline,':','Color',colors(3,:),'LineWidth',1.0);
    h4=plot(ax,x,criterion,'--','Color',colors(4,:),'LineWidth',1.15);
    xline(ax,0,':','Color',[.55 .55 .55],'HandleVisibility','off');
    xline(ax,outageHours,':','Color',[.55 .55 .55],'HandleVisibility','off');
    xlim(ax,[-1 12]);ylim(ax,yl);xticks(ax,[0 2 4 6 8 10 12]);
    ylabel(ax,'Zone temperature (°C)','FontSize',10);
    title(ax,[char('a'+k-1) ') ' names{k}],'FontSize',10,'FontWeight','normal');
    text(ax,outageHours/2,ymax-.6,'cooling off','HorizontalAlignment','center', ...
        'FontSize',8.5,'Color',[.35 .35 .35]);
    text(ax,11.7,ymax-1.4,sprintf('Whole-tail RMSE: %.3f °C',wholeRMSE), ...
        'HorizontalAlignment','right','FontSize',8.7);
    set(ax,'FontSize',9,'LineWidth',.75,'TickDir','out');
    if k==1,firstHandles=[h1 h2 h3 h4];end

    ax=nexttile(layout,k+2);hold(ax,'on');box(ax,'on');
    patch(ax,[0 outageHours outageHours 0],[-8 -8 8 8], ...
        [.94 .94 .94],'EdgeColor','none','HandleVisibility','off');
    plot(ax,x,error,'Color',colors(2,:),'LineWidth',1.4);
    yline(ax,0,'-','Color',[.55 .55 .55],'LineWidth',.65);
    xline(ax,0,':','Color',[.55 .55 .55]);xline(ax,outageHours,':','Color',[.55 .55 .55]);
    xlim(ax,[-1 12]);ylim(ax,[-8 8]);xticks(ax,[0 2 4 6 8 10 12]);yticks(ax,-8:4:8);
    xlabel(ax,'Hours after cooling interruption','FontSize',10);
    ylabel(ax,'Proxy minus EnergyPlus (°C)','FontSize',10);
    title(ax,sprintf('%c) Error; whole-tail max |error| = %.3f °C','c'+k-1,maxAbs), ...
        'FontSize',10,'FontWeight','normal');
    set(ax,'FontSize',9,'LineWidth',.75,'TickDir','out');
    sample=table(repmat(string(cases{k}),sum(selected),1),data.timestamp(selected),x, ...
        actual,surrogate,baseline,criterion,error, ...
        'VariableNames',{'case_id','native_timestamp','hours_from_event','EnergyPlus_C', ...
        'unchanged_proxy_C','paired_baseline_C','research_setpoint_plus2_C','error_C'});
    allSamples=[allSamples;sample]; %#ok<AGROW>
end
legendHandle=legend(firstHandles,{'Independent EnergyPlus','Unchanged 1R1C proxy', ...
    'Paired normal baseline','Research criterion: setpoint +2°C'}, ...
    'NumColumns',2,'Orientation','horizontal','FontSize',9,'Box','off');
legendHandle.Layout.Tile='south';
annotation(fig,'textbox',[.03 .005 .94 .045], ...
    'String','探索性温度代理：定量验证未通过；设定点 +2°C 为研究判据，不是临床安全阈值。', ...
    'FontName','SimSun','FontSize',9,'EdgeColor','none','HorizontalAlignment','center');
savefig(fig,fullfile(outputDir,'temperature_validation.fig'));
print(fig,fullfile(outputDir,'temperature_validation.svg'),'-dsvg');
exportgraphics(fig,fullfile(outputDir,'temperature_validation.pdf'),'ContentType','vector','BackgroundColor','white');
exportgraphics(fig,fullfile(outputDir,'temperature_validation.png'),'Resolution',600,'BackgroundColor','white');
writetable(allSamples,fullfile(outputDir,'temperature_validation_plotted_samples.csv'));
receipt=struct('source_root',sourceRoot,'case_ids',{cases},'native_step_minutes',10, ...
    'resampled',false,'production_proxy_changed',false,'plotted_rows',height(allSamples), ...
    'line_objects',numel(findall(fig,'Type','line')),'image_objects',numel(findall(fig,'Type','image')), ...
    'quantitative_validation_passed',false,'clinical_threshold_asserted',false);
fid=fopen(fullfile(outputDir,'temperature_figure_receipt.json'),'w','n','UTF-8');
fwrite(fid,jsonencode(receipt),'char');fclose(fid);close(fig);
end
