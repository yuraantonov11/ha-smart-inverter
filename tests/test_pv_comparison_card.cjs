const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
let Card;
class Element {
  querySelector(){return {};}
  querySelectorAll(){return [];}
}
const sandbox={HTMLElement:Element,window:{},customElements:{get:()=>null,define:(name,c)=>Card=c},Intl,Date,Number,Math};
vm.runInNewContext(fs.readFileSync('frontend/pv-comparison-card.js','utf8'),sandbox);
const card=new Card();
card.setConfig({entity:'actual',forecast_entity:'forecast'});
card._today=()=>'2026-10-03';
const point=(time,power_w)=>({time:`2026-10-03T${time}:00+03:00`,power_w});
const points=card._points([point('00:00',0),point('00:30',35),point('01:00',null)],'2026-10-03',true);
assert.equal(points.length,2);
assert.equal(points[1].hour,.5); // 48 actual points never stretched to 24 hours.
assert.equal(card._path([{hour:0,value:0},{hour:.5,value:35},{hour:2,value:50}],v=>v,v=>v,.51),'M0,0 L0.5,35 M2,50');
card.hass={config:{time_zone:'Europe/Kyiv'},states:{actual:{attributes:{points:[],previous_day:{date:'2026-10-02',points:[{time:'2026-10-02T16:30:00+03:00',power_w:492}]}}},forecast:{attributes:{hourly_forecast_date:'2026-10-03',hourly_forecast_w:Array(24).fill(100),total_kwh:2.4}}}};
assert.ok(card.innerHTML.includes('492 Вт'));
assert.ok(card.innerHTML.includes('23:00–24:00'));
assert.ok(!card.innerHTML.includes('NaN'));
assert.ok(card.innerHTML.includes('Немає даних'));
card._hass.states.forecast.attributes.hourly_forecast_date='2026-10-02';
card._render();
assert.ok(card.innerHTML.includes('Немає прогнозу'));
console.log('PV chart: timestamp alignment, gaps, nulls, prior date and stale forecast passed');
