"""Fixed installer lifted from setup/setup-agents.sh P105_RUNTIME.

Same official/mirror SHA512 and native OpenCode pins; runs only inside the
cancellable setup worker. No setup shell/configuration/account sections.
"""
import urllib.error
import base64, hashlib, io, json, os, pathlib, platform, re, shutil, subprocess, sys, tarfile, tempfile, urllib.request, urllib.parse, zipfile

# Official immutable v2.2.5 release (9bbb8ce18c80f099e0e448775f59d965ead40a21).
# Never releases/latest: that endpoint follows the maintained v1 branch.
OPENCODE_VERSION='2.2.5'
OPENCODE_SHA256={
    'linux-x64': '09f44302c10fd036912f59c81fd1aef5df45dd06f649197f9dfc9bbe2a79614e',
    'linux-arm64': '5a528f42caf576c6cdece6a191eb273d3db54effa5518e0402684d8f8026aeb6',
    'linux-x64-musl': '889389e99ffa39fa41d512b2c9c15316522d97398fb912503ef667f80e22a827',
    'linux-arm64-musl': 'bbf927e3f7a92040f25fd78436c251f41deb25cefac1c906e79c01aa572bf73b',
    'darwin-x64': '4d4419022e6ffbd48afc97c5101bdf001d38b0499f516169b9a80a87a340fd56',
    'darwin-arm64': '25ba9908979fa7d764792c82589b4b5768ab1511154e0317d98aa77d216ce767',
}

