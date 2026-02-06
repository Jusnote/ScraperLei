from importer_normas_leg import ParserTextoNormas
from pathlib import Path
html = Path('CCNEWOFICIAL.htm').read_text(encoding='latin-1')
parser = ParserTextoNormas(html)
print(len(parser.blocks))
for block in parser.blocks[:40]:
    t = block['texto'].strip()
    if t:
        print('---')
        print(t[:200])
