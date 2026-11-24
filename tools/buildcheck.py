from pathlib import Path
import subprocess

ROOT = Path('.')


def main():
    python = sorted((ROOT / 'chromaledger').glob('*.py')) + sorted((ROOT / 'tools').glob('*.py'))
    javascript = sorted((ROOT / 'web' / 'js').glob('*.js'))
    for file in python:
        compile(file.read_bytes(), str(file), 'exec')
    for file in javascript:
        subprocess.run(['node', '--input-type=module', '--check'], input=file.read_bytes(), check=True)
    print(f'语法检查通过：{len(python)} 个 Python 模块，{len(javascript)} 个浏览器模块。')


if __name__ == '__main__':
    main()
