from pathlib import Path
import argparse,json,subprocess,sys,shutil,os
from portable import PACKAGE,config,binary
p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args();out=Path(a.out).resolve()
if out.exists():raise SystemExit('Use a new smoke output directory')
cfg=config();node=binary(cfg.get('node'),'node');lo=binary(cfg.get('soffice'),'soffice') or binary(None,'libreoffice')
if not node or not lo:raise SystemExit('Run doctor and configure dependencies first')
if cfg.get('fontconfig_file'):os.environ['FONTCONFIG_FILE']=cfg['fontconfig_file']
subprocess.run([node,str(PACKAGE/'runtime/smoke.mjs'),str(out)],check=True)
sys.path.insert(0,str(PACKAGE/'framework'))
from harness.model_led_drawing import render,figure_contract
os.environ['PATH']=str(Path(lo).parent)+os.pathsep+os.environ.get('PATH','')
import pymupdf
report={'generic_probe_not_scientific_generation':True,'real_export_and_reimport':True,'files':{}}
for name in ['native','roundtrip']:
 geometry=figure_contract(out/(name+'.pptx'),{'aspect_ratio':[16,9],'slide_count':1})
 render(out/(name+'.pptx'),out/(name+'-render'))
 with pymupdf.open(out/(name+'-render')/(name+'.pdf')) as pdf:
  fonts=sorted({x[3] for page in pdf for x in page.get_fonts()})
  text=''.join(page.get_text() for page in pdf)
  if 'environment check' not in text:raise SystemExit('Actual LO PDF does not contain probe text')
 report['files'][name]={'geometry':geometry,'actual_pdf_fonts':fonts,'text_present':True,'Arial_and_Times_New_Roman_present':any('Arial' in x for x in fonts) and any('TimesNewRoman' in x.replace(' ','') for x in fonts)}
(out/'smoke-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
if not all(x['Arial_and_Times_New_Roman_present'] for x in report['files'].values()):raise SystemExit('Font condition differs: configure-fonts.py with your legal fonts, inspect report, and rerun to a new directory. Do not silently accept fallback as baseline.')
