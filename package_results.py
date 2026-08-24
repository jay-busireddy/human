from pathlib import Path
import zipfile, os
root=Path(__file__).resolve().parent
results=Path(os.environ.get('HC_RESULTS_DIR',str(root/'results'))).resolve()
if not results.exists():
    raise SystemExit(f'Results directory does not exist: {results}')
out=root/'HUMANIZING_CONTROL_EXECUTABLE_TEST_RESULTS.zip'
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
    for p in results.rglob('*'):
        if p.is_file():z.write(p,Path('results')/p.relative_to(results))
    z.write(root/'config.json','config.json')
    for name in ['requirements.txt','FULL_RUN_INSTRUCTIONS_WINDOWS.md','PATCH_NOTES_v1_4_0.md']:
        p=root/name
        if p.exists():z.write(p,name)
print(out)
print('Packaged results from:',results)
