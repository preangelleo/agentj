#!/usr/bin/env python3
"""Fetch only digest-pinned dependencies; explicit opt-in to review-only model artifacts."""
import argparse,hashlib,json,pathlib,tarfile,tempfile,urllib.request
root=pathlib.Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--review-model-license',action='store_true');a=p.parse_args()
if not a.review_model_license:p.error('Read MODEL_PROVENANCE.json license evidence first; use --review-model-license for review builds')
spec=json.loads((root/'MODEL_PROVENANCE.json').read_text());assets=root/'app/src/main/assets/models';assets.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='agentj-models-') as tmp:
 files={}
 for name,url in spec['urls'].items():
  dst=pathlib.Path(tmp)/name
  with urllib.request.urlopen(url,timeout=180) as r:dst.write_bytes(r.read(150*1024*1024))
  if hashlib.sha256(dst.read_bytes()).hexdigest()!=spec['sha256'][name]:raise SystemExit('digest mismatch: '+name)
  files[name]=dst
 with tarfile.open(files['model.tar.bz2']) as tar:
  for name in ['tokens.txt','encoder-epoch-13-avg-2-chunk-16-left-64.int8.onnx','decoder-epoch-13-avg-2-chunk-16-left-64.onnx','joiner-epoch-13-avg-2-chunk-16-left-64.int8.onnx']:
   member=tar.getmember(spec['model']+'/'+name)
   if not member.isfile() or member.size>100*1024*1024:raise SystemExit('invalid model member')
   (assets/name).write_bytes(tar.extractfile(member).read())
 (assets/'silero_vad.onnx').write_bytes(files['silero_vad.onnx'].read_bytes())
 (assets/'keywords.txt').write_text('') # dynamic keywords only; no bootstrap phrase
 lib=root/'app/libs/sherpa-onnx-1.13.8.aar';lib.parent.mkdir(parents=True,exist_ok=True);lib.write_bytes(files['sherpa.aar'].read_bytes())
print('Verified pinned review-build dependencies. No credentials or relay models used.')
