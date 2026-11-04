"""Isolated real-Home-Assistant smoke test; no Hive credentials or real heating."""
import asyncio
import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.const import __version__
from homeassistant import loader, bootstrap
from homeassistant.components.frontend import DATA_PANELS


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
        hass.states.async_set('schedule.heating_trial', 'off')
        await controller._reconcile()
        assert not controller._demand
        print(json.dumps({'home_assistant': __version__, 'entry': str(entry.state), 'controller': controller.entity_id,
                          'boost_requests': len(calls), 'live_options': True, 'fault_sensor': True,
                          'durable_request': bool((await controller._store.async_load())['pending_until']),
                          'native_schedule_options': True, 'fault_survived_reload': True, 'sidebar': True}))
        assert await hass.config_entries.async_unload(entry.entry_id)
    finally:
        await hass.async_stop()


asyncio.run(main())
