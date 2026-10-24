function receipt = run_local_recipe02(delivery,work)
% Functional local-data replay of the three delivered original functions.
% The canonical QA authority remains the original scientific figure chain.
assert(~isfolder(work),'Fresh E output directory required');
mkdir(work);addpath(fullfile(delivery,'source'),'-begin');
r1=plot_sci_engineering_publication(fullfile(delivery,'data'),fullfile(work,'native01'));
r2=style_sci_class_ticks(fullfile(work,'native01'),fullfile(work,'ticks01'));
r3=style_final12_f1_padding(fullfile(work,'ticks01'),fullfile(work,'final02'));
files=dir(fullfile(delivery,'*.fig'));assert(numel(files)==2);
results=cell(numel(files),1);totalAxes=0;totalBars=0;totalYValues=0;
for k=1:numel(files)
    reference=fullfile(delivery,files(k).name);replayed=fullfile(work,'final02',files(k).name);
    a=openfig(reference,'invisible');b=openfig(replayed,'invisible');
    x=snapshot(a);y=snapshot(b);
    assert(isequaln(x,y),'Replay changed plotted values, axes, text, colors or fonts');
    assert(string(a.UserData.actual_source)~=string(b.UserData.actual_source));
    assert(string(b.UserData.actual_source)==string(fullfile(delivery,'data')));
    results{k}=struct('name',files(k).name,'exact_snapshot_equal',true, ...
        'axes',numel(x.axes),'bar_series',x.bar_series,'YData_value_count',x.y_count, ...
        'canonical_actual_source',a.UserData.actual_source,'replay_actual_source',b.UserData.actual_source);
    totalAxes=totalAxes+numel(x.axes);totalBars=totalBars+x.bar_series;totalYValues=totalYValues+x.y_count;
    close(a);close(b);
end
assert(totalAxes==10 && totalBars==50 && totalYValues==200);
receipt=struct('schema','hospital_ies.final12.local_data_recipe_functional_check.v1', ...
    'status','actual_recipe_completed_and_exact_native_semantics_checked', ...
    'MATLAB_release',version('-release'),'delivery',delivery,'work',work, ...
    'figures',{results},'axes',totalAxes,'bar_series',totalBars,'YData_value_count',totalYValues, ...
    'exact_snapshot_includes','XData/YData/CData/FaceColor/axis_UserData/limits/ticks/labels/fonts/figure_dimensions', ...
    'figure_actual_source_path_changed_as_expected',true, ...
    'new_full_page_visual_review_performed',false, ...
    'canonical_original_QA_reissued',false,'physics_or_LP_started',false, ...
    'native_receipt',r1,'tick_receipt',r2,'padding_receipt',r3);
fid=fopen(fullfile(work,'LOCAL_RECIPE_FUNCTIONAL_QA.json'),'w','n','UTF-8');
assert(fid~=-1);fprintf(fid,'%s',jsonencode(receipt,PrettyPrint=true));fclose(fid);
end
function s=snapshot(f)
axes=findall(f,'Type','axes');out=cell(numel(axes),1);nb=0;ny=0;
for j=1:numel(axes)
    a=axes(j);bars=findall(a,'Type','bar');series=cell(numel(bars),1);
    for i=1:numel(bars)
        h=bars(i);series{i}=struct('XData',h.XData,'YData',h.YData,'CData',h.CData, ...
            'FaceColor',h.FaceColor,'DisplayName',h.DisplayName);
        nb=nb+1;ny=ny+numel(h.YData);
    end
    out{j}=struct('XLim',a.XLim,'YLim',a.YLim,'ZLim',a.ZLim,'XTick',a.XTick,'YTick',a.YTick, ...
        'XTickLabel',{a.XTickLabel},'YTickLabel',{a.YTickLabel},'FontName',a.FontName, ...
        'FontSize',a.FontSize,'XLabel',label(a.XLabel),'YLabel',label(a.YLabel), ...
        'Title',label(a.Title),'UserData',a.UserData,'series',{series});
end
fontObjects=findall(f,'-property','FontName');fonts=cell(numel(fontObjects),1);
for i=1:numel(fontObjects),h=fontObjects(i);fonts{i}=struct('Type',h.Type,'FontName',h.FontName,'FontSize',h.FontSize);end
s=struct('axes',{out},'fonts',{fonts},'bar_series',nb,'y_count',ny, ...
    'Units',f.Units,'PositionSize',f.Position(3:4),'PaperUnits',f.PaperUnits,'PaperSize',f.PaperSize);
end
function s=label(h)
s=struct('String',{h.String},'FontName',h.FontName,'FontSize',h.FontSize,'Interpreter',h.Interpreter);
end
