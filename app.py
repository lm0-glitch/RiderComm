import os
import re
import secrets
import time
import threading
from flask import Flask, render_template_string, jsonify, request
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

MAX_RIDERS = 20
CODE_RE = re.compile(r"^[A-Z0-9_-]{3,32}$")
groups = {}
sid_index = {}
lock = threading.RLock()

def valid_number(v):
    try: n = int(v)
    except: return None
    return str(n) if 1 <= n <= MAX_RIDERS else None

def code():
    while True:
        c = "TRAIL-" + str(secrets.randbelow(9000) + 1000)
        with lock:
            if c not in groups: return c

def state(g):
    return {"code":g["code"],"name":g["name"],"speaker":g["speaker"],
            "riders":{k:{x:r.get(x) for x in ("number","leader","lat","lon","accuracy","heading","speed","timestamp")}
                      | {"connected":bool(r.get("sid"))} for k,r in g["riders"].items()}}

def ref(sid):
    with lock:
        x=sid_index.get(sid)
        if not x:return None
        g=groups.get(x[0]); r=g and g["riders"].get(x[1])
        return (x[0],x[1],r) if r else None

def broadcast(c):
    with lock:
        if c in groups: payload=state(groups[c])
        else:return
    socketio.emit("group_state",payload,room=c)

@app.route("/")
def index(): return render_template_string(HTML)

@app.route("/health")
def health(): return jsonify(ok=True,groups=len(groups),time=int(time.time()))

@socketio.on("create_group")
def create(data):
    name=str((data or {}).get("name","")).strip()
    if not name:return emit("error_message",{"message":"יש להזין שם קבוצה."})
    c=code()
    with lock: groups[c]={"code":c,"name":name,"riders":{},"speaker":None}
    emit("group_created",{"code":c,"name":name})

@socketio.on("join_group")
def join(data):
    d=data or {}; c=str(d.get("code","")).strip().upper(); n=valid_number(d.get("number"))
    leader=bool(d.get("leader"))
    if not CODE_RE.match(c):return emit("error_message",{"message":"קוד קבוצה לא תקין."})
    if not n:return emit("error_message",{"message":"מספר הברזל חייב להיות 1-20."})
    with lock:
        g=groups.get(c)
        if not g:return emit("error_message",{"message":"הקבוצה לא נמצאה."})
        old=g["riders"].get(n)
        if old and old.get("sid") and old["sid"]!=request.sid:
            return emit("error_message",{"message":"מספר הברזל כבר בשימוש."})
        oldref=sid_index.get(request.sid)
        if oldref and oldref!=(c,n):
            og=groups.get(oldref[0])
            if og and oldref[1] in og["riders"]: og["riders"][oldref[1]]["sid"]=None
            if og and og["speaker"]==oldref[1]: og["speaker"]=None
            leave_room(oldref[0])
        if not old and sum(bool(x.get("sid")) for x in g["riders"].values())>=MAX_RIDERS:
            return emit("error_message",{"message":"הקבוצה מלאה."})
        g["riders"][n]={"number":int(n),"sid":request.sid,"leader":leader,
                        "lat":old.get("lat") if old else None,"lon":old.get("lon") if old else None,
                        "accuracy":old.get("accuracy") if old else None,"heading":old.get("heading") if old else None,
                        "speed":old.get("speed") if old else None,"timestamp":old.get("timestamp") if old else None}
        sid_index[request.sid]=(c,n)
    join_room(c);emit("joined",{"code":c,"number":int(n),"leader":leader});broadcast(c)

@socketio.on("update_leader")
def leader(data):
    r=ref(request.sid)
    if r:r[2]["leader"]=bool((data or {}).get("leader"));broadcast(r[0])

@socketio.on("update_location")
def location(data):
    r=ref(request.sid)
    if not r or not isinstance(data,dict):return
    c,n,rr=r
    try:lat=float(data["lat"]);lon=float(data["lon"])
    except:return
    if not -90<=lat<=90 or not -180<=lon<=180:return
    def num(k):
        try:return None if data.get(k) is None else float(data[k])
        except:return None
    with lock:
        rr.update(lat=lat,lon=lon,accuracy=num("accuracy"),heading=num("heading"),
                  speed=num("speed"),timestamp=int(data.get("timestamp") or time.time()*1000))
        p={k:rr.get(k) for k in ("number","lat","lon","accuracy","heading","speed","timestamp","leader")}
    socketio.emit("location_update",p,room=c)

