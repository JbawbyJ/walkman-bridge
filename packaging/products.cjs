'use strict';
const path = require('node:path');
const products = Object.freeze({
  bridge: Object.freeze({
    productName: 'Walkman Bridge',
    executableName: 'Walkman Bridge',
    appId: 'com.redlotus.nightops.bridge',
    packageName: 'walkman-bridge',
    artifactName: 'Walkman-Bridge-Setup-${version}-${arch}.${ext}',
  }),
  player: Object.freeze({
    productName: 'Red Lotus Player',
    executableName: 'Red Lotus Player',
    appId: 'com.redlotus.nightops.player',
    packageName: 'red-lotus-player',
    artifactName: 'Red-Lotus-Player-Setup-${version}-${arch}.${ext}',
  }),
});

function createConfig(product) {
  if (!Object.hasOwn(products, product)) throw new Error('Explicit product must be bridge or player');
  const definition = products[product];
  return {
    extends: path.join(__dirname, '..', 'electron-builder.yml'),
    appId: definition.appId,
    productName: definition.productName,
    executableName: definition.executableName,
    artifactName: definition.artifactName,
    directories: { output: `dist_electron/${product}` },
    extraMetadata: { name: definition.packageName, productName: definition.productName },
    extraResources: [{ from: `packaging/build/redlotus/${product}`, to: '.', filter: ['**/*'] }],
    nsis: { shortcutName: definition.productName, uninstallDisplayName: definition.productName },
  };
}
module.exports = { products, createConfig };
