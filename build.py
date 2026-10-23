#!/usr/bin/env python3
# Builds plugin.video.seerrhub-<version>.zip for Kodi's "Install from zip file".
import os
import re
import zipfile

ADDON = 'plugin.video.seerrhub'
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, ADDON, 'addon.xml')) as fh:
    version = re.search(r'<addon[^>]*\bversion="([^"]+)"', fh.read()).group(1)
out = os.path.join(here, '%s-%s.zip' % (ADDON, version))
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(os.path.join(here, ADDON)):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__')
        for name in sorted(files):
            if not name.endswith('.pyc'):
                path = os.path.join(root, name)
                z.write(path, os.path.relpath(path, here))
print(out)
