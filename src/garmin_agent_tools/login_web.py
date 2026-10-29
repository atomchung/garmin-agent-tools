"""Short-lived loopback login form. Credentials never enter command arguments or chat."""
from __future__ import annotations
import html
import queue
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from .core import private_login, state_home
from .auth import AuthError


def serve(region: str, port: int = 8765, timeout: int = 600, *, open_browser: bool = False, backend: str = 'api') -> None:
    """Serve a same-host, nonce-protected login for the user's private browser takeover."""
    if backend == 'personal':
        from . import personal
        personal.home()
    elif backend == 'api':
        state_home()
    else:
        raise ValueError('Unknown login backend')
    nonce=secrets.token_urlsafe(24)
    shared={'stage':'login','diagnosis':None}
    lock=threading.Lock()
    otp=queue.Queue(maxsize=1)
    origin=f'http://127.0.0.1:{port}'
    root=f'/{nonce}'

    def stage(value, diagnosis=None):
        with lock:
            shared.update(stage=value,diagnosis=diagnosis)

    def mfa():
        stage('mfa')
        try:
            return otp.get(timeout=min(timeout,300))
        except queue.Empty:
            raise AuthError('mfa_timeout') from None

    def worker(email,password):
        try:
            if backend == 'personal':
                personal.configure_key(password)
            else:
                private_login(email,password,region,mfa)
            stage('success')
        except AuthError as exc:
            stage('failed',exc.report())
        except Exception:
            stage('failed',AuthError().report())
        finally:
            email=password=None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def allowed(self):
            return self.headers.get('Host')==f'127.0.0.1:{port}' and urlsplit(self.path).path==root and not urlsplit(self.path).query

        def send_page(self,status,body):
            encoded=body.encode()
            self.send_response(status)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(encoded)))
            self.send_header('Cache-Control','no-store')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('X-Frame-Options','DENY')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; style-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if not self.allowed():
                self.send_page(404,'Not found'); return
            with lock:
                current=dict(shared)
            refresh='<meta http-equiv="refresh" content="2">' if current['stage']=='working' else ''
            style='''<style>
              *{box-sizing:border-box}body{margin:0;padding:40px 20px;background:#f2f5f6;color:#18313b;font:17px/1.6 system-ui,sans-serif}
              main{max-width:720px;margin:auto}header{margin-bottom:28px}.eyebrow{font-size:13px;letter-spacing:.12em;color:#506c77}
              h1{font-size:32px;line-height:1.25;margin:8px 0 12px}h2{font-size:23px;margin-top:0}p{margin:12px 0}
              .card{background:white;border:1px solid #d6e1e5;border-radius:16px;padding:28px;margin:18px 0}
              .note{font-size:14px;color:#45616d}label{display:block;margin:18px 0}input{display:block;width:100%;padding:12px;margin-top:6px;border:1px solid #8ca3ae;border-radius:7px;font:inherit}
              button{background:#165b64;color:white;border:0;border-radius:7px;padding:12px 20px;font:inherit;cursor:pointer}
              a{color:#145969}.steps{font-size:14px;color:#45616d}.result{border-left:5px solid #b76421}.success{border-left:5px solid #267b5b}
              details{margin-top:18px}summary{cursor:pointer;font-weight:600}code{overflow-wrap:anywhere;font-size:14px}
              @media(max-width:480px){body{padding:24px 14px}.card{padding:20px}h1{font-size:28px}}
            </style>'''
            head='<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'+refresh+'<title>Private Garmin login</title>'+style+'</head><body><main>'
            info='''<header><div class="eyebrow">GARMIN AGENT TOOLS · 本機私人登入</div>
            <h1>由你親自連接 Garmin</h1><p>帳密與驗證碼由你輸入；Agent 不需要代填，也不需要看見它們。</p>
            <div class="steps">輸入帳密 → Garmin 驗證／MFA（若需要） → 儲存登入憑證<br>資料讀取與跨程式重用，需要之後分別驗證。</div></header>
            <p class="note">這是非官方工具的本機表單，並非 Garmin 官方登入頁。你的輸入會經過本機程式，再以 HTTPS 送往 Garmin；不傳給工具發布者。本機憑證不是對同權限的電腦管理者加密。請勿把任何憑證貼進對話。</p>'''
            if backend == 'personal':
                info='''<header><div class="eyebrow">GARMIN AGENT TOOLS · 官方 PERSONAL API</div><h1>你在 Garmin 登入，工具只接收 Key</h1>
                <p>先在 Garmin 官網自行登入並建立 Personal API Key，再回到本機頁面交給程式。Agent 不需要看見 Key、密碼或驗證碼。</p>
                <div class="steps">Garmin 官網登入 → 建立 Key → 本機換取 token → 分別驗證 MCP 讀取與程式重用</div></header>
                <p class="note">Key 與 token 只存於本機私人資料夾，不送給工具發布者或對話。具有同等電腦權限的程序仍可能讀取它們。此頁不會讀資料、建立課表或寫入行事曆。</p>'''
            hidden=f'<input type="hidden" name="csrf" value="{nonce}">'
            form=f'<form action="{root}" method="post">'+hidden
            if current['stage']=='login':
                body='<section class="card"><h2>開始本機程式登入</h2>'+form+'<label>Garmin email · 電子郵件<input type="email" name="email" autocomplete="username" required></label><label>Garmin password · 密碼<input type="password" name="password" autocomplete="off" required></label><button type="submit">由我送出登入</button></form><p class="note">遇到限流或拒絕存取會停止，不會自動換路徑重試。</p></section>'
                if backend == 'personal':
                    body='<section class="card"><h2>連接官方 Personal API</h2><p><a href="'+personal.KEY_PAGE+'" target="_blank" rel="noreferrer noopener">到 Garmin 官網登入並建立 Key</a></p><p>在 Garmin 選擇 Create Key、閱讀條款並選擇有效期。Key 只顯示一次，請在私人操作中保管。</p>'+form+'<label>Personal API Key<input type="password" name="api_key" autocomplete="off" required></label><button type="submit">由我連接唯讀資料</button></form><p class="note">此動作只向 Garmin 換取 token；遇到 429 或拒絕就停止。若剛遭限流，請先停止，不要反覆送出。</p></section>'
            elif current['stage']=='mfa':
                body='<section class="card"><h2>Garmin 要求驗證碼</h2><p>請查看 Garmin 發給你的驗證碼，由你在這裡送出。</p>'+form+'<label>Garmin verification code<input type="password" name="otp" autocomplete="one-time-code" required></label><button type="submit">送出驗證碼</button></form></section>'
            elif current['stage']=='success':
                body='<section class="card success"><h2>登入完成 · Login completed</h2><p>登入憑證已儲存在本機。之後程式使用這份憑證存取 Garmin，不必每次重填密碼。</p><p><strong>資料讀取：尚未驗證。第二個程式重用：尚未驗證。</strong></p><p>你可以關閉此頁並告訴 Agent 登入已完成。這次沒有建立課表、排日曆或推送手錶。</p></section>'
                if backend == 'personal':
                    body='<section class="card success"><h2>token 已取得並儲存在本機</h2><p>工具已取得 Garmin 發出的存取憑證。Key 和 token 不會顯示在對話中。</p><p><strong>MCP 資料讀取：尚未驗證。第二個程式重用：尚未驗證。</strong></p><p>請只告訴 Agent「連接完成」，不要貼 Key 或終端內容。</p></section>'
            elif current['stage']=='failed':
                diagnosis=current['diagnosis'] or AuthError().report()
                labels={'rate_limited':'Garmin 暫時限制請求（429）','invalid_credentials':'Garmin 回報帳號或密碼不符','access_denied':'Garmin 拒絕這次存取','account_blocked':'Garmin 回報帳號遭鎖定','network_error':'網路連線未完成','authentication_failed':'程式登入未完成，原因未知','mfa_rejected':'Garmin 回報驗證碼不符','mfa_timeout':'等待驗證碼已逾時','credential_override':'目前設定會覆蓋這次登入','storage_error':'本機憑證儲存未完成'}
                password='Garmin 明確回報帳密不符。' if diagnosis['code']=='invalid_credentials' else '尚無法判斷帳密正誤。'
                body='<section class="card result"><h2>'+html.escape(labels.get(diagnosis['code'],'登入未完成'))+'</h2><p>'+password+'</p><p>本次嘗試已停止，不會自動重試。</p><p>'+html.escape(diagnosis['message'])+'</p><p>'+html.escape(diagnosis['next_step'])+'</p><details><summary>提供給排查者的安全代碼</summary><code>'+html.escape(diagnosis['code'])+'</code><p class="note">此處不包含帳密、token 或 Garmin 原始回應。</p></details></section>'
            else:
                body='<section class="card"><h2>正在等待 Garmin 回應…</h2><p>Signing in… 此頁會更新進度，請勿重複送出。自動更新畫面不會重新登入。</p></section>'
            help_text='''<section class="card"><h2>你可以自行分辨的三件事</h2>
            <p><strong>官網登入</strong>：你能否在 Garmin 官方網站完成登入。<br><strong>程式登入</strong>：本機工具能否取得並儲存自己的憑證。<br><strong>資料讀取</strong>：程式能否取得你要求的資料。</p>
            <p>三者分開驗證；官網成功不表示本機程式已連接，429 也不代表密碼錯誤。</p>
            <p class="note">若剛收到 429 或封鎖訊號，先停止本次嘗試；不要藉其他登入入口連續重試。</p>
            <a href="https://connect.garmin.com/" target="_blank" rel="noreferrer noopener">Garmin 官方網站</a>
            <details><summary>想全程在 Garmin 官網登入？</summary><p>官方 Personal API 支援你在 Garmin 網站建立 API Key，再由程式換取 access token。本工具目前的這個表單使用另一套登入方式，尚未接入 Personal API；請勿把 Key 貼進本表單或對話，也不要複製瀏覽器 cookie。</p>
            <a href="https://developer.garmin.com/gc-developer-program/personal/personal-api/" target="_blank" rel="noreferrer noopener">閱讀 Garmin 官方 Personal API 流程</a></details></section>'''
            help_text=help_text.replace('尚未接入 Personal API；請勿把 Key 貼進本表單或對話，也不要複製瀏覽器 cookie。','另有 --backend personal 的專用 Key 表單；請勿把 Key 貼進帳密表單或對話，也不要複製瀏覽器 cookie。')
            if backend == 'personal':
                help_text='<section class="card"><h2>token 是什麼？</h2><p>它是程式存取資料的憑證。Garmin 文件規定最長 24 小時，或 Key 到期為止；程式可用尚有效的 Key 取得下一份 token。這不等於讀取成功，仍需用 MCP 實測。</p><p>本機 Personal MCP 只有讀取與連線狀態兩個工具，不提供課表、日曆或手錶寫入。</p></section>'
            self.send_page(200,head+info+body+help_text+'</main></body></html>')

        def do_POST(self):
            if not self.allowed() or self.headers.get('Origin')!=origin or self.headers.get('Content-Type','').split(';')[0]!='application/x-www-form-urlencoded':
                self.send_page(403,'Refused'); return
            try:
                length=int(self.headers.get('Content-Length','0'))
            except ValueError:
                self.send_page(400,'Invalid request'); return
            if not 0<length<=8192:
                self.send_page(413,'Invalid request'); return
            try:
                values=parse_qs(self.rfile.read(length).decode(),max_num_fields=4)
            except (ValueError,UnicodeDecodeError):
                self.send_page(400,'Invalid request'); return
            if not secrets.compare_digest(values.get('csrf',[''])[0],nonce):
                self.send_page(403,'Refused'); return
            with lock:
                current=shared['stage']
                if current=='login' and backend=='personal' and values.get('api_key'):
                    shared['stage']='working'
                    threading.Thread(target=worker,args=(None,values['api_key'][0]),daemon=True).start()
                elif current=='login' and backend=='api' and values.get('email') and values.get('password'):
                    shared['stage']='working'
                    threading.Thread(target=worker,args=(values['email'][0],values['password'][0]),daemon=True).start()
                elif current=='mfa' and values.get('otp') and otp.empty():
                    shared['stage']='working'
                    otp.put_nowait(values['otp'][0])
                else:
                    self.send_page(409,'Login stage changed; reload'); return
            self.send_response(303); self.send_header('Location',root); self.send_header('Cache-Control','no-store'); self.end_headers()

    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    server.timeout=0.5
    print(origin+root,flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(origin+root)
    deadline=time.monotonic()+timeout
    try:
        while time.monotonic()<deadline:
            server.handle_request()
    finally:
        server.server_close()