@socketio.on("voice_start")
def voice_start():
    r=ref(request.sid)
    if not r:return
    c,n,rr=r
    with lock:
        g=groups[c]
        if g["speaker"] is not None and g["speaker"]!=n:
            return emit("voice_denied",{"message":f"רוכב {g['speaker']} מדבר כרגע."})
        g["speaker"]=n
    socketio.emit("voice_start",{"number":int(n)},room=c)

@socketio.on("voice_chunk")
def voice_chunk(data):
    r=ref(request.sid)
    if not r or not isinstance(data,dict):return
    c,n,_=r
    with lock:
        if groups[c]["speaker"]!=n:return
    a=data.get("audio")
    if not isinstance(a,str) or not a.startswith("data:") or len(a)>750000:return
    socketio.emit("voice_chunk",{"number":int(n),"audio":a,"mime":data.get("mime","audio/webm"),"sequence":data.get("sequence",0)},room=c,include_self=False)

@socketio.on("voice_stop")
def voice_stop():
    r=ref(request.sid)
    if not r:return
    c,n,_=r
    with lock:
        if groups[c]["speaker"]!=n:return
        groups[c]["speaker"]=None
    socketio.emit("voice_stop",{"number":int(n)},room=c)

def cleanup(sid):
    with lock:
        x=sid_index.pop(sid,None)
        if not x:return
        c,n=x;g=groups.get(c)
        if not g:return
        rr=g["riders"].get(n)
        if rr and rr.get("sid")==sid:rr["sid"]=None
        released=g["speaker"]==n
        if released:g["speaker"]=None
        empty=not any(r.get("sid") for r in g["riders"].values())
        if empty:del groups[c]
    try:leave_room(c,sid=sid)
    except:pass
    if not empty:
        socketio.emit("rider_left",{"number":int(n)},room=c)
        if released:socketio.emit("voice_stop",{"number":int(n)},room=c)
        broadcast(c)

@socketio.on("disconnect")
def disconnect():cleanup(request.sid)

if __name__=="__main__":
    socketio.run(app,host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False,allow_unsafe_werkzeug=True)

