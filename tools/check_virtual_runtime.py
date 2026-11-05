"""Isolated real-Home-Assistant smoke test; no Hive credentials or real heating."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.const import __version__
from homeassistant import loader, bootstrap
from homeassistant.components.frontend import DATA_PANELS
from homeassistant.exceptions import Unauthorized


async def main():
    hass = HomeAssistant('/config')
    loader.async_setup(hass)
    try:
        assert await bootstrap.async_from_config_dict({'frontend': {}, 'hive_virtual': {}}, hass)
        hass.states.async_set('climate.hive_trial', 'heat', {'temperature': 7, 'temperature_unit': '°C', 'preset_mode': 'none', 'preset_modes': ['none', 'boost'], 'min_temp': 5, 'max_temp': 32})
        hass.states.async_set('sensor.down_trial', '18', {'unit_of_measurement': '°C', 'device_class': 'temperature'})
        hass.states.async_set('sensor.up_trial', '19', {'unit_of_measurement': '°C', 'device_class': 'temperature'})
        form = await hass.config_entries.flow.async_init('hive_virtual', context={'source': 'user'})
        assert form['type'] == 'form', form
        created = await hass.config_entries.flow.async_configure(form['flow_id'], {
            'name': 'Runtime trial', 'hive_entity': 'climate.hive_trial',
            'downstairs_sensors': ['sensor.down_trial'], 'upstairs_sensors': ['sensor.up_trial'],
            'default_area': 'Downstairs', 'sensor_report_entities': {}})
        assert created['type'] == 'create_entry', created
        await hass.async_block_till_done()
        entry = created['result']
        controller = entry.runtime_data
        assert hass.states.get(controller.entity_id), entry.state
        assert controller.hvac_mode == 'off'
        assert 'hive-virtual-heating' in hass.data[DATA_PANELS]
        assert any(s.entity_id.startswith('button.') and s.attributes.get('thermostat_unique_id') == controller.unique_id for s in hass.states.async_all())
        calls = []
        async def boost(call):
            saved = await controller._store.async_load()
            assert saved['pending_until']
            calls.append(dict(call.data))
        hass.services.async_register('hive', 'boost_heating_on', boost)
        hass.states.async_set('climate.hive_trial', 'heat', {'temperature': 7, 'temperature_unit': '°C', 'preset_mode': 'none', 'preset_modes': ['none', 'boost'], 'min_temp': 5, 'max_temp': 32})
        await controller.async_set_hvac_mode('heat')
        assert len(calls) == 1, (calls, controller.extra_state_attributes)
        assert calls[0]['time_period'] == '00:05:00'
        deadline = controller._pending_until
        hass.config_entries.async_update_entry(entry, options={**entry.data, 'boost_minutes': 8})
        await hass.async_block_till_done()
        assert entry.runtime_data is controller
        assert controller.hvac_mode == 'heat'
        assert controller._pending_until == deadline
        hass.states.async_set('sensor.down_trial', '-20', {'unit_of_measurement': '°C'})
        await controller._reconcile()
        assert controller._latched_fault
        assert any(s.entity_id.startswith('binary_sensor.') and s.state == 'on' for s in hass.states.async_all())
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert 'hive-virtual-heating' not in hass.data[DATA_PANELS]
        assert await hass.config_entries.async_setup(entry.entry_id)
        controller = entry.runtime_data
        assert controller._latched_fault
        assert controller._pending_until == deadline
        assert controller.hvac_mode == 'off'
        assert 'hive-virtual-heating' in hass.data[DATA_PANELS]
        hass.states.async_set('sensor.down_trial', '18', {'unit_of_measurement': '°C'})
        await controller.async_reset_fault()
        assert not controller._latched_fault
        hass.states.async_set('schedule.heating_trial', 'on', {'temperature': 21, 'area': 'Upstairs'})
        menu = await hass.config_entries.options.async_init(entry.entry_id)
        source_form = await hass.config_entries.options.async_configure(menu['flow_id'], {'next_step_id': 'schedule_source'})
        assert source_form['type'] == 'form'
        result = await hass.config_entries.options.async_configure(menu['flow_id'], {'use_schedule_helper': True, 'schedule_entity': 'schedule.heating_trial'})
        assert result['type'] == 'create_entry', result
        await hass.async_block_till_done()
        await controller.async_set_hvac_mode('auto')
        assert controller.target_temperature == 21
        assert controller.extra_state_attributes['active_area'] == 'Upstairs'
        fallback = next(s for s in hass.states.async_all('sensor') if s.attributes.get('heating_role') == 'sensor_fallback')
        assert fallback.state == 'inactive'
        upstairs = hass.states.get('sensor.up_trial')
        hass.states.async_set(upstairs.entity_id, 'unavailable', upstairs.attributes)
        await controller._reconcile()
        assert hass.states.get(fallback.entity_id).state == 'fixed_target'
        hass.states.async_set(upstairs.entity_id, upstairs.state, upstairs.attributes)
        await controller._reconcile()
        assert hass.states.get(fallback.entity_id).state == 'inactive'
        hass.states.async_set('schedule.heating_trial', 'off')
        await controller._reconcile()
        assert not controller._demand
        # Exercise the registered WebSocket handlers with real HA schemas and
        # decorators. Only the connection transport and physical Hive are fake.
        async def call_ws(kind, payload=None, admin=True):
            result, event = {}, asyncio.Event()
            def send_result(ident, value=None):
                result['value'] = value
                event.set()
            def send_error(ident, code, message):
                result['error'] = code
                result['message'] = message
                event.set()
            def unexpected(msg, err):
                result['exception'] = repr(err)
                event.set()
            connection = SimpleNamespace(user=SimpleNamespace(is_admin=admin), send_result=send_result,
                                         send_error=send_error, async_handle_exception=unexpected)
            handler, schema = hass.data['websocket_api'][kind]
            handler(hass, connection, schema({'id': 100, 'type': kind, **(payload or {})}))
            await asyncio.wait_for(event.wait(), 10)
            assert 'exception' not in result, result
            return result
        try:
            await call_ws('hive_virtual/config', {'entity_id': controller.entity_id}, admin=False)
            raise AssertionError('Non-admin settings read was accepted')
        except Unauthorized:
            pass
        page = (await call_ws('hive_virtual/config', {'entity_id': controller.entity_id}))['value']
        conflict = await call_ws('hive_virtual/save', {'entity_id': controller.entity_id, 'revision': 'old', 'cost': {}})
        assert conflict['error'] == 'conflict'
        hass.states.async_set('sensor.gas_daily_trial', '30', {'unit_of_measurement': 'kWh'})
        hass.states.async_set('sensor.gas_rate_trial', '7.5', {'unit_of_measurement': 'p/kWh'})
        hass.states.async_set('sensor.gas_cost_trial', '2.75', {'unit_of_measurement': 'GBP'})
        costs = {'gas_energy_today_entity': 'sensor.gas_daily_trial', 'gas_cost_today_entity': 'sensor.gas_cost_trial',
                 'unit_rate_entity': 'sensor.gas_rate_trial', 'rate_in_pence': True, 'boiler_input_kw': 12,
                 'learn_from_gas_meter': True}
        saved = await call_ws('hive_virtual/save', {'entity_id': controller.entity_id, 'revision': page['revision'], 'cost': costs})
        assert saved.get('value', {}).get('saved'), saved
        await hass.async_block_till_done()
        controller._runtime.seconds = 3600
        assert controller._gas_learning.source[1] == 'sensor.gas_daily_trial'
        assert abs(controller.cost_estimates()['heating_cost'] - .9) < .01
        assert controller.cost_estimates()['household_variable_cost'] == 2.75
        assert controller.cost_estimates()['household_gas_kwh'] == 30
        for index in range(3):
            start = index * 600
            meter = controller._gas_learning
            meter.update(start, False, None)
            meter.update(start + 30, True, (start, 100, 'kWh'))
            meter.update(start + 60, True, (start + 60, 100, 'kWh'))
            meter.update(start + 150, True, (start + 60, 100, 'kWh'))
            meter.update(start + 240, True, (start + 240, 101, 'kWh'))
            meter.update(start + 270, False, None)
        assert controller.extra_state_attributes['gas_learning_runs'] == 3
        assert controller.extra_state_attributes['learned_gas_kw'] == 20
        assert abs(controller.cost_estimates()['heating_cost'] - 1.5) < .01
        assert await controller._persist()
        assert (await controller._store.async_load())['gas_learning']['runs'] == [20, 20, 20]
        await controller._reconcile()
        assert len([s for s in hass.states.async_all('sensor') if s.attributes.get('heating_role') == 'area_temperature']) == 2
        assert await async_setup_component(hass, 'schedule', {})
        helper = (await call_ws('schedule/create', {'name': 'Runtime editable schedule'}))['value']
        payload = {'schedule_id': helper['id'], 'name': helper['name'],
                   'monday': [{'from': '22:00:00', 'to': '24:00:00', 'data': {'temperature': 18, 'area': 'Upstairs', 'custom': 'retained'}}]}
        native_saved = await call_ws('schedule/update', payload)
        assert 'error' not in native_saved, native_saved
        native_items = (await call_ws('schedule/list'))['value']
        assert next(i for i in native_items if i['id'] == helper['id'])['monday'][0]['data']['custom'] == 'retained'
        assert controller.hvac_mode == 'auto'
        assert controller._pending_until == deadline
        config = {**entry.data, **entry.options}
        config.pop('schedule_entity', None)
        hass.config_entries.async_update_entry(entry, options=config)
        await hass.async_block_till_done()
        page = (await call_ws('hive_virtual/config', {'entity_id': controller.entity_id}))['value']
        invalid = await call_ws('hive_virtual/save', {'entity_id': controller.entity_id, 'revision': page['revision'],
                                                    'schedule': [{'time': '99:00', 'temperature': 20, 'area': 'Upstairs'}]})
        assert invalid['error'] == 'invalid_settings'
        periods = [{'time': '00:00', 'temperature': 19, 'area': 'Upstairs', 'days': list(range(7))}]
        builtin_saved = await call_ws('hive_virtual/save', {'entity_id': controller.entity_id, 'revision': page['revision'], 'schedule': periods})
        assert builtin_saved.get('value', {}).get('saved'), builtin_saved
        await hass.async_block_till_done()
        assert controller.config['schedule'][0]['temperature'] == 19
        assert controller.hvac_mode == 'auto'
        assert controller._pending_until == deadline
        print(json.dumps({'home_assistant': __version__, 'entry': str(entry.state), 'controller': controller.entity_id,
                          'boost_requests': len(calls), 'live_options': True, 'fault_sensor': True,
                          'durable_request': bool((await controller._store.async_load())['pending_until']),
                          'native_schedule_options': True, 'fault_survived_reload': True, 'sidebar': True,
                          'page_settings_permissions': True, 'native_schedule_save': True, 'cost_units': True,
                          'temperature_sensors': True, 'edit_conflict_detection': True, 'builtin_schedule_save': True,
                          'learned_gas_cost': True}))
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert await hass.config_entries.async_setup(entry.entry_id)
        assert entry.runtime_data.extra_state_attributes['learned_gas_kw'] == 20
        assert entry.runtime_data.extra_state_attributes['gas_learning_runs'] == 3
        controller = entry.runtime_data
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        charges = [{'start': (start + timedelta(minutes=30 * i)).isoformat(),
                    'end': (start + timedelta(minutes=30 * (i + 1))).isoformat()} for i in range(48)]
        hass.states.async_set('sensor.edf_trial', '34.954', {'unit_of_measurement': 'kWh', 'charges': charges})
        config = {**controller.config, 'cost': {**controller.config['cost'], 'gas_reported_entity': 'sensor.edf_trial'}}
        await controller.async_update_config(config)
        assert controller.extra_state_attributes['reported_gas']['kwh'] == 34.954
        assert controller.extra_state_attributes['gas_learning_days'] == 0
        assert await hass.config_entries.async_unload(entry.entry_id)
    finally:
        await hass.async_stop()


asyncio.run(main())
