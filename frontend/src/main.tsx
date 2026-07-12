import React, {useEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './styles.css';

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const VOICE_ENABLED = true;
type State = any;

declare global { interface Window { AMap?: any; _AMapSecurityConfig?: {securityJsCode: string} } }

function AmapRoute({state, onParkPosition}: {state: State, onParkPosition: (longitude: number, latitude: number) => void}) {
  const element = useRef<HTMLDivElement>(null); const map = useRef<any>(null); const vehicleMarker = useRef<any>(null); const displayedPosition = useRef<[number, number] | null>(null); const cameraPosition = useRef<[number, number] | null>(null); const navigationWasActive = useRef(false); const animationFrame = useRef<number | null>(null); const [ready, setReady] = useState(false);
  const key = import.meta.env.VITE_AMAP_JS_KEY; const security = import.meta.env.VITE_AMAP_SECURITY_CODE;
  const route = state.navigation.route; const destination = state.navigation.destination;
  const routeSignature = route && destination ? `${destination.id || destination.name}:${route.distance_km}:${route.polyline?.length}` : 'none';
  useEffect(() => {
    if (!key || !element.current) return;
    const load = () => new Promise<void>((resolve, reject) => {
      if (window.AMap) return resolve();
      if (security) window._AMapSecurityConfig = {securityJsCode: security};
      const script = document.createElement('script'); script.src = `https://webapi.amap.com/maps?v=2.0&key=${key}&plugin=AMap.Driving`; script.onload = () => resolve(); script.onerror = () => reject(); document.head.appendChild(script);
    });
    load().then(() => { if (element.current && !map.current) { map.current = new window.AMap.Map(element.current, {zoom: 11, center: [state.vehicle.longitude, state.vehicle.latitude]}); setReady(true); } }).catch(() => undefined);
  }, [key, security]);
  useEffect(() => {
    if (!ready || !map.current || !window.AMap) return;
    map.current.clearMap(); const start: [number, number] = [state.vehicle.longitude, state.vehicle.latitude]; displayedPosition.current = start; vehicleMarker.current = new window.AMap.Marker({position: start, title: '车辆当前位置', content: '<div class="vehicle-marker">🚗</div>', offset: new window.AMap.Pixel(-17, -17), zIndex: 200}); const overlays = [vehicleMarker.current];
    if (route?.polyline && destination) {
      const drawFallback = () => { overlays.push(new window.AMap.Polyline({path: route.polyline, strokeColor: '#45d8d1', strokeWeight: 6}), new window.AMap.Marker({position: [destination.lng, destination.lat], title: destination.name})); map.current.add(overlays); map.current.setFitView(overlays, false, [60,60,60,60]); };
      if (window.AMap.Driving) {
        const driving = new window.AMap.Driving({map: map.current, policy: window.AMap.DrivingPolicy.LEAST_TIME, hideMarkers: true});
        driving.search(start, [destination.lng, destination.lat], (status: string) => { if (status !== 'complete') drawFallback(); else map.current.add(vehicleMarker.current); });
      } else drawFallback();
    }
    else { map.current.add(overlays); map.current.setCenter(start); }
  }, [ready, routeSignature]);
  useEffect(() => {
    if (!ready || !map.current || !vehicleMarker.current) return;
    const target: [number, number] = [state.vehicle.longitude, state.vehicle.latitude];
    const from = displayedPosition.current || target;
    if (animationFrame.current) cancelAnimationFrame(animationFrame.current);
    if (state.navigation.status !== 'active') { vehicleMarker.current.setPosition(target); displayedPosition.current = target; cameraPosition.current = target; navigationWasActive.current = false; return; }
    if (!navigationWasActive.current) { map.current.setZoomAndCenter(16, target); cameraPosition.current = target; navigationWasActive.current = true; }
    else { const camera = cameraPosition.current!; if (Math.hypot(target[0] - camera[0], target[1] - camera[1]) > 0.001) { map.current.panTo(target); cameraPosition.current = target; } }
    const started = performance.now();
    const animate = (now: number) => {
      const progress = Math.min(1, (now - started) / 900);
      const eased = progress * (2 - progress);
      const position: [number, number] = [from[0] + (target[0] - from[0]) * eased, from[1] + (target[1] - from[1]) * eased];
      vehicleMarker.current?.setPosition(position); displayedPosition.current = position;
      if (progress < 1) animationFrame.current = requestAnimationFrame(animate);
    };
    animationFrame.current = requestAnimationFrame(animate);
    return () => { if (animationFrame.current) cancelAnimationFrame(animationFrame.current); };
  }, [ready, state.vehicle.longitude, state.vehicle.latitude, state.navigation.status]);
  const canSetParking = state.navigation.status === 'idle' && state.vehicle.speed_kmh === 0;
  const setParkingFromCenter = () => { const center = map.current?.getCenter(); if (center) onParkPosition(center.lng, center.lat); };
  return key ? <><div ref={element} className="amap-canvas" />{canSetParking && <button className="set-parking" onClick={setParkingFromCenter}>设为停车位置</button>}</> : null;
}

function App() {
  const [state, setState] = useState<State | null>(null);
  const [input, setInput] = useState('');
  const [status, setStatus] = useState('正在连接');
  const [startupError, setStartupError] = useState('');
  const [recording, setRecording] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const recognition = useRef<any>(null);
  const chunks = useRef<Blob[]>([]);
  const advancing = useRef(false);

  const setParkPosition = async (longitude: number, latitude: number) => {
    if (!state || state.navigation.status !== 'idle' || state.vehicle.speed_kmh !== 0) return;
    try {
      const vehicle = {...state.vehicle, longitude, latitude, speed_kmh: 0};
      const response = await fetch(`${API}/api/sessions/${state.session_id}/simulation`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({vehicle})});
      const data = await response.json(); if (!response.ok) throw new Error(data.detail || '设置停车位置失败');
      setState(data.state); setStatus('停车位置已更新');
    } catch (error) { setStatus(`设置位置失败：${error instanceof Error ? error.message : '未知错误'}`); }
  };

  const send = async (text: string) => {
    if (!state || !text.trim()) return;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 20_000);
    try {
      setStatus('Agent 正在处理');
      const r = await fetch(`${API}/api/sessions/${state.session_id}/messages`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text}), signal: controller.signal});
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || 'Agent 请求失败');
      setState(data.state); setInput(''); setStatus('等待指令');
      if (VOICE_ENABLED) void speak(data.response);
    } catch (error) {
      const message = error instanceof DOMException && error.name === 'AbortError' ? 'Agent 响应超时，请检查后端或 DeepSeek 网络' : error instanceof Error ? error.message : '请检查后端服务';
      setStatus(`发送失败：${message}`);
    } finally { window.clearTimeout(timeout); }
  };

  const scenario = async (id: string) => {
    if (!state) return;
    const r = await fetch(`${API}/api/sessions/${state.session_id}/scenarios/${id}`, {method: 'POST'});
    const data = await r.json(); setState(data.state); if (VOICE_ENABLED) data.messages?.forEach((m: string) => void speak(m));
  };

  const updateDriver = async (field: string, value: number) => {
    if (!state) return;
    const driver = {...state.driver, [field]: value};
    const r = await fetch(`${API}/api/sessions/${state.session_id}/simulation`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({driver})});
    const data = await r.json(); setState(data.state); if (VOICE_ENABLED) data.messages?.forEach((m: string) => void speak(m));
  };

  const cancelNavigation = async () => {
    if (!state) return;
    try {
      const response = await fetch(`${API}/api/sessions/${state.session_id}/navigation/cancel`, {method: 'POST'});
      const data = await response.json(); if (!response.ok) throw new Error(data.detail || '结束导航失败');
      setState(data.state); setStatus(data.response || '导航已结束');
    } catch (error) { setStatus(`结束导航失败：${error instanceof Error ? error.message : '未知错误'}`); }
  };

  const startRecording = async () => {
    const BrowserRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (BrowserRecognition) {
      const instance = new BrowserRecognition();
      recognition.current = instance;
      instance.lang = 'zh-CN'; instance.interimResults = false; instance.maxAlternatives = 1;
      instance.onresult = async (event: any) => {
        const text = event.results?.[0]?.[0]?.transcript?.trim();
        if (text) { setInput(text); await send(text); }
        else setStatus('未识别到语音，请重试或使用键盘输入');
      };
      instance.onerror = (event: any) => setStatus(`语音识别失败：${event.error || '未知错误'}`);
      instance.onend = () => { setRecording(false); recognition.current = null; };
      try { instance.start(); setRecording(true); setStatus('正在录音'); }
      catch (error) { setStatus(`麦克风不可用：${error instanceof Error ? error.message : '请检查浏览器权限'}`); }
      return;
    }
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('当前浏览器不支持麦克风录音');
      const stream = await navigator.mediaDevices.getUserMedia({audio: true});
      chunks.current = [];
      recorder.current = new MediaRecorder(stream);
      recorder.current.ondataavailable = e => chunks.current.push(e.data);
      recorder.current.onstop = async () => {
        try {
          stream.getTracks().forEach(t => t.stop());
          if (!chunks.current.length) throw new Error('没有录到音频，请检查麦克风权限');
          const blob = await toWav(new Blob(chunks.current, {type: recorder.current?.mimeType || 'audio/webm'}));
          setStatus('正在识别');
          const form = new FormData(); form.append('audio', blob, 'recording.wav');
          const r = await fetch(`${API}/api/speech/transcribe`, {method: 'POST', body: form}); const data = await r.json();
          if (!r.ok) throw new Error(data.detail || '语音识别请求失败');
          if (data.text) { setInput(data.text); await send(data.text); }
          else { setStatus(data.error || '未识别到语音，请重试或使用键盘输入'); }
        } catch (error) { setStatus(`语音不可用：${error instanceof Error ? error.message : '请使用键盘输入'}`); }
      };
      recorder.current.start(); setRecording(true); setStatus('正在录音');
    } catch (error) { setStatus(`麦克风不可用：${error instanceof Error ? error.message : '请检查浏览器权限'}`); }
  };
  const stopRecording = () => {
    if (recognition.current) recognition.current.stop();
    else recorder.current?.stop();
    setRecording(false);
  };

  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 8_000);
    (async () => {
      try {
        const r = await fetch(`${API}/api/sessions`, {method: 'POST', signal: controller.signal});
        const s = await r.json();
        if (!r.ok) throw new Error(s.detail || '创建会话失败');
        setState(s); setStatus('等待指令');
      } catch (error) {
        const message = error instanceof DOMException && error.name === 'AbortError' ? '连接后端超时' : error instanceof Error ? error.message : '无法连接后端';
        setStartupError(`${message}（${API}）`); setStatus('后端连接失败');
      } finally { window.clearTimeout(timeout); }
    })();
    return () => { controller.abort(); window.clearTimeout(timeout); };
  }, []);

  useEffect(() => {
    if (!state || state.navigation.status !== 'active') return;
    const tick = async () => {
      if (advancing.current) return;
      advancing.current = true;
      try {
        const response = await fetch(`${API}/api/sessions/${state.session_id}/navigation/advance`, {method: 'POST'});
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || '仪表模拟失败');
        setState(data);
      } catch (error) { setStatus(`仪表模拟失败：${error instanceof Error ? error.message : '未知错误'}`); }
      finally { advancing.current = false; }
    };
    void tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [state?.session_id, state?.navigation.status]);

  if (!state) return <main className="loading"><div><b>CabinGuard 正在启动…</b>{startupError && <p>启动失败：{startupError}</p>}</div></main>;
  const route = state.navigation.route;
  return <main className="app-shell">
    <header><div><span className="eyebrow">CABINGUARD V2</span><h1>主动式智能座舱</h1></div><div className="status"><i />{status}</div></header>
    <section className="layout">
      <aside className="panel simulator"><h2>车辆与驾驶员</h2><Metric label="车速" value={`${state.vehicle.speed_kmh.toFixed(1)} km/h`} /><Metric label="驾驶时长" value={formatDuration(state.driver.driving_duration_minutes)} />
        <label>疲劳程度 <output>{Math.round(state.driver.fatigue_level * 100)}%</output><input type="range" min="0" max="1" step=".01" value={state.driver.fatigue_level} onChange={e => updateDriver('fatigue_level', +e.target.value)} /></label>
        <label>注意力 <output>{Math.round(state.driver.attention_level * 100)}%</output><input type="range" min="0" max="1" step=".01" value={state.driver.attention_level} onChange={e => updateDriver('attention_level', +e.target.value)} /></label>
        <div className="scenario-grid"><button onClick={() => scenario('commute')}>正常通勤</button><button onClick={() => scenario('rainy')}>雨天出行</button><button className="danger" onClick={() => scenario('fatigue')}>疲劳驾驶</button></div>
      </aside>
      <section className="center"><div className="map panel"><AmapRoute state={state} onParkPosition={setParkPosition}/><div className="map-top"><span>当前路线</span><b>{state.navigation.status === 'active' ? `导航中 · ${state.navigation.simulated_speed_kmh.toFixed(1)} km/h` : '停车状态：点击地图设置位置'}</b></div>{!import.meta.env.VITE_AMAP_JS_KEY && <div className="road"><span className="pin start">●</span><div className="route-line" style={{width: route ? '100%' : '0%'}} /><span className="car" style={{left: '8%'}}>▰</span><span className="pin end">★</span></div>}{route ? <div className="route-info"><b>{route.distance_km} km</b><span>预计 {route.duration_minutes} 分钟</span>{state.navigation.status !== 'idle' && <button onClick={cancelNavigation}>结束导航并清除路线</button>}</div> : <p>停车时可点击地图设置车辆位置，然后说“带我去虹桥站”</p>}</div>
        <div className="cabin-cards"><Card icon="♨" label="空调" value={`${state.cabin.temperature}℃ · ${state.cabin.climate_mode}`} /><Card icon="♫" label="媒体" value={`${state.cabin.media_mode} · ${state.cabin.volume}%`} /><Card icon="▧" label="座椅" value={`通风 ${state.cabin.seat_ventilation} · 按摩 ${state.cabin.seat_massage}`} />{state.weather && <Card icon="☂" label="天气" value={`${state.weather.temperature}℃ · ${state.weather.weather}`} />}</div>
        {state.active_alert && <div className="alert">⚠ {state.active_alert}</div>}
      </section>
      <aside className="panel agent"><h2>Agent 决策面板</h2><div className="history">{state.messages.map((m: any, i: number) => <div key={i} className={m.role}><small>{m.role === 'user' ? '你' : 'CabinGuard'}</small>{m.content}</div>)}</div>{state.pending_action && <div className="confirm"><b>需要确认</b><p>{state.pending_action.prompt}</p><button onClick={() => send('确认')}>确认执行</button><button onClick={() => send('取消')}>取消</button></div>}<h3>工具与安全日志</h3><div className="logs">{state.tool_logs.map((log: any, i: number) => <div key={i}><b className={log.decision}>{log.decision}</b><span>{log.tool}</span><small>{log.message}</small></div>)}</div></aside>
    </section>
    <footer>{VOICE_ENABLED ? <><button className={recording ? 'recording' : ''} onClick={recording ? stopRecording : startRecording}>{recording ? '■ 停止录音' : '● 开始说话'}</button><button onClick={() => speechSynthesis.cancel()}>停止播报</button></> : <span className="text-mode">纯文字模式：语音输入与播报已暂时关闭</span>}<form onSubmit={e => { e.preventDefault(); send(input); }}><input value={input} onChange={e => setInput(e.target.value)} placeholder="例如：带我去虹桥站，顺便看看天气"/><button>发送</button></form></footer>
  </main>;
}
function Metric({label, value}: {label: string, value: string}) { return <div className="metric"><span>{label}</span><b>{value}</b></div>; }
function Card({icon, label, value}: {icon: string, label: string, value: string}) { return <div className="card"><i>{icon}</i><span>{label}</span><b>{value}</b></div>; }
function formatDuration(minutes: number) { const seconds = Math.round(minutes * 60); return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`; }
async function speak(text: string) {
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text); utterance.lang = 'zh-CN'; utterance.rate = .95; speechSynthesis.speak(utterance);
}
async function toWav(blob: Blob): Promise<Blob> { const ctx = new AudioContext(); const buffer = await ctx.decodeAudioData(await blob.arrayBuffer()); const samples = buffer.getChannelData(0); const target = 16000; const ratio = buffer.sampleRate / target; const out = new Int16Array(Math.ceil(samples.length / ratio)); for (let i = 0; i < out.length; i++) out[i] = Math.max(-1, Math.min(1, samples[Math.floor(i * ratio)])) * 0x7fff; const header = new ArrayBuffer(44); const view = new DataView(header); const put = (o: number, s: string) => [...s].forEach((c, i) => view.setUint8(o + i, c.charCodeAt(0))); put(0,'RIFF'); view.setUint32(4, 36 + out.byteLength, true); put(8,'WAVE'); put(12,'fmt '); view.setUint32(16,16,true); view.setUint16(20,1,true); view.setUint16(22,1,true); view.setUint32(24,target,true); view.setUint32(28,target*2,true); view.setUint16(32,2,true); view.setUint16(34,16,true); put(36,'data'); view.setUint32(40,out.byteLength,true); ctx.close(); return new Blob([header,out], {type:'audio/wav'}); }
createRoot(document.getElementById('root')!).render(<App />);
