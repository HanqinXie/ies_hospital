function results=test_state_object_refusals(manifestPath,knownSHA,sourceDir,outputDir)
% Temporary altered FIGs are negative interface tests, never publication data.
arguments
    manifestPath (1,1) string
    knownSHA (1,1) string
    sourceDir (1,1) string
    outputDir (1,1) string
end
assert(isfolder(sourceDir)&&~isfolder(outputDir));mkdir(outputDir);
kinds=["GT_coordinate","GT_RGB","title_text","title_font"];
expected=["stateqa:GTCoordinates","stateqa:GTColors","stateqa:Title","stateqa:Fonts"];
results=cell(4,1);
for i=1:4
    folder=fullfile(outputDir,kinds(i));copyfile(sourceDir,folder);
    figPath=fullfile(folder,'STATE_fixed067_C_H0.fig');f=openfig(figPath,'invisible');
    cleanup=onCleanup(@()close(f));
    switch kinds(i)
        case "GT_coordinate"
            h=findall(f,'Tag','GT_1');assert(isscalar(h));x=h.XData;x(1)=x(1)+.5;h.XData=x;
        case "GT_RGB"
            h=findall(f,'Tag','GT_1');assert(isscalar(h));rgb=h.CData;rgb(1,:)=[.123 .456 .789];h.CData=rgb;
        case "title_text"
            h=findall(f,'Tag','MAIN_TITLE');assert(isscalar(h));h.String='wrong identity title';
        case "title_font"
            h=findall(f,'-isa','matlab.graphics.layout.TiledChartLayout');assert(isscalar(h));h.Title.FontName='Helvetica';
    end
    savefig(f,figPath);clear cleanup;
    caught=false;identifier="";message="";
    try
        qa_state_figure(manifestPath,knownSHA,folder,fullfile(outputDir,kinds(i)+"_audit"));
    catch exc
        caught=true;identifier=string(exc.identifier);message=string(exc.message);
    end
    assert(caught&&identifier==expected(i),'Negative test was not rejected by its exact intended assertion.');
    results{i}=struct('case',kinds(i),'expected_identifier',expected(i),'actual_identifier',identifier, ...
        'actual_message',message,'actual_rejected',caught,'not_scientific_or_publication_data',true);
end
fid=fopen(fullfile(outputDir,'NATIVE_OBJECT_REFUSALS.json'),'w','n','UTF-8');assert(fid>=0);
cleanup=onCleanup(@()fclose(fid));fprintf(fid,'%s',jsonencode(struct('tests',{results}, ...
    'all_four_intended_refusals',true,'scientific_gate',false,'physics_attempts',0),'PrettyPrint',true));
end
