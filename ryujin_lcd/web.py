from __future__ import annotations

import html

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response

from .config import STAT_MODULE_IDS, Settings, app_dir, normalize_modules
from .conflicts import diagnostic, process_conflicts, stop_process_conflicts
from .engine import Engine


INDEX = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Ryujin LCD 控制台</title>
<style>
:root{color-scheme:dark;font-family:Inter,"Segoe UI",sans-serif;background:#070b12;color:#eaf3ff}
body{margin:0;min-height:100vh;background:radial-gradient(circle at 20% 0,#12314a 0,transparent 35%),#070b12}
main{max-width:980px;margin:auto;padding:44px 24px}.head{display:flex;justify-content:space-between;align-items:center;gap:20px}
.tag{color:#74e7ff;letter-spacing:2px;font-size:14px}.grid{display:grid;grid-template-columns:1.1fr .9fr;gap:20px;margin-top:24px}
.card{background:#0d1522dd;border:1px solid #21344a;border-radius:20px;padding:22px;box-shadow:0 20px 60px #0008}
img{width:100%;aspect-ratio:4/3;object-fit:contain;background:#02050a;border-radius:12px}.metrics{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.metric{background:#111e2e;border-radius:12px;padding:12px}.label{color:#8fa3b9;font-size:12px}.value{font-size:23px;font-weight:700;margin-top:3px}
button,.upload{border:0;border-radius:10px;padding:11px 16px;font-weight:650;cursor:pointer;background:#76e7ff;color:#061018;display:inline-block}
button.secondary{background:#1b2b3d;color:#eaf3ff}.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}input[type=file]{display:none}
pre{white-space:pre-wrap;color:#b7c6d8;font:12px/1.5 ui-monospace,monospace;max-height:180px;overflow:auto}.warn{color:#ffc36e}.ok{color:#72f1b8}
.module{display:flex;align-items:center;gap:7px;margin:7px 0;font-size:13px}.module button{padding:4px 8px;font-size:12px}.module label{min-width:92px}
@media(max-width:760px){.grid{grid-template-columns:1fr}}
</style></head><body><main><div class="head"><div class="tag">ASUS ROG · 0B05:1988</div><div><div id="runState">读取中…</div><div id="fps" class="tag" style="margin-top:5px;text-align:right"></div></div></div>
<div class="grid"><section class="card"><img id="preview" src="/api/preview"><div class="row"><label class="upload">上传背景<input id="file" type="file" accept="image/png,image/jpeg,image/webp,image/gif"></label><button id="start">启动 LCD</button><button id="stop" class="secondary">停止</button></div><p id="message"></p></section>
<section class="card"><div class="metrics">
<div class="metric"><div class="label">CPU 温度</div><div class="value" id="ct">--</div></div><div class="metric"><div class="label">CPU 占用</div><div class="value" id="cu">--</div></div>
<div class="metric"><div class="label">CPU 频率</div><div class="value" id="cf">--</div></div><div class="metric"><div class="label">CPU 电压</div><div class="value" id="cv">--</div></div>
<div class="metric"><div class="label">CPU 功耗</div><div class="value" id="cp">--</div></div>
<div class="metric"><div class="label">GPU 温度</div><div class="value" id="gt">--</div></div><div class="metric"><div class="label">GPU 占用</div><div class="value" id="gu">--</div></div>
<div class="metric"><div class="label">GPU 频率</div><div class="value" id="gf">--</div></div><div class="metric"><div class="label">GPU 电压</div><div class="value" id="gv">--</div></div>
</div><h3>LCD 统计模块</h3><div id="modules">读取中…</div><h3>独占检查</h3><pre id="diag">读取中…</pre></section></div></main>
<script>
const $=id=>document.getElementById(id),whole=(v,u)=>v==null?'--':Math.round(v)+u,dec=(v,u,n)=>v==null?'--':Number(v).toFixed(n)+u,ghz=v=>v==null?'--':dec(v/1000,' GHz',1);
async function status(){let s=await(await fetch('/api/status')).json(),m=s.metrics||{};$('ct').textContent=whole(m.cpu_temp_c,'°');$('cu').textContent=whole(m.cpu_usage,'%');$('cf').textContent=ghz(m.cpu_freq_mhz);$('cv').textContent=dec(m.cpu_voltage_v,' V',2);$('cp').textContent=whole(m.cpu_power_w,' W');$('gt').textContent=whole(m.gpu_temp_c,'°');$('gu').textContent=whole(m.gpu_usage,'%');$('gf').textContent=ghz(m.gpu_freq_mhz);$('gv').textContent=dec(m.gpu_voltage_v,' V',2);$('runState').innerHTML=s.running?'<span class="ok">● 正在驱动</span>':'<span class="warn">● 已停止</span>';$('fps').textContent=s.animation_active?('动画 '+(s.actual_fps||0)+' / '+s.target_fps+' FPS'):'';$('message').textContent=s.error||''}
async function modules(){let d=await(await fetch('/api/modules')).json(),names=d.catalog;$('modules').innerHTML=['cpu','gpu'].map(kind=>'<div><b>'+kind.toUpperCase()+'</b>'+d[kind+'_modules'].map((id,i)=>'<div class="module"><label>'+names[id]+'</label><button onclick="moveModule(\''+kind+'\','+i+',-1)">↑</button><button onclick="moveModule(\''+kind+'\','+i+',1)">↓</button><button class="secondary" onclick="removeModule(\''+kind+'\',\''+id+'\')">隐藏</button></div>').join('')+'<select onchange="addModule(\''+kind+'\',this.value);this.value=\'\'"><option value="">添加模块…</option>'+Object.entries(names).filter(([id])=>!d[kind+'_modules'].includes(id)).map(([id,name])=>'<option value="'+id+'">'+name+'</option>').join('')+'</select></div>').join('')}
async function saveModules(kind,items){await fetch('/api/modules',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({[kind+'_modules']:items})});modules();refreshPreview()}
async function moveModule(kind,index,delta){let d=await(await fetch('/api/modules')).json(),items=d[kind+'_modules'],to=index+delta;if(to<0||to>=items.length)return;[items[index],items[to]]=[items[to],items[index]];saveModules(kind,items)}
async function removeModule(kind,id){let d=await(await fetch('/api/modules')).json();saveModules(kind,d[kind+'_modules'].filter(x=>x!==id))}
async function addModule(kind,id){if(!id)return;let d=await(await fetch('/api/modules')).json();saveModules(kind,[...d[kind+'_modules'],id])}
function refreshPreview(){$('preview').src='/api/preview?t='+Date.now()}
async function diag(){let d=await(await fetch('/api/diagnostic')).json();$('diag').textContent=JSON.stringify(d,null,2)}
$('file').onchange=async e=>{let f=e.target.files[0];if(!f)return;let b=new FormData();b.append('file',f);let r=await fetch('/api/background',{method:'POST',body:b});$('message').textContent=(await r.json()).message;status()}
async function startLCD(stopConflicts=false){let r=await fetch('/api/start?stop_conflicts='+stopConflicts,{method:'POST'}),j=await r.json();if(r.status===409&&j.detail&&j.detail.conflicts&&!stopConflicts){let lines=j.detail.conflicts.map(x=>x.process+' (PID '+x.pid+')').join('\n');if(confirm('检测到以下程序可能抢占 LCD：\n\n'+lines+'\n\n是否停止这些进程并继续启动？'))return startLCD(true)}$('message').textContent=j.message||(typeof j.detail==='string'?j.detail:(j.detail&&j.detail.message))||'';status();diag()}
$('start').onclick=()=>startLCD(false);$('stop').onclick=async()=>{await fetch('/api/stop',{method:'POST'});status()};status();diag();modules();refreshPreview();setInterval(status,2200);setInterval(refreshPreview,200);
</script></body></html>"""


def create_app(settings: Settings, mock: bool = False, force: bool = False,
               auto_start: bool = False) -> FastAPI:
    app = FastAPI(title="Ryujin LCD", docs_url=None, redoc_url=None)
    engine = Engine(settings, mock=mock)
    engine.refresh_preview()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX

    @app.get("/api/status")
    def status() -> dict:
        return engine.status()

    @app.get("/api/modules")
    def modules() -> dict:
        return {
            "catalog": {"temperature": "温度", "load": "负载", "clock": "频率", "voltage": "电压", "power": "功耗"},
            "cpu_modules": settings.cpu_modules,
            "gpu_modules": settings.gpu_modules,
        }

    @app.put("/api/modules")
    def update_modules(payload: dict) -> dict:
        for key in ("cpu_modules", "gpu_modules"):
            if key in payload:
                settings_value = normalize_modules(payload[key])
                setattr(settings, key, settings_value)
        settings.save()
        engine.refresh_preview()
        return modules()

    @app.get("/api/diagnostic")
    def get_diagnostic() -> dict:
        return diagnostic()

    @app.get("/api/preview")
    def preview() -> Response:
        if not engine.preview:
            engine.refresh_preview()
        return Response(engine.preview, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/background")
    async def background(file: UploadFile = File(...)) -> dict:
        allowed = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
        if file.content_type not in allowed:
            raise HTTPException(415, "仅支持 PNG/JPEG/WebP/GIF")
        data = await file.read(20 * 1024 * 1024 + 1)
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(413, "背景文件不得超过 20MB")
        target = app_dir() / f"background{allowed[file.content_type]}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        settings.background = str(target)
        settings.save()
        engine.refresh_preview()
        kind = "动态背景" if file.content_type == "image/gif" else "背景"
        return {"message": f"{kind}已保存：{html.escape(file.filename or target.name)}"}

    @app.post("/api/start")
    def start(stop_conflicts: bool = False) -> dict:
        conflicts = process_conflicts()
        if conflicts and not (force or mock) and not stop_conflicts:
            raise HTTPException(409, {"message": "检测到可能抢占 LCD 的程序", "conflicts": [x.__dict__ for x in conflicts]})
        if conflicts and stop_conflicts and not mock:
            outcome = stop_process_conflicts()
            if outcome["remaining"] or outcome["errors"]:
                detail = "；".join(outcome["errors"]) or "部分进程在结束后重新启动"
                raise HTTPException(409, {"message": f"未能排除全部冲突：{detail}。请以管理员身份运行 RyujinLCD。", "conflicts": outcome["remaining"]})
        try:
            engine.start()
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc
        return {"message": "LCD 驱动已启动"}

    @app.post("/api/stop")
    def stop() -> dict:
        engine.stop()
        return {"message": "LCD 驱动已停止"}

    @app.on_event("shutdown")
    def shutdown() -> None:
        engine.stop()

    @app.on_event("startup")
    def startup() -> None:
        if not auto_start:
            return
        conflicts = process_conflicts()
        if conflicts and not (force or mock):
            names = ", ".join(item.process for item in conflicts)
            engine.error = f"自动启动已取消：检测到可能抢占 LCD 的程序：{names}"
            return
        try:
            engine.start()
        except Exception as exc:
            # Keep the Web service available so the status page reports why
            # automatic startup failed and the user can retry manually.
            engine.error = f"自动启动失败：{exc}"

    app.state.engine = engine
    return app
