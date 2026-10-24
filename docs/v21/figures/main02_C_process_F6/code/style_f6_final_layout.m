function receipt=style_f6_final_layout(sourceDir,outputDir,probeOnly)
% Geometry-only new output. Original plotter/QA/statistics/data stay unchanged.
arguments
    sourceDir (1,1) string
    outputDir (1,1) string
    probeOnly (1,1) logical = false
end
assert(isfolder(sourceDir)&&~isfolder(outputDir),'Existing source and fresh output required.');
R=jsondecode(fileread(fullfile(sourceDir,'MATLAB_FIGURE_RECEIPT.json')));
names=string(R.figures(:));assert(~isempty(names));
if probeOnly,candidates=names(startsWith(names,'F6_'));assert(~isempty(candidates));names=candidates(1);end
files=dir(fullfile(sourceDir,'*'));files=files(~[files.isdir]);before=cell(numel(files),1);
for k=1:numel(files)
    p=fullfile(sourceDir,files(k).name);
    before{k}=struct('name',files(k).name,'bytes',files(k).bytes,'sha256',hashFile(p));
end
copyfile(sourceDir,outputDir);details=cell(numel(names),1);changed=0;
for i=1:numel(names)
    stem=fullfile(outputDir,names(i));entry=struct('name',names(i),'modified',false);
    if startsWith(names(i),'F6_')
        f=openfig(stem+'.fig','invisible');cleanup=onCleanup(@()close(f));
        original=snapshot(f);ax=findall(f,'Type','axes');
        tiles=arrayfun(@(a)a.Layout.Tile,ax);power=ax(tiles==4);assert(isscalar(power));
        legends=findall(f,'Type','legend');hits=[];
        for k=1:numel(legends)
            if isequal(string(legends(k).String(:)),["GT";"GRID";"DG"]),hits(end+1)=k;end %#ok<AGROW>
        end
        assert(isscalar(hits),'Unique existing actual-power legend required.');
        lg=legends(hits);old=struct('Location',string(lg.Location), ...
            'Orientation',string(lg.Orientation),'Position',lg.Position,'Units',string(lg.Units));
        layout=power.Parent;assert(isa(layout,'matlab.graphics.layout.TiledChartLayout'));
        oldPadding=string(layout.Padding);layout.Padding='loose';
        lg.Orientation='horizontal';lg.Location='northoutside';drawnow;
        assert(string(lg.Location)=="northoutside"&&string(lg.Orientation)=="horizontal");
        store=ax(tiles==5);assert(isscalar(store));label=store.YLabel;
        originalDGLabel=struct('Units',string(label.Units),'Position',label.Position, ...
            'PositionMode',positionMode(label));
        label.Units='centimeters';labelBeforeCm=label.Position;
        p=labelBeforeCm;p(1)=p(1)-.45;label.Position=p;drawnow;
        dgLabelChange=struct('tile',5,'original_native',originalDGLabel, ...
            'before_cm',labelBeforeCm,'after_cm',label.Position, ...
            'horizontal_shift_cm',-.45,'after_Units',string(label.Units), ...
            'after_PositionMode',positionMode(label));
        viewChanges=restoreViewLimits(original,f);drawnow;
        assert(string(f.Units)=="centimeters");pageCm=f.Position(3:4);
        originalPaper=struct('Units',string(f.PaperUnits),'Size',f.PaperSize, ...
            'Position',f.PaperPosition,'PositionMode',string(f.PaperPositionMode));
        f.PaperUnits='centimeters';f.PaperSize=pageCm;
        f.PaperPosition=[0 0 pageCm];f.PaperPositionMode='manual';
        canvas=annotation(f,'rectangle',[0 0 1 1],'FaceColor','none', ...
            'Color',[1 1 1],'LineWidth',.1,'Tag','publication_page_canvas');
        assert(isequal(canvas.Position,[0 0 1 1])&&string(canvas.FaceColor)=="none");
        final=snapshot(f);assert(isequaln(original,final),'Legend styling changed scientific data or UserData.');
        savefig(f,stem+'.fig');print(f,stem+'.svg','-dsvg');
        exportgraphics(f,stem+'.pdf','ContentType','vector','BackgroundColor','white');
        print(f,stem+'.png','-dpng','-r600');
        assert(isequaln(original,snapshot(f)),'Export changed scientific data or UserData.');
        entry=struct('name',names(i),'modified',true,'power_tile',4,'before',old, ...
            'after',struct('Location',string(lg.Location),'Orientation',string(lg.Orientation), ...
            'Position',lg.Position,'Units',string(lg.Units)), ...
            'all_axes_X_Y_Z_CData_and_UserData_exact',true,'scientific_series_count',numel(original.series), ...
            'original_axis_view_limits_restored',{viewChanges}, ...
            'layout_padding_before',oldPadding,'layout_padding_after',string(layout.Padding), ...
            'DG_label_geometry_only',dgLabelChange, ...
            'original_paper',originalPaper,'full_physical_page_cm',pageCm, ...
            'PDF_export','MATLAB exportgraphics vector; explicit white vector canvas boundary', ...
            'PNG_export','MATLAB print -dpng -r600 full physical page; original native FIG fonts', ...
            'canvas_geometry',struct('Tag',string(canvas.Tag),'Position',canvas.Position, ...
                'Color',canvas.Color,'FaceColor',string(canvas.FaceColor),'LineWidth',canvas.LineWidth, ...
                'not_scientific_data_series',true,'does_not_fill_or_cover_axes',true), ...
            'native_inventory_endpoints_preserved',true,'neighbor_overlap_visual_review','PENDING');
        changed=changed+1;clear cleanup;
    end
    formats=struct;
    for ext=["fig","svg","pdf","png"]
        p=stem+'.'+ext;info=dir(p);assert(isscalar(info)&&info.bytes>0);
        formats.(ext)=struct('path',p,'bytes',info.bytes,'sha256',hashFile(p));
    end
    entry.formats=formats;details{i}=entry;
