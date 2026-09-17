import subprocess, sys, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
result = subprocess.run([sys.executable, '-m', 'pytest', 'tests', '-q', '--tb=short', '--color=no'], capture_output=True, text=True, timeout=120)
with open('pytest_output2.txt', 'w', encoding='utf-8') as f:
    f.write(result.stdout[-3000:])
    f.write('\n---STDERR---\n')
    f.write(result.stderr[-1000:])
with open('pytest_exit.txt', 'w', encoding='utf-8') as f:
    f.write(str(result.returncode))
print('DONE', result.returncode)