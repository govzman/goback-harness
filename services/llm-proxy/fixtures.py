"""Deterministic Responses-style scripts for the session and smoke tests."""

import shlex


def read(path):
    return ("read_file", {"path": path})


def write(path, content):
    return ("write_file", {"path": path, "content": content})


def run(command):
    return ("run_command", {"command": command})


def python(code):
    return run("python -c " + shlex.quote(code))


SCRIPTS = {
    "B1.1": [read("config/app.yaml"), write("answer.txt", "8137\n")],
    "B2.1": [run("grep -R '^def parse_date' src"), write("answer.txt", "src/dates.py\n")],
    "B3.1": [read("spec.md"), write("config.json", '{"service": "metrics", "port": 9120, "debug": false}\n')],
    "B4.1": [read("README.md"), python(
        "from pathlib import Path; p=Path('README.md'); "
        "p.write_bytes(p.read_bytes().replace(b'install goback==1.2', b'install goback==1.3', 1))")],
    "B5.1": [run("python stats.py data.csv"), write("answer.txt", "7\n")],
    "B6.1": [read("spec.md"), write("slug.py", "import re\n\ndef slugify(text):\n"
                                    "    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')\n")],
    "B7.1": [read("schedule.csv"), write("answer.txt", "yes 14 minutes\n")],
    "B8.1": [run("cat sales/*.csv"), write("answer.txt", "165\n")],
    "isolation": [python(
        "import os, socket\nfrom pathlib import Path\n"
        "def inaccessible(name):\n"
        "    try: Path(name).read_bytes()\n"
        "    except OSError: return True\n"
        "    return False\n"
        "assert os.getuid() != 0; "
        "assert not any(os.environ.get(k) for k in ('LLM_API_KEY', 'OPENAI_API_KEY', 'CLIENT_TOKENS')); "
        "status = dict(row.split(':', 1) for row in Path('/proc/self/status').read_text().splitlines()); "
        "assert status['NoNewPrivs'].strip() == '1' and int(status['CapEff'].strip(), 16) == 0; "
        "assert not Path('/var/run/docker.sock').exists(); "
        "assert inaccessible('/work/.git/canary'); "
        "assert inaccessible('/work/escape'); "
        "assert {item.name for item in Path('/sys/class/net').iterdir()} == {'lo'}; "
        "s=socket.socket(); s.settimeout(2); assert s.connect_ex(('192.0.2.1', 443)) != 0; "
        "Path('isolation.txt').write_text('ok')")],
    "limits": [python("""\
import os
import errno
import tempfile
from pathlib import Path
cg = Path('/sys/fs/cgroup')
if (cg / 'cgroup.controllers').exists():
    memory = int((cg / 'memory.max').read_text())
    pids = int((cg / 'pids.max').read_text())
    quota, period = map(int, (cg / 'cpu.max').read_text().split())
else:
    paths = {}
    for line in Path('/proc/self/cgroup').read_text().splitlines():
        _, controllers, path = line.split(':', 2)
        paths.update((controller, path.lstrip('/')) for controller in controllers.split(','))
    def limit(controller, property):
        leaf = cg / controller / paths[controller] / property
        direct = cg / controller / property
        return int((leaf if leaf.exists() else direct).read_text())
    memory = limit('memory', 'memory.limit_in_bytes')
    pids = limit('pids', 'pids.max')
    quota = limit('cpu', 'cpu.cfs_quota_us')
    period = limit('cpu', 'cpu.cfs_period_us')
assert memory == 256*1024*1024, memory
assert pids == 64, pids
assert quota == period, (quota, period)
tmp = os.statvfs('/tmp')
assert tmp.f_frsize * tmp.f_blocks <= 64*1024*1024
mounts = (line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines())
assert any(parts[4] == '/' and 'ro' in parts[5].split(',') for parts in mounts)
with tempfile.TemporaryFile(dir='/tmp', buffering=0) as file:
    chunk = b'x' * (1024*1024)
    try:
        for _ in range(65):
            file.write(chunk)
    except OSError as error:
        assert error.errno == errno.ENOSPC
    else:
        raise AssertionError('temporary storage exceeded its maximum')
Path('limits.txt').write_text('ok')
""")],
    "slow": [run("(sleep 5; echo leaked > escaped-process.txt) & wait")],
    "timeout": [run("sleep 120")],
    "echo": [read("probe.txt")],
    "bench_fs": [run("python bench_fs_worker.py")],
    "bench_exec": [run("true")],
}
