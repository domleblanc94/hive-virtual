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
    </style><header><button id="menu" aria-label="Open sidebar">☰</button><img src="/hive_virtual/brand/icon.png" alt=""><h1>Hive Heating</h1></header><main><div id="controllers"></div>
    <section><h2>Temperature schedule</h2><p>Create a Schedule helper, then select it under this integration’s Configure → Schedule source.</p><p>Each time block needs Additional data with <b>temperature</b> (°C) and <b>area</b> (Upstairs or Downstairs). An uncovered block means no new heating requests.</p>
    <p><a href="/config/helpers">Open schedule helpers</a> · <a href="/config/integrations/integration/hive_virtual">Configure Hive Virtual Thermostat</a></p>
    <small>Active Hive boosts are left to expire. Off and sensor faults stop new requests. After checking a latched sensor fault, select Off, press Reset fault, then select Auto or Heat.</small></section><p id="error" role="alert"></p></main>`;
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
        card = document.createElement('section');
        card.innerHTML = `<h2></h2><p class="reading"></p><p class="status"></p><p class="fault"></p><label>Target °C <input type="number" min="7" max="30" step="0.5"></label><div class="actions"></div><p class="schedule"></p><div class="periods"></div>`;
        card.querySelector('input').onchange = e => this._call('climate','set_temperature',{entity_id:s.entity_id,temperature:Number(e.target.value)});
        for (const [label, mode] of [['Follow schedule','auto'],['Manual hold','heat'],['Off','off']]) {
          const b = document.createElement('button');b.textContent=label;b.onclick=()=>this._call('climate','set_hvac_mode',{entity_id:s.entity_id,hvac_mode:mode});card.querySelector('.actions').append(b);
        }
        const reset = document.createElement('button');reset.textContent='Reset fault';reset.onclick=()=>{
          const b=Object.values(this._hass.states).find(e=>e.entity_id.startsWith('button.') && e.attributes.thermostat_unique_id===s.attributes.controller_unique_id);
          if(b)this._call('button','press',{entity_id:b.entity_id});
        };card.querySelector('.actions').append(reset);
        root.append(card);this._cards.set(s.entity_id,card);
      }
      card.querySelector('h2').textContent=s.attributes.friendly_name || s.entity_id;
      card.querySelector('.reading').textContent=`${s.attributes.current_temperature ?? 'Unknown'} °C · ${s.attributes.active_area ?? 'Unknown area'} · ${s.state}`;
      const statusNames={controlling:'Monitoring room temperature',waiting_for_boost_expiry:'Waiting for boost expiry and cooldown',boost_active:'Hive boost active',boost_active_sensor_fault:'Hive boost active; sensor fault blocks further heating',starting_bounded_boost:'Starting a timed heating request',setting_safe_baseline:'Setting the low fallback temperature',waiting_for_hive_refresh:'Waiting for a fresh Hive update',sensor_fault:'Sensor readings need attention',control_error:'Hive could not be reached',hive_unavailable:'Hive is unavailable'};
      card.querySelector('.status').textContent=`Status: ${statusNames[s.attributes.control_status] || (s.attributes.control_status || 'Starting').replaceAll('_',' ')}`;
      card.querySelector('.fault').textContent=(s.attributes.fault_reason || '').replaceAll('_',' ');
      const input=card.querySelector('input');if(this.shadowRoot.activeElement!==input)input.value=s.attributes.temperature ?? '';
      card.querySelector('.schedule').textContent=s.attributes.schedule_entity ? `Schedule helper: ${s.attributes.schedule_entity}` : 'Using built-in weekly schedule';
      const periods=card.querySelector('.periods');periods.replaceChildren();
      for(const p of s.attributes.schedule_entity ? [] : (s.attributes.schedule || [])) {
        const row=document.createElement('p');row.textContent=`${p.time} · ${p.temperature} °C · ${p.area} · ${(p.days||[]).map(d=>['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][d]).join(', ')}`;periods.append(row);
      }
    }
  }
  async _call(domain, service, data) {
    const error=this.shadowRoot.getElementById('error');
    try {await this._hass.callService(domain,service,data);error.textContent='';}
    catch(e){error.textContent=e.message || String(e);}
  }
}
customElements.define('hive-virtual-panel', HiveVirtualPanel);
