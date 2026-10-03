#!/usr/bin/env python3
"""Prepare IINA with one matching set of custom headers and runtime libraries."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


FFMPEG_ABI = {
    'avcodec': 63, 'avdevice': 63, 'avfilter': 12, 'avformat': 63,
    'avutil': 61, 'swresample': 7, 'swscale': 10,
}
DIRECT_LIBRARIES = ('mpv', 'avcodec', 'avformat', 'avutil', 'swscale')
HEADER_FAMILIES = ('libavcodec', 'libavformat', 'libavutil', 'libswscale', 'mpv')


def output(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


def validate_libraries(root, arch):
    libraries = sorted(root.glob('*.dylib'))
    if not libraries:
        raise ValueError(f'No dylibs in {root}')
    for library in libraries:
        if arch not in output('lipo', '-archs', str(library)).split():
            raise ValueError(f'{library.name} does not contain {arch}')
        dependencies = output('otool', '-L', str(library)).splitlines()[1:]
        identifier = dependencies[0].strip().split(' (', 1)[0] if dependencies else ''
        if identifier != f'@rpath/{library.name}':
            raise ValueError(f'{library.name}: install name {identifier} does not match its filename')
        for line in dependencies:
            dependency = line.strip().split(' (', 1)[0]
            if dependency.startswith(('/usr/lib/', '/System/Library/')):
                continue
            if not dependency.startswith('@rpath/'):
                raise ValueError(f'{library.name}: non-relocatable dependency {dependency}')
            if not (root / dependency.removeprefix('@rpath/')).is_file():
                raise ValueError(f'{library.name}: missing dependency {dependency}')
    return libraries


def validate_bundle(root, arch, ffmpeg_version, mpv_ref):
    metadata = json.loads((root / 'bundle.json').read_text())
    expected = {
        'schema_version': 1, 'arch': arch, 'ffmpeg_version': ffmpeg_version,
        'mpv_ref': mpv_ref, 'render_backend': 'gpu-next',
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ValueError(f'Bundle {key}: expected {value!r}, got {metadata.get(key)!r}')
    libraries = validate_libraries(root, arch)
    if metadata.get('dylibs') != [p.name for p in libraries]:
        raise ValueError('Bundle dylib list does not match its manifest')
    for name, major in FFMPEG_ABI.items():
        headers = root / 'include' / f'lib{name}'
        source = '\n'.join(p.read_text() for p in headers.glob('version*.h'))
        match = re.search(rf'#define\s+LIB{name.upper()}_VERSION_MAJOR\s+(\d+)', source)
        if not match or int(match[1]) != major:
            raise ValueError(f'lib{name} headers must use ABI {major}')
        if not (root / f'lib{name}.{major}.dylib').is_file():
            raise ValueError(f'Missing lib{name}.{major}.dylib')
    render_header = (root / 'include/mpv/render.h').read_text()
    if not re.search(r'MPV_RENDER_PARAM_BACKEND\s*=\s*21', render_header):
        raise ValueError('libmpv headers do not support the gpu-next backend')
    if not (root / 'libmpv.2.dylib').is_file():
        raise ValueError('Missing libmpv.2.dylib')
    return libraries


def patch_project(project, libraries):
    source = project.read_text()
    # All dylib references are generated from this bundle. Keep system frameworks.
    source = ''.join(line for line in source.splitlines(keepends=True)
                     if not re.search(r'/\* [^*]*\.dylib(?: in [^*]+)? \*/', line))
    definitions, references, copies, links = [], [], [], []

    def identity(name, role):
        return hashlib.sha256(f'iina-custom:{name}:{role}'.encode()).hexdigest()[:24].upper()

    for library in libraries:
        name = library.name
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+\.dylib', name):
            raise ValueError(f'Invalid dylib filename: {name}')
        ref, copy, link = (identity(name, role) for role in ('ref', 'copy', 'link'))
        definitions.append(f'\t\t{copy} /* {name} in Copy Dylibs */ = {{isa = PBXBuildFile; fileRef = {ref} /* {name} */; settings = {{ATTRIBUTES = (CodeSignOnCopy, ); }}; }};\n')
        references.append(f'\t\t{ref} /* {name} */ = {{isa = PBXFileReference; lastKnownFileType = "compiled.mach-o.dylib"; name = "{name}"; path = "deps/lib/{name}"; sourceTree = "<group>"; }};\n')
        copies.append(f'\t\t\t\t{copy} /* {name} in Copy Dylibs */,\n')
        if any(re.fullmatch(rf'lib{family}\.\d+\.dylib', name) for family in DIRECT_LIBRARIES):
            definitions.append(f'\t\t{link} /* {name} in Frameworks */ = {{isa = PBXBuildFile; fileRef = {ref} /* {name} */; }};\n')
            links.append(f'\t\t\t\t{link} /* {name} in Frameworks */,\n')

    def insert_section(text, section, lines):
        marker = f'/* Begin {section} section */\n'
        if text.count(marker) != 1:
            raise ValueError(f'Missing or ambiguous {section}')
        return text.replace(marker, marker + ''.join(lines))

    def insert_list(text, identifier, field, lines):
        pattern = rf'(\t\t{identifier} /\* [^*]+ \*/ = \{{\n.*?\t\t\t{field} = \(\n)'
        text, count = re.subn(pattern, lambda m: m[1] + ''.join(lines), text, count=1, flags=re.S)
        if count != 1:
            raise ValueError(f'Missing project object {identifier}')
        return text

    source = insert_section(source, 'PBXBuildFile', definitions)
    source = insert_section(source, 'PBXFileReference', references)
    source = insert_list(source, '84817C991DBDF57C00CC2279', 'files', copies)
    source = insert_list(source, '84EB1ED31D2F51D3004FA5A1', 'files', links)
    children = [f'\t\t\t\t{identity(p.name, "ref")} /* {p.name} */,\n' for p in libraries]
    source = insert_list(source, '848290731D95978100C3C76C', 'children', children)
    return source


def prepare(args):
    libraries = validate_bundle(args.bundle, args.arch, args.ffmpeg_version, args.mpv_ref)
    project = args.repo / 'iina.xcodeproj/project.pbxproj'
    source = patch_project(project, libraries)
    library_dir = args.repo / 'deps/lib'
    library_dir.mkdir(parents=True, exist_ok=True)
    for old in library_dir.glob('*.dylib'):
        old.unlink()
    for library in libraries:
        shutil.copy2(library, library_dir / library.name)
    for family in HEADER_FAMILIES:
        target = args.repo / 'deps/include' / family
        ignore = (target / '.gitignore').read_bytes() if (target / '.gitignore').exists() else None
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(args.bundle / 'include' / family, target)
        if ignore is not None:
            (target / '.gitignore').write_bytes(ignore)
    project.write_text(source)
    output('plutil', '-lint', str(project))
    print(f'Prepared {len(libraries)} matching {args.arch} libraries and headers')


def verify_app(args):
    frameworks = args.app / 'Contents/Frameworks'
    validate_libraries(frameworks, args.arch)
    binary = args.app / 'Contents/MacOS/IINA'
    if args.arch not in output('lipo', '-archs', str(binary)).split():
        raise ValueError(f'IINA does not contain {args.arch}')
    for line in output('otool', '-L', str(binary)).splitlines()[1:]:
        dependency = line.strip().split(' (', 1)[0]
        if dependency.startswith('@rpath/') and not (frameworks / dependency[7:]).exists():
            raise ValueError(f'IINA requires missing {dependency}')
        if dependency.startswith(('/opt/', '/usr/local/', '/Users/')):
            raise ValueError(f'IINA requires non-relocatable {dependency}')
    for name, major in FFMPEG_ABI.items():
        if not (frameworks / f'lib{name}.{major}.dylib').is_file():
            raise ValueError(f'App is missing lib{name}.{major}.dylib')
    print(f'Validated {args.arch} app dependency closure; no ABI aliases created')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('prepare')
    command.add_argument('--bundle', type=Path, required=True)
    command.add_argument('--repo', type=Path, default=Path(__file__).resolve().parent.parent)
    command.add_argument('--arch', choices=('arm64', 'x86_64'), required=True)
    command.add_argument('--ffmpeg-version', default='9.0.1')
    command.add_argument('--mpv-ref', default='v0.41.0')
    command.set_defaults(run=prepare)
    command = commands.add_parser('verify-app')
    command.add_argument('--app', type=Path, required=True)
    command.add_argument('--arch', choices=('arm64', 'x86_64'), required=True)
    command.set_defaults(run=verify_app)
    args = parser.parse_args()
    try:
        args.run(args)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
