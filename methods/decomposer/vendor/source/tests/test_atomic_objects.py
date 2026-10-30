import copy,json,unittest
from pathlib import Path
from decomposer.atomic_objects import normalize,text_crop_conflicts
from decomposer.asset_policy import decide

class AtomicObjectsTests(unittest.TestCase):
 def setUp(self):
  self.rec={'canvas':{'width':1000,'height':800},'objects':[{'id':'visual','category':'image','semantic_level':'leaf','representation_preference':'native','bbox':[10,10,100,300],'text':'','confidence':.99,'candidate_ids':[]},{'id':'labels','category':'text','semantic_level':'leaf','representation_preference':'native','bbox':[80,20,100,180],'text':'docs\nsrc','confidence':1,'candidate_ids':['o1','o2'],'style_hint':{'font_family':'Arial','font_size':30,'line_spacing':1.5},'connects':None}]}
  self.ev={'candidates':[{'id':'o1','source':'ocr','text':'docs','bbox':[80,20,80,30],'confidence':1},{'id':'o2','source':'ocr','text':'src','bbox':[80,120,60,30],'confidence':.3}]}
 def test_recognized_low_confidence_ocr_is_not_ignored(self):
  hits=text_crop_conflicts([10,100,100,100],self.rec,'visual',self.ev)
  self.assertEqual([h['evidence_id'] for h in hits],['o2'])
 def test_native_word_overlap_denies_crop(self):
  out=decide(self.rec,self.ev,{})
  d=out['decisions'][0];self.assertEqual(d['decision'],'native');self.assertIn('crop_intersects_separate_native_text',d['reason_codes'])
 def test_whitespace_inside_multiline_frame_does_not_reject(self):
  self.assertFalse(text_crop_conflicts([10,65,100,30],self.rec,'visual',self.ev))
 def test_incidental_photo_text_without_native_owner_does_not_reject(self):
  self.rec['objects']=self.rec['objects'][:1]
  self.assertEqual(decide(self.rec,self.ev,{})['decisions'][0]['decision'],'localized_raster')
 def test_split_labels_and_change_conflicting_composite_to_geometry(self):
  original=copy.deepcopy(self.rec);r,p=normalize(self.rec,self.ev)
  self.assertEqual(self.rec,original);self.assertEqual(r['objects'][0]['category'],'shape')
  children=r['objects'][1:];self.assertEqual([o['text'] for o in children],['docs','src']);self.assertEqual([o['bbox'] for o in children],[c['bbox'] for c in self.ev['candidates']]);self.assertEqual('\n'.join(o['text'] for o in children),self.rec['objects'][1]['text'])
 def test_keep_code_block(self):
  self.rec['objects'][1]['style_hint']['font_family']='Courier New';r,_=normalize(self.rec,self.ev);self.assertEqual(r['objects'][1]['text'],'docs\nsrc')
 def test_keep_dense_prose(self):
  self.ev['candidates'][1]['bbox'][1]=52;r,_=normalize(self.rec,self.ev);self.assertEqual(len(r['objects']),2)
 def test_mismatched_ocr_must_not_rewrite_content(self):
  self.ev['candidates'][1]['text']='sre';r,_=normalize(self.rec,self.ev);self.assertEqual(r['objects'][1]['text'],'docs\nsrc')
 def test_do_not_break_connection_identity(self):
  self.rec['objects'][0]['connects']={'source_id':'visual','target_id':'labels'};r,_=normalize(self.rec,self.ev);self.assertEqual(len(r['objects']),2)

if __name__=='__main__':unittest.main()
