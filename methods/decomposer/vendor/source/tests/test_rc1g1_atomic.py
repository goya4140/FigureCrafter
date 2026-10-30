from PIL import Image,ImageDraw
import numpy as np
from decomposer.render_contracts import observed_patch_frame,_raster

def test_no_colored_patch_edges_never_invents_photo_field():
 assert observed_patch_frame(Image.new('RGB',(200,200),'white'),[50,50,80,80]) is None

def test_observed_patch_edges_recover_frame_and_color():
 im=Image.new('RGB',(200,200),'white');ImageDraw.Draw(im).rectangle([60,55,140,135],outline='#DD3333',width=2)
 box,color=observed_patch_frame(im,[55,50,80,80]);assert abs(box[0]-60)<3 and abs(box[1]-55)<3;assert 77<=box[2]<=83 and 77<=box[3]<=83;assert color=='#DD3333'

def test_annotation_removal_changes_only_observed_ink(tmp_path):
 im=Image.new('RGB',(100,100),'#FCEDE8');ImageDraw.Draw(im).line([(20,20),(80,20)],fill='#EF9C99',width=1)
 removal={'kind':'line','points':[[20,20],[80,20]],'width':1,'color':'#EF9C99','owner':'rays'}
 _,asset,_=_raster(im,[0,0,100,100],'photo','base',tmp_path,native_removals=[removal]);after=np.array(Image.open(asset['path']));before=np.array(im)
 assert (after[:,:,3]==255).all();assert np.array_equal(after[30:,:,:3],before[30:]);assert tuple(after[20,50,:3])!=tuple(before[20,50]);assert asset['annotation_background_status']=='estimated_only_at_observed_ink_pixels'
