"""P422 deterministic startup assets, from the transparent official master. Run with Pillow.
No network or generated artwork. iOS needs exact screen dimensions; both orientations and schemes.
"""
from pathlib import Path
from PIL import Image
import hashlib
import json

web = Path(__file__).resolve().parents[1]
public = web / 'public'
assets = public / 'startup'
assets.mkdir(exist_ok=True)
master = Image.open(web.parent / 'brand/img/robot-source-16.png').convert('RGBA')
# iPhone SE through Air/Pro Max, and the common iPad full-screen sizes.
screens = [(320,568,2),(375,667,2),(414,736,3),(375,812,3),(390,844,3),(393,852,3),
           (402,874,3),(414,896,2),(420,912,3),(430,932,3),(440,956,3),
           (768,1024,2),(810,1080,2),(820,1180,2),(834,1194,2),(834,1210,2),(1024,1366,2),(1032,1376,2)]
links = []
records = []
def version(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]
for scheme, bg in [('light','#faf9f5'),('dark','#141414')]:
    art = master.copy()
    if scheme == 'dark':
        # A light robot on Sooty, dark face/eyes retained. Transparent everywhere outside the robot.
        for y in range(16):
            for x in range(16):
                if art.getpixel((x,y)) == (98,152,135,255): art.putpixel((x,y),(250,249,245,255))
    box = art.getbbox()
    for n in [192,512]:
        icon = Image.new('RGBA',(n,n))
        k = n // 20
        robot = art.crop(box).resize(((box[2]-box[0])*k,(box[3]-box[1])*k),Image.Resampling.NEAREST)
        icon.alpha_composite(robot,((n-robot.width)//2,(n-robot.height)//2))
        icon.save(assets / f'icon-{scheme}-{n}.png',optimize=True)
        if n == 512:
            mask = Image.new('RGBA',(n,n),bg)
            mask.alpha_composite(icon)
            mask.save(assets / f'maskable-{scheme}.png',optimize=True)
    manifest = json.loads((public / 'manifest.webmanifest').read_text())
    manifest['background_color'] = bg
    manifest['theme_color'] = '#141414'
    manifest['icons'] = [dict(src=f'startup/{name}?v={version(assets / name)}',sizes=size,type='image/png',purpose=purpose)
        for name,size,purpose in [(f'icon-{scheme}-192.png','192x192','any'),(f'icon-{scheme}-512.png','512x512','any'),(f'maskable-{scheme}.png','512x512','maskable')]]
    (public / f'manifest-{scheme}.webmanifest').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    for w,h,dpr in screens:
        for orientation in ['portrait','landscape']:
            iw,ih = (w*dpr,h*dpr) if orientation == 'portrait' else (h*dpr,w*dpr)
            canvas = Image.new('RGBA',(iw,ih),bg)
            # Full-screen image: no embedded apple-touch-icon plate. System edges always Sooty.
            top = (62 if h >= 812 and w < 500 else 24)*dpr
            bottom = (34 if h >= 812 and w < 500 else 20)*dpr
            canvas.paste('#141414',(0,0,iw,top))
            canvas.paste('#141414',(0,ih-bottom,iw,ih))
            logo = Image.open(assets / f'icon-{scheme}-192.png')
            n = 96*dpr
            logo = logo.resize((n,n),Image.Resampling.NEAREST)
            canvas.alpha_composite(logo,((iw-n)//2,(ih-n)//2))
            name = f'{w}x{h}-{dpr}-{orientation}-{scheme}.png'
            path = assets / name
            canvas.convert('RGB').quantize(colors=8,method=Image.Quantize.MEDIANCUT).save(path,optimize=True,bits=4)
            media = f'(device-width: {w}px) and (device-height: {h}px) and (-webkit-device-pixel-ratio: {dpr}) and (orientation: {orientation}) and (prefers-color-scheme: {scheme})'
            links.append(f'<link rel="apple-touch-startup-image" href="startup/{name}?v={version(path)}" media="{media}">')
            records.append(dict(file=name,width=iw,height=ih,media=media,scheme=scheme))
(assets / 'screens.json').write_text(json.dumps(records,indent=2)+'\n')
p = public / 'index.html'
s = p.read_text()
a = s.find('<!-- P422 startup images -->')
if a >= 0:
    b = s.index('<!-- /P422 startup images -->',a)+len('<!-- /P422 startup images -->')
    s = s[:a].rstrip()+"\n"+s[b:].lstrip("\n")
insert = '<!-- P422 startup images -->\n'+'\n'.join(links)+'\n<!-- /P422 startup images -->'
s = s.replace('<link rel="manifest"',insert+'\n<link rel="manifest"',1)
p.write_text(s)
print(f'Generated {len(records)} iOS startup images and two Android manifest/icon sets')
