"""Standalone official SAM3 worker; JSON stdin/stdout, diagnostics on stderr."""
import base64
import contextlib
import io
import json
import os
import sys
import time


def extract(request):
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    import torch
    from PIL import Image
    import sam3.model.vitdet as vitdet
    from sam3.model_builder import build_sam3_image_model
    from sam3.model.sam3_image_processor import Sam3Processor
    # Same numerically equivalent unfused kernel used by DrawAI on this CUDA build.
    def safe(activation, linear, value):
        result = linear(value)
        if activation in (torch.nn.functional.relu, torch.nn.ReLU): return torch.nn.functional.relu(result)
        if activation in (torch.nn.functional.gelu, torch.nn.GELU): return torch.nn.functional.gelu(result)
        raise ValueError('Unsupported SAM activation')
    vitdet.addmm_act = safe
    options = request['options']; device = options['device']
    if device.startswith('cuda'): torch.cuda.set_device(device)
    image = Image.open(io.BytesIO(base64.b64decode(request['image']))).convert('RGB')
    model = build_sam3_image_model(checkpoint_path=options['checkpoint'], bpe_path=options['bpe'],
                                  load_from_HF=False, device=device)
    model = model.to(device).eval()
    processor = Sam3Processor(model, device=device)
    items = []
    with torch.inference_mode():
        state = processor.set_image(image)
        for label in options['prompts']:
            processor.set_confidence_threshold(options['threshold'])
            output = processor.set_text_prompt(state=state, prompt=label)
            for box, score in zip(output['boxes'].detach().cpu().tolist(), output['scores'].detach().cpu().tolist()):
                x,y,xx,yy = box
                x,y = max(0.,x),max(0.,y); xx,yy = min(float(image.width),xx),min(float(image.height),yy)
                if xx <= x or yy <= y: continue
                items.append({'id':f'sam{len(items):04d}', 'parser':'SAM3', 'label':label,
                              'bbox':[x,y,xx-x,yy-y], 'confidence':float(score), 'text':''})
    return {'provider':'official-SAM3-local', 'canvas':list(image.size), 'items':items}


if __name__ == '__main__':
    request = json.load(sys.stdin); start = time.monotonic()
    with contextlib.redirect_stdout(sys.stderr): result = extract(request)
    result['seconds'] = round(time.monotonic()-start, 3)
    print(json.dumps(result, allow_nan=False))
