import importlib.util,tempfile,unittest,json
from pathlib import Path
from PIL import Image
import pymupdf
P=Path(__file__).resolve().parents[1]/'run.py';spec=importlib.util.spec_from_file_location('fc_image_test',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class ImageContractTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.r=Path(self.tmp.name);i=self.r/'inputs';i.mkdir()
  (i/'method.txt').write_text('Synthetic Method fixture; no scientific claim')
  for n in [1,2,3]:Image.new('RGB',(160,90),'white').save(i/f'style-reference-{n:02}.png')
  self.w=m.prepare(self.r/'session',i,'unit-test');self.s=m.Session(self.w)
 def ready(self):
  for n in ['understanding','representation','decision']:self.s.call('write_file',{'path':f'work/{n}.md','content':'synthetic test notes'})
  self.s.call('write_file',{'path':'work/prompt.txt','content':'synthetic test prompt'})
  for n in [1,2,3]:self.s.call('view_image',{'path':f'run/inputs/style-reference-{n:02}.png'})
  self.s.call('read_text',{'path':'run/inputs/method.txt'})
 def request(self):return self.s.call('image_request',{'mode':'generate','prompt':'work/prompt.txt'})
 def result(self,size=(160,90),bad=False):
  self.ready();v=self.request();p=self.r/'output.png';Image.new('RGB',size,'blue').save(p)
  self.s.call('write_file',{'path':'work/provider.json','content':json.dumps({'tool':'image_gen.imagegen','request':v['request'],'source_output_sha256':'wrong' if bad else m.sha(p)})})
  return self.s.call('image_result',{'request':v['request'],'image':str(p),'receipt':'work/provider.json'})
 def review(self,status='accepted',high=False):
  for n in ['candidate.png','publication.png']:self.s.call('view_image',{'path':'image-iterations/000/'+n})
  rv={'status':status,'observations':'Synthetic review','findings':[{'id':'x','severity':'high' if high else 'low','region':'sample','problem':'synthetic','source':'test'}],'limitations':['test only']}
  self.s.call('write_file',{'path':'work/review.json','content':json.dumps(rv)})
  return self.s.call('image_review',{'iteration':'000','path':'work/review.json'})
 def test_native_execute_is_unavailable(self):
  with self.assertRaisesRegex(ValueError,'disabled'):self.s.call('execute',{})
 def test_request_requires_scientific_notes(self):
  self.s.call('write_file',{'path':'work/prompt.txt','content':'test'})
  with self.assertRaisesRegex(ValueError,'planning'):self.request()
 def test_generation_receives_only_scientific_style_inputs(self):
  self.ready();v=self.request();self.assertEqual(len(v['image_gen_arguments']['referenced_image_paths']),3);self.assertIsNone(m.read(self.w/'requests/000/request.json')['parent'])
 def test_receipt_must_match_actual_output(self):
  with self.assertRaisesRegex(ValueError,'receipt'):self.result(bad=True)
 def test_bad_aspect_does_not_silently_stretch(self):
  with self.assertRaisesRegex(ValueError,'aspect'):self.result(size=(100,100))
 def test_prompt_mutation_is_detected(self):
  self.ready();self.request();(self.w/'requests/000/prompt.txt').write_text('changed')
  with self.assertRaisesRegex(ValueError,'prompt changed'):self.s.validate()
 def test_unobserved_image_cannot_be_reviewed(self):
  self.result();self.s.call('write_file',{'path':'work/review.json','content':'{}'})
  with self.assertRaisesRegex(ValueError,'observations'):self.s.call('image_review',{'iteration':'000','path':'work/review.json'})
 def test_high_scientific_error_blocks_acceptance(self):
  self.result()
  with self.assertRaisesRegex(ValueError,'high-severity'):self.review(high=True)
 def test_edit_binds_reviewed_parent_and_findings(self):
  self.result();self.review(status='needs_edit');v=self.s.call('image_request',{'mode':'edit','prompt':'work/prompt.txt','parent':'000','findings':['x']});d=m.read(self.w/'requests/001/request.json');self.assertEqual(d['parent_sha256'],m.sha(self.w/'image-iterations/000/candidate.png'));self.assertEqual(v['image_gen_arguments']['referenced_image_paths'],[str(self.w/'image-iterations/000/candidate.png')])
 def test_unknown_finding_does_not_allow_edit(self):
  self.result();self.review(status='needs_edit')
  with self.assertRaisesRegex(ValueError,'findings'):self.s.call('image_request',{'mode':'edit','prompt':'work/prompt.txt','parent':'000','findings':['wrong']})
 def test_tampered_parent_is_detected(self):
  self.result();self.review(status='needs_edit');self.s.call('image_request',{'mode':'edit','prompt':'work/prompt.txt','parent':'000','findings':['x']});Image.new('RGB',(160,90),'red').save(self.w/'image-iterations/000/candidate.png')
  with self.assertRaisesRegex(ValueError,'parent changed|iteration changed'):self.s.validate()
 def test_failed_review_cannot_finish(self):
  self.result();self.review(status='needs_edit');self.s.call('write_file',{'path':'work/caption.md','content':'test'})
  with self.assertRaisesRegex(ValueError,'accepted'):self.s.call('finish',{'iteration':'000','caption':'work/caption.md'})
 def test_registered_output_cannot_be_replaced(self):
  self.result()
  with self.assertRaisesRegex(ValueError,'already'):self.s.call('image_result',{'request':'000','image':str(self.r/'output.png'),'receipt':'work/provider.json'})
 def test_edit_budget_is_three_total_even_on_same_old_parent(self):
  self.result();self.review(status='needs_edit')
  for n in range(3):
   v=self.s.call('image_request',{'mode':'edit','prompt':'work/prompt.txt','parent':'000','findings':['x']})
   self.assertEqual(v['request'],f'{n+1:03}')
  with self.assertRaisesRegex(ValueError,'budget exhausted: 3/3'):
   self.s.call('image_request',{'mode':'edit','prompt':'work/prompt.txt','parent':'000','findings':['x']})
  self.assertEqual(len(list((self.w/'requests').iterdir())),4)
 def test_generation_does_not_reset_edit_budget(self):
  self.result();self.review(status='needs_edit')
  for _ in range(3):self.s.call('image_request',{'mode':'edit','prompt':'work/prompt.txt','parent':'000','findings':['x']})
  self.request()
  with self.assertRaisesRegex(ValueError,'budget exhausted'):
   self.s.call('image_request',{'mode':'edit','prompt':'work/prompt.txt','parent':'000','findings':['x']})
if __name__=='__main__':unittest.main()
