// Generic environment probe; no paper, references, model scene or layout policy.
import fs from 'node:fs/promises';
import path from 'node:path';
import {loadArtifact} from './load-artifact.mjs';
const {Presentation,PresentationFile,FileBlob}=await loadArtifact();
const out=path.resolve(process.argv[2]);await fs.mkdir(out,{recursive:true});
const p=Presentation.create({slideSize:{width:1600,height:900}});
const s=p.slides.add();s.background.fill='#FFFFFF';
for(const [i,font] of ['Arial','Times New Roman'].entries()){
 const t=s.shapes.add({geometry:'textbox',name:'font-probe-'+i,position:{left:100,top:150+i*150,width:1200,height:100},fill:'none',line:{fill:'none',width:0}});
 t.text=font+' environment check';t.text.style={fontSize:40,typeface:font,color:'#202020'};
}
const first=await PresentationFile.exportPptx(p);await first.save(path.join(out,'native.pptx'));
const imported=await PresentationFile.importPptx(await FileBlob.load(path.join(out,'native.pptx')));
const roundtrip=await PresentationFile.exportPptx(imported);await roundtrip.save(path.join(out,'roundtrip.pptx'));
console.log(JSON.stringify({native:path.join(out,'native.pptx'),roundtrip:path.join(out,'roundtrip.pptx')}));
