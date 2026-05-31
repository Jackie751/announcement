# auth.py — 前端密码验证中间件
# 替代 nginx basic auth，提供美观的登录页面
# 用法：在 local.py 同目录放置此文件，然后用 auth.py 启动（它会包装 local.py 的 Flask app）

import os
import hmac
import hashlib
import time
from functools import wraps
from flask import request, redirect, make_response, Response

SECRET_KEY  = os.environ.get("AUTH_SECRET", "change-me-please-use-env")
PASSWORD    = os.environ.get("AUTH_PASSWORD", "")
COOKIE_NAME = "lm_auth"
COOKIE_TTL  = 60 * 60 * 8  # 8小时


def _make_token(ts: str) -> str:
    msg = f"{ts}:{PASSWORD}".encode()
    return hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def _valid_cookie(cookie_val: str) -> bool:
    if not cookie_val or ":" not in cookie_val:
        return False
    ts, sig = cookie_val.split(":", 1)
    try:
        if time.time() - float(ts) > COOKIE_TTL:
            return False
    except ValueError:
        return False
    expected = _make_token(ts)
    return hmac.compare_digest(expected, sig)


LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Local Manager — 验证</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Noto+Sans+SC:wght@300;400;500&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:#05050f;color:#dde;font-family:'Noto Sans SC',sans-serif;}
canvas{position:fixed;inset:0;pointer-events:none;z-index:0;}
.wrap{position:relative;z-index:1;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px;}
.card{width:100%;max-width:400px;background:rgba(8,5,28,.75);border:1px solid rgba(180,126,255,.22);border-radius:18px;padding:40px 36px 36px;backdrop-filter:blur(20px);}
.logo{text-align:center;margin-bottom:32px;}
.logo h1{font-family:'Orbitron',monospace;font-size:1em;color:#b47eff;letter-spacing:.18em;margin-bottom:6px;}
.logo p{font-family:'Share Tech Mono',monospace;font-size:11px;color:rgba(180,200,255,.3);letter-spacing:.1em;}
.field{margin-bottom:20px;}
label{display:block;font-family:'Share Tech Mono',monospace;font-size:10px;color:rgba(180,200,255,.35);letter-spacing:.12em;text-transform:uppercase;margin-bottom:8px;}
.input-wrap{position:relative;display:flex;align-items:center;}
input[type=password],input[type=text]{
  width:100%;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.1);
  border-radius:10px;color:#eef;padding:13px 44px 13px 16px;font-size:15px;
  font-family:'Noto Sans SC',sans-serif;outline:none;transition:border-color .2s,background .2s;
  -webkit-text-security:disc;
}
input[type=text]{-webkit-text-security:none;}
input:focus{border-color:rgba(180,126,255,.6);background:rgba(180,126,255,.04);}
.eye-btn{position:absolute;right:14px;background:none;border:none;cursor:pointer;color:rgba(180,200,255,.3);font-size:16px;line-height:1;padding:4px;transition:color .2s;}
.eye-btn:hover{color:rgba(180,200,255,.7);}
.submit-btn{
  width:100%;padding:14px;border-radius:10px;border:none;cursor:pointer;
  background:linear-gradient(135deg,#b47eff,#7c4fff);color:#fff;
  font-size:15px;font-family:'Noto Sans SC',sans-serif;font-weight:500;
  letter-spacing:.04em;transition:opacity .2s,transform .15s;margin-top:4px;
}
.submit-btn:hover{opacity:.88;}
.submit-btn:active{transform:scale(.98);}
.submit-btn:disabled{opacity:.4;cursor:not-allowed;}
.error{
  display:none;margin-top:16px;padding:11px 14px;border-radius:8px;
  background:rgba(248,113,113,.1);border:1px solid rgba(248,113,113,.2);
  color:#f87171;font-size:13px;text-align:center;
}
.error.show{display:block;}
.footer{margin-top:28px;text-align:center;font-family:'Share Tech Mono',monospace;font-size:10px;color:rgba(180,200,255,.15);letter-spacing:.08em;}
.shake{animation:shake .38s ease;}
@keyframes shake{0%,100%{transform:translateX(0)}20%{transform:translateX(-7px)}40%{transform:translateX(7px)}60%{transform:translateX(-5px)}80%{transform:translateX(5px)}}
.spinner{display:none;width:18px;height:18px;border:2px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite;margin:0 auto;}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<canvas id="c"></canvas>
<div class="wrap">
  <div class="card" id="card">
    <div class="logo">
      <h1>LOCAL MANAGER</h1>
      <p>VPS · SECURE ACCESS</p>
    </div>
    <form id="form" autocomplete="off">
      <div class="field">
        <label for="pw">Access Password</label>
        <div class="input-wrap">
          <input type="password" id="pw" name="pw" placeholder="••••••••••••" autofocus autocomplete="current-password">
          <button type="button" class="eye-btn" id="eyeBtn" aria-label="显示/隐藏密码">
            <svg id="eyeIcon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>
            </svg>
          </button>
        </div>
      </div>
      <button type="submit" class="submit-btn" id="btn">
        <span id="btnText">进入</span>
        <div class="spinner" id="spinner"></div>
      </button>
      <div class="error" id="err">密码错误，请重试</div>
    </form>
    <div class="footer">SESSION · 8H · ENCRYPTED</div>
  </div>
</div>

<script>
(function(){
  var c=document.getElementById('c'),ctx=c.getContext('2d');
  function resize(){c.width=window.innerWidth;c.height=window.innerHeight;}
  resize();window.addEventListener('resize',resize);
  var dots=[];for(var i=0;i<60;i++)dots.push({
    x:Math.random()*window.innerWidth,y:Math.random()*window.innerHeight,
    r:Math.random()*1.2+.3,vx:(Math.random()-.5)*.18,vy:(Math.random()-.5)*.18,
    a:Math.random()*.4+.1,h:Math.random()<.5?'255,110,180':Math.random()<.5?'0,229,255':'180,120,255'
  });
  (function draw(){
    ctx.clearRect(0,0,c.width,c.height);
    for(var i=0;i<dots.length;i++){
      var d=dots[i];d.x+=d.vx;d.y+=d.vy;
      if(d.x<0)d.x=c.width;if(d.x>c.width)d.x=0;
      if(d.y<0)d.y=c.height;if(d.y>c.height)d.y=0;
      ctx.beginPath();ctx.arc(d.x,d.y,d.r,0,Math.PI*2);
      ctx.fillStyle='rgba('+d.h+','+d.a+')';ctx.fill();
    }
    requestAnimationFrame(draw);
  })();
})();

var eyeBtn=document.getElementById('eyeBtn');
var pw=document.getElementById('pw');
var shown=false;
var eyeOpen='<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>';
var eyeSlash='<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>';
eyeBtn.addEventListener('click',function(){
  shown=!shown;
  pw.type=shown?'text':'password';
  document.getElementById('eyeIcon').innerHTML=shown?eyeSlash:eyeOpen;
});

document.getElementById('form').addEventListener('submit',function(e){
  e.preventDefault();
  var btn=document.getElementById('btn');
  var spinner=document.getElementById('spinner');
  var btnText=document.getElementById('btnText');
  var err=document.getElementById('err');
  var card=document.getElementById('card');
  err.classList.remove('show');
  btn.disabled=true;
  btnText.style.display='none';
  spinner.style.display='block';

  fetch('/auth/login',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({password:pw.value})
  }).then(function(r){return r.json();}).then(function(d){
    if(d.ok){
      btn.style.background='linear-gradient(135deg,#4ade80,#22c55e)';
      btnText.textContent='✓';
      btnText.style.display='block';
      spinner.style.display='none';
      setTimeout(function(){window.location.href=d.redirect||'/';},400);
    }else{
      btn.disabled=false;
      btnText.style.display='block';
      spinner.style.display='none';
      err.classList.add('show');
      card.classList.remove('shake');
      void card.offsetWidth;
      card.classList.add('shake');
      pw.value='';pw.focus();
    }
  }).catch(function(){
    btn.disabled=false;
    btnText.style.display='block';
    spinner.style.display='none';
    err.textContent='网络错误，请重试';
    err.classList.add('show');
  });
});

