// PV comparison: one local calendar day, explicit W units and real timestamps.
class PvComparisonCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity || !config.forecast_entity) throw new Error('entity and forecast_entity are required');
    this._config = config;
    this._showPrevious ??= true;
  }
  set hass(value) { this._hass = value; this._render(); }
  getCardSize() { return 6; }
  getGridOptions() { return { columns: 12, rows: 'auto' }; }
  _escape(v) { return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  _today() {
    const parts = new Intl.DateTimeFormat('en-CA', {timeZone:this._hass.config?.time_zone || 'UTC',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
    const get = t => parts.find(p => p.type === t).value;
    return `${get('year')}-${get('month')}-${get('day')}`;
  }
  _points(rows, date, future = false) {
    return (Array.isArray(rows) ? rows : []).filter(p => p.time?.slice(0,10) === date
      && typeof p.power_w === 'number' && Number.isFinite(p.power_w) && p.power_w >= 0
      && Number.isFinite(Date.parse(p.time)) && (future || Date.parse(p.time) <= Date.now())).map(p => ({
        hour:Number(p.time.slice(11,13)) + Number(p.time.slice(14,16))/60,
        value:p.power_w, label:p.time.slice(11,16), date
      })).sort((a,b) => a.hour-b.hour);
  }
  _path(points, x, y, gap) {
    return points.map((p,i) => `${!i || p.hour-points[i-1].hour > gap ? 'M':'L'}${x(p.hour)},${y(p.value)}`).join(' ');
  }
  _render() {
    if (!this._config || !this._hass) return;
    const c=this._config, esc=v=>this._escape(v), today=this._today();
    const actual=this._hass.states[c.entity]?.attributes || {};
    const fc=this._hass.states[c.forecast_entity]?.attributes || {};
    const facts=this._points(actual.points, today);
    const previous=actual.previous_day || {};
    const prior=this._showPrevious ? this._points(previous.points, previous.date, true) : [];
    const forecast=fc.hourly_forecast_date === today && Array.isArray(fc.hourly_forecast_w)
      ? fc.hourly_forecast_w.slice(0,24).flatMap((v,h)=> typeof v === 'number' && Number.isFinite(v) && v>=0
        ? [{hour:h,value:v,label:`${String(h).padStart(2,'0')}:00–${String(h+1).padStart(2,'0')}:00`,date:today}] : []) : [];
    const top=Math.max(100, Math.ceil(Math.max(...facts.map(p=>p.value),...prior.map(p=>p.value),...forecast.map(p=>p.value),0)/100)*100);
    const W=640,H=270,L=48,R=14,T=16,B=28, cw=W-L-R,ch=H-T-B;
    const x=h=>+(L+h/24*cw).toFixed(2), y=v=>+(T+ch-v/top*ch).toFixed(2);
    let grid='';
    for(let i=0;i<=4;i++) grid+=`<line x1="${L}" x2="${W-R}" y1="${y(top*i/4)}" y2="${y(top*i/4)}" class="grid"/><text x="${L-8}" y="${y(top*i/4)+4}" text-anchor="end">${top*i/4}</text>`;
    for(let h=0;h<=24;h+=3) grid+=`<text x="${x(h)}" y="${H-7}" text-anchor="middle">${String(h).padStart(2,'0')}:00</text>`;
    const numbers=new Intl.NumberFormat('uk-UA',{maximumFractionDigits:2});
    const tip=(name,p)=>`${name} · ${p.date} ${p.label} · ${numbers.format(p.value)} Вт`;
    const dots=(points,color,name)=>points.map(p=>`<circle cx="${x(p.hour)}" cy="${y(p.value)}" r="4" fill="${color}" tabindex="0" data-tip="${esc(tip(name,p))}"><title>${esc(tip(name,p))}</title></circle>`).join('');
    // Each forecast value estimates one hour. Draw its interval, not a
    // spline which invents intermediate peaks or below-zero night values.
    const steps=forecast.map((p,i)=>`${!i || p.hour!==forecast[i-1].hour+1?'M':'L'}${x(p.hour)},${y(p.value)} L${x(p.hour+1)},${y(p.value)}`).join(' ');
    const latest=facts.at(-1);
    this.innerHTML=`<ha-card>
      <style>
        ha-card{padding:20px;box-sizing:border-box;overflow:hidden;font-family:var(--paper-font-body1_-_font-family,Arial);color:var(--primary-text-color)}
        h2{margin:0;font-size:18px;line-height:1.4}.sub,.hint{color:var(--secondary-text-color);font-size:12px;line-height:1.5}.sub{margin:5px 0 16px}
        .stats{display:flex;gap:24px;flex-wrap:wrap;margin-bottom:15px}.value{font-size:25px;font-weight:600}.caption{font-size:12px;color:var(--secondary-text-color)}
        .legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;margin-bottom:10px}.legend span::before{content:'';display:inline-block;width:18px;border-top:3px solid var(--line);margin-right:6px;vertical-align:middle}
        svg{width:100%;height:auto;display:block;overflow:visible}svg text{font-size:11px;fill:var(--secondary-text-color)}.grid{stroke:var(--divider-color,#444);stroke-width:1}circle{cursor:pointer}circle:focus{outline:none;stroke:var(--primary-text-color);stroke-width:2}
        button{background:none;border:1px solid var(--divider-color,#555);border-radius:8px;color:var(--primary-text-color);padding:7px 10px;cursor:pointer;font:inherit;font-size:12px;margin-top:12px}.hint{margin-top:10px;min-height:36px}
        @media(max-width:450px){ha-card{padding:16px}.value{font-size:23px}}
      </style>
      <h2>${esc(c.title || 'Генерація та прогноз PV')}</h2>
      <div class="sub">${esc(today)} · час Home Assistant · спільна шкала у ватах</div>
      <div class="stats"><div><div class="value">${latest ? numbers.format(latest.value)+' Вт' : 'Немає даних'}</div><div class="caption">Факт API${latest?' · '+esc(latest.label):''}</div></div>
      <div><div class="value">${forecast.length && Number.isFinite(fc.total_kwh) ? numbers.format(fc.total_kwh)+' кВт·год' : 'Немає прогнозу'}</div><div class="caption">Прогноз за всю сьогоднішню добу</div></div></div>
      <div class="legend"><span style="--line:#2ecc71">Факт сьогодні</span><span style="--line:#36a9ff">Прогноз сьогодні · по годинах</span>${prior.length?`<span style="--line:#a3aab5">${esc(previous.date)} · середнє двох вимірів/год</span>`:''}</div>
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Фактична генерація та погодинний прогноз у ватах"><text x="${L}" y="10">Вт</text>${grid}
      ${prior.length?`<path d="${this._path(prior,x,y,1.01)}" stroke="#a3aab5" stroke-width="2" stroke-dasharray="3 5" fill="none"/>`:''}
      <path d="${steps}" stroke="#36a9ff" stroke-width="2" stroke-dasharray="7 4" fill="none"/>
      <path d="${this._path(facts,x,y,.51)}" stroke="#2ecc71" stroke-width="2.5" fill="none"/>
      ${dots(prior,'#a3aab5','Попередній день')}${dots(forecast,'#36a9ff','Прогноз')}${dots(facts,'#2ecc71','Факт')}
      </svg><button aria-pressed="${this._showPrevious}">${this._showPrevious?'Приховати':'Показати'} вчорашню генерацію</button>
      <div class="hint">Факт — серверні вимірювання кожні 30 хв. Прогноз — оцінка годинної потужності. Натисніть точку для деталей.</div>
    </ha-card>`;
    this.querySelector('button').onclick=()=>{this._showPrevious=!this._showPrevious;this._render();};
    this.querySelectorAll('[data-tip]').forEach(el=>{
      const show=()=>{this.querySelector('.hint').textContent=el.dataset.tip;};
      el.onclick=show; el.onfocus=show; el.onmouseenter=show;
    });
  }
}
if (!customElements.get('pv-comparison-card')) customElements.define('pv-comparison-card',PvComparisonCard);
window.customCards=window.customCards || [];
window.customCards.push({type:'pv-comparison-card',name:'PV actual and forecast',description:'Timestamped cloud measurements and hourly forecast on one W axis.'});
