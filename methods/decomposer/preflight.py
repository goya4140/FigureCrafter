"""Visualize actual detector evidence BEFORE model recognition; never synthesize masks."""
from pathlib import Path
from collections import Counter
import argparse, hashlib, html, json
from PIL import Image, ImageDraw, ImageFont


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def inspect(image, candidates, output):
    image, candidates, output = Path(image), Path(candidates), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    base = Image.open(image).convert('RGB')
    data = json.loads(candidates.read_text())
    if list(base.size) != data['canvas']: raise ValueError('Detector/image canvas mismatch')
    try: font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', 14)
    except OSError: font = ImageFont.load_default()
    sam = [x for x in data['items'] if x['source'] == 'sam3']
    ocr = [x for x in data['items'] if x['source'] == 'ocr']
    colors = {'picture':'#D55200', 'content box':'#2069BD', 'diagram':'#8B3EBC', 'arrow':'#009078', 'text':'#C12A91'}
    views = [('sam-all.png', sam), ('sam-pictures.png', [x for x in sam if x['label']=='picture']),
             ('sam-containers.png', [x for x in sam if x['label'] in ['content box','diagram']]),
             ('sam-arrows.png', [x for x in sam if x['label']=='arrow']), ('ocr-all.png', ocr)]
    for name, items in views:
        im = base.copy(); draw = ImageDraw.Draw(im)
        for item in items:
            x,y,w,h = item['bbox']; col = colors.get(item['label'], '#A02080')
            draw.rectangle((x,y,x+w,y+h),outline=col,width=2)
            label = item['id']; b = draw.textbbox((0,0),label,font=font)
            ty=max(0,y-17); draw.rectangle((x,ty,x+b[2]+4,ty+17),fill='white')
            draw.text((x+2,ty),label,font=font,fill=col)
        im.save(output/name)
    pictures=[x for x in sam if x['label']=='picture']
    for start in range(0,len(pictures),12):
        sheet=Image.new('RGB',(1600,900),'#F4F5F7');d=ImageDraw.Draw(sheet)
        for j,item in enumerate(pictures[start:start+12]):
            col,row=j%4,j//4; x,y,w,h=item['bbox'];crop=base.crop((max(0,int(x)),max(0,int(y)),min(base.width,round(x+w)),min(base.height,round(y+h))))
            crop.thumbnail((380,258));sx=col*400+10;sy=row*300+30
            sheet.paste(crop,(sx+(380-crop.width)//2,sy+(258-crop.height)//2))
            d.text((sx,row*300+8),f'{item["id"]}  score={item["confidence"]:.3f}',font=font,fill='black')
        sheet.save(output/f'sam-picture-crops-{start//12+1}.png')
    overlaps=[]
    for p in pictures:
        px,py,pw,ph=p['bbox']
        hits=[]
        for t in ocr:
            x,y,w,h=t['bbox'];a=max(0,min(px+pw,x+w)-max(px,x))*max(0,min(py+ph,y+h)-max(py,y))
            if a>0: hits.append({'id':t['id'],'text':t['text'],'fraction_of_text_box':round(a/max(1,w*h),3)})
        if hits: overlaps.append({'picture_id':p['id'],'texts':hits})
    report={'image_sha256':sha(image),'candidates_sha256':sha(candidates),'canvas':list(base.size),
            'sam_count':len(sam),'sam_roles':dict(Counter(x['label'] for x in sam)), 'ocr_count':len(ocr),
            'sam_payload_has_pixel_masks':any(any(k in x for k in ['mask','mask_rle','segmentation']) for x in sam),
            'picture_text_overlaps':overlaps,'low_confidence_ocr':[x for x in ocr if x['confidence']<.85],
            'scope':'Detector proposals only; not accepted model recognition, not scientific acceptance. Box overlap is a review cue, not a deletion rule.'}
    (output/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    rows=''.join('<tr><td>'+html.escape(x['id'])+'</td><td>'+html.escape(x['text'])+f'</td><td>{x["confidence"]:.3f}</td><td>'+html.escape(str(x['bbox']))+'</td></tr>' for x in sorted(ocr,key=lambda x:(x['bbox'][1],x['bbox'][0])))
    names=[n for n,_ in views]+[p.name for p in output.glob('sam-picture-crops-*.png')]
    figures=''.join('<section><h2>'+html.escape(n)+'</h2><img src="'+n+'"></section>' for n in names)
    (output/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>SAM/OCR pre-recognition inspection</title><style>body{font:16px system-ui;margin:24px;color:#17232c}img{width:100%;height:auto}section{margin:32px 0}table{border-collapse:collapse}td,th{padding:6px;border:1px solid #ccc}h2{font-size:20px}</style><h1>RoboScape：进入识别前的 SAM / OCR 实际候选</h1><p>这批 SAM 返回候选框，没有返回像素掩膜。框图不是分割掩膜，也不是最终识别结果。原图不修改。</p>'+figures+'<h2>OCR 原文、置信度与坐标</h2><table><tr><th>ID</th><th>文字</th><th>置信度</th><th>bbox</th></tr>'+rows+'</table>')
    return report


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['image','candidates','output']:p.add_argument('--'+n,required=True)
    a=p.parse_args();r=inspect(a.image,a.candidates,a.output)
    print(json.dumps({k:r[k] for k in ['sam_count','sam_roles','ocr_count','sam_payload_has_pixel_masks','picture_text_overlaps']},ensure_ascii=False,indent=2))