def install(cli, source, prefix, scratch):
    if cli not in ('claude','codex','opencode') or source not in ('official','mirror'):
        raise ValueError('shape')
    prefix=pathlib.Path(prefix)
    registry={'official':'https://registry.npmjs.org','mirror':'https://registry.npmmirror.com'}[source]
    osname=platform.system(); arch=platform.machine()
    if osname not in ('Linux','Darwin') or arch not in ('x86_64','amd64','aarch64','arm64'):
        raise ValueError('unsupported_platform')
    if arch=='amd64': arch='x86_64'
    if cli!='opencode' and (not shutil.which('node') or not shutil.which('npm')):
        install_node(source, prefix.parent/'node', scratch)
        os.environ['PATH']=str(prefix.parent/'node/bin')+os.pathsep+os.environ.get('PATH','')
        if not shutil.which('node') or not shutil.which('npm'):
            raise ValueError('node_missing')
    OFFICIAL='https://registry.npmjs.org'; MIRROR='https://registry.npmmirror.com'
    from . import opencode_install
    pkg={'codex':'@openai/codex','claude':'@anthropic-ai/claude-code','opencode':opencode_install.PACKAGE,'gemini':'@google/gemini-cli'}[cli]
    cpu='x64' if arch=='x86_64' else 'arm64'; system='darwin' if osname=='Darwin' else 'linux'
    libc='musl' if system=='linux' and ('musl' in subprocess.run(['ldd','--version'],capture_output=True,text=True).stderr or list(pathlib.Path('/lib').glob('ld-musl-*'))) else 'glibc'
    def run(args,timeout=40):
        return subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,text=True)
    def meta(base,name,version):
        req=urllib.request.Request(base+'/'+name+'/'+version,headers={'User-Agent':'agentj-setup'})
        with urllib.request.urlopen(req,timeout=12) as response:
            raw=response.read(8*1024*1024+1)
            if len(raw)>8*1024*1024: raise ValueError('metadata too large')
            return json.loads(raw)
    def canonical(name,version):
        if version.startswith('npm:'):
            name,version=version[4:].rsplit('@',1)
        return name,version

    def native_deps(doc):
        matches=[]
        for name,spec in doc.get('optionalDependencies',{}).items():
            if system+'-'+cpu not in name: continue
            if ('musl' in name)!=(libc=='musl'): continue
            # Prefer portable baseline on x64; do not require AVX2.
            matches.append((name,spec))
        baseline=[d for d in matches if 'baseline' in d[0]]
        return baseline or matches

    def verify(command):
        path=pathlib.Path(command).resolve()
        for args in ([command,'--version'],[command,'--help']):
            result=run(args)
            if result.returncode: raise ValueError('runtime --version/--help (macOS dyld / _ubrk_clone: upgrade system libraries)')
        # Native standalone installers, or npm wrapper + matching optional package.
        candidates=[]
        if path.suffix not in ('.js','.cjs','.mjs'):
            candidates.append(path)
        roots=[prefix/'lib/node_modules']
        for parent in path.parents:
            if parent.name=='node_modules': roots.append(parent); break
        for root in dict.fromkeys(roots):
            # Keep an existing npm v1 install usable; verify the package that owns its wrapper.
            owner_pkg='opencode-ai' if cli=='opencode' and (root/'opencode-ai') in path.parents else pkg
            parent=root/owner_pkg
            if not (parent/'package.json').exists(): continue
            doc=json.loads((parent/'package.json').read_text())
            deps=native_deps(doc)
            if not deps: raise ValueError('matching platform optionalDependency '+system+'-'+cpu+'-'+libc)
            for name,_ in deps:
                locations=[parent/'node_modules'/name,root/name]
                found=False
                for location in locations:
                    if not location.exists(): continue
                    found=True
                    candidates.extend(p for p in location.rglob('*') if p.is_file() and (p.name in ('codex','claude','opencode','rg') or p.suffix=='.node'))
                if not found: raise ValueError(name)
        native=[]
        for path in dict.fromkeys(candidates):
            desc=run(['file','-b',str(path)]).stdout
            if 'ELF' not in desc and 'Mach-O' not in desc: continue
            expected=('x86-64','x86_64') if cpu=='x64' else ('aarch64','arm64','ARM64')
            if not any(x in desc for x in expected) or ('Mach-O' in desc)!=(system=='darwin'):
                raise ValueError('native architecture '+path.name)
            if not os.access(path,os.X_OK): raise ValueError('executable '+path.name)
            if path.name=='rg' and run([str(path),'--version']).returncode: raise ValueError('ripgrep')
            if path.suffix=='.node':
                if run(['node','-e','require(process.argv[1])',str(path)]).returncode: raise ValueError('native Node addon '+path.name)
                native.append(path)
            elif path.name!='rg':
                if run([str(path),'--version']).returncode: raise ValueError('native runtime '+path.name)
                native.append(path)
        if not native: raise ValueError('platform native binary '+system+'-'+cpu+'-'+libc)
        if cli=='codex' and not any(p.name=='rg' for p in candidates): raise ValueError('bundled ripgrep')


    def install_opencode():
        target=system+'-'+cpu+('-musl' if libc=='musl' else '')
        ext='.zip' if system=='darwin' else '.tar.gz'
        url='https://github.com/anomalyco/opencode/releases/download/v'+OPENCODE_VERSION+'/opencode-'+target+ext
        request=urllib.request.Request(url,headers={'User-Agent':'agentj-setup'})
        with urllib.request.urlopen(request,timeout=120) as response:
            archive=response.read(256*1024*1024+1)
        if len(archive)>256*1024*1024 or hashlib.sha256(archive).hexdigest()!=OPENCODE_SHA256[target]:
            raise ValueError('OpenCode official archive SHA256 mismatch')
        # Extract one known member only, never archive paths or symlinks.
        if ext=='.zip':
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                data=bundle.read('opencode')
        else:
            with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as bundle:
                member=bundle.getmember('opencode')
                if not member.isfile(): raise ValueError('OpenCode archive binary type')
                data=bundle.extractfile(member).read()
        bindir=prefix/'bin'; bindir.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.opencode-',dir=bindir) as tmp:
            binary=pathlib.Path(tmp)/'opencode'; binary.write_bytes(data); binary.chmod(0o755)
            verify(str(binary))
            result=run([str(binary),'--version'])
            if result.returncode or not re.fullmatch(r'(?:opencode\s+)?v?'+re.escape(OPENCODE_VERSION),result.stdout.strip()):
                raise ValueError('OpenCode pinned version mismatch')
            os.replace(binary,bindir/'opencode')
        print('OpenCode '+OPENCODE_VERSION+': official native archive SHA256 verified',flush=True)

    def integrity_pair(name,version):
        name,version=canonical(name,version)
        official=meta(OFFICIAL,name,version); mirror=meta(MIRROR,name,version)
        integrity=official['dist'].get('integrity','')
        if not integrity.startswith('sha512-') or mirror['dist'].get('integrity')!=integrity:
            raise ValueError('official/mirror SHA512 mismatch '+name)
        # Download mirror bytes separately: compare actual bytes, not just metadata.
        url=mirror['dist']['tarball']
        if urllib.parse.urlsplit(url).hostname not in ('registry.npmmirror.com','registry.npmjs.org'): raise ValueError('tarball host')
        if not url.startswith('https://'): raise ValueError('non-HTTPS tarball')
        h=hashlib.sha512(); total=0
        with urllib.request.urlopen(url,timeout=30) as response:
            while True:
                block=response.read(1024*1024)
                if not block: break
                total+=len(block)
                if total>300*1024*1024: raise ValueError('tarball size')
                h.update(block)
        if base64.b64encode(h.digest()).decode()!=integrity.split('-',1)[1]: raise ValueError('mirror tarball SHA512 '+name)
        print('SHA512 official = mirror = downloaded bytes: '+name+'@'+official['version'],flush=True)

    try:
        existing=shutil.which(cli)
        if existing:
            verify(existing)
        elif cli=='opencode':
            install_opencode()
        else:
            # The canonical endpoint is separate from a user-supplied download registry.
            # If official metadata cannot be obtained, fail closed: never assert equality without evidence.
            doc=meta(OFFICIAL,pkg,'latest'); version=doc['version']
            if not isinstance(version,str) or not re.fullmatch(r'[0-9][0-9A-Za-z.+-]{0,40}',version): raise ValueError('version')
            if not doc.get('dist',{}).get('integrity','').startswith('sha512-'): raise ValueError('checksum')
            args=['npm','install','--cpu='+cpu,'--os='+system,'--global','--prefix',str(prefix),'--include=optional','--ignore-scripts=false','--fetch-retries=0','--fetch-timeout=20000','--no-audit','--no-fund',pkg+'@'+version]
            def install(source):
                if source.rstrip('/')==MIRROR:
                    integrity_pair(pkg,version)
                    for name,spec in native_deps(doc): integrity_pair(name,spec)
                # Ignore user scope registries/omit settings: all optional binaries use this source.
                env=dict(os.environ)
                for k in list(env):
                    if k.lower().startswith('npm_config_'): del env[k]
                config_dir=tempfile.TemporaryDirectory(prefix='agentj-npm-config-',dir=scratch)
                for name in ('user','global'): pathlib.Path(config_dir.name,name).touch(mode=0o600)
                env['NPM_CONFIG_USERCONFIG']=str(pathlib.Path(config_dir.name,'user')); env['NPM_CONFIG_GLOBALCONFIG']=str(pathlib.Path(config_dir.name,'global'))
                result=subprocess.run(args+['--registry',source],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=240,text=True)
                config_dir.cleanup()
                if result.returncode: raise ValueError('npm installation failed or timed out')
                verify(str(prefix/'bin'/cli))
            try: install(registry)
            except (ValueError,subprocess.TimeoutExpired):
                if registry.rstrip('/')==MIRROR: raise
                print(cli+': automatic npmmirror fallback (including optional platform binaries)',flush=True)
                install(MIRROR)
        print(cli+': installation complete; platform binaries, bundled components and offline help passed',flush=True)
    except Exception as exc:
        # Network/library exceptions may carry sensitive URL details; expose only controlled reasons.
        reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
        repair = (opencode_install.install_text('en') if cli == 'opencode' else
                  'npm install --global --include=optional --prefix "$HOME/.local/share/agentj-setup/npm" --registry=https://registry.npmjs.org ' + pkg)
        print('安装不完整：缺 '+reason+'; '+cli+'. Repair: '+repair,file=sys.stderr)
        network = (isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError)) or isinstance(exc, TimeoutError)
        raise ValueError('network' if network else 'install_failed') from None
    return True


