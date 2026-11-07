// Local drafts are intentionally separate from inverter state. Rendering never
// sends a command; only an explicit Apply does. Confirmation requires telemetry.
class SmartSolarControlsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode:'open'});
    this._draft = {};
    this._pending = {};
    this._busy = false;
  }
  setConfig(config) {
    if(!Array.isArray(config.entities)) throw new Error('entities is required');
    const seen=new Set();
    for(const row of config.entities){
      if(!row || !/^(select|number|switch)\.[\w]+$/.test(row.entity) || seen.has(row.entity)) throw new Error('unique select, number or switch entities required');
      seen.add(row.entity);
    }
    this._config=config;
    this._built=false;
    if(this._hass) this._build();
  }
  set hass(hass) {
    this._hass=hass;
    if(!this._config) return;
    if(!this._built) this._build();
    for(const [id,pending] of Object.entries(this._pending)){
      if(pending.phase==='awaiting' && this._matches(id,pending.value)) pending.phase='confirmed';
    }
    this._updateStatus();
  }
  getCardSize(){return 5;}
  getGridOptions(){return {columns:12,rows:'auto'};}
  _escape(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  _current(id){return this._hass?.states?.[id];}
  _available(id){const s=this._current(id)?.state;return s!==undefined && !['unknown','unavailable'].includes(s);}
  _matches(id,value){
    if(!this._available(id)) return false;
    if(id.startsWith('number.')) return Math.abs(Number(this._current(id).state)-Number(value))<1e-8;
    return this._current(id).state===String(value);
  }
  _validate(id,value){
    if(!this._available(id)) throw new Error('Поточні налаштування ще не отримані');
    const attrs=this._current(id).attributes || {};
    if(id.startsWith('select.')){
      if(!attrs.options?.includes(value)) throw new Error('Ця опція недоступна');
      return value;
    }
    if(id.startsWith('switch.')){
      if(value!=='on' && value!=='off') throw new Error('Оберіть стан');
      return value;
    }
    if(String(value).trim()==='') throw new Error('Вкажіть число');
    const number=Number(value);
    if(!Number.isFinite(number)) throw new Error('Вкажіть скінченне число');
    if(attrs.min!==undefined && number<Number(attrs.min)) throw new Error(`Мінімум: ${attrs.min}`);
    if(attrs.max!==undefined && number>Number(attrs.max)) throw new Error(`Максимум: ${attrs.max}`);
    if(Number(attrs.step)>0){
      const steps=(number-Number(attrs.min || 0))/Number(attrs.step);
      if(Math.abs(steps-Math.round(steps))>1e-6) throw new Error(`Крок: ${attrs.step}`);
    }
    return number;
  }
  _changes(){
    const changes=[];
    for(const row of this._config.entities){
      const id=row.entity;
      if(!Object.prototype.hasOwnProperty.call(this._draft,id)) continue;
      const value=this._validate(id,this._draft[id]);
      const pending=this._pending[id];
      if(this._matches(id,value) || (pending?.phase==='awaiting' && String(pending.value)===String(value))) continue;
      changes.push({id,value});
    }
    return changes;
  }
  async _apply(){
    if(this._busy) return;
    let changes;
    try{changes=this._changes();}catch(error){this._message=error.message;this._updateStatus();return;}
    this._busy=true;
    this._message=changes.length?'Надсилання змін…':'Нових змін немає';
    this._updateStatus();
    try{
      for(const {id,value} of changes){
        this._pending[id]={value,phase:'sending'};
        this._updateStatus();
        try{
          const domain=id.split('.')[0];
          if(domain==='select') await this._hass.callService('select','select_option',{entity_id:id,option:value});
          else if(domain==='number') await this._hass.callService('number','set_value',{entity_id:id,value});
          else await this._hass.callService('switch',value==='on'?'turn_on':'turn_off',{entity_id:id});
          this._pending[id].phase=this._matches(id,value)?'confirmed':'awaiting';
        }catch(error){this._pending[id]={value,phase:'failed',error:String(error.message || error)};}
      }
      if(changes.length) this._message='Команди оброблено. Фактичні значення підтверджуються наступною телеметрією.';
    }finally{this._busy=false;this._updateStatus();}
  }
  _build(){
    const esc=v=>this._escape(v);
    const fields=this._config.entities.map(row=>{
      const id=row.entity,state=this._current(id),attrs=state?.attributes || {};
      const value=this._draft[id] ?? state?.state ?? '';
      let input;
      if(id.startsWith('select.')) input=`<select data-entity="${esc(id)}"><option value="" ${!this._available(id)?'selected':''} disabled>Оберіть значення</option>${(attrs.options || []).map(v=>`<option value="${esc(v)}" ${String(v)===String(value)?'selected':''}>${esc(v)}</option>`).join('')}</select>`;
      else if(id.startsWith('switch.')) input=`<select data-entity="${esc(id)}"><option value="" disabled ${!['on','off'].includes(value)?'selected':''}>Оберіть стан</option><option value="off" ${value==='off'?'selected':''}>Вимкнено</option><option value="on" ${value==='on'?'selected':''}>Увімкнено</option></select>`;
      else input=`<input data-entity="${esc(id)}" type="number" value="${esc(this._available(id)?value:'')}" ${attrs.min!==undefined?`min="${esc(attrs.min)}"`:''} ${attrs.max!==undefined?`max="${esc(attrs.max)}"`:''} step="${esc(attrs.step || 'any')}" inputmode="decimal">`;
      return `<label><span>${esc(row.name || attrs.friendly_name || id)}${attrs.unit_of_measurement?` · ${esc(attrs.unit_of_measurement)}`:''}</span>${input}<small data-current="${esc(id)}"></small><small data-status="${esc(id)}"></small></label>`;
    }).join('');
    this.shadowRoot.innerHTML=`<style>
      :host{display:block;min-width:0;color:var(--primary-text-color);font-family:var(--primary-font-family,Roboto,sans-serif)}
      ha-card{display:block;background:var(--ha-card-background,var(--card-background-color));border-radius:var(--ha-card-border-radius,12px);padding:16px;border:1px solid var(--divider-color)}h2{font-size:18px;margin:0 0 8px}p{font-size:13px;color:var(--secondary-text-color);line-height:1.5;margin:0 0 16px}.fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:16px}label{display:flex;flex-direction:column;gap:6px;min-width:0;font-size:14px}input,select,button{font:inherit;color:var(--primary-text-color);background:var(--secondary-background-color);border:1px solid var(--divider-color);border-radius:8px;min-height:44px;padding:9px 12px;box-sizing:border-box;min-width:0;width:100%}input:focus-visible,select:focus-visible,button:focus-visible{outline:2px solid var(--primary-color);outline-offset:2px}small{font-size:12px;color:var(--secondary-text-color);overflow-wrap:anywhere}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:20px}.actions button{width:auto;cursor:pointer}button.primary{background:var(--primary-color);color:var(--text-primary-color,#fff);border-color:transparent}button:disabled{opacity:.5;cursor:default}.message{margin-top:12px;font-size:13px;line-height:1.5}
      </style><ha-card><h2>${esc(this._config.title || 'Ручне керування')}</h2><p>Відредагуйте потрібні поля й натисніть «Застосувати». HEMS може змінювати пріоритети відповідно до своєї стратегії.</p><div class="fields">${fields}</div><div class="actions"><button class="primary" data-action="apply">Застосувати</button><button data-action="reset">Відкинути чернетку</button></div><div class="message" role="status" aria-live="polite"></div></ha-card>`;
    for(const input of this.shadowRoot.querySelectorAll('[data-entity]')) input.addEventListener('input',()=>{this._draft[input.dataset.entity]=input.value;this._message='Є незастосовані зміни';this._updateStatus();});
    this.shadowRoot.querySelector('[data-action="apply"]').onclick=()=>this._apply();
    this.shadowRoot.querySelector('[data-action="reset"]').onclick=()=>{this._draft={};this._message='Чернетку відкинуто';this._build();this._updateStatus();};
    this._built=true;
    this._updateStatus();
  }
  _updateStatus(){
    if(!this._built) return;
    for(const row of this._config.entities){
      const id=row.entity;
      const input=[...this.shadowRoot.querySelectorAll('[data-entity]')].find(e=>e.dataset.entity===id);
      this._syncConstraints(input,id);
      input.disabled=this._busy || !this._available(id);
      if(!Object.prototype.hasOwnProperty.call(this._draft,id) && this._available(id)) input.value=this._current(id).state;
      const current=[...this.shadowRoot.querySelectorAll('[data-current]')].find(e=>e.dataset.current===id);
      current.textContent=this._available(id)?`Поточне: ${this._current(id).state}`:'Ще немає поточного значення';
      const status=[...this.shadowRoot.querySelectorAll('[data-status]')].find(e=>e.dataset.status===id);
      const pending=this._pending[id];
      status.textContent=!pending?'':pending.phase==='sending'?'Надсилається…':pending.phase==='awaiting'?'Надіслано · очікуємо підтвердження':pending.phase==='confirmed'?'Підтверджено телеметрією':`Помилка: ${pending.error}`;
    }
    for(const button of this.shadowRoot.querySelectorAll('button')) button.disabled=this._busy;
    this.shadowRoot.querySelector('.message').textContent=this._message || '';
  }
  _syncConstraints(input,id){
    const attrs=this._current(id)?.attributes || {};
    if(id.startsWith('number.')){
      for(const key of ['min','max','step']){
        const value=attrs[key];
        if(value!==undefined && value!==null) input.setAttribute(key,String(value));
        else if(key==='step') input.setAttribute('step','any');
        else input.removeAttribute(key);
      }
      return;
    }
    const choices=id.startsWith('switch.')?['off','on']:(Array.isArray(attrs.options)?attrs.options.map(String):[]);
    const hasDraft=Object.prototype.hasOwnProperty.call(this._draft,id);
    const selected=hasDraft?String(this._draft[id]):String(this._current(id)?.state ?? '');
    // Include a disabled draft option if a previously selected choice disappears.
    // It remains visible and validation refuses it rather than silently changing
    // the operator's draft to a different inverter mode.
    const orphaned=hasDraft && selected!=='' && !choices.includes(selected);
    const signature=JSON.stringify([choices,orphaned?selected:null]);
    if(input.dataset.optionsSignature===signature) return;
    const make=(value,label,disabled=false)=>{
      const option=input.ownerDocument.createElement('option');
      option.value=value;option.textContent=label;option.disabled=disabled;
      return option;
    };
    const options=[make('','Оберіть значення',true),...choices.map(value=>make(value,id.startsWith('switch.')?(value==='on'?'Увімкнено':'Вимкнено'):value))];
    if(orphaned) options.push(make(selected,`${selected} (недоступно)`,true));
    // Replace only option children: the select node and keyboard focus survive.
    input.replaceChildren(...options);
    input.value=selected;
    input.dataset.optionsSignature=signature;
  }
}
if(!customElements.get('smart-solar-controls-card')) customElements.define('smart-solar-controls-card',SmartSolarControlsCard);
window.customCards=window.customCards || [];
if(!window.customCards.some(c=>c.type==='smart-solar-controls-card'))window.customCards.push({type:'smart-solar-controls-card',name:'Smart Solar manual controls',description:'Explicit Apply with telemetry confirmation.'});
