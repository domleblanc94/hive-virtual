export const DAYS=['monday','tuesday','wednesday','thursday','friday','saturday','sunday'];
function node(tag,text){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;}
function input(type,value){const e=node('input');e.type=type;e.value=value;return e;}
function choice(values,value){const e=node('select');for(const v of values){const o=node('option',typeof v==='string'?v:v.label);o.value=typeof v==='string'?v:v.value;e.append(o);}e.value=value;return e;}
function field(label,control){const e=node('label');e.append(node('span',label),control);return e;}
function button(label,action){const b=node('button',label);b.type='button';b.onclick=action;return b;}
function sensorPicker(label, options, selected) {
  const search = input('search', '');
  search.placeholder = 'Name or entity ID';
  const select = choice(options, selected);
  search.oninput = () => {
    const query = search.value.trim().toLowerCase();
    const current = select.value;
    const matches = options.filter(({value, label}) => !value || value === current ||
      `${label} ${value}`.toLowerCase().includes(query));
    select.replaceChildren(...choice(matches, current).options);
    select.value = current;
  };
  select.onchange = search.oninput;
  const group = node('div');
  group.className = 'sensor-picker';
  group.append(field(`Search ${label.toLowerCase()}`, search), field(label, select));
  return {group, select};
}
export function nativeRows(item){return DAYS.flatMap((day,i)=>(item[day]||[]).map(block=>({time:block.from.slice(0,5),end:block.to.slice(0,5),temperature:block.data?.temperature,area:block.data?.area,days:[i],extra:{...block.data}})));}
export function nativePayload(rows,item){
  const result={type:'schedule/update',schedule_id:item.id,name:item.name};if(item.icon)result.icon=item.icon;
  for(const day of DAYS)result[day]=[];
  for(const r of rows)for(const d of r.days)result[DAYS[d]].push({from:r.time+':00',to:r.end+':00',data:{...r.extra,temperature:r.temperature,area:r.area}});
  return result;
}
export async function scheduleEditor(panel,host,state){
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
      status.textContent='Schedule saved. Your current heating mode is retained.';save.disabled=true;
    }catch(e){status.textContent=e.message||String(e);save.disabled=false;}
  });host.append(save,button('Close editor',()=>host.replaceChildren()),status);
}
export async function costEditor(panel,host,state){
  const hass=panel._hass,config=await hass.callWS({type:'hive_virtual/config',entity_id:state.entity_id}),old=config.cost;
  host.replaceChildren();host.append(node('h3','Gas costs and estimates'));
  host.append(node('p','Use daily sensors for whole-house usage and costs. For learning, choose a cumulative gas meter that updates during heating runs. Gas used for hot water or cooking during those runs can affect the estimate.'));
  const sensors=[{value:'',label:'Not configured'},...Object.values(hass.states).filter(e=>e.entity_id.startsWith('sensor.')).map(e=>({value:e.entity_id,label:`${e.attributes.friendly_name||e.entity_id} (${e.state} ${e.attributes.unit_of_measurement||''})`}))];
  const controls={};
  for(const[key,label]of Object.entries({gas_energy_today_entity:'Gas usage today (kWh, Wh, MWh or m³)',gas_cost_today_entity:'Existing gas cost today (optional)',unit_rate_entity:'Gas unit-rate sensor (per kWh)',standing_charge_entity:'Daily standing-charge sensor (shown separately)',gas_meter_entity:'Cumulative gas meter for learning (kWh, Wh, MWh or m³)'})){
    const options=old[key]&&!sensors.some(s=>s.value===old[key])?[...sensors,{value:old[key],label:`${old[key]} (currently missing)`}]:sensors;
    const {group,select}=sensorPicker(label,options,old[key]||'');
    controls[key]=select;host.append(group);
  }
  for(const[key,label,max]of [['boiler_input_kw','Manual gas input power (kW; 0 leaves the estimate blank until learned)',100],['gas_kwh_per_m3','kWh per m³ conversion (only for a volume sensor; 0 disables conversion)',20]]){
    const c=input('number',old[key]||0);c.min=0;c.max=max;c.step=.01;controls[key]=c;host.append(field(label,c));
  }
  controls.currency=input('text',old.currency||'GBP');controls.currency.pattern='[A-Z]{3}';host.append(field('Currency code (GBP, EUR, etc.)',controls.currency));
  for(const[key,label]of [['rate_in_pence','Unit-rate sensor uses pence/cents per kWh (divide by 100)'],['cost_in_pence','Cost and standing-charge sensors use pence/cents (divide by 100)'],['learn_from_gas_meter','Learn from gas meter']]){
    const c=input('checkbox','');c.checked=!!old[key];controls[key]=c;host.append(field(label,c));
  }
  const status=node('p');status.setAttribute('role','status');
  host.append(button('Save cost settings',async()=>{
    try{
      const cost={};for(const[key,c]of Object.entries(controls)){
        if(!c.reportValidity())throw Error('Check the highlighted cost settings.');
        if(c.type==='checkbox')cost[key]=c.checked;else if(c.type==='number')cost[key]=Number(c.value);else if(c.value)cost[key]=c.value;
      }
      await hass.callWS({type:'hive_virtual/save',entity_id:state.entity_id,revision:config.revision,cost});status.textContent='Cost settings saved. Close and reopen to make another change.';
    }catch(e){status.textContent=e.message||String(e);}
  }),button('Close editor',()=>host.replaceChildren()),status);
}
