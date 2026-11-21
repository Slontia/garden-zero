#!/usr/bin/env python3
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import pathlib,re,subprocess,sys
source=pathlib.Path(__file__).resolve().parents[1]
root=pathlib.Path(subprocess.check_output(['git','-c','safe.directory='+str(source.parent),'-C',str(source),'rev-parse','--show-toplevel'],text=True).strip())
paths=subprocess.check_output(['git','-c','safe.directory='+str(root),'diff','--cached','--name-only','--diff-filter=ACM','-z'],cwd=root).split(b'\0');errors=[]
for raw in paths:
 if not raw:continue
 name=raw.decode();p=pathlib.PurePosixPath(name)
 relative=name[5:] if name.startswith('core/') else name
 if relative.startswith(('data/','system/','config/','secrets/','runtime/','cache/','logs/','backups/','installed/')) or (p.name.startswith('.env') and p.name!='.env.example') or p.suffix.lower() in ('.img','.vhdx','.dump','.key'):
  errors.append('Disallowed staged path: '+name);continue
 data=subprocess.check_output(['git','-c','safe.directory='+str(root),'show',':'+name],cwd=root)
 if subprocess.check_output(['git','-c','safe.directory='+str(root),'ls-files','--stage','--',name],cwd=root).startswith((b'160000',b'120000')):errors.append('Nested repository or linked file: '+name)
 if len(data)>5*1024**2:errors.append('Large staged file: '+name)
 if re.search(rb'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----',data):errors.append('Private key detected: '+name)
 if re.search(rb'(?:ghp_|github_pat_)[A-Za-z0-9_]{25,}|AKIA[0-9A-Z]{16}',data):errors.append('Credential token detected: '+name)
print('\n'.join(errors) if errors else 'Staged NAS deployment files passed path/size/key/token checks.')
raise SystemExit(bool(errors))
