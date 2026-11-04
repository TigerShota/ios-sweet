# -*- coding: utf-8 -*-
"""v28 shim: xnu run 41813660692 (0107 - zap PAS argument ABI fix)."""
import json, os, subprocess, time, zipfile

GH = r'D:\hackios\tools\gh\bin\gh.exe'
REPO = 'TigerShota/ios-sweet'
XNU_ID = 41813660692
OUT = r'D:\hackios\out\shim-v28'
CMDLINE = ('debug=0xc serial=3 cpus=8 keepsyms=1 -enable_kprintf_spam '
           '-noprogress rd=ufsroot0 unentitled_ios_sim_launch=1 '
           'launchd_unsecure_cache=1 ignition_level=1 wdt_test=no_user_checkin '
           'ufsrw=1 rootrw=1 wdt=-1 sweetusb=0 sweettouch=0 '
           'sweetfb_noconsole=1 sweetfb_hz=30 ufslogsecs=5 '
           'sweetsample=SpringBoard,backboardd,lsd,MobileGestaltHelper,securityd,'
           'installd,misagent,misd,installcoordinationd,containermanagerd,launchd,logd '
           'sweetiolog=MobileGestaltHelper,securityd,lsd,backboardd '
           'gauguingpu=2')


def gh_json(endpoint, method='GET', body=None):
    cmd = [GH, 'api', endpoint, '-X', method]
    if body is not None:
        jf = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_gh-body.json')
        open(jf, 'w').write(json.dumps(body))
        cmd += ['--input', jf]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError('gh %s failed: %s' % (endpoint, p.stderr.decode('utf-8', 'replace')[:400]))
    return json.loads(p.stdout.decode('utf-8')) if p.stdout.strip() else {}


def log(msg):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), msg), flush=True)


xr = gh_json('/repos/%s/actions/runs/%d' % (REPO, XNU_ID))
log('xnu run %d: %s / %s' % (XNU_ID, xr['status'], xr['conclusion']))
if xr['status'] != 'completed' or xr['conclusion'] != 'success':
    raise SystemExit('xnu run not success: %s' % xr['html_url'])

gh_json('/repos/%s/actions/workflows/gauguin-shim.yml/dispatches' % REPO, 'POST', {
    'ref': 'main',
    'inputs': {'xnu_run_id': str(XNU_ID), 'kernel': 'kernel.development.sweet',
               'cmdline': CMDLINE, 'highmem': '0'},
})
log('shim dispatched')

shim = None
for dispatch_try in range(4):
    t0 = time.time()
    shim = None
    for i in range(40):
        time.sleep(30)
        runs = gh_json('/repos/%s/actions/workflows/gauguin-shim.yml/runs?per_page=3' % REPO)
        for r in runs.get('workflow_runs', []):
            if r['event'] == 'workflow_dispatch' and r['created_at'] >= time.strftime(
                    '%Y-%m-%dT%H:%M:%SZ', time.gmtime(t0 - 10)):
                if r['status'] == 'completed':
                    shim = r
                    break
        if shim:
            break
        log('waiting shim run (%d s)' % (time.time() - t0))
    if not shim:
        raise SystemExit('shim run never appeared')
    if shim['conclusion'] == 'success':
        log('shim OK run=%d %s' % (shim['id'], shim['html_url']))
        break
    log('shim run %d concluded %s (attempt %d)' % (shim['id'], shim['conclusion'], dispatch_try + 1))
    if dispatch_try == 3:
        raise SystemExit('shim failed 4 times: %s' % shim['html_url'])
    gh_json('/repos/%s/actions/workflows/gauguin-shim.yml/dispatches' % REPO, 'POST', {
        'ref': 'main',
        'inputs': {'xnu_run_id': str(XNU_ID), 'kernel': 'kernel.development.sweet',
                   'cmdline': CMDLINE, 'highmem': '0'},
    })
    log('re-dispatched')

arts = gh_json('/repos/%s/actions/runs/%d/artifacts' % (REPO, shim['id']))
items = arts.get('artifacts', [])
if not items:
    raise SystemExit('no artifacts')
os.makedirs(OUT, exist_ok=True)
for a in items:
    zp = os.path.join(OUT, a['name'] + '.zip')
    p = subprocess.run([GH, 'api', '/repos/%s/actions/artifacts/%d/zip' % (REPO, a['id'])],
                       capture_output=True)
    if p.returncode != 0:
        raise SystemExit('download failed')
    open(zp, 'wb').write(p.stdout)
    with zipfile.ZipFile(zp) as z:
        z.extractall(OUT)
    log('artifact %s -> %s' % (a['name'], OUT))

pl = os.path.join(OUT, 'shim', 'out', 'payload.bin')
data = open(pl, 'rb').read()
marks = {
    # 0107: the fajita argument vector + the pre-call prints
    'init vector fmt': b'GAUGUIN ZAP: INIT_IMAGE fn=%#x arginfo=%#x args=%#llx/%#llx',
    'mem vector fmt': b'GAUGUIN ZAP: MEM_SETUP fn=%#x arginfo=%#x args=%#llx/%#llx',
    'auth vector fmt': b'GAUGUIN ZAP: AUTH_AND_RESET fn=%#x arginfo=%#x args=0/0',
    'init status': b'GAUGUIN ZAP: INIT_IMAGE status %lld result %llu',
    'mem status': b'GAUGUIN ZAP: MEM_SETUP status %lld result %llu',
    'auth status': b'GAUGUIN ZAP: AUTH_AND_RESET status %lld result %llu',
    'shutdown status': b'GAUGUIN ZAP: SHUTDOWN status %lld',
    'scm interrupted': b'GAUGUIN SCM: fn=%#x interrupted x%u (cont=%#llx res1=%#llx)',
    'scm busy': b'GAUGUIN SCM: fn=%#x busy x%u',
    # surviving marks
    'zap avail atomic': b'GAUGUIN ZAP: pas %s ATOMIC available: status %lld result %llu',
    'zap avail': b'GAUGUIN ZAP: pas %s available: status %lld result %llu',
    'zap meta copied': b'GAUGUIN ZAP: meta copied %u dwords, %u mismatch',
    'zap segment': b'GAUGUIN ZAP: segment %u bytes -> img+0',
    'zap authed': b'GAUGUIN ZAP: authenticated and started (PAS 13)',
    'L2 done': b'GAUGUIN GPU: L2 done',
    'GX ALIVE': b'GAUGUIN GX: GX ALIVE',
    'CP verified': b'GAUGUIN CP: verified',
    'cold proceed': b'GAUGUIN CP: cold - proceeding',
    'me_init verdict': b'GAUGUIN CP: ME_INIT %s (rptr=%u wptr=%u)',
    'secmode verdict': b'GAUGUIN CP: SET_SECURE_MODE %s (rptr=%u wptr=%u)',
    'post status': b'GAUGUIN CP: post cp_status1=%#x',
    'CP started': b'GAUGUIN CP: started - the CP executes our packets',
    'cp status': b'GAUGUIN CP: cp_status1=%#x',
    'hw fault': b'GAUGUIN CP: hw_fault=%#x',
}
ok = True
for name, m in marks.items():
    n = data.count(m)
    log('payload %-20s -> %d' % (name, n))
    if n != 1:
        ok = False
if not ok:
    raise SystemExit('PAYLOAD VERIFICATION FAILED - do not flash')
log('PAYLOAD VERIFICATION PASSED (%d/%d)' % (len(marks), len(marks)))
img = os.path.join(OUT, 'shim', 'out', 'shim-boot.img')
log('image ready: %s (%d B)' % (img, os.path.getsize(img)))
print('DONE')
