from pathlib import Path
import base64, json, mimetypes, sys
source = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2]).resolve()
(output / 'worker').mkdir(parents=True, exist_ok=True)
assets = {}
for path in sorted(source.rglob('*')):
    if not path.is_file():
        continue
    rel = path.relative_to(source)
    if any(part.startswith('.') for part in rel.parts) or path.suffix == '.map':
        continue
    content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    if path.suffix == '.js': content_type = 'application/javascript'
    assets['/' + rel.as_posix()] = {'type': content_type, 'body': base64.b64encode(path.read_bytes()).decode()}
code = (Path(__file__).resolve().parents[1] / 'hosting/sites/bridge.js').read_text()
(output / 'worker/index.js').write_text(code + '\nconst ASSETS = ' + json.dumps(assets, separators=(',', ':')) + ';\n')
print('Embedded', len(assets), 'existing build assets;', (output / 'worker/index.js').stat().st_size, 'bytes')
