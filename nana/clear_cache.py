"""Clear Python cache files."""
from pathlib import Path


package_dir = Path(__file__).resolve().parent
cli_dir = package_dir / "cli"

# Clear __pycache__
pycache = cli_dir / '__pycache__'
if pycache.exists():
    count = 0
    for f in pycache.glob('handle_text.*.pyc'):
        f.unlink()
        count += 1
    for f in pycache.glob('*.pyc'):
        f.unlink()
        count += 1
    print(f'Cleared {count} files from {pycache}')

# Clear individual pyc files
for f in cli_dir.glob('handle_text.cpython*.pyc'):
    f.unlink()
    print(f'Removed: {f.name}')

# Clear top-level pycache
for f in package_dir.glob('__pycache__/handle_text.*.pyc'):
    f.unlink()
    print(f'Removed top-level: {f.name}')

print('Done clearing cache')