HTML = """<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#111827">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<title>TRAIL RIDERS</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
*{box-sizing:border-box}html,body,#app,#map{margin:0;width:100%;height:100%;overflow:hidden;font-family:Arial,sans-serif}
body{background:#111827;color:#fff}button,input,select{font:inherit}button{border:0}
.hidden{display:none!important}.login{position:absolute;inset:0;z-index:20;display:grid;place-items:center;padding:20px;background:linear-gradient(135deg,#111827,#1f2937)}
.card{width:min(440px,100%);padding:25px;border-radius:24px;background:#111827;box-shadow:0 20px 70px #0008}
h1{margin:0 0 6px}.muted{color:#9ca3af}.section{border-top:1px solid #ffffff18;margin-top:20px;padding-top:18px}
label{display:block;margin:10px 0 7px;color:#cbd5e1}input,select{width:100%;height:50px;padding:0 14px;border-radius:12px;border:1px solid #374151;background:#0b1220;color:#fff}
button.primary{width:100%;height:50px;margin-top:10px;border-radius:12px;background:#2563eb;color:#fff;font-weight:800}
.check{display:flex;gap:10px;align-items:center;padding:12px;background:#0b1220;border-radius:12px}.check input{width:20px;height:20px}
#top{position:absolute;z-index:5;top:10px;left:10px;right:10px;display:flex;gap:8px;pointer-events:none}.pill{pointer-events:auto;background:#0f172aee;border:1px solid #ffffff18;border-radius:14px;padding:9px 12px}.title{flex:1;font-weight:900}.title small{display:block;color:#9ca3af;font-weight:400}
#speaker{position:absolute;z-index:8;top:72px;left:50%;transform:translateX(-50%);background:#dc2626;border-radius:999px;padding:10px 17px;font-weight:900}
#bottom{position:absolute;z-index:7;left:0;right:0;bottom:0;padding:12px 12px calc(12px + env(safe-area-inset-bottom));display:flex;justify-content:space-between;align-items:end}
.btn{width:50px;height:50px;border-radius:15px;background:#0f172af2;color:#fff;font-size:21px;box-shadow:0 6px 25px #0006}.group{display:flex;gap:8px}
#ptt{width:88px;height:88px;border-radius:50%;background:#2563eb;color:#fff;border:4px solid #fff;font-size:32px;touch-action:none;user-select:none;box-shadow:0 10px 35px #0007}
#ptt.active{background:#dc2626;transform:scale(1.08)}#ptt small{display:block;font-size:10px}
#panel{position:absolute;z-index:10;top:0;right:0;bottom:0;width:min(360px,90%);padding:20px;background:#0f172af7;transform:translateX(105%);transition:.2s;overflow:auto}.open{transform:translateX(0)!important}
.row{display:flex;align-items:center;gap:10px;padding:10px;margin:8px 0;border-radius:14px;background:#1e293b}.num{width:40px;height:40px;border-radius:50%;display:grid;place-items:center;background:#334155;font-weight:900}.lead{background:#eab308;color:#111}.dot{width:10px;height:10px;border-radius:50%;background:#64748b}.on{background:#22c55e}.info{flex:1}.info small{color:#94a3b8}
#toast{position:absolute;z-index:50;bottom:120px;left:50%;transform:translateX(-50%);display:none;background:#111827;border:1px solid #ffffff18;padding:11px 16px;border-radius:12px;max-width:90%;text-align:center}
.marker{display:grid;place-items:center;width:42px;height:42px;border-radius:50%;border:3px solid white;background:#334155;color:white;font-weight:900;box-shadow:0 4px 12px #0007}.marker.me{background:#2563eb}.marker.leader{background:#eab308;color:#111}
</style>
</head>
<body>
<div id="app">
<div id="login" class="login"><div class="card">
<h1>🏍️ TRAIL RIDERS</h1><div class="muted">ניווט קבוצתי ותקשורת בזמן אמת</div>
<label>שם הקבוצה</label><input id="name" maxlength="50" placeholder="טיול הכרמל">
<button class="primary" id="create">צור רכיבה</button>
<div class="section"><label>קוד קבוצה</label><input id="code" maxlength="32" placeholder="TRAIL-4821">
<label>מספר ברזל</label><select id="number"></select>
<label class="check"><input id="leader" type="checkbox">⭐ אני רכב מוביל</label>
<button class="primary" id="join">הצטרף לרכיבה</button></div>
</div></div>
<div id="map"></div>
<div id="top" class="hidden"><div class="pill title">🏍️ <span id="groupName"></span><small id="groupCode"></small></div><div class="pill" id="conn">🔴 לא מחובר</div></div>
<div id="speaker" class="hidden"></div>
<div id="panel"><div style="display:flex;justify-content:space-between;align-items:center"><h2>רוכבים</h2><button class="btn" id="close">×</button></div><div id="list"></div></div>
<div id="bottom" class="hidden"><div class="group"><button class="btn" id="riders">👥</button><button class="btn" id="locate">📍</button><button class="btn" id="gpxBtn">📂</button><input id="gpx" type="file" accept=".gpx" hidden></div><button id="ptt">🎙️<small>PTT</small></button></div>
<div id="toast"></div>
</div>
<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const $=id=>document.getElementById(id),socket=io({transports:["websocket","polling"]});
let map,groupCode="",groupName="",myNumber=null,isLeader=false,watch=null,myPos=null,route=null,recording=false,recorder=null,audioCtx=null;
const markers=new Map(),riders=new Map();
for(let i=1;i<=20;i++){let o=document.createElement("option");o.value=i;o.textContent=i;$("number").appendChild(o)}
function toast(s,t=3000){let x=$("toast");x.textContent=s;x.style.display="block";clearTimeout(x.t);x.t=setTimeout(()=>x.style.display="none",t)}
function init(){if(map)return;map=L.map("map",{zoomControl:false}).setView([31.78,35.21],8);L.control.zoom({position:"bottomright"}).addTo(map);L.tileLayer("https://israelhiking.osm.org.il/Tiles/{z}/{x}/{y}.png",{maxZoom:19,attribution:"Israel Hiking Map / OpenStreetMap"}).addTo(map)}
function icon(n,l,m){return L.divIcon({className:"",html:`<div class="marker ${m?"me":""} ${l?"leader":""}">${l?"★ ":""}${n}</div>`,iconSize:[42,42],iconAnchor:[21,21]})}
function marker(d){if(d.lat==null||d.lon==null)return;let k=String(d.number),m=markers.get(k),mine=Number(d.number)===Number(myNumber);if(!m){m=L.marker([d.lat,d.lon],{icon:icon(d.number,d.leader,mine),zIndexOffset:mine?1000:d.leader?500:0}).addTo(map);markers.set(k,m)}else{m.setLatLng([d.lat,d.lon]);m.setIcon(icon(d.number,d.leader,mine))}m.bindPopup(`<b>${mine?"אתה":"רוכב "+d.number}</b><br>${d.leader?"⭐ רכב מוביל<br>":""}${d.speed!=null?(Number(d.speed)*3.6).toFixed(1)+" קמ״ש":"מהירות לא זמינה"}`)}
function render(){let a=[...riders.values()].sort((x,y)=>x.number-y.number);$("list").innerHTML=a.map(r=>`<div class="row"><div class="num ${r.leader?"lead":""}">${r.leader?"★":r.number}</div><div class="info"><b>רוכב ${r.number}${Number(r.number)===Number(myNumber)?" (אתה)":""}</b><small>${r.leader?"רכב מוביל · ":""}${r.connected?"מחובר":"לא מחובר"}</small></div><i class="dot ${r.connected?"on":""}"></i></div>`).join("")||"<p>אין רוכבים</p>"}
function state(s){groupName=s.name;groupCode=s.code;$("groupName").textContent=groupName;$("groupCode").textContent="קוד: "+groupCode;riders.clear();Object.values(s.riders||{}).forEach(r=>{riders.set(String(r.number),r);marker(r)});for(const k of markers.keys())if(!riders.has(k)){map.removeLayer(markers.get(k));markers.delete(k)}render();s.speaker!=null?showSpeaker(s.speaker):hideSpeaker()}
function showSpeaker(n){$("speaker").textContent=`🔊 רוכב ${n} מדבר...`;$("speaker").classList.remove("hidden")}
function hideSpeaker(){$("speaker").classList.add("hidden")}
function gps(){if(!navigator.geolocation)return toast("הדפדפן אינו תומך ב-GPS");watch=navigator.geolocation.watchPosition(p=>{let c=p.coords;myPos={lat:c.latitude,lon:c.longitude,accuracy:c.accuracy,heading:c.heading,speed:c.speed,timestamp:p.timestamp};socket.emit("update_location",myPos);marker({number:myNumber,leader:isLeader,...myPos})},e=>toast(e.code===1?"יש לאשר הרשאת מיקום":"GPS אינו זמין"),{enableHighAccuracy:true,maximumAge:2000,timeout:15000})}
async function wake(){try{if("wakeLock"in navigator)await navigator.wakeLock.request("screen")}catch(e){}}
$("create").onclick=()=>{let n=$("name").value.trim();if(n)socket.emit("create_group",{name:n});else toast("הזן שם קבוצה")};
$("join").onclick=()=>{let c=$("code").value.trim().toUpperCase();if(!c)return toast("הזן קוד קבוצה");socket.emit("join_group",{code:c,number:+$("number").value,leader:$("leader").checked})};
$("locate").onclick=()=>myPos?map.setView([myPos.lat,myPos.lon],Math.max(map.getZoom(),15)):toast("עדיין אין מיקום GPS");
$("riders").onclick=()=>$("panel").classList.add("open");$("close").onclick=()=>$("panel").classList.remove("open");
$("gpxBtn").onclick=()=>$("gpx").click();
$("gpx").onchange=e=>{let f=e.target.files[0];if(!f)return;let r=new FileReader();r.onload=()=>{try{let x=new DOMParser().parseFromString(r.result,"application/xml"),p=[...x.getElementsByTagName("trkpt")].map(q=>[+q.getAttribute("lat"),+q.getAttribute("lon")]).filter(q=>Number.isFinite(q[0])&&Number.isFinite(q[1]));if(p.length<2)p=[...x.getElementsByTagName("rtept")].map(q=>[+q.getAttribute("lat"),+q.getAttribute("lon")]).filter(q=>Number.isFinite(q[0])&&Number.isFinite(q[1]));if(p.length<2)throw Error("לא נמצא מסלול GPX תקין");if(route)map.removeLayer(route);route=L.polyline(p,{color:"#ef4444",weight:6,opacity:.9}).addTo(map);map.fitBounds(route.getBounds(),{padding:[30,30]});toast("המסלול נטען")}catch(err){toast(err.message)}};r.readAsText(f);e.target.value=""};
socket.on("connect",()=>{$("conn").textContent="🟢 מחובר";if(groupCode&&myNumber!=null)socket.emit("join_group",{code:groupCode,number:myNumber,leader:isLeader})});
socket.on("disconnect",()=>{$("conn").textContent="🔴 אין חיבור"});
socket.on("group_created",d=>{$("code").value=d.code;toast("הקבוצה נוצרה: "+d.code,5000)});
socket.on("joined",async d=>{groupCode=d.code;myNumber=d.number;isLeader=d.leader;$("login").classList.add("hidden");$("top").classList.remove("hidden");$("bottom").classList.remove("hidden");init();gps();await wake();toast("הצטרפת לרכיבה")});
socket.on("group_state",state);
socket.on("location_update",d=>{let r=riders.get(String(d.number))||{number:d.number};Object.assign(r,d,{connected:true});riders.set(String(d.number),r);marker(r);render()});
socket.on("rider_left",d=>{let r=riders.get(String(d.number));if(r){r.connected=false;render()}});
socket.on("error_message",d=>toast(d.message||"שגיאה"));
socket.on("voice_start",d=>showSpeaker(d.number));socket.on("voice_stop",hideSpeaker);
function mime(){return window.MediaRecorder&&["audio/webm;codecs=opus","audio/webm","audio/mp4","audio/ogg;codecs=opus"].find(x=>MediaRecorder.isTypeSupported(x))||""}
async function audio(){audioCtx??=new(window.AudioContext||window.webkitAudioContext)();if(audioCtx.state==="suspended")await audioCtx.resume()}
socket.on("voice_chunk",async d=>{try{await audio();let b=atob(d.audio.split(",")[1]),u=new Uint8Array(b.length);for(let i=0;i<b.length;i++)u[i]=b.charCodeAt(i);let ab=await audioCtx.decodeAudioData(u.buffer),s=audioCtx.createBufferSource();s.buffer=ab;s.connect(audioCtx.destination);s.start()}catch(e){}});
async function start(){if(recording)return;try{await audio();let stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});socket.emit("voice_start");let ok=await new Promise(res=>{let t=setTimeout(()=>{cleanup();res(false)},1200);function yes(d){if(+d.number!==+myNumber)return;clearTimeout(t);cleanup();res(true)}function no(){clearTimeout(t);cleanup();res(false)}function cleanup(){socket.off("voice_start",yes);socket.off("voice_denied",no)}socket.on("voice_start",yes);socket.once("voice_denied",no)});if(!ok){stream.getTracks().forEach(t=>t.stop());return toast("ערוץ הדיבור תפוס")};let mt=mime();recorder=new MediaRecorder(stream,mt?{mimeType:mt}:{});recording=true;$("ptt").classList.add("active");$("ptt").innerHTML="🔴<small>מדבר</small>";recorder.ondataavailable=e=>{if(!e.data.size)return;let r=new FileReader();r.onload=()=>socket.emit("voice_chunk",{audio:r.result,mime:e.data.type,sequence:Date.now()});r.readAsDataURL(e.data)};recorder.onstop=()=>stream.getTracks().forEach(t=>t.stop());recorder.start(250)}catch(e){toast("אין גישה למיקרופון. אשר הרשאה בדפדפן")}}
function stop(){if(!recording)return;recording=false;$("ptt").classList.remove("active");$("ptt").innerHTML="🎙️<small>PTT</small>";try{if(recorder&&recorder.state!=="inactive")recorder.stop()}catch(e){}recorder=null;socket.emit("voice_stop")}
$("ptt").onpointerdown=e=>{e.preventDefault();$("ptt").setPointerCapture?.(e.pointerId);start()};$("ptt").onpointerup=e=>{e.preventDefault();stop()};$("ptt").onpointercancel=stop;$("ptt").onlostpointercapture=()=>{if(recording)stop()};$("ptt").oncontextmenu=e=>e.preventDefault();
document.addEventListener("pointerdown",()=>audio().catch(()=>{}),{once:true});
</script>
</body></html>"""
