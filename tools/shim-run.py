# -*- coding: utf-8 -*-
"""Drive the gauguin-shim workflow: dispatch a build, list runs, fetch artifacts.

  python tools/shim-run.py ls
  python tools/shim-run.py dispatch <xnu_run_id> [extra boot args...]
  python tools/shim-run.py get <shim_run_id> <tag>

`dispatch` sends the workflow's default cmdline plus the extra args, then prints
the queued run id (it does not wait).  `get` waits for that run, downloads its
artifacts into <SWEETLOG_OUT>/<tag>/ and prints the image path.

The token comes from GH_TOKEN or GITHUB_TOKEN (needs actions:write + repo read).
"""
import os
import re
import sys
import time
import zipfile

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get('GH_REPO', 'TigerShota/ios-sweet')
API = 'https://api.github.com/repos/' + REPO
OUT_ROOT = os.environ.get('SWEETLOG_OUT') or os.path.join(HERE, '..', '..',
                                                          'out')
YML = os.path.join(HERE, '..', '.github', 'workflows', 'gauguin-shim.yml')
TOKEN = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN') or ''
H = {'Authorization': 'Bearer ' + TOKEN, 'User-Agent': 'gauguin-shim-run'}
PROXIES = None
if os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy'):
    PROXIES = {}

if not TOKEN:
    raise SystemExit('set GH_TOKEN (or GITHUB_TOKEN) first')


def api(path):
    r = requests.get(API + path, headers=H, proxies=PROXIES, timeout=60)
    if r.status_code >= 300:
        raise SystemExit('%s -> %d %s' % (path, r.status_code, r.text[:200]))
    return r.json()


def default_cmdline():
    yml = open(YML, encoding='utf-8').read()
    m = re.search(r'cmdline:\s*\n\s*description:.*?\n\s*default:\s*"([^"]*)"',
                  yml, re.S)
    if not m:
        raise SystemExit('cannot find the cmdline default in ' + YML)
    return m.group(1)


def cmd_ls():
    for run in api('/actions/workflows/gauguin-shim.yml/runs?per_page=8'
                   )['workflow_runs']:
        print('%s %s %-10s %s' % (run['id'], run['created_at'],
                                  run['conclusion'] or run['status'],
                                  run['head_sha'][:8]))


def cmd_dispatch():
    if len(sys.argv) < 3:
        raise SystemExit('usage: dispatch <xnu_run_id> [extra args...]')
    xnu = sys.argv[2]
    cmd = (default_cmdline() + ' ' + ' '.join(sys.argv[3:])).strip()
    print('xnu', xnu, '| cmdline len', len(cmd), flush=True)
    r = requests.post(API + '/actions/workflows/gauguin-shim.yml/dispatches',
                      headers=H, proxies=PROXIES,
                      json={'ref': 'main', 'inputs': {'xnu_run_id': xnu,
                                                      'cmdline': cmd}},
                      timeout=60)
    print('dispatch', r.status_code, r.text[:200], flush=True)
    if r.status_code >= 300:
        raise SystemExit(1)
    t0 = time.time()
    while time.time() - t0 < 120:
        time.sleep(8)
        runs = api('/actions/workflows/gauguin-shim.yml/runs?per_page=3'
                   )['workflow_runs']
        for run in runs:
            if run['status'] != 'completed':
                print('run', run['id'], run['status'], flush=True)
                return
    print('queued (run not visible yet, use ls)')


def cmd_get():
    if len(sys.argv) < 4:
        raise SystemExit('usage: get <shim_run_id> <tag>')
    run_id, tag = sys.argv[2], sys.argv[3]
    while True:
        run = api('/actions/runs/' + run_id)
        print(' ', run['status'], run.get('conclusion'), flush=True)
        if run['status'] == 'completed':
            break
        time.sleep(20)
    if run.get('conclusion') != 'success':
        raise SystemExit('run conclusion %s' % run.get('conclusion'))
    out = os.path.join(OUT_ROOT, tag)
    os.makedirs(out, exist_ok=True)
    for a in api('/actions/runs/%s/artifacts' % run_id)['artifacts']:
        z = os.path.join(out, a['name'] + '.zip')
        r = requests.get(a['archive_download_url'], headers=H,
                         proxies=PROXIES, timeout=900)
        open(z, 'wb').write(r.content)
        with zipfile.ZipFile(z) as f:
            f.extractall(out)
        print('saved', z, r.status_code, len(r.content), flush=True)
    img = os.path.join(out, 'shim', 'out', 'shim-boot.img')
    print('IMG', img, os.path.getsize(img) if os.path.exists(img) else 'MISSING')


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'ls'
    if cmd == 'ls':
        return cmd_ls()
    if cmd == 'dispatch':
        return cmd_dispatch()
    if cmd == 'get':
        return cmd_get()
    raise SystemExit('unknown command ' + cmd)


if __name__ == '__main__':
    main()