pw.addEventListener('keydown',function(){
  document.getElementById('err').classList.remove('show');
});
</script>
</body>
</html>"""


def init_auth(app):
    """
    在 Flask app 上注册 auth 路由和 before_request 钩子。
    在 local.py 末尾调用：
        from auth import init_auth
        init_auth(app)
    """
    if not PASSWORD:
        print("[AUTH] ⚠️  未设置 AUTH_PASSWORD，跳过认证（直接访问）")
        return

    @app.route("/auth/login", methods=["POST"])
    def auth_login():
        from flask import jsonify
        data = request.get_json(silent=True) or {}
        pwd  = data.get("password", "")
        next_url = request.args.get("next", "/")
        if pwd == PASSWORD:
            ts  = str(time.time())
            sig = _make_token(ts)
            token = f"{ts}:{sig}"
            resp = jsonify({"ok": True, "redirect": next_url})
            resp.set_cookie(
                COOKIE_NAME, token,
                max_age=COOKIE_TTL,
                httponly=True,
                samesite="Lax",
                secure=request.is_secure,
            )
            return resp
        return jsonify({"ok": False}), 401

    @app.route("/auth/logout")
    def auth_logout():
        resp = redirect("/")
        resp.delete_cookie(COOKIE_NAME)
        return resp

    @app.before_request
    def require_auth():
        if request.path.startswith("/auth/"):
            return
        cookie = request.cookies.get(COOKIE_NAME, "")
        if _valid_cookie(cookie):
            return
        if request.path.startswith("/api/") or request.method == "POST":
            from flask import jsonify
            return jsonify({"error": "unauthorized"}), 401
        resp = make_response(LOGIN_HTML)
        resp.headers["Content-Type"] = "text/html; charset=utf-8"
        return resp

    print(f"[AUTH] ✅ 密码认证已启用，Cookie 有效期 {COOKIE_TTL//3600}h")