def install_node(source, destination, scratch):
    """Existing official SHASUMS256 route; no new hashes or source URLs."""
    system=platform.system().lower()
    machine=platform.machine().lower()
    arch={'x86_64':'x64','amd64':'x64','aarch64':'arm64','arm64':'arm64'}.get(machine)
    if system not in ('linux','darwin') or not arch:
        raise ValueError('unsupported_platform')
    ext='tar.gz' if system=='darwin' else 'tar.xz'
    official='https://nodejs.org/dist/latest-v22.x'
    base=official if source=='official' else 'https://registry.npmmirror.com/-/binary/node/latest-v22.x'
    def fetch(url, limit):
        req=urllib.request.Request(url,headers={'User-Agent':'agentj-setup'})
        with urllib.request.urlopen(req,timeout=45) as response:
            if urllib.parse.urlsplit(response.geturl()).scheme!='https':
                raise ValueError('checksum')
            data=response.read(limit+1)
        if len(data)>limit: raise ValueError('checksum')
        return data
    sums=fetch(official+'/SHASUMS256.txt',1024*1024).decode()
    suffix='-'+system+'-'+arch+'.'+ext
    rows=[line.split() for line in sums.splitlines() if len(line.split())==2 and line.split()[1].endswith(suffix)]
    if len(rows)!=1: raise ValueError('node_missing')
    digest,name=rows[0]
    if not re.fullmatch(r'node-v22\.[0-9]+\.[0-9]+-'+system+'-'+arch+r'\.tar\.(?:gz|xz)',name) or not re.fullmatch(r'[0-9a-f]{64}',digest):
        raise ValueError('checksum')
    data=fetch(base+'/'+name,300*1024*1024)
    if hashlib.sha256(data).hexdigest()!=digest: raise ValueError('checksum')
    # A private staging directory is owned/removed by the parent worker lifecycle.
    stage=pathlib.Path(scratch)/'node'; stage.mkdir()
    with tarfile.open(fileobj=io.BytesIO(data)) as bundle:
        for member in bundle.getmembers():
            parts=pathlib.PurePosixPath(member.name).parts
            if not parts or parts[0]!=name.split('.tar.')[0] or '..' in parts or member.isdev():
                raise ValueError('checksum')
        # Require Python's standard link/path filter; no unsafe old-Python fallback.
        bundle.extractall(stage,filter='data')
    extracted=stage/name.split('.tar.')[0]
    if not (extracted/'bin/node').is_file() or not (extracted/'bin/npm').exists():
        raise ValueError('node_missing')
    if destination.exists():
        # Existing incomplete runtime is not deleted or recursively replaced.
        raise ValueError('node_missing')
    destination.parent.mkdir(parents=True,exist_ok=True)
    os.replace(extracted,destination)
