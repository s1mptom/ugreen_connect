/* Finding a charger's entities whatever language their ids were made in.
 *
 * Home Assistant makes an entity id out of the entity's name in the language
 * it was set up in, so on a German install the total is
 * `sensor.<device>_gesamtleistung` and the cards, which looked for
 * `_total_power`, found nothing (#45). They now go by the translation key the
 * registry carries, and by the port a port's entities name in an attribute.
 *
 * Run with `node --test tests_js/`.
 */

import assert from 'node:assert/strict';
import test from 'node:test';

const { chargerEntity, chargers, findAll, findOne, ports } = await import(
  '../custom_components/ugreen_connect/www/ugreen-ui.js'
);

const DEV = 'ugreen_nexode_pro_x783';

// What a German install holds, as the entity ids Home Assistant made there.
function german() {
  const states = {};
  const entities = {};
  const put = (id, key, attributes = {}, device = 'dev-300') => {
    states[id] = { entity_id: id, state: '1.0', attributes };
    entities[id] = { entity_id: id, device_id: device, platform: 'ugreen_connect', translation_key: key };
  };
  put(`sensor.${DEV}_gesamtleistung`, 'total_power');
  put(`sensor.${DEV}_cloud_status`, 'status');
  put(`select.${DEV}_lademodus`, 'charging_mode');
  put(`update.${DEV}_firmware`, 'firmware');
  put(`sensor.${DEV}_energie`, 'energy_total_charger');
  for (const port of ['C1', 'C2']) {
    const p = port.toLowerCase();
    put(`sensor.${DEV}_${p}_leistung`, 'port_power', { port });
    put(`sensor.${DEV}_${p}_spannung`, 'port_voltage', { port });
    put(`sensor.${DEV}_${p}_energie`, 'energy_total', { port });
    put(`binary_sensor.${DEV}_${p}_ladt`, 'charging', { port });
    // The event's English id ends `_charging_bout` now, not `_charging`.
    put(`event.${DEV}_${p}_ladevorgang`, 'charging_event', { port });
    put(`switch.${DEV}_${p}_zuerst_geladen`, 'priority_port', { port });
  }
  // Another integration's entity that happens to end the same way.
  states['sensor.stove_total_power'] = { entity_id: 'sensor.stove_total_power', state: '900', attributes: {} };
  entities['sensor.stove_total_power'] = { entity_id: 'sensor.stove_total_power', platform: 'other' };
  const devices = { 'dev-300': { name: 'UGREEN Nexode Pro X783', model_id: 'X783' } };
  return { states, entities, devices };
}

test('the charger is found by its total, whatever the total is called', () => {
  const hass = german();
  assert.deepEqual(chargers(hass).map((c) => [c.id, c.total]), [['dev-300', `sensor.${DEV}_gesamtleistung`]]);
});

test('its own entities are found by what they are', () => {
  const hass = german();
  assert.equal(findOne(hass, undefined, 'sensor', '_total_power'), `sensor.${DEV}_gesamtleistung`);
  assert.equal(findOne(hass, undefined, 'select', '_charging_mode'), `select.${DEV}_lademodus`);
  assert.equal(findOne(hass, 'dev-300', 'update', '_firmware'), `update.${DEV}_firmware`);
  assert.equal(chargerEntity(hass, 'dev-300', 'sensor', 'energy'), `sensor.${DEV}_energie`);
});

test('a port and everything about it, from the port it names', () => {
  const hass = german();
  const [c1, c2] = ports(hass, 'dev-300');
  assert.deepEqual([c1.name, c2.name], ['C1', 'C2']);
  assert.equal(c1.id, `sensor.${DEV}_c1_leistung`);
  assert.equal(c1.of('sensor', 'voltage'), `sensor.${DEV}_c1_spannung`);
  assert.equal(c2.of('sensor', 'energy'), `sensor.${DEV}_c2_energie`);
  assert.equal(c1.charging, `binary_sensor.${DEV}_c1_ladt`);
  assert.equal(c1.event, `event.${DEV}_c1_ladevorgang`);
  assert.deepEqual(findAll(hass, 'dev-300', 'switch', '_charged_first'),
    [`switch.${DEV}_c1_zuerst_geladen`, `switch.${DEV}_c2_zuerst_geladen`]);
});

test('an entity its owner renamed is still found', () => {
  const hass = german();
  const id = `sensor.${DEV}_gesamtleistung`;
  const renamed = 'sensor.desk_charger';
  hass.states[renamed] = { ...hass.states[id], entity_id: renamed };
  hass.entities[renamed] = { ...hass.entities[id], entity_id: renamed };
  delete hass.states[id];
  delete hass.entities[id];
  assert.equal(findOne(hass, 'dev-300', 'sensor', '_total_power'), renamed);
});

test("another integration's entity is never taken for the charger's", () => {
  const hass = german();
  assert.ok(!findAll(hass, undefined, 'sensor', '_total_power').includes('sensor.stove_total_power'));
});
