"""Extract these two Chromium PDFs using per-font ToUnicode and ActualText.
Limited to their inline resources, Flate streams and hexadecimal Tj strings.
Not a general PDF reader. Original PDFs remain authoritative.
"""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import re
import sys
import zlib
import unicodedata
from pathlib import Path

for filename in sys.argv[1:]:
    path = Path(filename)
    objects = {}
    for m in re.finditer(rb'(\d+)\s+0\s+obj\b(.*?)endobj', path.read_bytes(), re.S):
        body = m[2]
        if b'stream' in body:
            header, body = body.split(b'stream', 1)
            body = re.sub(rb'^\r?\n', b'', body)
            length = re.search(rb'/Length (\d+)\b', header)
            if not length:
                raise ValueError('Missing direct stream length')
            body = body[:int(length[1])]
            if b'/FlateDecode' in header:
                body = zlib.decompress(body)
        objects[int(m[1])] = body
    fonts = {}
    for number, body in objects.items():
        ref = re.search(rb'/ToUnicode (\d+) 0 R', body)
        if ref:
            cmap = objects[int(ref[1])]
            mapping = {}
            for block in re.findall(rb'beginbfchar(.*?)endbfchar', cmap, re.S):
                for a, b in re.findall(rb'<([\dA-Fa-f]+)>\s*<([\dA-Fa-f]+)>', block):
                    mapping[bytes.fromhex(a.decode())] = bytes.fromhex(b.decode()).decode('utf-16-be')
            for block in re.findall(rb'beginbfrange(.*?)endbfrange', cmap, re.S):
                for a, b, c in re.findall(rb'<([\dA-Fa-f]+)>\s*<([\dA-Fa-f]+)>\s*<([\dA-Fa-f]+)>', block):
                    width = len(a) // 2
                    for code in range(int(a, 16), int(b, 16) + 1):
                        value = int(c, 16) + code - int(a, 16)
                        mapping[code.to_bytes(width, 'big')] = value.to_bytes(len(c)//2, 'big').decode('utf-16-be')
            fonts[number] = mapping
    pages = [b for b in objects.values() if re.search(rb'/Type\s*/Page\b', b)]
    lines = []
    unknown = 0
    for index, page in enumerate(pages, 1):
        lines.append(f'\n--- Page {index} ---\n')
        resources = {name: fonts[int(ref)] for name, ref in re.findall(rb'/(F\d+) (\d+) 0 R', page)}
        content = objects[int(re.search(rb'/Contents (\d+) 0 R', page)[1])]
        previous_y = None
        for block in re.findall(rb'BT(.*?)ET', content, re.S):
            tm = re.search(rb'([-\d.]+) ([-\d.]+) Tm', block)
            y = tm[2] if tm else None
            if y != previous_y:
                lines.append('\n')
                previous_y = y
            font = None
            actual = None
            for token in re.finditer(rb'/(F\d+) [\d.]+ Tf|/ActualText <([\dA-Fa-f]+)>|<([\dA-Fa-f]+)> Tj|\bEMC\b', block):
                if token[1]:
                    font = token[1]
                elif token[2]:
                    actual = bytes.fromhex(token[2].decode()).decode('utf-16-be').lstrip('\ufeff')
                elif token[3]:
                    raw = bytes.fromhex(token[3].decode())
                    if actual is not None:
                        lines.append(actual)
                        actual = ''
                    else:
                        mapping = resources[font]
                        width = len(next(iter(mapping)))
                        for offset in range(0, len(raw), width):
                            char = mapping.get(raw[offset:offset+width])
                            unknown += char is None
                            lines.append(char if char is not None else '�')
                else:
                    actual = None
    output = Path(__file__).resolve().parents[2] / 'system/runtime/pdf-text' / (path.stem + '.txt')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(unicodedata.normalize('NFKC', ''.join(lines)), encoding='utf-8')
    print(f'{path.name}: {len(pages)} pages; {unknown} unmapped glyphs; {output}')
    if unknown:
        raise ValueError('Incomplete extraction')
