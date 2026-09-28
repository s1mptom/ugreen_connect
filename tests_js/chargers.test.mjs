/* Which charger a card is about, with two on one account.
 *
 * The cards used to look for `device_id` on the state object, where nothing
 * puts it: a `device_id` in a card's config filtered nothing, and two chargers
 * ran together on every card. Home Assistant says which device an entity
 * belongs to in its entity registry, which the frontend has as `hass.entities`.
 *
 * Run with `node --test tests_js/*.test.mjs`.
 */

import assert from 'node:assert/strict';
import test from 'node:test';

const { belongs, chargers, findAll, findOne, ports, resolveDevice } = await import(
  '../custom_components/ugreen_connect/www/ugreen-ui.js'
);

function hassWith(...devices) {
  const states = {};
  const entities = {};
  const registry = {};
  for (const { id, prefix, name, model } of devices) {
    registry[id] = { name, name_by_user: null, model_id: model };
    for (const suffix of ['total_power', 'c1_power', 'c2_power']) {
      const entityId = `sensor.${prefix}_${suffix}`;
      states[entityId] = {
        entity_id: entityId,
        state: '1.0',
        attributes: { friendly_name: `${name} ${suffix.replace('_power', '').toUpperCase()} Power` },
      };
      entities[entityId] = { entity_id: entityId, device_id: id, platform: 'ugreen_connect' };
    }
  }
  return { states, entities, devices: registry };
}

const X783 = { id: 'dev-300', prefix: 'ugreen_nexode_pro_x783', name: 'UGREEN Nexode Pro X783', model: 'X783' };
const X776 = { id: 'dev-160', prefix: 'ugreen_nexode_pro_x776', name: 'UGREEN Nexode Pro X776', model: 'X776' };

test('every charger is found once, named as its device page names it', () => {
  const hass = hassWith(X783, X776);
  assert.deepEqual(
    chargers(hass).map((c) => [c.id, c.name, c.model]),
    [['dev-160', 'UGREEN Nexode Pro X776', 'X776'], ['dev-300', 'UGREEN Nexode Pro X783', 'X783']],
  );
});

test('a name its owner gave comes first', () => {
  const hass = hassWith(X783);
  hass.devices['dev-300'].name_by_user = 'Desk';
  assert.equal(chargers(hass)[0].name, 'Desk');
});

test('an entity belongs to its own device, whatever its id says', () => {
  const hass = hassWith(X783, X776);
  assert.equal(belongs(hass, 'sensor.ugreen_nexode_pro_x783_c1_power', 'dev-300'), true);
  assert.equal(belongs(hass, 'sensor.ugreen_nexode_pro_x783_c1_power', 'dev-160'), false);
});

test('a card pointed at one charger sees only that one', () => {
  const hass = hassWith(X783, X776);
  const found = findAll(hass, 'dev-160', 'sensor', '_power');
  assert.ok(found.length > 0);
  assert.ok(found.every((id) => id.includes('x776')), found.join());
  assert.equal(findOne(hass, 'dev-300', 'sensor', '_total_power'), 'sensor.ugreen_nexode_pro_x783_total_power');
});

test('a card pointed at none shows the first charger, never both', () => {
  const hass = hassWith(X783, X776);
  assert.equal(resolveDevice(hass, undefined), 'dev-160');
  assert.equal(resolveDevice(hass, 'dev-300'), 'dev-300');
  const names = ports(hass, undefined).map((p) => p.id);
  assert.ok(names.every((id) => id.includes('x776')), names.join());
});

test('without the registry, the entity id is what there is to go on', () => {
  const hass = hassWith(X783);
  delete hass.entities;
  assert.equal(belongs(hass, 'sensor.ugreen_nexode_pro_x783_c1_power', 'dev-300'), true);
  assert.deepEqual(chargers(hass), []);
});

test('the list is worked out again when the registry changes', () => {
  const hass = hassWith(X783);
  assert.equal(chargers(hass).length, 1);
  const both = hassWith(X783, X776);
  hass.entities = both.entities;
  hass.devices = both.devices;
  assert.equal(chargers(hass).length, 2);
});

const { chargerNames } = await import('../custom_components/ugreen_connect/www/ugreen-ui.js');

test('a second charger of the same name, whose ids end _2, is found whole', () => {
  // Home Assistant numbers the second charger's entity ids when the name is taken.
  const hass = hassWith(X783);
  for (const suffix of ['total_power', 'c1_power', 'c2_power']) {
    const id = `sensor.ugreen_nexode_pro_x783_${suffix}_2`;
    hass.states[id] = { entity_id: id, state: '2.0', attributes: { port: suffix.split('_')[0].toUpperCase() } };
    hass.entities[id] = { entity_id: id, device_id: 'dev-300b', platform: 'ugreen_connect' };
  }
  hass.devices['dev-300b'] = { name: 'UGREEN Nexode Pro X783', model_id: 'X783' };
  assert.equal(chargers(hass).length, 2);
  const second = ports(hass, 'dev-300b');
  assert.deepEqual(second.map((p) => p.id), [
    'sensor.ugreen_nexode_pro_x783_c1_power_2', 'sensor.ugreen_nexode_pro_x783_c2_power_2',
  ]);
  assert.equal(second[0].of('sensor', 'voltage'), 'sensor.ugreen_nexode_pro_x783_c1_voltage_2');
  assert.equal(second[0].charging, 'binary_sensor.ugreen_nexode_pro_x783_c1_charging_2');
});

test('a port is named by its attribute, not by the last word of a translated name', () => {
  const hass = hassWith(X783);
  hass.states['sensor.ugreen_nexode_pro_x783_c1_power'].attributes = {
    friendly_name: 'UGREEN Nexode Pro X783 C1 Leistung', port: 'C1',
  };
  assert.equal(ports(hass, 'dev-300')[0].name, 'C1');
});

test('a charger that has left the account is not the one shown by default', () => {
  const hass = hassWith(X783, X776);
  hass.states['sensor.ugreen_nexode_pro_x776_total_power'].state = 'unavailable';
  assert.equal(resolveDevice(hass, undefined), 'dev-300');
});

test('chargers are called by the name given to them, else by their product', () => {
  const list = (...specs) => specs.map(([own, app, product, model]) => ({ own, app, product, model, name: own || app }));
  assert.deepEqual(
    chargerNames(list(['', 'UGREEN Nexode Pro X783', 'UGREEN Nexode Pro 300W', 'X783'],
      ['', 'UGREEN Nexode Pro X776', 'UGREEN Nexode Pro 160W', 'X776'])),
    ['Nexode Pro 300W', 'Nexode Pro 160W'],
  );
  // Named in the UGREEN app, or in Home Assistant, which wins.
  assert.deepEqual(
    chargerNames(list(['', 'Desk', 'UGREEN Nexode Pro 300W', 'X783'], ['Kitchen', 'Hall', 'UGREEN Nexode Pro 300W', 'X783'])),
    ['Desk', 'Kitchen'],
  );
  // Two that read the same are numbered.
  assert.deepEqual(
    chargerNames(list(['', 'UGREEN Nexode Pro X783', 'UGREEN Nexode Pro 300W', 'X783'],
      ['', 'UGREEN Nexode Pro X783', 'UGREEN Nexode Pro 300W', 'X783'])),
    ['Nexode Pro 300W (1)', 'Nexode Pro 300W (2)'],
  );
});
