"""Turn RAPC output into an OTA release; never byte-split a COD module."""
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_release(build_dir, output_dir, name):
    build_dir, output_dir = Path(build_dir), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    cod = build_dir / f"{name}.cod"
    jad = build_dir / f"{name}.jad"
    if not cod.is_file() or not jad.is_file():
        raise ValueError("RAPC의 COD/JAD 산출물이 없습니다.")
    if zipfile.is_zipfile(cod):
        with zipfile.ZipFile(cod) as archive:
            members = archive.infolist()
            if not members or len(members) > 4096:
                raise ValueError("잘못된 sibling COD 컨테이너")
            seen = set()
            for member in members:
                filename = member.filename
                if not re.fullmatch(re.escape(name) + r"(?:-\d+)?\.cod", filename):
                    raise ValueError(f"예상하지 못한 COD 항목: {filename}")
                if filename.lower() in seen or member.file_size > 16 * 1024 * 1024:
                    raise ValueError("중복 또는 과도한 COD 항목")
                seen.add(filename.lower())
                (output_dir / filename).write_bytes(archive.read(member))
    else:
        shutil.copy2(cod, output_dir / cod.name)
        for sibling in build_dir.glob(f"{name}-*.cod"):
            if re.fullmatch(re.escape(name) + r"-\d+\.cod", sibling.name):
                shutil.copy2(sibling, output_dir / sibling.name)
    modules = sorted(output_dir.glob('*.cod'), key=lambda p: (p.stem != name, int(p.stem.rsplit('-', 1)[1]) if p.stem != name else 0))
    if not modules or modules[0].name != cod.name:
        raise ValueError("주 COD 모듈이 없습니다.")
    # Preserve all RAPC metadata (entry points, module dependencies, flags, etc.).
    # Recalculate only the three per-module OTA fields using extracted bytes.
    original = jad.read_text(encoding='utf-8', errors='strict')
    lines = [line for line in original.splitlines()
             if not re.match(r'^RIM-COD-(?:URL|Size|SHA1)(?:-\d+)?:', line, re.I)]
    for index, module in enumerate(modules):
        suffix = f'-{index}' if index else ''
        data = module.read_bytes()
        sha1 = ' '.join(f'{byte:02x}' for byte in hashlib.sha1(data).digest())
        lines += [f'RIM-COD-URL{suffix}: {module.name}',
                  f'RIM-COD-Size{suffix}: {len(data)}', f'RIM-COD-SHA1{suffix}: {sha1}']
    (output_dir / jad.name).write_bytes(('\r\n'.join(lines) + '\r\n').encode('utf-8'))
    files = [{'name': p.name, 'size': p.stat().st_size, 'sha256': digest(p)}
             for p in [output_dir / jad.name, *modules]]
    manifest = {'schema': 'blackberry-ota-v1', 'jad': jad.name,
                'modules': len(modules), 'files': files}
    (output_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    package = output_dir / f'{name}-ota.zip'
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
        for entry in [*files, {'name': 'manifest.json'}]:
            archive.write(output_dir / entry['name'], entry['name'])
    return manifest, package


def validate_package(package, destination):
    """Receiver helper: validate whole release before moving it into public storage.

    destination must be a new staging directory. The caller publishes atomically
    only after this returns, and removes the staging directory on error.
    """
    destination = Path(destination)
    with zipfile.ZipFile(package) as archive:
        members = archive.infolist()
        names = [m.filename for m in members]
        if len(names) > 4098 or len(set(n.lower() for n in names)) != len(names):
            raise ValueError('Duplicate/excessive package entries')
        if sum(m.file_size for m in members) > 256 * 1024 * 1024:
            raise ValueError('Package too large')
        if any(not re.fullmatch(r'[A-Za-z0-9_.-]+', n) for n in names):
            raise ValueError('Unsafe package path')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('schema') != 'blackberry-ota-v1':
            raise ValueError('Unsupported manifest schema')
        entries = manifest['files']
        expected = [entry['name'] for entry in entries]
        if len(set(expected)) != len(expected) or set(names) != set(expected) | {'manifest.json'}:
            raise ValueError('Incomplete/unexpected release files')
        modules = [n for n in expected if n.endswith('.cod')]
        if len(modules) != manifest['modules'] or not modules:
            raise ValueError('Incorrect module count')
        jad_name = manifest['jad']
        if jad_name not in expected or not jad_name.endswith('.jad') or len(expected) != len(modules) + 1:
            raise ValueError('Invalid JAD')
        for entry in entries:
            data = archive.read(entry['name'])
            if len(data) != entry['size'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError('Release integrity failure')
            if entry['name'].endswith('.cod') and zipfile.is_zipfile(__import__('io').BytesIO(data)):
                raise ValueError('OTA modules must be raw COD files')
        fields = {}
        for line in archive.read(jad_name).decode('utf-8').splitlines():
            key, sep, value = line.partition(':')
            if sep:
                if key in fields and key.startswith('RIM-COD-'):
                    raise ValueError('Duplicate JAD module field')
                fields[key] = value.strip()
        references = []
        for index in range(len(modules)):
            suffix = f'-{index}' if index else ''
            filename = fields.get('RIM-COD-URL' + suffix)
            if filename not in modules:
                raise ValueError('Missing JAD module reference')
            data = archive.read(filename)
            if fields.get('RIM-COD-Size' + suffix) != str(len(data)) or fields.get('RIM-COD-SHA1' + suffix, '').replace(' ', '').lower() != hashlib.sha1(data).hexdigest():
                raise ValueError('Invalid JAD module integrity')
            references.append(filename)
        if set(references) != set(modules) or sum(bool(re.fullmatch(r'RIM-COD-URL(?:-\d+)?', k)) for k in fields) != len(modules):
            raise ValueError('Incomplete JAD references')
        destination.mkdir(parents=True, exist_ok=False)
        for entry in entries:
            (destination / entry['name']).write_bytes(archive.read(entry['name']))
        (destination / 'manifest.json').write_bytes(archive.read('manifest.json'))
    return manifest
