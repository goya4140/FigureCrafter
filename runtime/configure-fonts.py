from pathlib import Path
import argparse,json,xml.etree.ElementTree as ET
root=Path(__file__).resolve().parent
p=argparse.ArgumentParser();p.add_argument('--font-dir',action='append',required=True);a=p.parse_args()
node=ET.Element('fontconfig')
for folder in a.font_dir:
 directory=Path(folder).resolve()
 if not directory.is_dir():raise SystemExit('Font directory does not exist: '+str(directory))
 ET.SubElement(node,'dir').text=str(directory)
cache=root/'font-cache-local';cache.mkdir(exist_ok=True)
ET.SubElement(node,'cachedir').text=str(cache)
conf=root/'fonts.local.conf';conf.write_text('<?xml version="1.0"?>'+ET.tostring(node,encoding='unicode'))
cfg=root/'runtime.local.json';d=json.loads(cfg.read_text())
d['fontconfig_file']=str(conf);cfg.write_text(json.dumps(d,ensure_ascii=False,indent=2));print(conf)
