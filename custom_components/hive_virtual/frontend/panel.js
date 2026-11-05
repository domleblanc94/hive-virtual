import {activityChart, temperatureChart} from './charts.js';
import {scheduleEditor, costEditor} from './editors.js';

class HiveVirtualPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'});
    this.shadowRoot.innerHTML = `<style>
      :host{display:block;color:var(--primary-text-color);background:var(--primary-background-color);min-height:100vh;font:16px var(--paper-font-body1_-_font-family, sans-serif)}
      header{display:flex;align-items:center;gap:16px;padding:16px;background:var(--app-header-background-color);color:var(--app-header-text-color)}
      header img{width:40px;height:40px;object-fit:contain}main{max-width:900px;margin:auto;padding:24px}section{padding:24px;margin:0 0 20px;border:1px solid var(--divider-color);border-radius:16px;background:var(--card-background-color)}
      button,input{font:inherit;padding:10px;margin:6px;border:1px solid var(--divider-color);border-radius:8px;background:var(--card-background-color);color:var(--primary-text-color)}button{cursor:pointer}a{color:var(--primary-color)}
      .fault{color:var(--error-color);font-weight:bold}small{display:block;line-height:1.5}.actions{display:flex;flex-wrap:wrap;gap:6px}h1{font-size:22px;margin:0}h2{margin-top:0}
      select{font:inherit;padding:9px;margin:6px;max-width:100%;border:1px solid var(--divider-color);border-radius:8px;background:var(--card-background-color);color:var(--primary-text-color)}
      .schedule-row{display:flex;flex-wrap:wrap;align-items:end;border-bottom:1px solid var(--divider-color);padding:12px 0;gap:8px}label{display:inline-flex;flex-direction:column;margin:4px;gap:4px}input[type=checkbox]{width:20px;height:20px}input[type=number]{width:90px}.editor{margin:14px 0}.editor>label{display:flex;max-width:100%}.chart svg{width:100%;height:auto;min-height:65px}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:16px 0}.metric{background:var(--primary-background-color);padding:15px;border-radius:10px}.metric b{display:block;font-size:22px;margin-top:6px}.history-error{font-size:14px}
      .thermostat{padding-bottom:16px}.reading{font-size:1.4rem;margin:12px 0}.fault:empty{display:none}.tabs{display:flex;flex-wrap:wrap;gap:4px;margin:0 0 16px}.tabs button{margin:0}.tabs button[aria-pressed=true],.actions button[aria-pressed=true]{border-color:var(--primary-color);color:var(--primary-color);font-weight:bold}.sensor-picker{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,2fr);align-items:end;border-bottom:1px solid var(--divider-color);padding:8px 0}.sensor-picker input,.sensor-picker select{min-width:0}.controller{margin-bottom:28px}
      @media(max-width:600px){main{padding:12px}section{padding:16px}.sensor-picker{grid-template-columns:minmax(0,1fr)}}
    </style><header><button id="menu" aria-label="Open sidebar">☰</button><img src="/hive_virtual/brand/icon.png" alt=""><h1>Hive Heating</h1></header><main><div id="controllers"></div>
    <p id="error" role="alert"></p></main>`;
    this.shadowRoot.getElementById('menu').onclick = () => this.dispatchEvent(new Event('hass-toggle-menu', {bubbles:true,composed:true}));
    this._cards = new Map();
  }
  set hass(hass) {
    this._hass = hass;
    const entities = Object.values(hass.states).filter(s => s.entity_id.startsWith('climate.') && s.attributes.hive_virtual_controller);
    const root = this.shadowRoot.getElementById('controllers');
    for (const [id, card] of this._cards) if (!entities.some(s => s.entity_id === id)) {card.remove();this._cards.delete(id);}
    for (const s of entities) {
      let card = this._cards.get(s.entity_id);
      if (!card) {
        card = document.createElement('article');
        card.className = 'controller';
        card.innerHTML = `
          <section class="thermostat"><h2></h2><p class="reading"></p><p class="status"></p><p class="fault"></p>
            <label>Target °C <input class="target" type="number" min="7" max="30" step="0.5" required></label>
            <div class="actions"></div><small class="fallback"></small>
          </section>
          <nav class="tabs" aria-label="Heating views">
            <button data-view="schedule" aria-pressed="true">Schedule</button>
            <button data-view="history" aria-pressed="false">History</button>
            <button data-view="costs" aria-pressed="false">Gas &amp; costs</button>
          </nav>
          <section data-pane="schedule"><h3>Temperature and area schedule</h3><p class="schedule"></p>
            <small>Choose an area for each period to control when heating switches between upstairs and downstairs. Select Follow schedule to use those periods.</small>
            <div class="periods"></div><div class="editor"></div>
            <a href="/config/integrations/integration/hive_virtual">Integration settings and schedule source</a>
          </section>
          <section data-pane="history" hidden><h3>Heating and house temperatures</h3>
            <label>History <select class="range"><option value="24">24 hours</option><option value="48">48 hours</option><option value="168">7 days</option></select></label>
            <button class="refresh">Refresh graphs</button><p class="history-error"></p><div class="activity chart"></div>
            <small>Amber is heating reported on, pale teal is off, and grey is unavailable or missing history. Hover to see interval times.</small>
            <div class="temperatures chart"></div>
          </section>
          <section data-pane="costs" hidden><h3>Today’s usage and cost estimates</h3><div class="metrics"></div><p class="cost-basis"></p>
            <small>Heating-only estimates use recorded heating hours, configured gas input power and the current tariff. Whole-house gas may include hot water and cooking. Standing charges are shown separately.</small>
            <div class="editor"></div>
          </section>`;
        card.querySelector('.target').onchange = ({target}) => {
          if (target.reportValidity()) this._call('climate','set_temperature',{entity_id:s.entity_id,temperature:target.valueAsNumber});
        };
        for (const tab of card.querySelectorAll('.tabs button')) tab.onclick = () => {
          for (const button of card.querySelectorAll('.tabs button')) button.setAttribute('aria-pressed', button === tab);
          for (const pane of card.querySelectorAll('[data-pane]')) pane.hidden = pane.dataset.pane !== tab.dataset.view;
          if (tab.dataset.view === 'history') this._history(card, s);
        };
        for (const [label, mode] of [['Follow schedule','auto'],['Manual hold','heat'],['Off','off']]) {
          const b = document.createElement('button');b.textContent=label;b.dataset.mode=mode;b.onclick=()=>this._call('climate','set_hvac_mode',{entity_id:s.entity_id,hvac_mode:mode});card.querySelector('.actions').append(b);
        }
        const reset = document.createElement('button');reset.textContent='Reset fault';reset.onclick=()=>{
          const b=Object.values(this._hass.states).find(e=>e.entity_id.startsWith('button.') && e.attributes.thermostat_unique_id===s.attributes.controller_unique_id);
          if(b)this._call('button','press',{entity_id:b.entity_id});
        };card.querySelector('.actions').append(reset);
        for(const[view,label,editor]of [['schedule','Edit schedule',scheduleEditor],['costs','Configure costs',costEditor]]){
          const pane=card.querySelector(`[data-pane="${view}"]`);
          const b=document.createElement('button');b.textContent=label;b.dataset.admin='true';b.disabled=!hass.user?.is_admin;
          const host=pane.querySelector('.editor');
          b.onclick=()=>editor(this,host,s).catch(e=>this._error(e));host.before(b);
        }
        card.querySelector('.range').onchange=()=>this._history(card,s,true);
        card.querySelector('.refresh').onclick=()=>this._history(card,s,true);
        root.append(card);this._cards.set(s.entity_id,card);
      }
      card.querySelector('h2').textContent=s.attributes.friendly_name || s.entity_id;
      for(const b of card.querySelectorAll('[data-mode]'))b.setAttribute('aria-pressed',b.dataset.mode===s.state);
      for(const b of card.querySelectorAll('button[data-admin]'))b.disabled=!hass.user?.is_admin;
      card.querySelector('.reading').textContent=`${s.attributes.current_temperature ?? 'Unknown'} °C · ${s.attributes.active_area ?? 'Unknown area'}`;
      card.querySelector('.fallback').textContent=s.attributes.preserve_hive_settings?'Hive controls heating at its own settings between boosts. Off stops virtual heating requests.':'Off stops virtual heating requests. Active Hive boosts are left to expire.';
      const statusNames={controlling:'Monitoring room temperature',waiting_for_boost_expiry:'Waiting for boost expiry and cooldown',boost_active:'Hive boost active',boost_active_sensor_fault:'Hive boost active; sensor fault blocks further heating',starting_bounded_boost:'Starting a timed heating request',setting_safe_baseline:'Setting the low fallback temperature',waiting_for_hive_refresh:'Waiting for a fresh Hive update',sensor_fault:'Sensor readings need attention',control_error:'Hive could not be reached',hive_unavailable:'Hive is unavailable'};
      card.querySelector('.status').textContent=`Status: ${statusNames[s.attributes.control_status] || (s.attributes.control_status || 'Starting').replaceAll('_',' ')}`;
      card.querySelector('.fault').textContent=(s.attributes.fault_reason || '').replaceAll('_',' ');
      const input=card.querySelector('.target');if(this.shadowRoot.activeElement!==input)input.value=s.attributes.temperature ?? '';
      card.querySelector('.schedule').textContent=s.attributes.schedule_entity ? `Schedule helper: ${s.attributes.schedule_entity}` : 'Using built-in weekly schedule';
      const periods=card.querySelector('.periods');periods.replaceChildren();
      for(const p of s.attributes.schedule_entity ? [] : (s.attributes.schedule || [])) {
        const row=document.createElement('p');row.textContent=`${p.time} · ${p.temperature} °C · ${p.area} · ${(p.days||[]).map(d=>['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][d]).join(', ')}`;periods.append(row);
      }
      this._metrics(card,s);
      if (!card.querySelector('[data-pane="history"]').hidden) this._history(card,s);
    }
  }
  _error(e){this.shadowRoot.getElementById('error').textContent=e.message||String(e);}
  _metrics(card,s){
    const values=s.attributes.cost_estimates||{},source=s.attributes.cost_sources||{},currency=s.attributes.currency||'GBP',root=card.querySelector('.metrics');root.replaceChildren();
    const missing=configured=>configured?'Unavailable':'Not configured';
    const money=(v,configured)=>{if(v===null||v===undefined)return missing(configured);try{return new Intl.NumberFormat(undefined,{style:'currency',currency}).format(v);}catch{return `${v.toFixed(2)} ${currency}`;}};
    for(const[label,value]of [['Reported heating hours',`${Number(s.attributes.daily_heating_hours||0).toFixed(2)} h`],['Estimated heating-only cost',money(values.heating_cost,source.boiler_input_kw>0&&source.unit_rate_entity)],['Whole-house gas usage',values.household_gas_kwh==null?missing(source.gas_energy_today_entity):`${values.household_gas_kwh.toFixed(2)} kWh`],['Whole-house gas cost',money(values.household_variable_cost,source.gas_cost_today_entity||(source.gas_energy_today_entity&&source.unit_rate_entity))],['Standing charge (separate)',money(values.standing_charge,source.standing_charge_entity)]]){
      const e=document.createElement('div');e.className='metric';e.textContent=label;const b=document.createElement('b');b.textContent=value;e.append(b);root.append(e);
    }
    card.querySelector('.cost-basis').textContent=s.attributes.cost_sources?.gas_cost_today_entity?'Household cost is the selected daily cost sensor, as reported; it may already include standing charges.':'Household cost is daily gas usage × the current unit rate, excluding standing charges. Changing tariffs reprice estimates; these are not a bill.';
  }
  async _history(card,state,force=false){
    if(card._historyLoading){if(force)card._historyQueued=true;return;}
    if(!force&&Date.now()-(card._historyAt||0)<60000)return;
    card._historyLoading=true;card._historyAt=Date.now();
    try{
      const linked=Object.values(this._hass.states).filter(e=>e.attributes.controller_unique_id===state.attributes.controller_unique_id);
      const activity=linked.find(e=>e.attributes.heating_role==='activity');
      const temperatures=linked.filter(e=>['area_temperature','target_temperature'].includes(e.attributes.heating_role));
      if(!activity&&!temperatures.length)throw Error('History sensors are starting. Restart after updating and allow readings to be recorded.');
      const end=Date.now(),start=end-Number(card.querySelector('.range').value)*3600000,ids=[activity,...temperatures].filter(Boolean).map(e=>e.entity_id);
      const response=await this._hass.callApi('GET',`history/period/${new Date(start).toISOString()}?end_time=${new Date(end).toISOString()}&filter_entity_id=${encodeURIComponent(ids.join(','))}&minimal_response`);
      const groups=new Map(response.filter(a=>a.length).map(a=>[a[0].entity_id,a]));
      const activityStates=activity?groups.get(activity.entity_id)||[]:[];
      card.querySelector('.activity').replaceChildren(activityChart(activityStates,start,end));
      card.querySelector('.temperatures').replaceChildren(temperatureChart(temperatures.map(e=>({name:e.attributes.area||(e.attributes.heating_role==='target_temperature'?'Target':e.attributes.friendly_name),states:groups.get(e.entity_id)||[]})),start,end));
      card.querySelector('.history-error').textContent=activityStates.length?'Recorded state, not a physical flame sensor. History depends on Recorder retention and exclusions.':'No recorded heating activity in this range. Enable Recorder/History and wait for new readings.';
    }catch(e){card.querySelector('.history-error').textContent=`Graphs unavailable: ${e.message||e}. Home Assistant History and Recorder must be enabled.`;}
    finally{card._historyLoading=false;if(card._historyQueued){card._historyQueued=false;this._history(card,state,true);}}
  }
  async _call(domain, service, data) {
    const error=this.shadowRoot.getElementById('error');
    try {await this._hass.callService(domain,service,data);error.textContent='';}
    catch(e){error.textContent=e.message || String(e);}
  }
}
customElements.define('hive-virtual-panel', HiveVirtualPanel);
