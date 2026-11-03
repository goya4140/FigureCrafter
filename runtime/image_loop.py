"""HTTP tool-loop transport for the unchanged Method/Nano Image Session.

The original Session owns planning/observation gates, image requests, the zero
edit budget, provider submission, review and freezing. No image is invented here.
"""
import argparse
import importlib.util
import json
from pathlib import Path


def load_image_method():
    source = Path(__file__).resolve().parents[1] / 'methods/method-text/figurecrafter-v251-image/run.py'
    spec = importlib.util.spec_from_file_location('figurecrafter_image', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def loop(method, workspace, config, max_turns):
    session = method.Session(workspace)
    if (session.root / 'freeze.json').exists():
        raise ValueError('Cannot resume a frozen session')
    if max_turns < 1:
        raise ValueError('max_turns must be positive')
    session.validate()
    settings_path = session.root / 'session.json'
    settings = json.loads(settings_path.read_text())
    if config['model'] != settings['model']:
        raise ValueError('Configured model differs from the prepared session')
    provider = method.fc.create(config)
    settings['provider'] = config['provider']
    method.write(settings_path, settings)
    checkpoint = session.root / 'conversation.json'
    messages = json.loads(checkpoint.read_text()) if checkpoint.exists() else [
        {'role': 'system', 'text': (session.root / 'MODEL.txt').read_text()},
        {'role': 'user', 'text': 'Read the run guides and inputs, generate the missing main figure and use finish to select it.'}]
    for _ in range(max_turns):
        response = provider.complete(messages, method.TOOLS)
        session.log({'type': 'model_response', 'provider': config['provider'],
                     'configured_model': config['model'], 'response': response})
        messages.append({'role': 'assistant', **response})
        method.write(checkpoint, messages)
        if not response['calls']:
            messages.append({'role': 'user', 'text': 'Production is not finished. Use tools, or report the specific blocker.'})
        for call in response['calls']:
            try:
                result = session.call(call['name'], call['arguments'])
            except Exception as error:
                result = {'error': str(error), 'automatic_repair': False}
            message = {'role': 'tool', 'call_id': call['id'], 'name': call['name'], 'result': result}
            if call.get('provider_call_id'):
                message['provider_call_id'] = call['provider_call_id']
            if result.get('image_path'):
                message['image_path'] = result['image_path']
            messages.append(message)
            # Finish snapshots this checkpoint and the full tool event log.
            # Never write into the workspace after the original Session freezes it.
            if call['name'] in {'finish', 'export_initial'} and 'error' not in result:
                return result
        method.write(checkpoint, messages)
    raise RuntimeError('Turn limit reached without delivery; resumable conversation retained')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--max-turns', type=int, default=80)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    result = loop(load_image_method(), args.workspace, config, args.max_turns)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
