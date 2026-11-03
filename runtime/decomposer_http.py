"""Portable HTTP transport for the pinned Decomposer external-json contract.

Reuse the pinned adapter's request construction and JSON parsing; add the
existing medium effort setting and validate the model identity explicitly.
Scientific prompts, schemas and reconstruction code remain in the vendor.
"""
import argparse
import base64
import importlib.util
import json
import mimetypes
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', required=True)
    parser.add_argument('--schema', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--reasoning-effort', default='medium')
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / 'methods/decomposer/vendor/source/adapters/openai_compatible_vision.py'
    spec = importlib.util.spec_from_file_location('pinned_vision_adapter', source)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    base_url = adapter.required_environment('MODEL_BASE_URL').rstrip('/')
    key = adapter.required_environment('MODEL_API_KEY')
    configured_model = os.environ.get('MODEL_NAME')
    if configured_model and configured_model != args.model:
        raise ValueError('MODEL_NAME differs from the explicitly configured Decomposer model')
    image = Path(args.image)
    body = adapter.build_body(stage=args.stage, model=args.model, prompt=sys.stdin.read(),
        schema=json.loads(Path(args.schema).read_text()),
        mime=mimetypes.guess_type(image.name)[0] or 'image/png',
        image_data=base64.b64encode(image.read_bytes()).decode('ascii'),
        response_mode='json_schema', reasoning_effort=args.reasoning_effort,
        max_tokens=None, image_detail='auto')
    request = urllib.request.Request(base_url + '/chat/completions',
        data=json.dumps(body).encode(), headers={'Authorization': 'Bearer ' + key,
        'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=1700) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'Model endpoint returned HTTP {error.code}; inspect local configuration') from None
    except urllib.error.URLError:
        raise RuntimeError('Model endpoint connection failed; inspect local configuration') from None
    print(json.dumps(adapter.response_metadata(payload)), file=sys.stderr)
    content = payload['choices'][0]['message'].get('content')
    if isinstance(content, list):
        content = ''.join(str(item.get('text', '')) for item in content if isinstance(item, dict))
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError('Provider returned empty content; no automatic model retry')
    print(json.dumps(json.loads(adapter.strip_fence(content)), ensure_ascii=False))


if __name__ == '__main__':
    main()
