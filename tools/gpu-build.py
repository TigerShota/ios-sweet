# -*- coding: utf-8 -*-
"""Build a flashable shim: python gpu-build.py <xnu_run_id> <out_name>
-> dispatch gauguin-shim -> download the artifact -> verify the payload markers."""
import json, os, subprocess, sys, time, zipfile

GH = r'D:\hackios\tools\gh\bin\gh.exe'
REPO = 'TigerShota/ios-sweet'
XNU_ID = int(sys.argv[1])
OUT = os.path.join(r'D:\hackios\out', sys.argv[2])
CMDLINE = ('debug=0xc serial=3 cpus=8 keepsyms=1 -enable_kprintf_spam '
           '-noprogress rd=ufsroot0 unentitled_ios_sim_launch=1 '
           'launchd_unsecure_cache=1 ignition_level=1 wdt_test=no_user_checkin '
           'ufsrw=1 rootrw=1 wdt=-1 sweetusb=0 sweettouch=0 '
           'sweetfb_noconsole=1 sweetfb_hz=30 ufslogsecs=5 '
           'sweetsample=SpringBoard,backboardd,lsd,MobileGestaltHelper,securityd,'
           'installd,misagent,misd,installcoordinationd,containermanagerd,launchd,logd '
           'sweetiolog=MobileGestaltHelper,securityd,lsd,backboardd '
           'gauguingpu=2 gauguinbt=0 gauguinbtclk=1')


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
    tok = subprocess.run([GH, 'auth', 'token'], capture_output=True).stdout.decode().strip()
    p = subprocess.run(['curl.exe', '-L', '--proxy', 'http://127.0.0.1:7890',
                        '-H', 'Authorization: Bearer ' + tok, '-o', zp,
                        '--connect-timeout', '20', '--max-time', '300',
                        'https://api.github.com/repos/%s/actions/artifacts/%d/zip' % (REPO, a['id'])],
                       capture_output=True)
    if p.returncode != 0:
        raise SystemExit('download failed: %s' % p.stderr.decode('utf-8', 'replace')[:300])
    with zipfile.ZipFile(zp) as z:
        z.extractall(OUT)
    log('artifact %s -> %s' % (a['name'], OUT))

pl = os.path.join(OUT, 'shim', 'out', 'payload.bin')
data = open(pl, 'rb').read()
marks = {
    'L2 done': b'GAUGUIN GPU: L2 done',
    'GX ALIVE': b'GAUGUIN GX: GX ALIVE',
    'CP verified': b'GAUGUIN CP: verified',
    'cold proceed': b'GAUGUIN CP: cold - proceeding',
    'oob acked fmt': b'GAUGUIN CP: oob gpu_set info=',
    'zap start': b'GAUGUIN ZAP: start',
    'zap avail fmt': b'GAUGUIN ZAP: pas %s available: status %lld result %llu',
    'zap meta fmt': b'GAUGUIN ZAP: meta magic=%#x phoff=%u phnum=%u',
    'zap phdr fmt': b'GAUGUIN ZAP: phdr %u: type %u off %#x paddr %#x filesz %u memsz %u flags %#x',
    'zap bad elf': b'GAUGUIN ZAP: bad metadata ELF',
    'zap ph2 bad': b'GAUGUIN ZAP: ph2 is not the RWE segment',
    'zap block fmt': b'GAUGUIN ZAP: block pa=%#llx img=%#llx meta=%#llx',
    'zap meta copied': b'GAUGUIN ZAP: meta copied %u dwords, %u mismatch',
    'zap init fmt': b'GAUGUIN ZAP: INIT_IMAGE status %lld result %llu',
    'zap mem fmt': b'GAUGUIN ZAP: MEM_SETUP status %lld result %llu',
    'zap segment fmt': b'GAUGUIN ZAP: segment %u bytes -> img+0',
    'zap auth fmt': b'GAUGUIN ZAP: AUTH_AND_RESET status %lld result %llu',
    'zap authed': b'GAUGUIN ZAP: authenticated and started (PAS 13)',
    'zap shutdown fmt': b'GAUGUIN ZAP: SHUTDOWN status %lld',
    'zap abort': b'GAUGUIN CP: L3b abort - zap failed',
    'me_init hdr fmt': b'GAUGUIN CP: me_init hdr=%#x',
    'secmode hdr fmt': b'GAUGUIN CP: secmode hdr=%#x',
    'me_init verdict': b'GAUGUIN CP: ME_INIT %s (rptr=%u wptr=%u)',
    'secmode submitted': b'GAUGUIN CP: SET_SECURE_MODE submitted (wptr=%u)',
    'secmode verdict': b'GAUGUIN CP: SET_SECURE_MODE %s (rptr=%u reg=%#x wptr=%u)',
    'post status fmt': b'GAUGUIN CP: post cp_status1=%#x',
    'abort secmode': b'GAUGUIN CP: L3b abort after SEC_MODE',
    'CP started': b'GAUGUIN CP: started - the CP executes our packets',
    'sqe cntl fmt': b'GAUGUIN CP: sqe_cntl=%#x',
    'cp status fmt': b'GAUGUIN CP: cp_status1=%#x',
    'rptr shadow fmt': b'GAUGUIN CP: rptr shadow=',
    'hw fault fmt': b'GAUGUIN CP: hw_fault=%#x',
    'cleared': b'GAUGUIN CP: oob gpu_set cleared',
    'BT read per-reg': b'GAUGUIN BT: read %s @%#x = %#x',
    'BT probe start': b'GAUGUIN BT: probe starting',
    'BT cold vote': b'GAUGUIN BT: cold vote=',
    'BT hot vote': b'GAUGUIN BT: hot vote=',
    'BT se1 fw': b'GAUGUIN BT: se1 fw=',
    'BT boot arg': b'gauguinbtclk',
    'step22 target fmt': b'GAUGUIN CP: target pa=%#llx va=%#x %ux%u pitch=%u pte0=%#x',
    'step22 submit fmt': b'GAUGUIN CP: step22 %u dwords, target %#x',
    'step22 verdict': b'GAUGUIN CP: step22 %s (flush=%#x tries=%u rptr=%u',
    'step22 px fmt': b'GAUGUIN CP: step22 px top=%#x mid=%#x bot=%#x ev=%#x/%#x',
    'panel geom fmt': b'GAUGUIN CP: panel %ux%u pitch=%u pa=%#llx bytes=%u pages=%u',
    'panel mapped fmt': b'GAUGUIN CP: panel mapped va=%#x tables=%u pages=%u pte0=%#x',
    'panel retire fmt': b'GAUGUIN CP: panel %s (fence=%#x tries=%u)',
    'panel px fmt': b'GAUGUIN CP: panel px row%u=%#x row%u=%#x row%u=%#x hits=%u/%u/%u of %u cpu map=%#llx',
    'panel rows fmt': b'GAUGUIN CP: panel rows orange=%u blue=%u other=%u firstother=row%u:%#x',
    'panel map window fmt': b'GAUGUIN CP: panel cpu map=%#llx rows %u..%u bytes=%u',
    # note: no 'smc #0' string mark - inline asm templates compile to
    # machine code, they never appear as string data in the payload
}
ok = True
for name, m in marks.items():
    n = data.count(m)
    log('payload %-24s -> %d' % (name, n))
    if n == 0:
        ok = False
if not ok:
    raise SystemExit('PAYLOAD VERIFICATION FAILED - do not flash')
log('PAYLOAD VERIFICATION PASSED')
img = os.path.join(OUT, 'shim', 'out', 'shim-boot.img')
log('image ready: %s (%d B)' % (img, os.path.getsize(img)))
print('DONE')
