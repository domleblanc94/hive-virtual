export const DAYS=['monday','tuesday','wednesday','thursday','friday','saturday','sunday'];
function node(tag,text){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;}
function input(type,value){const e=node('input');e.type=type;e.value=value;return e;}
function choice(values,value){const e=node('select');for(const v of values){const o=node('option',typeof v==='string'?v:v.label);o.value=typeof v==='string'?v:v.value;e.append(o);}e.value=value;return e;}
function field(label,control){const e=node('label');e.append(node('span',label),control);return e;}
function button(label,action){const b=node('button',label);b.type='button';b.onclick=action;return b;}
function sensorPicker(id, options, selected) {
  const control = input('text', selected);
  control.placeholder = 'Search by name or entity ID';
  control.autocomplete = 'off';
  const list = node('datalist');
  list.id = id;
  control.setAttribute('list', id);
  for (const {value,label} of options) {
    const option = node('option');
    option.value = value;
    option.label = label;
    list.append(option);
  }
  control.oninput = () => control.setCustomValidity('');
  return {control, list};
}

const moneyUnits = new Set([...Intl.supportedValuesOf('currency').map(code => code.toLowerCase()),
  'p', 'c', 'pence', 'cents', '£', '€', '$', '¢']);

function costSensorOptions(sensors, key, selected) {
  const energyFields = ['gas_energy_today_entity', 'gas_meter_entity', 'gas_reported_entity'];
  return sensors.flatMap(({entity_id: value, state, attributes}) => {
    const unit = attributes.unit_of_measurement || '';
    const normalized = unit.toLowerCase().replace(/\s/g, '');
    const charges = attributes.charges;
    const dated = Array.isArray(charges) && charges.length >= 46 && charges.length <= 50 &&
      charges.every(row => row?.start && row?.end);
    let matches;
    if (energyFields.includes(key)) {
      matches = ['kWh', 'Wh', 'MWh', 'm³', 'm3'].includes(unit) &&
        (key === 'gas_reported_entity' ? dated : !dated);
    } else {
      const [currency, per, ...extra] = normalized.split('/');
      matches = !extra.length && moneyUnits.has(currency) &&
        (key === 'unit_rate_entity' ? per === 'kwh' : [undefined, 'day', 'd'].includes(per));
      if (key === 'gas_cost_today_entity' && dated) matches = false;
    }
    if (!matches && value !== selected) return [];
    const label = `${attributes.friendly_name || value} (${state} ${unit})${matches ? '' : ' · currently selected'}`;
    return [{value, label}];
  });
}
export function nativeRows(item){return DAYS.flatMap((day,i)=>(item[day]||[]).map(block=>({time:block.from.slice(0,5),end:block.to.slice(0,5),temperature:block.data?.temperature,area:block.data?.area,days:[i],extra:{...block.data}})));}
export function nativePayload(rows,item){
  const result={type:'schedule/update',schedule_id:item.id,name:item.name};if(item.icon)result.icon=item.icon;
  for(const day of DAYS)result[day]=[];
  for(const r of rows)for(const d of r.days)result[DAYS[d]].push({from:r.time+':00',to:r.end+':00',data:{...r.extra,temperature:r.temperature,area:r.area}});
  return result;
}
export async function scheduleEditor(panel,host,state,collapse){
  const hass=panel._hass, config=await hass.callWS({type:'hive_virtual/config',entity_id:state.entity_id});
  let original=null;
  if(config.schedule_entity){
    const registry=await hass.callWS({type:'config/entity_registry/get',entity_id:config.schedule_entity});
    const items=await hass.callWS({type:'schedule/list'});
    original=items.find(item=>item.id===registry.unique_id);
    if(!original)throw Error('This schedule is defined in YAML and is read-only here. Edit its configuration file.');
  }
  host.replaceChildren();const title=node('h3',original?'Edit native schedule blocks':'Edit weekly temperature periods');host.append(title);
  host.append(node('p',original?'Set Area to Upstairs or Downstairs for each block. Gaps stop new heating requests. Use 24:00 for midnight.':'Set Start and Area to choose when the thermostat switches floors. Each period runs until the next matching period. Times use Home Assistant’s timezone.'));
  const list=node('div'), forms=[];host.append(list);
  function addRow(old={time:'12:00',end:'13:00',temperature:20,area:config.areas[0],days:[0,1,2,3,4,5,6]}){
    const row=node('div');row.className='schedule-row';
    const time=input('time',old.time),end=input('text',old.end||'13:00'),temp=input('number',old.temperature??20),area=choice(config.areas,old.area||config.areas[0]);
    temp.min=7;temp.max=30;temp.step=.5;time.required=true;temp.required=true;end.pattern='(?:[01][0-9]|2[0-3]):[0-5][0-9]|24:00';end.placeholder='HH:MM';
    row.append(field('Start',time));if(original)row.append(field('End (24:00 = midnight)',end));row.append(field('Target °C',temp),field('Area',area));
    const checks=DAYS.map((day,index)=>{const c=input('checkbox','');c.checked=old.days.includes(index);row.append(field(day.slice(0,3),c));return c;});
    const record={row,time,end,temp,area,checks,extra:old.extra||{}};forms.push(record);
    row.append(button('Remove',()=>{row.remove();forms.splice(forms.indexOf(record),1);}));list.append(row);
  }
  for(const r of original?nativeRows(original):config.schedule)addRow(r);
  host.append(button('Add period',()=>addRow()));
  const status=node('p');status.setAttribute('role','status');
  const save=button('Save schedule',async()=>{
    save.disabled=true;
    try{
      const rows=forms.map(f=>{
        if(!f.time.reportValidity()||!f.temp.reportValidity()||(original&&!f.end.reportValidity()))throw Error('Check each time and temperature.');
        const days=f.checks.flatMap((c,i)=>c.checked?[i]:[]);if(!days.length)throw Error('Select at least one day for each period.');
        return{time:f.time.value,end:f.end.value,temperature:Number(f.temp.value),area:f.area.value,days,extra:f.extra};
      });
      if(original){
        const current=(await hass.callWS({type:'schedule/list'})).find(i=>i.id===original.id);
        if(JSON.stringify(current)!==JSON.stringify(original))throw Error('Schedule changed elsewhere. Close and reopen the editor.');
        await hass.callWS(nativePayload(rows,original));
      }else await hass.callWS({type:'hive_virtual/save',entity_id:state.entity_id,revision:config.revision,schedule:rows.map(({end,extra,...r})=>r)});
      status.textContent='Schedule saved. Your current heating mode is retained.';host.dataset.saved='true';save.disabled=true;
    }catch(e){status.textContent=e.message||String(e);save.disabled=false;}
  });host.append(save,button('Close editor',collapse),status);
}
export async function costEditor(panel,host,state){
  const hass=panel._hass,config=await hass.callWS({type:'hive_virtual/config',entity_id:state.entity_id}),old=config.cost;
  host.replaceChildren();host.append(node('h3','Gas costs and estimates'));
  host.append(node('p','Learning uses Gas usage today by default. Choose a separate meter only if it updates more often during heating runs. Hot water or cooking during a run can affect the estimate.'));
  host.append(node('small','Use the delayed gas field for a daily total with timestamped half-hour readings. With learning enabled, delayed readings take priority over live meter learning.'));
  const sensors=Object.values(hass.states).filter(e=>e.entity_id.startsWith('sensor.'));
  const controls={};
  for(const[key,label]of Object.entries({gas_energy_today_entity:'Gas usage today (kWh, Wh, MWh or m³)',gas_cost_today_entity:'Existing gas cost today (optional)',unit_rate_entity:'Gas unit-rate sensor (per kWh)',standing_charge_entity:'Daily standing-charge sensor (shown separately)',gas_meter_entity:'Separate gas meter for learning (optional)',gas_reported_entity:'Delayed daily gas usage (optional)'})){
    const selected = old[key] || '';
    const options = costSensorOptions(sensors, key, selected);
    if (selected && !options.some(option => option.value === selected)) {
      options.push({value:selected, label:`${selected} (currently missing)`});
    }
    const {control,list}=sensorPicker(`${state.entity_id}-${key}`,options,selected);
    if (key === 'gas_meter_entity') control.placeholder = 'Leave blank to use Gas usage today';
    controls[key]=control;host.append(field(label,control),list);
  }
  for(const[key,label,max]of [['boiler_input_kw','Manual gas input power (kW; 0 leaves the estimate blank until learned)',100],['gas_kwh_per_m3','kWh per m³ conversion (only for a volume sensor; 0 disables conversion)',20]]){
    const c=input('number',old[key]||0);c.min=0;c.max=max;c.step=.01;controls[key]=c;host.append(field(label,c));
  }
  const baxi = 'https://www.baxi.co.uk/professional/products/boilers/combi-boilers/baxi-800-combi-2';
  const worcester = 'https://www.worcester-bosch.co.uk/support/literature/download/6720891161';
  const vaillant = 'https://professional.vaillant.co.uk/downloads/product-manuals/ecotec-plus-1/ecotec-plus-combi-and-system-installation-instructions-2422739.pdf';
  const glowWorm = 'https://www.glow-worm.co.uk/glow-worm/product-manuals/energy-3/combi-8/energy7-combi-installation-and-maintenance-instructions-1948938.pdf';
  const boilers = [
    ['Baxi 824 Combi 2', 4.9, 20.6, 'gross', baxi],
    ['Baxi 830 Combi 2', 6, 25.7, 'gross', baxi],
    ['Baxi 836 Combi 2', 7.2, 25.7, 'gross', baxi],
    ['Worcester Bosch Greenstar 4000 25/30 Combi (standard natural gas)', 3.2, 24.6, 'net', worcester],
    ['Vaillant ecoTEC plus 825 (VUW 196/5-5)', 4, 19.6, 'net', vaillant],
    ['Vaillant ecoTEC plus 832 (VUW 246/5-5)', 5.5, 24.7, 'net', vaillant],
    ['Glow-worm Energy7 25c', 5.1, 18.4, '', glowWorm],
    ['Glow-worm Energy7 30c', 6.1, 25.5, '', glowWorm],
    ['Glow-worm Energy7 35c', 7.2, 25.7, '', glowWorm],
  ];
  const preset = choice([{value:'',label:'Choose a model'}, ...boilers.map(([label], index) => ({value:String(index),label}))], '');
  const reference = node('small');
  host.append(field('Boiler reference presets (optional)',preset),reference);
  preset.onchange = () => {
    reference.replaceChildren();
    if (!preset.value) return;
    const [name,min,max,basis,url] = boilers[Number(preset.value)];
    const kw = ((min + max) / 2).toFixed(2);
    controls.boiler_input_kw.value = kw;
    const link = node('a', `${name} specifications`);
    link.href = url;
    const rating = basis ? ` (${basis})` : '';
    reference.append(`Published heating input ${min}–${max} kW${rating}. Filled in the midpoint, ${kw} kW, as an editable estimate. `, link);
  };
  controls.currency=input('text',old.currency||'GBP');controls.currency.pattern='[A-Z]{3}';host.append(field('Currency code (GBP, EUR, etc.)',controls.currency));
  for(const[key,label]of [['rate_in_pence','Unit-rate sensor uses pence/cents per kWh (divide by 100)'],['cost_in_pence','Cost and standing-charge sensors use pence/cents (divide by 100)'],['learn_from_gas_meter','Learn from gas meter']]){
    const c=input('checkbox','');c.checked=!!old[key];controls[key]=c;host.append(field(label,c));
  }
  const status=node('p');status.setAttribute('role','status');
  host.append(button('Save cost settings',async()=>{
    try{
      const cost={};for(const[key,c]of Object.entries(controls)){
        if(c.list){
          c.value=c.value.trim();
          c.setCustomValidity(!c.value||[...c.list.options].some(option=>option.value===c.value)?'':'Choose a sensor from the suggestions or clear the field.');
        }
        if(!c.reportValidity())throw Error('Check the highlighted cost settings.');
        if(c.type==='checkbox')cost[key]=c.checked;else if(c.type==='number')cost[key]=Number(c.value);else if(c.value)cost[key]=c.value;
      }
      await hass.callWS({type:'hive_virtual/save',entity_id:state.entity_id,revision:config.revision,cost});status.textContent='Cost settings saved. Close and reopen to make another change.';
    }catch(e){status.textContent=e.message||String(e);}
  }),button('Close editor',()=>host.replaceChildren()),status);
}
