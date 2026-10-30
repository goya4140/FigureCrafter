"""Standalone PP-OCRv5 detector + rectified transcription, fully local weights."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def extract(request):
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
    import cv2
    import numpy as np
    from PIL import Image
    from paddleocr import TextDetection, TextRecognition
    options = request['options']; root = Path(options['model_root'])
    image = Image.open(io.BytesIO(base64.b64decode(request['image']))).convert('RGB')
    bgr = np.asarray(image)[:,:,::-1].copy()
    det = TextDetection(model_name='PP-OCRv5_server_det', model_dir=str(root/'PP-OCRv5_server_det'),
                        device=options['device'], enable_mkldnn=False)
    rec = TextRecognition(model_name='PP-OCRv5_server_rec', model_dir=str(root/'PP-OCRv5_server_rec'),
                          device=options['device'], enable_mkldnn=False)
    items, crops = [], []
    with tempfile.TemporaryDirectory(prefix='v4-ocr-') as tmp:
        source = Path(tmp)/'input.png'; image.save(source)
        detected = list(det.predict(str(source)))[0]
    for polygon in detected['dt_polys']:
        p = np.asarray(polygon, dtype=np.float32)
        if p.shape != (4,2) or not np.isfinite(p).all(): raise ValueError('Invalid OCR quadrilateral')
        p[:,0] = p[:,0].clip(0,image.width-1); p[:,1] = p[:,1].clip(0,image.height-1)
        w = max(2,round(max(np.linalg.norm(p[1]-p[0]),np.linalg.norm(p[2]-p[3]))))
        h = max(2,round(max(np.linalg.norm(p[3]-p[0]),np.linalg.norm(p[2]-p[1]))))
        target = np.float32([[0,0],[w-1,0],[w-1,h-1],[0,h-1]])
        crops.append(cv2.warpPerspective(bgr,cv2.getPerspectiveTransform(p,target),(w,h)))
        x,y = p.min(axis=0); xx,yy = p.max(axis=0)
        items.append({'id':f'ocr{len(items):04d}','parser':'PP-OCRv5','label':'text',
                      'bbox':[float(x),float(y),max(.01,float(xx-x)),max(.01,float(yy-y))],
                      'polygon':p.tolist()})
    results = list(rec.predict(crops)) if crops else []
    if len(results) != len(items): raise ValueError('OCR count mismatch')
    for item,result in zip(items,results):
        item.update(text=result['rec_text'],confidence=float(result['rec_score']))
    return {'provider':'PP-OCRv5-local','canvas':list(image.size),'items':items}


if __name__ == '__main__':
    request = json.load(sys.stdin); start = time.monotonic()
    with contextlib.redirect_stdout(sys.stderr): result = extract(request)
    result['seconds'] = round(time.monotonic()-start,3)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
