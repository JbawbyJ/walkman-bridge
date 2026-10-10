'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createConfig } = require('./products.cjs');

test('product identities and staging roots cannot overlap', () => {
  const bridge = createConfig('bridge');
  const player = createConfig('player');
  assert.equal(bridge.productName, 'Walkman Bridge');
  assert.equal(player.productName, 'Red Lotus Player');
  for (const field of ['appId', 'executableName', 'artifactName']) assert.notEqual(bridge[field], player[field]);
  assert.notEqual(bridge.extraMetadata.name, player.extraMetadata.name);
  assert.notEqual(bridge.directories.output, player.directories.output);
  assert.notEqual(bridge.extraResources[0].from, player.extraResources[0].from);
});

test('visible product identity is renamed while upgrade identity remains stable', () => {
  const bridge = createConfig('bridge'), player = createConfig('player');
  assert.equal(bridge.appId, 'com.redlotus.nightops.bridge');
  assert.equal(player.appId, 'com.redlotus.nightops.player');
  assert.equal(bridge.executableName, 'Walkman Bridge');
  assert.equal(bridge.artifactName, 'Walkman-Bridge-Setup-${version}-${arch}.${ext}');
  for (const config of [bridge, player]) {
    for (const value of [config.productName, config.executableName, config.artifactName,
      config.nsis.shortcutName, config.nsis.uninstallDisplayName, config.extraMetadata.name]) {
      assert.doesNotMatch(value, /night[ -]?ops/i);
    }
  }
});

test('unknown product cannot silently create a bridge installer', () => {
  for (const product of [undefined, '', '../bridge', 'other', '__proto__']) {
    assert.throws(() => createConfig(product), /Explicit product/);
  }
});
