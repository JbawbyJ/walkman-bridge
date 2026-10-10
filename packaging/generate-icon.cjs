'use strict';
// Format adapter for the approved 128px lotus: preserve the original PNG
// byte-for-byte inside a 256px SVG canvas accepted by electron-builder.
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const image = fs.readFileSync(path.join(root, 'frontend/src/assets/lotus.png'));
const destination = path.join(__dirname, 'build/lotus-icon.svg');
fs.mkdirSync(path.dirname(destination), { recursive: true });
fs.writeFileSync(destination, `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="256" height="256" viewBox="0 0 128 128"><image width="128" height="128" xlink:href="data:image/png;base64,${image.toString('base64')}"/></svg>\n`);
