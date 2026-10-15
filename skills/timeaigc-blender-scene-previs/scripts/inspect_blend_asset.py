"""Run with Blender --background --factory-startup --disable-autoexec --python.

Example after --:
 --asset model.blend --descriptor draft.json --measurements measurements.json
 --color 0.15 0.72 0.3 --output asset-contract.json --geometry-output geometry.json

The draft chooses stable IDs and physical semantics; inspection does not invent
those decisions. Both outputs are exclusive new files. Source is never saved.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from blend_asset import inspect_blend_file


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset', required=True)
    parser.add_argument('--descriptor', required=True)
    parser.add_argument('--measurements', required=True, help='JSON object of confirmed dimension names and meter values')
    parser.add_argument('--color', nargs=3, type=float, required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--geometry-output')
    args = parser.parse_args(argv)
    inputs = [Path(value).resolve() for value in (args.asset, args.descriptor, args.measurements)]
    outputs = [Path(value).resolve() for value in (args.output, args.geometry_output) if value]
    if len(set(outputs)) != len(outputs) or any(path in inputs or path.exists() for path in outputs):
        raise ValueError('Inspection outputs must be distinct new files, never source files or existing evidence')
    descriptor = json.loads(inputs[1].read_text(encoding='utf-8-sig'))
    measurements = json.loads(inputs[2].read_text(encoding='utf-8-sig'))
    contract, geometry = inspect_blend_file(inputs[0], descriptor, measurements, args.color)
    for path, data in zip(outputs, (contract, geometry)):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
    print(json.dumps({'status': 'verified', 'asset': str(inputs[0]), 'geometry_sha256': geometry['geometry_sha256'], 'outputs': [str(path) for path in outputs]}))


if __name__ == '__main__':
    main(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
