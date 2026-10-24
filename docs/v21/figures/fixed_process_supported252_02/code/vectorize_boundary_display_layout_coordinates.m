function receipt=vectorize_boundary_display_layout_coordinates(f)
% Display equivalence only. Preserve every scientific scalar CData and grid.
axes=findall(f,'Type','axes');assert(numel(axes)==3);records=cell(3,1);
% The approved F5 is exactly one row of three tiles. Read all existing peers
% once and keep their original page positions; colorbar(a) would reflow them.
layouts=findall(f,'Type','tiledlayout');assert(numel(layouts)==1&&isequal(layouts.GridSize,[1 3]));
frozenBars=findall(f,'Type','colorbar');assert(numel(frozenBars)==3);
for cb=frozenBars(:)',cb.Units='normalized';end
drawnow;x=arrayfun(@(cb)cb.Position(1),frozenBars);[~,order]=sort(x);frozenBars=frozenBars(order);
assert(all(diff(sort(x))>0));originalPositions=cell(3,1);
for k=1:3,originalPositions{k}=frozenBars(k).Position;end
for k=1:3,frozenBars(k).Position=originalPositions{k};end
drawnow;
for k=1:3,assert(isequaln(frozenBars(k).Position,originalPositions{k}));end
for j=1:numel(axes)
    a=axes(j);assert(string(a.UserData.schema)=="hospital_ies.boundary_patch_grid.v1");
    cmap=colormap(a);limits=a.CLim;assert(size(cmap,2)==3&&limits(2)>limits(1));
    patches=findall(a,'Tag','native_boundary_cell');maps=cell(numel(patches),1);
    for i=1:numel(patches)
        p=patches(i);assert(string(p.FaceColor)=="flat"&&string(p.CDataMapping)=="scaled");
        original=struct('XData',p.XData,'YData',p.YData,'CData',p.CData,'UserData',p.UserData);
        value=double(p.CData);assert(isscalar(value)&&isfinite(value));
        index=min(size(cmap,1),max(1,1+floor((value-limits(1))/diff(limits)*size(cmap,1))));
        mapped=cmap(index,:);p.FaceColor=mapped;
        assert(isequaln(original,struct('XData',p.XData,'YData',p.YData,'CData',p.CData,'UserData',p.UserData)));
        assert(isequaln(p.FaceColor,cmap(index,:)));
        maps{i}=struct('value',value,'palette_index_one_based',index,'exact_RGB',mapped, ...
            'source_mapping','scaled CData: floor((value-CLim1)/(CLim2-CLim1)*N)+1, clipped1..N', ...
            'scientific_CData_coordinates_UserData_unchanged',true);
    end
    cb=frozenBars(a.Layout.Tile);units=string(cb.Units);position=originalPositions{a.Layout.Tile};assert(isequaln(cb.Position,position));
    % R2024b getpixelposition(cb,true) was actually observed to return the
    % same layout-relative values. Apply the one known1x3 parent's transform.
    assert(string(layouts.Units)=="normalized"&&isequal(layouts.Parent,f));outer=layouts.OuterPosition;
    figurePosition=[outer(1)+position(1)*outer(3),outer(2)+position(2)*outer(4),position(3)*outer(3),position(4)*outer(4)];
    ticks=cb.Ticks;tickLabels=string(cb.TickLabels);label=string(cb.Label.String);font=string(cb.FontName);
    assert(isprop(cb,'Face'),'Actual native ColorBar face not accessible; stop rather than hide unknown graphics.');
    priorFaceVisible=string(cb.Face.Visible);cb.Face.Visible='off';
    n=size(cmap,1);bands=gobjects(n,1);
    for k=1:n
        xy=[figurePosition(1),figurePosition(2)+figurePosition(4)*(k-1)/n,figurePosition(3),figurePosition(4)/n];
        bands(k)=annotation(f,'rectangle',xy,'FaceColor',cmap(k,:),'LineStyle','none', ...
            'Tag','publication_vector_colorbar_band');
        bands(k).UserData=struct('not_scientific_cell',true,'source_axes_tile',a.Layout.Tile, ...
            'palette_index_one_based',k,'palette_size',n,'CLim',limits,'exact_RGB',cmap(k,:));
        assert(isequaln(bands(k).FaceColor,cmap(k,:))&&isequaln(bands(k).Position,xy));
    end
    assert(isequaln(cb.Ticks,ticks)&&isequal(string(cb.TickLabels),tickLabels)&&string(cb.Label.String)==label);
    assert(isequaln(colormap(a),cmap)&&isequaln(a.CLim,limits)&&string(cb.FontName)==font);
    records{j}=struct('axes_tile',a.Layout.Tile,'scientific_patch_count',numel(patches), ...
        'patch_mapping',{maps},'colormap',cmap,'CLim',limits,'original_colorbar_face_visible',priorFaceVisible, ...
        'current_colorbar_face_visible',string(cb.Face.Visible),'colorbar_position_normalized',position, ...
        'colorbar_figure_position_normalized',figurePosition,'annotation_coordinates','figure_normalized_from_native_tiledlayout_outer_transform','reference_layout_outer_position',outer, ...
        'prior_colorbar_units',units,'ticks',ticks,'tick_labels',tickLabels,'label',label, ...
        'font',font,'native_annotation_band_count',n,'ticks_labels_colormap_CLim_unchanged',true);
end
drawnow;
for k=1:3,assert(isequaln(frozenBars(k).Position,originalPositions{k}));end
receipt=struct('schema','hospital_ies.native_vector_boundary_display.v4','axes',{records}, ...
    'original_ColorBar_positions_read_once_and_exact',true, ...
    'scientific_scalar_values_and_unknowns_unchanged',true,'interpolated',false, ...
    'image_backdrop',false,'display_only',true);
end
