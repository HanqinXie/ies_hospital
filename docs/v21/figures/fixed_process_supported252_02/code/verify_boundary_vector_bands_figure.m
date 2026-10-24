function verify_boundary_vector_bands_figure(f,display)
% Independent native display-object cross-check against recorded original map.
axes=findall(f,'Type','axes');bars=findall(f,'Type','colorbar');
bands=findall(f,'Tag','publication_vector_colorbar_band');assert(numel(bands)==3*256);
for j=1:numel(display.axes)
    reference=display.axes{j};hit=arrayfun(@(a)a.Layout.Tile==reference.axes_tile,axes);assert(sum(hit)==1);a=axes(hit);
    cmap=colormap(a);assert(isequaln(cmap,reference.colormap)&&isequaln(a.CLim,reference.CLim)&&isequaln(cmap,parula(256)));
    matching=arrayfun(@(b)isstruct(b.UserData)&&b.UserData.source_axes_tile==reference.axes_tile,bands);
    actual=bands(matching);assert(numel(actual)==256);seen=false(256,1);position=reference.colorbar_figure_position_normalized;
    for h=actual(:)'
        u=h.UserData;k=u.palette_index_one_based;assert(k>=1&&k<=256&&k==fix(k)&&~seen(k));seen(k)=true;
        expectedPosition=[position(1),position(2)+position(4)*(k-1)/256,position(3),position(4)/256];
        assert(u.not_scientific_cell&&u.palette_size==256&&isequaln(u.CLim,a.CLim));
        assert(isequaln(u.exact_RGB,cmap(k,:))&&isequaln(h.FaceColor,cmap(k,:))&&isequaln(h.Position,expectedPosition));
        assert(string(h.LineStyle)=="none");
    end
    assert(all(seen));matches=false(size(bars));
    for k=1:numel(bars)
        cb=bars(k);matches(k)=string(cb.Units)=="normalized"&&isequaln(cb.Position,reference.colorbar_position_normalized);
    end
    assert(sum(matches)==1);cb=bars(matches);
    assert(string(cb.Face.Visible)=="off"&&isequaln(cb.Ticks,reference.ticks));
    assert(isequal(string(cb.TickLabels),reference.tick_labels)&&string(cb.Label.String)==reference.label&&string(cb.FontName)==reference.font);
    patches=findall(a,'Tag','native_boundary_cell');assert(numel(patches)==reference.scientific_patch_count);
    for p=patches(:)'
        index=min(256,max(1,1+floor((double(p.CData)-a.CLim(1))/diff(a.CLim)*256)));
        assert(isequaln(p.FaceColor,cmap(index,:))&&string(p.CDataMapping)=="scaled");
    end
end
end
