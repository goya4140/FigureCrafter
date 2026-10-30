"""Resolve paths recorded by the former next workspace without rewriting evidence."""
from pathlib import Path
import json

MAIN = Path(__file__).resolve().parents[5]
MAP = MAIN / '07_docs/reorganization/20261015-next-merge/path-map.json'

def resolve_path(value):
    path = Path(value)
    data = json.loads(MAP.read_text())
    raw = str(path)
    prefix = data['old_root'] + '/'
    if not raw.startswith(prefix):
        return path
    relative = raw[len(prefix):]
    # Runtime reads use the canonical dataset; the historical copy remains intact.
    if relative == 'data' or relative.startswith('data/'):
        return MAIN / '05_data/datasets/benchmark_annotated' / relative[4:].lstrip('/')
    for row in data['rules']:
        old = row['old']
        if relative == old or relative.startswith(old + '/'):
            return MAIN / row['new'] / relative[len(old):].lstrip('/')
    raise ValueError('Unmapped historical path: ' + raw)
