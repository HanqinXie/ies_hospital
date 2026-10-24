function receipt=style_final12_f1_padding(sourceDir,outputDir)
% Page/export geometry only. Exact helper tail copied from approved437 F6.
arguments
    sourceDir (1,1) string
    outputDir (1,1) string
end
assert(isfolder(sourceDir)&&~isfolder(outputDir),'Existing source and fresh output required.');
R=jsondecode(fileread(fullfile(sourceDir,'MATLAB_FIGURE_RECEIPT.json')));
names=string(R.figures(:));
assert(numel(names)==2&&sum(startsWith(names,'F1_'))==1&&sum(startsWith(names,'F2_'))==1);
files=dir(fullfile(sourceDir,'*'));files=files(~[files.isdir]);before=cell(numel(files),1);
for k=1:numel(files)
    p=fullfile(sourceDir,files(k).name);
    before{k}=struct('name',files(k).name,'bytes',files(k).bytes,'sha256',hashFile(p));
end
copyfile(sourceDir,outputDir);details=cell(numel(names),1);
for i=1:numel(names)
    stem=fullfile(outputDir,names(i));f=openfig(stem+'.fig','invisible');cleanup=onCleanup(@()close(f));
    original=snapshot(f);assert(string(f.Units)=="centimeters");pageCm=f.Position(3:4);
    fontObjects=findall(f,'-property','FontName');fontBefore=fontSnapshot(fontObjects);
    layouts=findall(f,'-isa','matlab.graphics.layout.TiledChartLayout');assert(numel(layouts)==1);
    paddingBefore=string(layouts.Padding);
    if startsWith(names(i),'F1_'),assert(paddingBefore=="compact");layouts.Padding='loose';end
    paddingAfter=string(layouts.Padding);
    oldPaper=struct('Units',string(f.PaperUnits),'Size',f.PaperSize,'Position',f.PaperPosition,'PositionMode',string(f.PaperPositionMode));
    f.PaperUnits='centimeters';f.PaperSize=pageCm;f.PaperPosition=[0 0 pageCm];f.PaperPositionMode='manual';
    canvas=annotation(f,'rectangle',[0 0 1 1],'FaceColor','none','Color',[1 1 1], ...
        'LineWidth',.1,'Tag','publication_page_canvas');
    assert(isequal(canvas.Position,[0 0 1 1])&&string(canvas.FaceColor)=="none");
    drawnow;viewChanges=restoreViewLimits(original,f);drawnow;
    assert(isequaln(original,snapshot(f)),'Page geometry changed scientific series/UserData/axis limits.');
    assert(isequaln(fontBefore,fontSnapshot(fontObjects)),'Layout changed original font properties.');
    savefig(f,stem+'.fig');print(f,stem+'.svg','-dsvg');
    exportgraphics(f,stem+'.pdf','ContentType','vector','BackgroundColor','white');
    print(f,stem+'.png','-dpng','-r600');
    assert(isequaln(original,snapshot(f)),'Export changed scientific data/UserData/axis limits.');
    assert(isequaln(fontBefore,fontSnapshot(fontObjects)),'Export changed original font properties.');
    formats=struct;
    for ext=["fig","svg","pdf","png"]
        p=stem+'.'+ext;info=dir(p);assert(isscalar(info)&&info.bytes>0);
        formats.(ext)=struct('path',p,'bytes',info.bytes,'sha256',hashFile(p));
    end
    details{i}=struct('name',names(i),'padding_before',paddingBefore,'padding_after',paddingAfter, ...
        'font_properties_exact',true,'scientific_X_Y_Z_CData_UserData_axislimits_exact',true, ...
        'scientific_series_count',numel(original.series),'oldPaper',oldPaper,'full_physical_page_cm',pageCm, ...
        'original_axis_limits_restored',{viewChanges},'canvas',struct('Position',canvas.Position, ...
        'FaceColor',string(canvas.FaceColor),'Color',canvas.Color,'not_scientific_series',true), ...
        'PDF_export','MATLAB exportgraphics vector with explicit white unfilled native canvas', ...
        'PNG_export','MATLAB print -dpng -r600 physical full page, native font properties unchanged', ...
        'SVG_export','MATLAB print -dsvg','formats',formats);
    clear cleanup;
end
for k=1:numel(before),v=before{k};assert(hashFile(fullfile(sourceDir,v.name))==v.sha256,'Source artifact changed.');end
receipt=struct('schema','hospital_ies.final12_padding_fullpage_style.v1','backend','MATLAB','release',version('-release'), ...
    'source_directory',sourceDir,'output_directory',outputDir, ...
    'style_source_path',string(mfilename('fullpath'))+'.m','style_source_sha256',hashFile(string(mfilename('fullpath'))+'.m'), ...
    'approved_helper_origin_sha256','437acb7b396123d212d2d35760f522af3fe9b7b9debe3d5966a08861f79bf37f', ...
    'source_files',{before},'source_files_unchanged',true,'figures',{details}, ...
    'base_fullpage_helper_sha256','d60c0ba1c88a6c08796f9724deebc3b5e52bd12065f905578a3e3c001759b32c', ...
    'changes','Only F1 tiledlayout.Padding compact->loose; original page-only exports and exact data/fonts/axis limits retained', ...
    'scientific_data_or_statistics_changed',false,'physics_attempts',0,'scientific_gate',false,'actual_format_visual_QA','PENDING');
fid=fopen(fullfile(outputDir,'FINAL12_PADDING_FULLPAGE_STYLE_RECEIPT.json'),'w','n','UTF-8');assert(fid>=0);
c=onCleanup(@()fclose(fid));fprintf(fid,'%s',jsonencode(receipt,'PrettyPrint',true));
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


function state=fontSnapshot(handles)
state=cell(numel(handles),1);
for k=1:numel(handles)
 h=handles(k);assert(isvalid(h));v=struct('class',class(h),'FontName',h.FontName);
 for field=["FontSize","FontUnits"],if isprop(h,field),v.(field)=h.(field);end,end
 state{k}=v;
end
end
