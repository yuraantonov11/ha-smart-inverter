// PV comparison: one local calendar day, explicit W units and real timestamps.
class PvComparisonCard extends HTMLElement {
  connectedCallback() {
    if(typeof ResizeObserver === 'undefined')return;
    this._resizeObserver=new ResizeObserver(entries=>{
      const width=entries[0]?.contentRect.width;
      if(width && width!==this._lastWidth){this._lastWidth=width;this._render();}
    });
    this._resizeObserver.observe(this);
  }
  disconnectedCallback(){this._resizeObserver?.disconnect();}
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
  _smoothPath(points, x, y, gap) {
    // Monotone cubic interpolation preserves every observed point and
    // stays between neighbouring values. Missing intervals remain gaps.
    const groups=[];
    for(const p of points) {
      const group=groups.at(-1), last=group?.at(-1);
      if(!last || p.hour-last.hour>gap || p.hour<=last.hour) groups.push([p]);
      else group.push(p);
    }
    return groups.map(ps=>{
      let path=`M${x(ps[0].hour)},${y(ps[0].value)}`;
      if(ps.length<2)return path;
      const slopes=ps.slice(1).map((p,i)=>(p.value-ps[i].value)/(p.hour-ps[i].hour));
      const tangents=ps.map((p,i)=>!i?slopes[0]:i===ps.length-1?slopes.at(-1):
        slopes[i-1]*slopes[i]<=0?0:2*slopes[i-1]*slopes[i]/(slopes[i-1]+slopes[i]));
      for(let i=0;i<ps.length-1;i++) {
        const a=ps[i],b=ps[i+1],dx=(b.hour-a.hour)/3;
        path+=` C${x(a.hour+dx)},${y(a.value+tangents[i]*dx)} ${x(b.hour-dx)},${y(b.value-tangents[i+1]*dx)} ${x(b.hour)},${y(b.value)}`;
      }
      return path;
    }).join(' ');
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
        ? [{hour:h+.5,value:v,label:`${String(h).padStart(2,'0')}:00–${String(h+1).padStart(2,'0')}:00`,date:today}] : []) : [];
    const top=Math.max(100, Math.ceil(Math.max(...facts.map(p=>p.value),...prior.map(p=>p.value),...forecast.map(p=>p.value),0)/100)*100);
    const W=Math.max(420,Math.min(1200,this.clientWidth?this.clientWidth-40:820)),H=320,L=48,R=16,T=20,B=28, cw=W-L-R,ch=H-T-B;
    const x=h=>+(L+h/24*cw).toFixed(2), y=v=>+(T+ch-v/top*ch).toFixed(2);
    let grid='';
    for(let i=0;i<=5;i++) grid+=`<line x1="${L}" x2="${W-R}" y1="${y(top*i/5)}" y2="${y(top*i/5)}" class="grid"/><text x="${L-8}" y="${y(top*i/5)+4}" text-anchor="end">${top*i/5}</text>`;
    for(let h=0;h<=24;h+=W>=900?1:W>=600?2:3) grid+=`<text x="${x(h)}" y="${H-7}" text-anchor="middle">${String(h).padStart(2,'0')}:00</text>`;
    const numbers=new Intl.NumberFormat('uk-UA',{maximumFractionDigits:2});
    const tip=(name,p)=>`${name} · ${p.date} ${p.label} · ${numbers.format(p.value)} Вт`;
    const dots=(points,color,name)=>points.map(p=>`<circle class="point" cx="${x(p.hour)}" cy="${y(p.value)}" r="3" fill="${color}" tabindex="0" data-tip="${esc(tip(name,p))}"><title>${esc(tip(name,p))}</title></circle>`).join('');
    const forecastPath=this._smoothPath(forecast,x,y,1.01);
    const actualPath=this._smoothPath(facts,x,y,.51);
    const validation=fc.hourly_response?.validation;
    const latest=facts.at(-1);
    this.innerHTML=`<ha-card>
      <style>
        ha-card{padding:20px;box-sizing:border-box;overflow:hidden;font-family:var(--paper-font-body1_-_font-family,Arial);color:var(--primary-text-color)}
        h2{margin:0;font-size:18px;line-height:1.4}.sub,.hint{color:var(--secondary-text-color);font-size:12px;line-height:1.5}.sub{margin:5px 0 16px}
        .stats{display:flex;gap:24px;flex-wrap:wrap;margin-bottom:15px}.value{font-size:25px;font-weight:600}.caption{font-size:12px;color:var(--secondary-text-color)}
        .legend{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;margin-bottom:16px}.legend span::before{content:'';display:inline-block;width:18px;border-top:2px solid var(--line);margin-right:6px;vertical-align:middle}
        svg{width:100%;height:auto;min-height:240px;display:block;overflow:visible}svg text{font-size:11px;fill:var(--secondary-text-color)}.grid{stroke:var(--divider-color,#444);stroke-width:.7;stroke-dasharray:2 5;opacity:.65}.point{cursor:pointer;opacity:.4}.point:hover,.point:focus{opacity:1;outline:none;stroke:var(--primary-text-color);stroke-width:2}
        button{background:none;border:1px solid var(--divider-color,#555);border-radius:8px;color:var(--primary-text-color);padding:7px 10px;cursor:pointer;font:inherit;font-size:12px;margin-top:12px}.hint{margin-top:10px;min-height:36px}
        @media(max-width:450px){ha-card{padding:16px}.value{font-size:23px}}
      </style>
      <h2>${esc(c.title || 'Генерація та прогноз PV')}</h2>
      <div class="sub">${esc(today)} · ${esc(this._hass.config?.time_zone || 'UTC')} · потужність PV</div>
      <div class="stats"><div><div class="value">${latest ? numbers.format(latest.value)+' Вт' : 'Немає даних'}</div><div class="caption">Факт API${latest?' · '+esc(latest.label):''}</div></div>
      <div><div class="value">${forecast.length && Number.isFinite(fc.total_kwh) ? numbers.format(fc.total_kwh)+' кВт·год' : 'Немає прогнозу'}</div><div class="caption">Прогноз за всю сьогоднішню добу</div></div></div>
      <div class="legend"><span style="--line:#2ecc71">Факт сьогодні</span><span style="--line:#36a9ff">Прогноз сьогодні</span>${prior.length?`<span style="--line:#a3aab5">Учора · ${esc(previous.date)} · ${previous.basis==='cloud_half_hour_samples'?'факт API':'погодинне середнє'}</span>`:''}</div>
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Фактична генерація та погодинний прогноз у ватах"><text x="${L}" y="10">Вт</text>${grid}
      <defs><linearGradient id="pv-area" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#36a9ff" stop-opacity=".16"/><stop offset="100%" stop-color="#36a9ff" stop-opacity="0"/></linearGradient></defs>
      ${forecast.length===24?`<path d="${forecastPath} L${x(forecast.at(-1).hour)},${y(0)} L${x(forecast[0].hour)},${y(0)} Z" fill="url(#pv-area)"/>`:''}
      ${prior.length?`<path d="${this._smoothPath(prior,x,y,previous.basis==='cloud_half_hour_samples'?.51:1.01)}" stroke="#a3aab5" stroke-width="1.8" stroke-dasharray="5 5" opacity=".75" fill="none"/>`:''}
      <path d="${forecastPath}" stroke="#36a9ff" stroke-width="2.2" fill="none"/>
      <path d="${actualPath}" stroke="#2ecc71" stroke-width="2.5" fill="none"/>
      ${dots(prior,'#a3aab5','Попередній день')}${dots(forecast,'#36a9ff','Прогноз')}${dots(facts,'#2ecc71','Факт')}
      </svg><button aria-pressed="${this._showPrevious}">${this._showPrevious?'Приховати':'Показати'} вчорашню генерацію</button>
      <div class="hint">Факт API — кожні 30 хв. Прогноз — середня потужність за годину, показана в її середині. Натисніть точку для деталей.</div>
      ${validation?`<div class="caption">Модель · архівна перевірка: ${esc(validation.test_days)} днів, MAE ${numbers.format(validation.daylight_mae_w)} Вт. Точність прогнозу погоди сюди не входить.</div>`:''}
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
