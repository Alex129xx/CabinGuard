import React, {useEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './styles.css';

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';
type State = any;

declare global { interface Window { AMap?: any; _AMapSecurityConfig?: {securityJsCode: string} } }

function AmapRoute({state}: {state: State}) {
  const element = useRef<HTMLDivElement>(null); const map = useRef<any>(null);
  const key = import.meta.env.VITE_AMAP_JS_KEY; const security = import.meta.env.VITE_AMAP_SECURITY_CODE;
  useEffect(() => {
    if (!key || !element.current) return;
    const load = () => new Promise<void>((resolve, reject) => {
      if (window.AMap) return resolve();
      if (security) window._AMapSecurityConfig = {securityJsCode: security};
      const script = document.createElement('script'); script.src = `https://webapi.amap.com/maps?v=2.0&key=${key}`; script.onload = () => resolve(); script.onerror = () => reject(); document.head.appendChild(script);
    });
    load().then(() => { if (element.current && !map.current) map.current = new window.AMap.Map(element.current, {zoom: 11, center: [state.vehicle.longitude, state.vehicle.latitude]}); }).catch(() => undefined);
  }, [key, security]);
  useEffect(() => {
    if (!map.current || !window.AMap) return;
    map.current.clearMap(); const start = [state.vehicle.longitude, state.vehicle.latitude]; const overlays = [new window.AMap.Marker({position: start, title: '当前位置'})];
    const route = state.navigation.route; const destination = state.navigation.destination;
    if (route?.polyline && destination) { overlays.push(new window.AMap.Polyline({path: route.polyline, strokeColor: '#45d8d1', strokeWeight: 6}), new window.AMap.Marker({position: [destination.lng, destination.lat], title: destination.name})); map.current.add(overlays); map.current.setFitView(overlays, false, [60,60,60,60]); }
    else { map.current.add(overlays); map.current.setCenter(start); }
  }, [state.vehicle.latitude, state.vehicle.longitude, state.navigation.route, state.navigation.destination]);
  return key ? <div ref={element} className="amap-canvas" /> : null;
}

function App() {
  const [state, setState] = useState<State | null>(null);
  const [input, setInput] = useState('');
  const [status, setStatus] = useState('正在连接');
  const [recording, setRecording] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);

  const send = async (text: string) => {
    if (!state || !text.trim()) return;
    setStatus('Agent 正在处理');
    const r = await fetch(`${API}/api/sessions/${state.session_id}/messages`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text})});
    const data = await r.json(); setState(data.state); setInput(''); setStatus('正在播报'); speak(data.response); setTimeout(() => setStatus('等待指令'), 600);
  };

  const scenario = async (id: string) => {
    if (!state) return;
    const r = await fetch(`${API}/api/sessions/${state.session_id}/scenarios/${id}`, {method: 'POST'});
    const data = await r.json(); setState(data.state); data.messages?.forEach((m: string) => speak(m));
  };

  const updateDriver = async (field: string, value: number) => {
    if (!state) return;
    const driver = {...state.driver, [field]: value};
    const r = await fetch(`${API}/api/sessions/${state.session_id}/simulation`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({driver})});
    const data = await r.json(); setState(data.state); data.messages?.forEach((m: string) => speak(m));
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({audio: true});
      chunks.current = [];
      recorder.current = new MediaRecorder(stream);
      recorder.current.ondataavailable = e => chunks.current.push(e.data);
      recorder.current.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        const blob = await toWav(new Blob(chunks.current, {type: recorder.current?.mimeType || 'audio/webm'}));
        setStatus('正在识别');
        const form = new FormData(); form.append('audio', blob, 'recording.wav');
        const r = await fetch(`${API}/api/speech/transcribe`, {method: 'POST', body: form}); const data = await r.json();
        if (data.text) { setInput(data.text); await send(data.text); } else { setStatus(data.error || '识别失败，请用键盘输入'); }
      };
      recorder.current.start(); setRecording(true); setStatus('正在录音');
    } catch { setStatus('麦克风不可用，请检查浏览器权限'); }
  };
  const stopRecording = () => { recorder.current?.stop(); setRecording(false); };

  useEffect(() => { (async () => {
    const r = await fetch(`${API}/api/sessions`, {method: 'POST'}); const s = await r.json(); setState(s); setStatus('等待指令');
    const ws = new WebSocket(API.replace('http', 'ws') + `/ws/sessions/${s.session_id}`); ws.onmessage = e => { const d = JSON.parse(e.data); if (d.type === 'state') setState(d.state); };
    return () => ws.close();
  })(); }, []);

  if (!state) return <main className="loading">CabinGuard 正在启动…</main>;
  const route = state.navigation.route;
  return <main className="app-shell">
    <header><div><span className="eyebrow">CABINGUARD V2</span><h1>主动式智能座舱</h1></div><div className="status"><i />{status}</div></header>
    <section className="layout">
      <aside className="panel simulator"><h2>车辆与驾驶员</h2><Metric label="车速" value={`${state.vehicle.speed_kmh} km/h`} /><Metric label="驾驶时长" value={`${state.driver.driving_duration_minutes} min`} />
        <label>疲劳程度 <output>{Math.round(state.driver.fatigue_level * 100)}%</output><input type="range" min="0" max="1" step=".01" value={state.driver.fatigue_level} onChange={e => updateDriver('fatigue_level', +e.target.value)} /></label>
        <label>注意力 <output>{Math.round(state.driver.attention_level * 100)}%</output><input type="range" min="0" max="1" step=".01" value={state.driver.attention_level} onChange={e => updateDriver('attention_level', +e.target.value)} /></label>
        <div className="scenario-grid"><button onClick={() => scenario('commute')}>正常通勤</button><button onClick={() => scenario('rainy')}>雨天出行</button><button className="danger" onClick={() => scenario('fatigue')}>疲劳驾驶</button></div>
      </aside>
      <section className="center"><div className="map panel"><AmapRoute state={state}/><div className="map-top"><span>当前路线</span><b>{state.navigation.status === 'active' ? '导航中' : '未导航'}</b></div>{!import.meta.env.VITE_AMAP_JS_KEY && <div className="road"><span className="pin start">●</span><div className="route-line" style={{width: route ? `${Math.max(14, 100 - state.navigation.progress * 75)}%` : '0%'}} /><span className="car" style={{left: `${8 + state.navigation.progress * 70}%`}}>▰</span><span className="pin end">★</span></div>}{route ? <div className="route-info"><b>{route.distance_km} km</b><span>预计 {route.duration_minutes} 分钟</span><button onClick={async () => { const r = await fetch(`${API}/api/sessions/${state.session_id}/navigation/advance`, {method: 'POST'}); setState(await r.json()); }}>模拟前进</button></div> : <p>说“带我去虹桥站”开始导航</p>}</div>
        <div className="cabin-cards"><Card icon="♨" label="空调" value={`${state.cabin.temperature}℃ · ${state.cabin.climate_mode}`} /><Card icon="♫" label="媒体" value={`${state.cabin.media_mode} · ${state.cabin.volume}%`} /><Card icon="▧" label="座椅" value={`通风 ${state.cabin.seat_ventilation} · 按摩 ${state.cabin.seat_massage}`} />{state.weather && <Card icon="☂" label="天气" value={`${state.weather.temperature}℃ · ${state.weather.weather}`} />}</div>
        {state.active_alert && <div className="alert">⚠ {state.active_alert}</div>}
      </section>
      <aside className="panel agent"><h2>Agent 决策面板</h2><div className="history">{state.messages.map((m: any, i: number) => <div key={i} className={m.role}><small>{m.role === 'user' ? '你' : 'CabinGuard'}</small>{m.content}</div>)}</div>{state.pending_action && <div className="confirm"><b>需要确认</b><p>{state.pending_action.prompt}</p><button onClick={() => send('确认')}>确认执行</button><button onClick={() => send('取消')}>取消</button></div>}<h3>工具与安全日志</h3><div className="logs">{state.tool_logs.map((log: any, i: number) => <div key={i}><b className={log.decision}>{log.decision}</b><span>{log.tool}</span><small>{log.message}</small></div>)}</div></aside>
    </section>
    <footer><button className={recording ? 'recording' : ''} onClick={recording ? stopRecording : startRecording}>{recording ? '■ 停止录音' : '● 开始说话'}</button><form onSubmit={e => { e.preventDefault(); send(input); }}><input value={input} onChange={e => setInput(e.target.value)} placeholder="例如：带我去虹桥站，顺便看看天气"/><button>发送</button></form><button onClick={() => speechSynthesis.cancel()}>停止播报</button></footer>
  </main>;
}
function Metric({label, value}: {label: string, value: string}) { return <div className="metric"><span>{label}</span><b>{value}</b></div>; }
function Card({icon, label, value}: {icon: string, label: string, value: string}) { return <div className="card"><i>{icon}</i><span>{label}</span><b>{value}</b></div>; }
async function speak(text: string) {
  speechSynthesis.cancel();
  try {
    const response = await fetch(`${API}/api/speech/synthesize`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text, style: 'general'})});
    if (response.ok) { const url = URL.createObjectURL(await response.blob()); const audio = new Audio(url); audio.onended = () => URL.revokeObjectURL(url); await audio.play(); return; }
  } catch { /* Browser TTS remains the final no-network fallback. */ }
  const utterance = new SpeechSynthesisUtterance(text); utterance.lang = 'zh-CN'; utterance.rate = .95; speechSynthesis.speak(utterance);
}
async function toWav(blob: Blob): Promise<Blob> { const ctx = new AudioContext(); const buffer = await ctx.decodeAudioData(await blob.arrayBuffer()); const samples = buffer.getChannelData(0); const target = 16000; const ratio = buffer.sampleRate / target; const out = new Int16Array(Math.ceil(samples.length / ratio)); for (let i = 0; i < out.length; i++) out[i] = Math.max(-1, Math.min(1, samples[Math.floor(i * ratio)])) * 0x7fff; const header = new ArrayBuffer(44); const view = new DataView(header); const put = (o: number, s: string) => [...s].forEach((c, i) => view.setUint8(o + i, c.charCodeAt(0))); put(0,'RIFF'); view.setUint32(4, 36 + out.byteLength, true); put(8,'WAVE'); put(12,'fmt '); view.setUint32(16,16,true); view.setUint16(20,1,true); view.setUint16(22,1,true); view.setUint32(24,target,true); view.setUint32(28,target*2,true); view.setUint16(32,2,true); view.setUint16(34,16,true); put(36,'data'); view.setUint32(40,out.byteLength,true); ctx.close(); return new Blob([header,out], {type:'audio/wav'}); }
createRoot(document.getElementById('root')!).render(<App />);
