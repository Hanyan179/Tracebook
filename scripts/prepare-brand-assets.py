#!/usr/bin/env python3
"""Package generated brand art into platform icon sizes; no redraw or restyling."""
from pathlib import Path
import json
import subprocess
import shutil
ROOT=Path(__file__).resolve().parents[1]
assets=ROOT/'Apple/Resources/Brand.xcassets'
(assets/'Contents.json').write_text(json.dumps({'info':{'author':'xcode','version':1}}))
(assets/'BrandMark.imageset/Contents.json').write_text(json.dumps({'images':[{'idiom':'universal','filename':'brand-mark.png'}],'info':{'author':'xcode','version':1},'properties':{'template-rendering-intent':'template'}}))
icon=assets/'AppIcon.appiconset';icon.mkdir(exist_ok=True)
images=[]
def add(size,scale,idiom,**extra):
 pixels=round(float(size)*int(scale[0]));name=f'icon-{pixels}.png'
 target=icon/name
 if not target.exists():
  subprocess.run(['sips','-z',str(pixels),str(pixels),str(ROOT/'design/app-icon-original.png'),'--out',str(target)],check=True,stdout=subprocess.DEVNULL)
 images.append(dict(idiom=idiom,size=f'{size}x{size}',scale=scale,filename=name,**extra))
images.append({'idiom':'universal','platform':'ios','size':'1024x1024','filename':'icon-1024.png'})
for n in [16,32,128,256,512]:
 for scale in ['1x','2x']:add(n,scale,'mac')
for n,role,subtype in [(24,'notificationCenter','38mm'),(27.5,'notificationCenter','42mm'),(40,'appLauncher','38mm'),(44,'appLauncher','40mm'),(46,'appLauncher','41mm'),(50,'appLauncher','44mm'),(51,'appLauncher','45mm'),(54,'appLauncher','49mm'),(86,'quickLook','38mm'),(98,'quickLook','42mm'),(108,'quickLook','44mm')]:add(n,'2x','watch',role=role,subtype=subtype)
for scale in ['2x','3x']:add(29,scale,'watch',role='companionSettings')
add(1024,'1x','watch-marketing')
for platform, idioms in [('Phone', ['universal']), ('Mac', ['mac']), ('Watch', ['watch', 'watch-marketing'])]:
 catalog=ROOT/('Apple/Resources/'+platform+'Icons.xcassets');target=catalog/'AppIcon.appiconset'
 target.mkdir(parents=True,exist_ok=True)
 chosen=[image for image in images if image['idiom'] in idioms]
 for image in chosen:shutil.copyfile(icon/image['filename'], target/image['filename'])
 (target/'Contents.json').write_text(json.dumps({'images':chosen,'info':{'author':'xcode','version':1}},indent=2))
 (catalog/'Contents.json').write_text(json.dumps({'info':{'author':'xcode','version':1}}))
shutil.rmtree(icon)
