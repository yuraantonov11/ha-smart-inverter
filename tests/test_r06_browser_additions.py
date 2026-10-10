"""Additional R06 contracts exercised against production JS in Chromium.

The shared fixture loads the six unmodified frontend scripts from a local HTTP
origin and captures uncaught browser ``pageerror`` events.
"""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse

from test_r06_browser_playwright import CARDS, FRONTEND, _Fixture


SCREENSHOT_DIR = Path(os.environ.get(
    "POWMR_R06_SCREENSHOT_DIR", "/opt/data/cache/scratch/powmr-r06-visual"
))
SCREENSHOT_CARD_FILTER = set(filter(None, (
    tag.strip() for tag in os.environ.get("POWMR_R06_SCREENSHOT_CARDS", "").split(",")
)))
LONG_TITLE = (
    "R06 visual audit — photovoltaic production, forecast, storage, "
    "grid exchange and household consumption"
)


class R06BrowserAddedCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fx = _Fixture()
        cls.fx.start_browser()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fx.stop()

    def _page(self, width: int = 1280):
        page, errors = self.fx.new_page({"width": width, "height": 900})
        self.addCleanup(page.context.close)
        page.add_init_script("""(() => {
          window.__r06ImageEvents=[];
          document.addEventListener('load', event => {
            if (event.target instanceof SVGImageElement) {
              window.__r06ImageEvents.push({type:'load',href:event.target.href.baseVal});
            }
          }, true);
          document.addEventListener('error', event => {
            if (event.target instanceof SVGImageElement) {
              window.__r06ImageEvents.push({type:'error',href:event.target.href.baseVal});
            }
          }, true);
        })();""")
        self.fx.response_logs[id(page)] = []
        page.on("response", lambda response: self.fx.response_logs[id(page)].append(response))
        self.fx.install_init_scripts(page.context)
        page.goto(self.fx.base_url + "/index.html", wait_until="load")
        page.add_style_tag(content="html,body{margin:0;padding:0} body{font-family:Arial,sans-serif}")
        return page, errors

    def _assert_svg_text_layout(self, page, selector: str, min_screen_font: float = 10.0) -> dict:
        layout = page.evaluate("""selector => {
          const split=selector.split(' >>> ');
          const svg=split.length===2
            ? document.querySelector(split[0])?.shadowRoot?.querySelector(split[1])
            : document.querySelector(selector);
          const vb=svg.viewBox.baseVal;
          const texts=Array.from(svg.querySelectorAll('text')).map(el=>{
            const b=el.getBBox(),r=el.getBoundingClientRect(),m=el.getScreenCTM();
            return {text:el.textContent.trim(),x:b.x,y:b.y,right:b.x+b.width,bottom:b.y+b.height,
              font:parseFloat(getComputedStyle(el).fontSize)*Math.hypot(m.c,m.d),
              rect:{left:r.left,top:r.top,right:r.right,bottom:r.bottom}};
          });
          const overlaps=[];
          for(let i=0;i<texts.length;i++) for(let j=i+1;j<texts.length;j++) {
            const a=texts[i].rect,b=texts[j].rect;
            if(Math.min(a.right,b.right)-Math.max(a.left,b.left)>1
              && Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1)
              overlaps.push({a:texts[i].text,b:texts[j].text,
                rectA:texts[i].rect,rectB:texts[j].rect,
                x:Math.min(a.right,b.right)-Math.max(a.left,b.left),
                y:Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)});
          }
          return {viewBox:{x:vb.x,y:vb.y,right:vb.x+vb.width,bottom:vb.y+vb.height},texts,overlaps};
        }""",selector)
        self.assertTrue(layout["texts"], f"no SVG text in {selector}")
        for item in layout["texts"]:
            self.assertGreaterEqual(item["x"], -0.5, f"SVG text clipped left: {item}; {layout}")
            self.assertGreaterEqual(item["y"], -0.5, f"SVG text clipped top: {item}; {layout}")
            self.assertLessEqual(item["right"], layout["viewBox"]["right"] + 0.5, f"SVG text clipped right: {item}; {layout}")
            self.assertLessEqual(item["bottom"], layout["viewBox"]["bottom"] + 0.5, f"SVG text clipped bottom: {item}; {layout}")
            self.assertGreaterEqual(item["font"], min_screen_font, f"SVG text below {min_screen_font}px screen size: {item}; {layout}")
        self.assertEqual(layout["overlaps"], [], f"overlapping SVG ticks/units/legend: {layout['overlaps']}")
        return layout

    @staticmethod
    def _assert_source_has_no_owned_resources(test: unittest.TestCase, filename: str, class_name: str) -> None:
        source = (FRONTEND / filename).read_text()
        marker = f"class {class_name} extends HTMLElement {{"
        start = source.index(marker)
        # These two classes have small bodies and a top-level closing brace;
        # subsequent custom-element registration code is intentionally excluded.
        end = source.index("\n}\n", start) + 2
        body = source[start:end]
        resource = re.compile(
            r"\b(?:new\s+(?:Resize|Mutation|Intersection)Observer|"
            r"addEventListener|removeEventListener|setTimeout|setInterval|"
            r"requestAnimationFrame|cancelAnimationFrame)\s*\(|"
            r"\.on[a-z]+\s*=|\.animate\s*\("
        )
        test.assertNotRegex(body, resource, f"unexpected lifecycle-owned resource in {class_name}")
        test.assertNotIn("connectedCallback()", body)
        test.assertNotIn("disconnectedCallback()", body)

    def test_forecast_and_total_energy_resource_inventory(self) -> None:
        self._assert_source_has_no_owned_resources(self, "forecast-card.js", "ForecastCard")
        self._assert_source_has_no_owned_resources(self, "total-energy-card.js", "TotalEnergyCard")
        forecast = (FRONTEND / "forecast-card.js").read_text()
        self.assertIn("this.attachShadow({ mode: \"open\" })", forecast)
        # Forecast's only persistent per-element resource is its shadow root;
        # total-energy renders into host light DOM. Neither needs a fake cleanup
        # counter for observers/listeners/timers that production never creates.

    def _reconnect_contract(self, tag: str, setup: str, update: str, check: str) -> None:
        page, errors = self._page()
        page.evaluate(setup)
        initial = page.evaluate(check, 0)
        self.assertTrue(initial["registered"] and initial["connected"] and initial["same"], initial)
        self.assertTrue(initial["valuesMatch"], f"initial {tag} DOM mismatch: {initial}")
        for i in range(1, 6):
            removed = page.evaluate(
                """() => { const c=window.__r06Original; c.remove();
                  return {same:c===window.__r06Card, connected:c.isConnected}; }"""
            )
            self.assertTrue(removed["same"], f"{tag} cycle {i}: original identity changed: {removed}")
            self.assertFalse(removed["connected"], f"{tag} cycle {i}: remove() did not disconnect: {removed}")
            attached = page.evaluate(
                """() => { const c=window.__r06Original; document.body.appendChild(c);
                  return {same:c===window.__r06Card, connected:c.isConnected,
                    registered:c instanceof customElements.get(c.localName)}; }"""
            )
            self.assertTrue(all(attached.values()), f"{tag} cycle {i}: same registered card did not reconnect: {attached}")
            page.evaluate(update, i)
            state = page.evaluate(check, i)
            self.assertTrue(state["valuesMatch"], f"{tag} cycle {i}: DOM failed to reflect changed hass: {state}")
        page.wait_for_timeout(50)
        self.assertEqual(errors, [], f"{tag} uncaught browser errors: {errors}")

    def test_forecast_same_element_five_real_document_reconnects(self) -> None:
        self._reconnect_contract(
            "forecast-card",
            """() => { const c=document.createElement('forecast-card');
              c.setConfig({entity:'sensor.r06_fc_lifecycle',title:'Forecast lifecycle'});
              window.__r06Card=c; window.__r06Original=c;
              window.__r06Hass=i=>({states:{'sensor.r06_fc_lifecycle':{state:String(1000+i),attributes:{
                hourly_forecast_w:Array.from({length:24},(_,h)=>100+h+i),peak_power_w:1800+i,total_kwh:12.5+i,hourly_weather:[]
              }}}}); c.hass=window.__r06Hass(0); document.body.appendChild(c); }""",
            "i => { window.__r06Card.hass=window.__r06Hass(i); }",
            """i => { const c=window.__r06Card,r=c.shadowRoot;
              const values=Array.from(r.querySelectorAll('.stats .val'),e=>e.textContent.trim());
              const path=r.querySelector('svg path[fill=none]')?.getAttribute('d')||'';
              return {registered:c instanceof customElements.get('forecast-card'),connected:c.isConnected,
                same:c===window.__r06Original,title:r.querySelector('.title')?.textContent.trim(),values,path,
                valuesMatch:r.querySelector('.title')?.textContent.trim()==='Forecast lifecycle'
                  && values[0]===`${1800+i} W` && values[1]===`${12.5+i} kWh` && path.startsWith('M')}; }""",
        )

    def test_total_energy_same_element_five_real_document_reconnects(self) -> None:
        self._reconnect_contract(
            "total-energy-card",
            """() => { const c=document.createElement('total-energy-card');
              c.setConfig({entity:'sensor.r06_total_lifecycle',title:'Total energy lifecycle'});
              window.__r06Card=c;window.__r06Original=c;
              window.__r06Hass=i=>({states:{'sensor.r06_total_lifecycle':{state:String(100+i),attributes:{today_kwh:2+i,year_kwh:20+i}}}});
              c.hass=window.__r06Hass(0);document.body.appendChild(c); }""",
            "i => { window.__r06Card.hass=window.__r06Hass(i); }",
            """i => { const c=window.__r06Card,title=c.querySelector('.te-title')?.textContent.trim();
              const total=c.querySelector('.te-total')?.textContent.trim();
              const chips=Array.from(c.querySelectorAll('.te-chip'),e=>e.textContent.trim());
              return {registered:c instanceof customElements.get('total-energy-card'),connected:c.isConnected,
                same:c===window.__r06Original,title,total,chips,
                valuesMatch:title==='Total energy lifecycle' && total===`${100+i}.00 kWh`
                  && chips[0]===`Today: ${2+i}.00 kWh` && chips[1]===`Year: ${20+i}.00 kWh`}; }""",
        )

    @staticmethod
    def _visual_setup(tag: str, title: str) -> str:
        """Shared branch body; fixture values remain nonzero and card-realistic."""
        # Pass config as a JSON argument to avoid quoting user strings into JS.
        return """({tag,title}) => {
          let c;
          let hass;
          const titleCfg=title;
          if(tag==='forecast-card') {
            c=document.createElement(tag); c.setConfig({entity:'sensor.visual_fc',title:titleCfg});
            hass={states:{'sensor.visual_fc':{state:'1800',attributes:{
              hourly_forecast_w:[0,80,150,250,400,700,1100,1500,1800,1600,1300,900,600,400,250,150,80,30,0,0,0,0,0,0],
              peak_power_w:1800,total_kwh:12.75,hourly_weather:[]}}}};
          } else if(tag==='total-energy-card') {
            c=document.createElement(tag); c.setConfig({entity:'sensor.visual_total',title:titleCfg});
            hass={states:{'sensor.visual_total':{state:'12345.67',attributes:{today_kwh:18.25,year_kwh:2456.75}}}};
          } else if(tag==='power-history-card') {
            c=document.createElement(tag); c.setConfig({entity:'sensor.visual_history',attribute:'hourly_power_kw',labels_attribute:'hourly_labels',title:titleCfg,name:'PV production',unit:'kW'});
            hass={states:{'sensor.visual_history':{state:'on',attributes:{
              hourly_power_kw:[0.1,0.3,0.7,1.1,1.6,2.1,2.6,2.2,1.8,1.3,0.9,0.5],
              hourly_labels:['06:00','07:00','08:00','09:00','10:00','11:00','12:00','13:00','14:00','15:00','16:00','17:00']}}}};
          } else if(tag==='pv-comparison-card') {
            c=document.createElement(tag); c.style.display='block'; c.style.width='100%';
            c.setConfig({entity:'sensor.visual_actual',forecast_entity:'sensor.visual_forecast',title:titleCfg});
            const today=new Date().toISOString().slice(0,10),now=new Date(Math.floor(Date.now()/60000)*60000).toISOString();
            const prev=new Date(Date.now()-86400000).toISOString().slice(0,10);
            const prior=prev+'T12:00:00Z';
            hass={config:{time_zone:'UTC'},states:{
              'sensor.visual_actual':{state:'on',attributes:{points:[{time:now,power_w:777}],previous_day:{date:prev,basis:'cloud_half_hour_samples',points:[{time:prior,power_w:500}]}}},
              'sensor.visual_forecast':{state:'on',attributes:{hourly_forecast_date:today,hourly_forecast_w:Array.from({length:24},(_,h)=>100+h*25),total_kwh:7.2}}
            }};
          } else if(tag==='k-flow-card') {
            c=document.createElement(tag); c.style.width='100%';
            c.setConfig({pv1_power:'sensor.visual_pv1',pv2_power:'sensor.visual_pv2',pv_total_power:'sensor.visual_pvt',
              grid_active_power:'sensor.visual_grid',consump:'sensor.visual_load',battery_soc:'sensor.visual_soc',battery_power:'sensor.visual_batt',
              battery_current:'sensor.visual_current',battery_voltage:'sensor.visual_voltage',battery_full_ah:200,
              inverter_name:titleCfg,today_pv:'sensor.visual_today',daily_savings:'sensor.visual_savings',_show_ev:true,
              charger_state:'sensor.visual_charger_state',charger_power:'sensor.visual_charger_power',
              charger_current:'sensor.visual_charger_current',charger_soc:'sensor.visual_charger_soc'});
            const s=(v,u='W')=>({state:String(v),attributes:{unit_of_measurement:u}});
            hass={states:{'sensor.visual_pv1':s(1200),'sensor.visual_pv2':s(650),'sensor.visual_pvt':s(1850),
              'sensor.visual_grid':s(230),'sensor.visual_load':s(1420),'sensor.visual_soc':s(78,'%'),
              'sensor.visual_batt':s(-410),'sensor.visual_current':s(8.2,'A'),'sensor.visual_voltage':s(51.8,'V'),
              'sensor.visual_today':s(8.75,'kWh'),'sensor.visual_savings':s(12.4,'UAH'),
              'sensor.visual_charger_state':{state:'charging',attributes:{}},'sensor.visual_charger_power':s(3600),
              'sensor.visual_charger_current':s(16,'A'),'sensor.visual_charger_soc':s(62,'%')}};
          } else {
            c=document.createElement('smart-solar-energy-flow');
            c.setConfig({title:titleCfg,entities:{solar:'sensor.visual_solar',home:'sensor.visual_home',grid:'sensor.visual_grid',battery:'sensor.visual_battery'}});
            const unit='watts measured from the inverter output and storage system';
            const s=v=>({state:String(v),attributes:{unit_of_measurement:unit}});
            hass={states:{'sensor.visual_solar':s(1250),'sensor.visual_home':s(890),'sensor.visual_grid':s(240),'sensor.visual_battery':s(520)}};
          }
          document.body.appendChild(c);window.__visualCard=c;c.hass=hass;
        }"""

    def test_all_six_production_cards_at_360_and_1280_with_nonzero_data_and_screenshots(self) -> None:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        tags = {
            "power-history-card.js":"power-history-card",
            "forecast-card.js":"forecast-card",
            "total-energy-card.js":"total-energy-card",
            "energy-flow-card.js":"smart-solar-energy-flow",
            "pv-comparison-card.js":"pv-comparison-card",
            "k-flow-card.js":"k-flow-card",
        }
        for filename in CARDS:
            tag=tags[filename]
            for width in (360,1280):
                with self.subTest(card=tag,viewport=width):
                    page,errors=self._page(width)
                    page.evaluate(self._visual_setup(tag,LONG_TITLE),{"tag":tag,"title":LONG_TITLE})
                    if tag=="forecast-card":
                        dom=page.evaluate("""() => {const r=__visualCard.shadowRoot,curve=r.querySelector('svg path[fill=none]');return {
                          title:r.querySelector('.title').textContent.trim(),values:Array.from(r.querySelectorAll('.stats .val'),e=>e.textContent.trim()),
                          hourTicks:Array.from(r.querySelectorAll('svg text')).filter(e=>/^\\d{1,2}:00$/.test(e.textContent.trim())).map(e=>e.textContent.trim()),
                          pathSegments:(curve?.getAttribute('d').match(/ C/g)||[]).length}}""")
                        self.assertEqual(dom["title"],LONG_TITLE);self.assertEqual(dom["values"],["1800 W","12.75 kWh"])
                        self.assertEqual(dom["hourTicks"],["0:00","6:00","12:00","18:00"]);self.assertEqual(dom["pathSegments"],23,dom)
                        self._assert_svg_text_layout(page,"forecast-card >>> svg")
                    elif tag=="total-energy-card":
                        dom=page.evaluate("""() => ({title:__visualCard.querySelector('.te-title')?.textContent.trim(),values:[__visualCard.querySelector('.te-total')?.textContent.trim(),...Array.from(__visualCard.querySelectorAll('.te-chip'),e=>e.textContent.trim())]})""")
                        self.assertEqual(dom,{"title":LONG_TITLE,"values":["12.35 MWh","Today: 18.25 kWh","Year: 2.46 MWh"]})
                    elif tag=="power-history-card":
                        dom=page.evaluate("""() => {const labels=Array.from(__visualCard.querySelectorAll('svg text'))
                          .filter(e=>/^\\d{2}:\\d{2}$/.test(e.textContent.trim())).map(e=>{const b=e.getBBox();return {text:e.textContent.trim(),x:b.x,right:b.x+b.width}});
                          const bars=Array.from(__visualCard.querySelectorAll('svg rect'));
                          return {text:__visualCard.textContent,bars:bars.length,barPositions:bars.map(e=>Number(e.getAttribute('x'))),labels,
                            data:bars.map(e=>e.querySelector('title')?.textContent.trim())};}""")
                        self.assertIn(LONG_TITLE,dom["text"]);self.assertIn("Source: sensor.visual_history",dom["text"]);self.assertIn("kW",dom["text"]);self.assertEqual(dom["bars"],12)
                        expected_ticks=(["06:00","08:00","10:00","12:00","14:00","17:00"] if width==360
                                        else [f"{h:02}:00" for h in range(6,18)])
                        self.assertEqual([x["text"] for x in dom["labels"]],expected_ticks,dom["labels"])
                        self.assertEqual(dom["data"],[f"{h:02}:00: {v:.3f}" for h,v in zip(range(6,18),[.1,.3,.7,1.1,1.6,2.1,2.6,2.2,1.8,1.3,.9,.5])])
                        self.assertEqual([round(x,5) for x in dom["barPositions"]],
                                         [round(45+i*445/12,5) for i in range(12)],dom["barPositions"])
                        self.assertTrue(all(x["x"]>=0 and x["right"]<=500 for x in dom["labels"]),f"clipped power-history time label(s): {dom['labels']}")
                        self._assert_svg_text_layout(page,"power-history-card svg")
                    elif tag=="pv-comparison-card":
                        dom=page.evaluate("""() => ({title:__visualCard.querySelector('h2')?.textContent.trim(),values:Array.from(__visualCard.querySelectorAll('.stats .value'),e=>e.textContent.trim()),
                          points:__visualCard.querySelectorAll('circle.point').length,
                          hourTicks:Array.from(__visualCard.querySelectorAll('svg text')).filter(e=>/^\\d{2}:00$/.test(e.textContent.trim())).map(e=>e.textContent.trim()),
                          yTicks:Array.from(__visualCard.querySelectorAll('svg text')).map(e=>e.textContent.trim()).filter(t=>/^\\d+$/.test(t))})""")
                        self.assertEqual(dom["title"],LONG_TITLE);self.assertIn("777 Вт",dom["values"]);self.assertIn("7,2 кВт·год",dom["values"]);self.assertEqual(dom["points"],26)
                        self.assertEqual(dom["hourTicks"],[f"{h:02}:00" for h in range(0,25,3)] if width==360 else [f"{h:02}:00" for h in range(25)],dom)
                        self.assertEqual(dom["yTicks"],["0","160","320","480","640","800"],dom)
                        self._assert_svg_text_layout(page,"pv-comparison-card svg")
                        legend_overlap=page.evaluate("""() => {const s=__visualCard.querySelector('svg').getBoundingClientRect();
                          return Array.from(__visualCard.querySelectorAll('.legend span')).filter(e=>{const r=e.getBoundingClientRect();
                            return Math.min(r.right,s.right)-Math.max(r.left,s.left)>1&&Math.min(r.bottom,s.bottom)-Math.max(r.top,s.top)>1;
                          }).map(e=>e.textContent.trim());}""")
                        self.assertEqual(legend_overlap,[],f"pv-comparison legend overlaps plot: {legend_overlap}")
                    elif tag=="k-flow-card":
                        dom=page.evaluate("""() => {const e=__visualCard.shadowRoot.getElementById('invNameLabel'),b=e.getBBox();return {
                          name:e?.textContent.trim(),aria:e?.getAttribute('aria-label'),width:b.width,
                          values:['pv1FlowVal','pv2FlowVal','fcGridVal','fcLoadVal','fcBattVal','invTodayPv','invDailySavings'].map(id=>__visualCard.shadowRoot.getElementById(id)?.textContent.trim()),
                          evValues:['evPowerVal','evCurrentVal','evSocVal'].map(id=>__visualCard.shadowRoot.getElementById(id)?.textContent.trim())};}""")
                        self.assertEqual(dom["name"],f"{LONG_TITLE[:8]}…");self.assertEqual(dom["values"],["1.20 kW","650 W","▼ 230 W","1.42 kW","78%","8.75 kWh","12.40 ₴"])
                        self.assertEqual(dom["evValues"],["3600 W","16.0 A","62 %"],dom)
                        self.assertEqual(dom["aria"],LONG_TITLE,dom);self.assertLessEqual(dom["width"],100,dom)
                    else:
                        dom=page.evaluate("""() => ({values:Array.from(__visualCard.shadowRoot.querySelectorAll('.node .value'),e=>e.textContent.trim()),labels:Array.from(__visualCard.shadowRoot.querySelectorAll('.node .label'),e=>e.textContent.trim())})""")
                        self.assertEqual(dom["values"],["1250","890","240","520"]);self.assertTrue(all("watts measured" in x for x in dom["labels"]),dom)
                    scroll=page.evaluate("() => document.documentElement.scrollWidth")
                    self.assertLessEqual(scroll,width,f"{tag} overflow: viewport={width}px document.scrollWidth={scroll}px")
                    self.assertEqual(errors,[],f"{tag} pageerror(s): {errors}")
                    if not SCREENSHOT_CARD_FILTER or tag in SCREENSHOT_CARD_FILTER:
                        shot=SCREENSHOT_DIR/f"{tag}-{width}px.png"
                        page.screenshot(path=str(shot),full_page=True)
                        self.assertTrue(shot.is_file() and shot.stat().st_size>0,str(shot))
                    page.context.close()

    def test_k_flow_icon_png_resources_are_served_and_loaded_by_svg(self) -> None:
        page,errors=self._page(360)
        setup_js=self._visual_setup("k-flow-card",LONG_TITLE)
        capture_loads="""for (const image of window.__visualCard.shadowRoot.querySelectorAll('image')) {
          image.addEventListener('load',()=>window.__r06ImageEvents.push({type:'load',href:image.href.baseVal}));
          image.addEventListener('error',()=>window.__r06ImageEvents.push({type:'error',href:image.href.baseVal}));
        }"""
        page.evaluate("args => { const render = " + setup_js + "; render(args);" + capture_loads + "}",
                     {"tag":"k-flow-card","title":LONG_TITLE})
        page.wait_for_load_state("networkidle",timeout=10000)
        refs=page.evaluate("""() => Array.from(__visualCard.shadowRoot.querySelectorAll('image'),e=>e.href.baseVal)""")
        expected={f"/local/community/powmr-inverter/{name}.png" for name in ("ev-charger-icon","grid-icon","home-icon")}
        self.assertEqual(set(urlparse(value).path for value in refs),expected,f"k-flow SVG image paths differ: {refs}")
        responses=self.fx.response_logs[id(page)]
        image_responses={urlparse(r.url).path:r for r in responses if urlparse(r.url).path in expected}
        self.assertEqual(set(image_responses),expected,f"missing browser HTTP responses for SVG images: {set(image_responses)}")
        for path,response in image_responses.items():
            self.assertEqual(response.status,200,f"SVG image request failed: {path} HTTP {response.status}")
            self.assertEqual(response.headers.get("content-type","").split(";",1)[0],"image/png",f"wrong content type: {path} {response.headers}")
            self.assertTrue(response.body().startswith(bytes.fromhex("89504e470d0a1a0a")),f"response is not a PNG: {path}")
        image_events=page.evaluate("() => window.__r06ImageEvents")
        loaded={urlparse(event["href"]).path for event in image_events if event["type"]=="load"}
        failed=[event for event in image_events if event["type"]=="error"]
        self.assertEqual(loaded,expected,f"SVG image load events missing: {image_events}")
        self.assertEqual(failed,[],f"SVG image load errors: {failed}")
        self.assertEqual(errors,[],f"k-flow pageerror(s): {errors}")

    def test_energy_flow_untrusted_unit_is_text_not_live_html(self) -> None:
        page,errors=self._page()
        payload='</span><img src=x onerror="window.__r06XssFired += 1"><span>'
        page.evaluate("window.__r06XssFired=0")
        page.evaluate("""payload => {const c=document.createElement('smart-solar-energy-flow');
          c.setConfig({entities:{solar:'sensor.escape_solar',home:'sensor.escape_home',grid:'sensor.escape_grid',battery:'sensor.escape_battery'}});
          const s=(v,u)=>({state:String(v),attributes:{unit_of_measurement:u}});
          c.hass={states:{'sensor.escape_solar':s(510,payload),'sensor.escape_home':s(420,'W'),
            'sensor.escape_grid':s(30,'W'),'sensor.escape_battery':s(60,'W')}};
          document.body.appendChild(c);window.__escapeCard=c;}""",payload)
        page.wait_for_timeout(100)
        result=page.evaluate("""payload => {const r=__escapeCard.shadowRoot,l=r.querySelector('.node.solar .label');
          return {label:l?.textContent,images:r.querySelectorAll('img').length,
            handlers:Array.from(r.querySelectorAll('*')).filter(e=>e.hasAttribute('onerror')).length,sentinel:window.__r06XssFired};}""",payload)
        self.assertEqual(result["label"],f"PV {payload}",result)
        self.assertEqual(result["images"],0,result)
        self.assertEqual(result["handlers"],0,result)
        self.assertEqual(result["sentinel"],0,result)
        self.assertEqual(errors,[],f"energy-flow pageerror(s): {errors}")

    def test_k_flow_untrusted_config_and_attribute_remain_svg_text(self) -> None:
        page,errors=self._page()
        payload='<img src=x onerror="window.__r06XssFired += 1">'
        page.evaluate("window.__r06XssFired=0")
        page.evaluate("""payload => {const c=document.createElement('k-flow-card');
          c.setConfig({inverter_name:payload,pv1_power:'sensor.escape_pv1',pv2_power:'sensor.escape_pv2',
            battery_soc:'sensor.escape_soc',battery_power:'sensor.escape_battery',
            _labels_custom_entities:true,label_cell_temp_minmax:'R06 override',label_entity_cell_temp:'sensor.escape_total'});
          c.hass={states:{'sensor.escape_pv1':{state:'100',attributes:{unit_of_measurement:payload}},
            'sensor.escape_pv2':{state:'50',attributes:{unit_of_measurement:'W'}},
            'sensor.escape_soc':{state:'55',attributes:{unit_of_measurement:'%'}},
            'sensor.escape_battery':{state:'20',attributes:{unit_of_measurement:'W'}},
            'sensor.escape_total':{state:'8.4',attributes:{unit_of_measurement:payload}}}};
          document.body.appendChild(c);window.__escapeCard=c;}""",payload)
        page.wait_for_timeout(100)
        result=page.evaluate("""payload => {const r=__escapeCard.shadowRoot;return {
          name:r.getElementById('invNameLabel')?.textContent,aria:r.getElementById('invNameLabel')?.getAttribute('aria-label'),pv:r.getElementById('pv1FlowVal')?.textContent,
          payloadElements:r.querySelectorAll('[onerror], img').length,sentinel:window.__r06XssFired};}""",payload)
        self.assertEqual(result["name"],f"{payload[:8]}…",result)
        self.assertEqual(result["aria"],payload,result)
        self.assertEqual(result["pv"],"100 W",result)
        self.assertEqual(result["payloadElements"],0,result)
        self.assertEqual(result["sentinel"],0,result)
        self.assertEqual(errors,[],f"k-flow pageerror(s): {errors}")

    def test_flow_instance_state_isolated_after_updates_and_real_clicks(self) -> None:
        page,errors=self._page()
        page.evaluate("""() => {
          const kc=(p,n)=>({inverter_name:n,pv1_power:`sensor.${p}_pv1`,pv2_power:`sensor.${p}_pv2`,pv_total_power:`sensor.${p}_pv`,
            grid_active_power:`sensor.${p}_grid`,consump:`sensor.${p}_load`,battery_soc:`sensor.${p}_soc`,battery_power:`sensor.${p}_battery`});
          const kh=(p,pv,load,soc)=>({states:{[`sensor.${p}_pv1`]:{state:String(pv),attributes:{unit_of_measurement:'W'}},
            [`sensor.${p}_pv2`]:{state:'25',attributes:{unit_of_measurement:'W'}},[`sensor.${p}_pv`]:{state:String(pv+25),attributes:{unit_of_measurement:'W'}},
            [`sensor.${p}_grid`]:{state:'40',attributes:{unit_of_measurement:'W'}},[`sensor.${p}_load`]:{state:String(load),attributes:{unit_of_measurement:'W'}},
            [`sensor.${p}_soc`]:{state:String(soc),attributes:{unit_of_measurement:'%'}},[`sensor.${p}_battery`]:{state:'0',attributes:{unit_of_measurement:'W'}}}});
          const ka=document.createElement('k-flow-card'),kb=document.createElement('k-flow-card');ka.setConfig(kc('ka','Inverter A'));kb.setConfig(kc('kb','Inverter B'));
          ka.hass=kh('ka',310,500,61);kb.hass=kh('kb',820,1100,83);document.body.append(ka,kb);window.__ka=ka;window.__kb=kb;window.__kh=kh;
          const ec=p=>({entities:{solar:`sensor.${p}_solar`,home:`sensor.${p}_home`,grid:`sensor.${p}_grid`,battery:`sensor.${p}_battery`}});
          const eh=(p,solar,home)=>({states:{[`sensor.${p}_solar`]:{state:String(solar),attributes:{unit_of_measurement:'W'}},
            [`sensor.${p}_home`]:{state:String(home),attributes:{unit_of_measurement:'W'}},[`sensor.${p}_grid`]:{state:'45',attributes:{unit_of_measurement:'W'}},
            [`sensor.${p}_battery`]:{state:'70',attributes:{unit_of_measurement:'W'}}}});
          const ea=document.createElement('smart-solar-energy-flow'),eb=document.createElement('smart-solar-energy-flow');ea.setConfig(ec('ea'));eb.setConfig(ec('eb'));
          ea.hass=eh('ea',410,390);eb.hass=eh('eb',920,870);document.body.append(ea,eb);window.__ea=ea;window.__eb=eb;window.__eh=eh;
        }""")
        initial=page.evaluate("""() => ({ka:['pv1FlowVal','fcLoadVal','fcBattVal'].map(x=>__ka.shadowRoot.getElementById(x).textContent),
          kb:['pv1FlowVal','fcLoadVal','fcBattVal'].map(x=>__kb.shadowRoot.getElementById(x).textContent),
          ea:Array.from(__ea.shadowRoot.querySelectorAll('.node .value'),e=>e.textContent),eb:Array.from(__eb.shadowRoot.querySelectorAll('.node .value'),e=>e.textContent)})""")
        self.assertEqual(initial["ka"],["310 W","500 W","61%"],initial)
        self.assertEqual(initial["kb"],["820 W","1.10 kW","83%"],initial)
        self.assertEqual(initial["ea"],["410","390","45","70"],initial)
        self.assertEqual(initial["eb"],["920","870","45","70"],initial)
        page.evaluate("() => {__ka.hass=__kh('ka',444,777,62);__ea.hass=__eh('ea',555,666);}")
        page.locator("k-flow-card").nth(0).evaluate("el=>el.shadowRoot.getElementById('fcLoadVal').dispatchEvent(new MouseEvent('click',{bubbles:true,composed:true}))")
        page.locator("smart-solar-energy-flow").nth(0).locator(".node.solar").click()
        after=page.evaluate("""() => ({ka:['pv1FlowVal','fcLoadVal','fcBattVal'].map(x=>__ka.shadowRoot.getElementById(x).textContent),
          kb:['pv1FlowVal','fcLoadVal','fcBattVal'].map(x=>__kb.shadowRoot.getElementById(x).textContent),
          ea:Array.from(__ea.shadowRoot.querySelectorAll('.node .value'),e=>e.textContent),eb:Array.from(__eb.shadowRoot.querySelectorAll('.node .value'),e=>e.textContent)})""")
        self.assertEqual(after["ka"],["444 W","777 W","62%"],after)
        self.assertEqual(after["kb"],initial["kb"],after)
        self.assertEqual(after["ea"],["555","666","45","70"],after)
        self.assertEqual(after["eb"],initial["eb"],after)
        self.assertEqual(errors,[],f"flow instance interaction pageerror(s): {errors}")


if __name__ == "__main__":
    unittest.main()