end
assert((~probeOnly&&changed==4)||(probeOnly&&changed==1),'Explicit single-probe or full-four F6 scope required.');
for k=1:numel(before),v=before{k};assert(hashFile(fullfile(sourceDir,v.name))==v.sha256,'Source artifact changed.');end
receipt=struct('schema','hospital_ies.f6_power_legend_style.v1','backend','MATLAB', ...
    'release',version('-release'),'source_figure_directory',sourceDir,'output_figure_directory',outputDir, ...
    'style_source_path',string(mfilename('fullpath'))+'.m','style_source_sha256',hashFile(string(mfilename('fullpath'))+'.m'), ...
    'changed_properties',["F6 tile4 legend Orientation","F6 tile4 legend Location/automatic Position", ...
        "F6 tiledlayout Padding loose for unclipped page margins", ...
        "Explicit unchanged-FIG physical PaperSize/PaperPosition and white unfilled vector canvas boundary", ...
        "PNG full-page MATLAB print -dpng -r600; vector PDF remains embedded-font exportgraphics", ...
        "DG inventory ylabel horizontal Position left by 0.45cm; label text/font unchanged", ...
        "Only automatically changed axes display limits restored to original values (explicit manual view)"], ...
    'source_files',{before},'source_files_unchanged',true,'scientific_data_or_statistics_changed',false, ...
    'is_historical_7123_wrapper',false,'figures',{details},'physics_attempts',0, ...
    'is_single_figure_probe',probeOnly,'actual_modified_F6_figures',changed, ...
    'unlisted_copied_originals_are_not_regenerated',probeOnly, ...
    'scientific_gate',false,'artifact_and_visual_QA','PENDING_ACTUAL_RECHECK');
fid=fopen(fullfile(outputDir,'F6_POWER_LEGEND_STYLE_RECEIPT.json'),'w','n','UTF-8');assert(fid>=0);
c=onCleanup(@()fclose(fid));fprintf(fid,'%s',jsonencode(receipt,'PrettyPrint',true));
end
function value=positionMode(label)
value="unknown_not_exposed";
if isprop(label,'PositionMode')
    try,value=string(label.PositionMode);catch err,value="unknown_"+string(err.identifier);end
end
end
function changes=restoreViewLimits(original,f)
% Legend/tiledlayout relayout can recompute an unrelated auto YLim. Keep the
% original displayed numeric range; no scientific series is changed.
axes=findall(f,'Type','axes');tiles=arrayfun(@(a)a.Layout.Tile,axes);[~,order]=sort(tiles);axes=axes(order);
assert(numel(axes)==numel(original.axes));changes={};
for k=1:numel(axes)
    a=axes(k);ref=original.axes{k};assert(a.Layout.Tile==ref.tile);
    for field=["XLim","YLim","ZLim"]
        if ~isequaln(a.(field),ref.(field))
            old=a.(field);mode=field+"Mode";oldMode=string(a.(mode));a.(field)=ref.(field);
            changes{end+1}=struct('tile',a.Layout.Tile,'property',field, ...
                'original_and_restored',ref.(field),'layout_auto_recomputed',old, ...
                'prior_mode',oldMode,'new_mode',string(a.(mode))); %#ok<AGROW>
        end
    end
end
end
function result=snapshot(f)
axes=findall(f,'Type','axes');tiles=arrayfun(@(a)a.Layout.Tile,axes);[~,order]=sort(tiles);axes=axes(order);
axisState=cell(numel(axes),1);series={};
for k=1:numel(axes)
    a=axes(k);axisState{k}=struct('tile',a.Layout.Tile,'UserData',a.UserData,'XLim',a.XLim,'YLim',a.YLim,'ZLim',a.ZLim);
    hs=findall(a);
    for j=1:numel(hs)
        h=hs(j);fields=["XData","YData","ZData","CData"];
        if ~any(arrayfun(@(v)isprop(h,v),fields)),continue;end
        v=struct('tile',a.Layout.Tile,'class',class(h),'UserData',h.UserData);
        for field=fields,if isprop(h,field),v.(field)=h.(field);end,end
        if isprop(h,'Color'),v.Color=h.Color;end
        if isprop(h,'LineStyle'),v.LineStyle=h.LineStyle;end
        if isprop(h,'Marker'),v.Marker=h.Marker;end
        series{end+1}=v; %#ok<AGROW>
    end
end
result=struct('figure_UserData',f.UserData,'axes',{axisState},'series',{series});
end
function value=hashFile(path)
fid=fopen(path,'rb');assert(fid>=0);c=onCleanup(@()fclose(fid));digest=java.security.MessageDigest.getInstance('SHA-256');
while true,b=fread(fid,1024*1024,'*uint8');if isempty(b),break;end;digest.update(typecast(b,'int8'));end
value=string(lower(reshape(dec2hex(typecast(digest.digest(),'uint8'),2)',1,[])));
end

