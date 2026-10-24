function receipt=style_sci_class_ticks(sourceDir,outputDir)
% Readability-only copy: rotate crowded four-class F1 tick labels, no data edits.
arguments
    sourceDir (1,1) string
    outputDir (1,1) string
end
assert(isfolder(sourceDir)&&~isfolder(outputDir),'Existing source and fresh output required.');
R=jsondecode(fileread(fullfile(sourceDir,'MATLAB_FIGURE_RECEIPT.json')));
names=string(R.figures(:));assert(~isempty(names));
originals=dir(fullfile(sourceDir,'*'));assert(~any([originals(~ismember({originals.name},{'.','..'})).isdir]),'Flat actual figure directory required.');
originals=originals(~[originals.isdir]);before=cell(numel(originals),1);
for k=1:numel(originals)
    p=fullfile(sourceDir,originals(k).name);
    before{k}=struct('name',originals(k).name,'bytes',originals(k).bytes,'sha256',hashFile(p));
end
copyfile(sourceDir,outputDir);details=cell(numel(names),1);
for i=1:numel(names)
    stem=fullfile(outputDir,names(i));changed=0;dataBefore={};dataAfter={};
    if startsWith(names(i),'F1_')
        f=openfig(stem+'.fig','invisible');cleanup=onCleanup(@()close(f));
        bars=findall(f,'Type','bar');dataBefore=barValues(bars);
        axes=findall(f,'Type','axes');
        for a=axes(:)'
            u=a.UserData;
            if isstruct(u)&&isfield(u,'plotted_classes')&&numel(u.plotted_classes)>2
                a.XTickLabelRotation=45;changed=changed+1;
            end
        end
        dataAfter=barValues(bars);assert(isequaln(dataBefore,dataAfter),'Tick styling changed bar data.');
        if changed>0
            drawnow;savefig(f,stem+'.fig');print(f,stem+'.svg','-dsvg');
            exportgraphics(f,stem+'.pdf','ContentType','vector','BackgroundColor','white');
            exportgraphics(f,stem+'.png','Resolution',600,'BackgroundColor','white');
            assert(isequaln(dataBefore,barValues(bars)),'Export changed bar data.');
        end
        clear cleanup;
    end
    formats=struct;
    for ext=["fig","svg","pdf","png"]
        p=stem+'.'+ext;info=dir(p);assert(isscalar(info)&&info.bytes>0);
        formats.(ext)=struct('path',p,'bytes',info.bytes,'sha256',hashFile(p));
    end
    details{i}=struct('name',names(i),'rotated_axes',changed,'rotation_degrees',45, ...
        'bar_numeric_and_userdata_exact',isequaln(dataBefore,dataAfter),'formats',formats);
end
for k=1:numel(before)
    x=before{k};assert(hashFile(fullfile(sourceDir,x.name))==x.sha256,'Source artifact changed.');
end
receipt=struct('schema','hospital_ies.class_tick_readability_style.v1', ...
    'backend','MATLAB','release',version('-release'),'source_figure_directory',sourceDir, ...
    'output_figure_directory',outputDir,'source_directory',string(R.source_directory), ...
    'style_source_path',string(mfilename('fullpath'))+'.m', ...
    'style_source_sha256',hashFile(string(mfilename('fullpath'))+'.m'), ...
    'only_changed_property','F1 axes XTickLabelRotation for more than two classes', ...
    'source_files_unchanged',true,'source_files',{before},'figures',{details}, ...
    'source_data_or_statistics_changed',false,'physics_attempts',0, ...
    'artifact_and_visual_QA','PENDING_REAL_FINAL_CHECKS');
fid=fopen(fullfile(outputDir,'CLASS_TICK_STYLE_RECEIPT.json'),'w','n','UTF-8');assert(fid>=0);
c=onCleanup(@()fclose(fid));fprintf(fid,'%s',jsonencode(receipt,'PrettyPrint',true));
end
function values=barValues(bars)
values=cell(numel(bars),1);
for k=1:numel(bars),b=bars(k);values{k}=struct('XData',b.XData,'YData',b.YData,'CData',b.CData,'UserData',b.UserData);end
end
function value=hashFile(path)
fid=fopen(path,'rb');assert(fid>=0);c=onCleanup(@()fclose(fid));digest=java.security.MessageDigest.getInstance('SHA-256');
while true,b=fread(fid,1024*1024,'*uint8');if isempty(b),break;end;digest.update(typecast(b,'int8'));end
value=string(lower(reshape(dec2hex(typecast(digest.digest(),'uint8'),2)',1,[])));
end
