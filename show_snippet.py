import sys
sys.stdout.reconfigure(encoding='utf-8')
start = int(sys.argv[1])
end = int(sys.argv[2])
with open('importer_normas_leg.py','r',encoding='utf-8') as f:
    for i,line in enumerate(f,1):
        if start <= i <= end:
            print(f'{i:04d}: {line.rstrip()}')
