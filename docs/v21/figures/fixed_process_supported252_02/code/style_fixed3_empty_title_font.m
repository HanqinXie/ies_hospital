function receipt=style_fixed3_empty_title_font(sourceDir,outputDir)
% Actual diagnosis: one empty native layout.Title perF6 defaults to Helvetica.
arguments
    sourceDir (1,1) string
    outputDir (1,1) string
end
assert(isfolder(sourceDir)&&~isfolder(outputDir));
R=jsondecode(fileread(fullfile(sourceDir,'MATLAB_FIGURE_RECEIPT.json')));
names=string(R.figures(:));assert(numel(names)==12&&sum(startsWith(names,'F6_'))==3);
files=dir(fullfile(sourceDir,'*'));files=files(~[files.isdir]);before=cell(numel(files),1);
for k=1:numel(files)
    p=fullfile(sourceDir,files(k).name);before{k}=struct('name',files(k).name,'sha256',hashFile(p));
end
copyfile(sourceDir,outputDir);details=cell(numel(names),1);total=0;
for i=1:numel(names)
    stem=fullfile(outputDir,names(i));changes={};
    if startsWith(names(i),'F6_')
        f=openfig(stem+'.fig','invisible');c=onCleanup(@()close(f));original=snapshot(f);
        layouts=findall(f,'Type','tiledlayout');assert(isscalar(layouts));h=layouts.Title;
        assert(isa(h,'matlab.graphics.layout.Text')&&all(strlength(string(h.String))==0,'all'));
        assert(string(h.FontName)=="Helvetica"&&string(h.Visible)=="on");
        prior=struct('class',class(h),'String',h.String,'FontName',string(h.FontName),'Visible',string(h.Visible));
        h.FontName='Times New Roman';drawnow;viewChanges=restoreViewLimits(original,f);drawnow;
        assert(isequal(h.String,prior.String)&&string(h.Visible)==prior.Visible);
        assert(isequaln(original,snapshot(f)),'Font-only repair changed scientific data/UserData/limits.');
        savefig(f,stem+'.fig');print(f,stem+'.svg','-dsvg','-painters');
        exportgraphics(f,stem+'.pdf','ContentType','vector','BackgroundColor','white');
        print(f,stem+'.png','-dpng','-r600');
        assert(isequaln(original,snapshot(f)),'Font-only export changed scientific data.');
        changes={struct('native_layout_empty_Title_before',prior,'FontName_after',string(h.FontName), ...
            'String_Visible_preserved',true,'scientific_snapshot_exact',true,'view_changes',{viewChanges})};
        total=total+1;clear c;
    end
    formats=struct;
    for ext=["fig","svg","pdf","png"]
        p=stem+'.'+ext;v=dir(p);assert(isscalar(v)&&v.bytes>0);
        formats.(ext)=struct('path',p,'bytes',v.bytes,'sha256',hashFile(p));
    end
    details{i}=struct('name',names(i),'font_changes',{changes},'formats',formats);
end
assert(total==3);
for k=1:numel(before),v=before{k};assert(hashFile(fullfile(sourceDir,v.name))==v.sha256);end
receipt=struct('schema','hospital_ies.fixed3_empty_native_title_font_only.v1','backend','MATLAB', ...
    'source_directory',sourceDir,'output_directory',outputDir,'FontName_only',true, ...
    'changed_objects',total,'all_empty_Title_strings_preserved',true,'data_or_statistics_changed',false, ...
    'source_files',{before},'source_files_unchanged',true,'figures',{details}, ...
    'original_v2_and_QA_unchanged',true,'actual_physics_started',0,'scientific_gate',false);
fid=fopen(fullfile(outputDir,'FONT_ONLY_STYLE_RECEIPT.json'),'w','n','UTF-8');assert(fid>=0);
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

