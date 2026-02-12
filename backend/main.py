import base64
import hashlib
import hmac
import os
import threading
import time
from typing import Optional

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

import db
from image_compress import compress_images_in_folder, watch_and_compress
from schemas import LogEntry, SftpSettings, Status, SyncRequest
from sftp_sync import sync_once, test_connection
from sftp_upload import watch_and_upload

app = FastAPI(title="SFTP Sync Service")
lock = threading.Lock()
compress_lock = threading.Lock()
upload_lock = threading.Lock()
_last_run: Optional[str] = None
_running = False
_stop_requested = False
_compress_running = False
_compress_stop_requested = False
_upload_running = False
_upload_stop_requested = False
APP_USER = os.getenv("APP_USER", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "password")
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me")
SESSION_NAME = "session"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"]
    ,
    allow_headers=["*"],
)


def _log(level: str, message: str, detail: Optional[str] = None) -> None:
    db.insert_log(level, message, detail)


def _sign_token(username: str, issued: int) -> str:
    payload = f"{username}:{issued}"
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload.encode() + b"|" + sig).decode()


def _verify_token(token: str) -> Optional[str]:
    try:
        raw = base64.urlsafe_b64decode(token.encode())
        payload, sig = raw.rsplit(b"|", 1)
        username, issued_str = payload.decode().split(":", 1)
    except Exception:
        return None

    expected = hmac.new(SESSION_SECRET.encode(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig):
        return None

    # Optional expiration (12h)
    issued = int(issued_str)
    if time.time() - issued > 60 * 60 * 12:
        return None
    return username


def require_auth(session: Optional[str] = Cookie(default=None, alias=SESSION_NAME)):
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = _verify_token(session)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


def _check_stop_requested() -> bool:
    """Check if stop was requested. Used as callback for sync_once."""
    global _stop_requested
    return _stop_requested


def _check_compress_stop_requested() -> bool:
    """Check if compress stop was requested."""
    global _compress_stop_requested
    return _compress_stop_requested


def _check_upload_stop_requested() -> bool:
    """Check if upload stop was requested."""
    global _upload_stop_requested
    return _upload_stop_requested


def _run_sync() -> None:
    global _last_run, _running, _stop_requested
    settings_data = db.load_settings()
    if not settings_data:
        _log("WARN", "同期スキップ: 設定が未構成です")
        return

    settings = SftpSettings(**settings_data)
    if not lock.acquire(blocking=False):
        _log("INFO", "同期スキップ: 既に実行中です")
        return
    _running = True
    _stop_requested = False

    try:
        _log("INFO", f"同期監視開始: {settings.host} → {settings.local_dir}")
        
        # 秒数が0の場合は1回のみ実行
        if settings.sync_interval_seconds == 0:
            _log("INFO", "1回のみ実行モード")
            cycle_count = 1
            try:
                _log("INFO", f"[監視サイクル {cycle_count}] 差異チェック開始")
                summary = sync_once(settings, _log, _check_stop_requested)
                if summary['copied'] > 0:
                    _log("INFO", f"[監視サイクル {cycle_count}] 差異検出 - コピー={summary['copied']}件")
                else:
                    _log("INFO", f"[監視サイクル {cycle_count}] 差異なし")
            except Exception as exc:  # noqa: BLE001
                _log("ERROR", f"[監視サイクル {cycle_count}] 同期処理エラー", detail=str(exc))
            _log("INFO", f"同期監視終了: 1回実行完了")
        else:
            _log("INFO", f"継続的監視モード: 監視間隔={settings.sync_interval_seconds}秒")
            cycle_count = 0
            while not _stop_requested:
                cycle_count += 1
                try:
                    _log("INFO", f"[監視サイクル {cycle_count}] 差異チェック開始")
                    summary = sync_once(settings, _log, _check_stop_requested)

                    if _stop_requested:
                        _log("WARN", f"同期監視停止: ユーザーによる停止要求 (サイクル={cycle_count}回)")
                        break

                    if summary['copied'] > 0:
                        _log("INFO", f"[監視サイクル {cycle_count}] 差異検出 - コピー={summary['copied']}件")
                    else:
                        _log("INFO", f"[監視サイクル {cycle_count}] 差異なし - 次のチェックまで待機")

                    # Wait specified seconds before next check (unless stop requested)
                    interval_ms = settings.sync_interval_seconds
                    for _ in range(interval_ms * 10):  # *10 for 0.1s increments
                        if _stop_requested:
                            break
                        time.sleep(0.1)

                except Exception as exc:  # noqa: BLE001
                    _log("ERROR", f"[監視サイクル {cycle_count}] 同期処理エラー", detail=str(exc))
                    # Continue monitoring even after error
                    time.sleep(settings.sync_interval_seconds)

            _log("INFO", f"同期監視終了: 合計{cycle_count}サイクル実行")

    except Exception as exc:  # noqa: BLE001
        _log("ERROR", "同期監視失敗: 予期しないエラーが発生しました", detail=str(exc))
    finally:
        _running = False
        _stop_requested = False
        lock.release()
        _last_run = db.get_connection().execute("SELECT datetime('now', '+9 hours')").fetchone()[0]


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()


@app.get("/", response_class=HTMLResponse)
def root_page():
    # Lightweight HTML/JS login + menu UI served directly from the API host.
    return """
        <!DOCTYPE html>
        <html lang=\"ja\">
        <head>
            <meta charset=\"UTF-8\" />
            <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
            <title>SFTP画像圧縮処理システム</title>
            <style>
                :root { font-family: 'Segoe UI', system-ui, sans-serif; background: #0f172a; color: #e2e8f0; }
                body { margin: 0; padding: 24px; }
                body.login-mode { display: flex; align-items: center; justify-content: center; min-height: 100vh; }
                .card { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 20px; max-width: 100%; box-shadow: 0 10px 40px rgba(0,0,0,0.35); }
                body.login-mode .card { max-width: 720px; width: 100%; }
                h1 { margin-top: 0; font-size: 22px; letter-spacing: 0.3px; }
                label { display: block; margin: 8px 0 4px; font-size: 13px; color: #cbd5e1; }
                input { width: 100%; padding: 10px 12px; border-radius: 8px; border: 1px solid #1f2937; background: #0b1220; color: #e2e8f0; }
                button { background: linear-gradient(90deg, #22c55e, #16a34a); border: none; color: #0b1220; font-weight: 700; padding: 10px 16px; border-radius: 10px; cursor: pointer; box-shadow: 0 8px 20px rgba(34,197,94,0.25); white-space: nowrap; flex-shrink: 0; }
                button:disabled { opacity: 0.4; cursor: not-allowed; }
                .row { display: flex; gap: 8px; margin-top: 12px; overflow-x: auto; align-items: center; padding-bottom: 8px; }
                .row::-webkit-scrollbar { height: 6px; }
                .row::-webkit-scrollbar-track { background: #0b1220; border-radius: 3px; }
                .row::-webkit-scrollbar-thumb { background: #1f2937; border-radius: 3px; }
                .menu { display: none; }
                .section { margin-top: 18px; padding: 14px; border: 1px solid #1f2937; border-radius: 10px; background: #0b1220; }
                pre { background: #0b1220; padding: 12px; border-radius: 10px; border: 1px solid #1f2937; color: #cbd5e1; overflow-x: auto; }
                .tag { display: inline-block; padding: 3px 8px; border-radius: 999px; background: #1e293b; color: #e2e8f0; font-size: 12px; margin-right: 6px; }
                a { color: #38bdf8; text-decoration: none; }
            </style>
        </head>
        <body class=\"login-mode\">
            <div class=\"card\">
                <h1>SFTP画像圧縮処理システム</h1>
                <div id=\"auth\">
                    <label for=\"user\">ユーザー名</label>
                    <input id=\"user\" value=\"admin\" maxlength=\"20\" style=\"max-width: 300px;\" />
                    <label for=\"pass\">パスワード</label>
                    <input id=\"pass\" type=\"password\" value=\"password\" maxlength=\"20\" style=\"max-width: 300px;\" />
                    <div class=\"row\">
                        <button id=\"loginBtn\">ログイン</button>
                    </div>
                    <p id=\"msg\" style=\"color:#f472b6; margin-top:8px\"></p>
                </div>
                <div id=\"menu\" class=\"menu\">
                    <div class=\"row\">
                        <button id=\"btnEditSettings\">設定を編集</button>
                        <button id=\"btnManageUsers\">ユーザー管理</button>
                        <button id=\"btnLogs\">ログ表示</button>
                        <button id=\"btnClearLogs\" style=\"background: linear-gradient(90deg,#94a3b8,#64748b); color:#0b1220;\">ログクリア</button>
                        <button id=\"btnSyncToggle\" style=\"background: linear-gradient(90deg,#3b82f6,#2563eb); color:#0b1220;\">SFTPから取込 開始</button>
                        <button id=\"btnCompressToggle\" style=\"background: linear-gradient(90deg,#3b82f6,#2563eb); color:#0b1220;\">画像圧縮 開始</button>
                        <button id=\"btnUploadToggle\" style=\"background: linear-gradient(90deg,#3b82f6,#2563eb); color:#0b1220;\">SFTPアップ 開始</button>
                        <button id=\"logoutBtn\" style=\"margin-left:auto; background: linear-gradient(90deg,#f97316,#ea580c); color:#0b1220\">ログアウト</button>
                    </div>
                    <div id=\"panel\" class=\"section\"></div>
                </div>
            </div>

            <script>
                const msg = document.getElementById('msg');
                const menu = document.getElementById('menu');
                const auth = document.getElementById('auth');
                const panel = document.getElementById('panel');

                async function api(path, opts = {}) {
                    const res = await fetch(path, {
                        credentials: 'include',
                        headers: { 'Content-Type': 'application/json', ...(opts.headers||{}) },
                        ...opts,
                    });
                    if (!res.ok) throw new Error(await res.text() || res.statusText);
                    return res.status === 204 ? null : res.json();
                }

                async function checkAuth() {
                    try {
                        await api('/auth/me');
                        auth.style.display = 'none';
                        menu.style.display = 'block';
                        document.body.classList.remove('login-mode');
                        msg.textContent = '';
                        // Check sync status on login/reload to restore button state
                        // Wait for next tick to ensure DOM is ready
                        setTimeout(() => checkSyncStatus(), 0);
                        setTimeout(() => checkCompressStatus(), 0);
                        setTimeout(() => checkUploadStatus(), 0);
                    } catch {
                        auth.style.display = 'block';
                        menu.style.display = 'none';
                        document.body.classList.add('login-mode');
                    }
                }

                async function login() {
                    msg.textContent = '';
                    try {
                        const username = document.getElementById('user').value;
                        const password = document.getElementById('pass').value;
                        await api('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) });
                        await checkAuth();
                    } catch (e) {
                        msg.textContent = e.message || 'login failed';
                    }
                }

                async function logout() {
                    await api('/auth/logout', { method: 'POST' });
                    await checkAuth();
                    panel.innerHTML = '';
                }
                                async function editSettings() {
                                        try {
                                                const current = await api('/settings');
                                                const s = current || { host:'', port:22, username:'', password:null, private_key_path:'', remote_dir:'', local_dir:'', compress_output_dir:'', compress_quality:85, remote_output_dir:'' };
                                                const hasPassword = s.password === '********';
                                                panel.innerHTML = `
                                                    <div class=\"tag\">Settings</div>
                                                    <div class=\"section\">
                                                        <label>接続先ドメイン (host)</label>
                                                        <input id=\"f_host\" value=\"${s.host || ''}\" />
                                                        <label>ポート (port)</label>
                                                        <input id=\"f_port\" type=\"number\" value=\"${s.port ?? 22}\" />
                                                        <label>ユーザー (username)</label>
                                                        <input id=\"f_user\" value=\"${s.username || ''}\" />
                                                        <label>パスワード (password)</label>
                                                        <input id=\"f_pass\" type=\"password\" value=\"${hasPassword ? s.password : ''}\" placeholder=\"${hasPassword ? '' : '新しいパスワードを入力'}\" />
                                                        ${hasPassword ? '<p style=\"color:#94a3b8; font-size:12px; margin:4px 0 10px\">※パスワードが保存されています (上記のマスク表示: ********)。変更する場合のみ新しいパスワードを入力してください。</p>' : ''}
                                                        <label>接続先フォルダー (remote_dir)</label>
                                                        <input id=\"f_remote\" value=\"${s.remote_dir || ''}\" />
                                                        <label>接続先出力フォルダー (remote_output_dir)</label>
                                                        <input id=\"f_remote_output\" value=\"${s.remote_output_dir || ''}\" placeholder=\"任意: SFTP接続先への出力先\" />
                                                        <label>コピー先フォルダー (local_dir)</label>
                                                        <input id=\"f_local\" value=\"${s.local_dir || ''}\" />
                                                        <label>画像圧縮出力先フォルダー (compress_output_dir)</label>
                                                        <input id=\"f_compress\" value=\"${s.compress_output_dir || ''}\" placeholder=\"任意: 画像圧縮後の出力先\" />
                                                        <label>画像圧縮率 (compress_quality: 1-100)</label>
                                                        <input id=\"f_quality\" type=\"number\" min=\"1\" max=\"100\" value=\"${s.compress_quality ?? 85}\" />
                                                        <p style=\"color:#94a3b8; font-size:12px; margin:4px 0 10px\">※数値が大きいほど高画質（ファイルサイズ大）、小さいほど低画質（ファイルサイズ小）</p>
                                                        <label>画像リサイズ 横幅dpi</label>
                                                        <input id=\"f_resize_width\" type=\"number\" min=\"1\" value=\"${s.resize_width_dpi || ''}\" placeholder=\"横幅dpi\" style=\"width:160px;\" />
                                                        <p style=\"color:#94a3b8; font-size:12px; margin:4px 0 10px\">※未入力の場合はリサイズしません。横幅を指定すると縦はアスペクト比を保持して自動計算されます。</p>
                                                        <label>SFTP取込 監視間隔 (sync_interval_seconds)</label>
                                                        <input id=\"f_sync_interval\" type=\"number\" min=\"1\" value=\"${s.sync_interval_seconds ?? 5}\" />
                                                        <p style=\"color:#94a3b8; font-size:12px; margin:4px 0 10px\">※秒単位: SFTPから取込処理の監視間隔</p>
                                                        <label>画像圧縮 監視間隔 (compress_interval_seconds)</label>
                                                        <input id=\"f_compress_interval\" type=\"number\" min=\"1\" value=\"${s.compress_interval_seconds ?? 10}\" />
                                                        <p style=\"color:#94a3b8; font-size:12px; margin:4px 0 10px\">※秒単位: 画像圧縮処理の監視間隔</p>
                                                        <label>SFTPアップロード 監視間隔 (upload_interval_seconds)</label>
                                                        <input id=\"f_upload_interval\" type=\"number\" min=\"1\" value=\"${s.upload_interval_seconds ?? 10}\" />
                                                        <p style=\"color:#94a3b8; font-size:12px; margin:4px 0 10px\">※秒単位: SFTPアップロード処理の監視間隔</p>
                                                        <div class=\"row\" style=\"margin-top:10px\">
                                                            <button id=\"saveSettings\">保存</button>
                                                            <button id=\"testSettings\" style=\"background: linear-gradient(90deg,#60a5fa,#3b82f6); color:#0b1220\">接続テスト</button>
                                                        </div>
                                                    </div>
                                                `;
                                                document.getElementById('saveSettings').onclick = async () => {
                                                    const saveBtn = document.getElementById('saveSettings');
                                                    const testBtn = document.getElementById('testSettings');
                                                    try {
                                                        const passwordValue = (document.getElementById('f_pass')).value;
                                                        const resizeWidth = (document.getElementById('f_resize_width')).value;
                                                        const payload = {
                                                            host: (document.getElementById('f_host')).value,
                                                            port: parseInt((document.getElementById('f_port')).value || '22'),
                                                            username: (document.getElementById('f_user')).value,
                                                            password: passwordValue === '********' ? '' : passwordValue,
                                                            remote_dir: (document.getElementById('f_remote')).value,
                                                            remote_output_dir: (document.getElementById('f_remote_output')).value,
                                                            local_dir: (document.getElementById('f_local')).value,
                                                            compress_output_dir: (document.getElementById('f_compress')).value,
                                                            compress_quality: parseInt((document.getElementById('f_quality')).value || '85'),
                                                            resize_width_dpi: resizeWidth ? parseInt(resizeWidth) : null,
                                                            sync_interval_seconds: parseInt((document.getElementById('f_sync_interval')).value || '5'),
                                                            compress_interval_seconds: parseInt((document.getElementById('f_compress_interval')).value || '10'),
                                                            upload_interval_seconds: parseInt((document.getElementById('f_upload_interval')).value || '10'),
                                                        };
                                                        // simple client-side validation
                                                        if (!payload.host || !payload.username || (!payload.password && !hasPassword && !s.private_key_path) || !payload.remote_dir || !payload.local_dir) {
                                                            panel.innerHTML += `<p style="color:#f472b6">必須項目が未入力です</p>`;
                                                            return;
                                                        }
                                                        saveBtn.disabled = true;
                                                        testBtn.disabled = true;
                                                        panel.innerHTML += `<p style="color:#94a3b8">保存中...</p>`;
                                                        await api('/settings', { method: 'POST', body: JSON.stringify(payload) });
                                                        panel.innerHTML = `<p style="color:#4ade80">保存しました。スケジューラーが更新されます。</p>`;
                                                        
                                                        // メインメニューに戻る
                                                        setTimeout(() => {
                                                            panel.innerHTML = '';
                                                            showMenu();
                                                        }, 1500);
                                                    } catch (e) {
                                                        panel.innerHTML += `<p style="color:#f472b6">${e.message}</p>`;
                                                    } finally {
                                                        saveBtn.disabled = false;
                                                        testBtn.disabled = false;
                                                    }
                                                };
                                                document.getElementById('testSettings').onclick = async () => {
                                                    const saveBtn = document.getElementById('saveSettings');
                                                    const testBtn = document.getElementById('testSettings');
                                                    const statusMsg = document.createElement('p');
                                                    statusMsg.style.color = '#94a3b8';
                                                    statusMsg.style.marginTop = '10px';
                                                    statusMsg.textContent = '接続テスト中...';
                                                    panel.appendChild(statusMsg);

                                                    try {
                                                        saveBtn.disabled = true;
                                                        testBtn.disabled = true;

                                                        const passwordValue = (document.getElementById('f_pass')).value;
                                                        const testResizeWidth = (document.getElementById('f_resize_width')).value;
                                                        const payload = {
                                                            host: (document.getElementById('f_host')).value,
                                                            port: parseInt((document.getElementById('f_port')).value || '22'),
                                                            username: (document.getElementById('f_user')).value,
                                                            password: passwordValue === '********' ? '' : passwordValue,
                                                            remote_dir: (document.getElementById('f_remote')).value,
                                                            remote_output_dir: (document.getElementById('f_remote_output')).value,
                                                            local_dir: (document.getElementById('f_local')).value,
                                                            compress_output_dir: (document.getElementById('f_compress')).value,
                                                            compress_quality: parseInt((document.getElementById('f_quality')).value || '85'),
                                                            resize_width_dpi: testResizeWidth ? parseInt(testResizeWidth) : null,
                                                            sync_interval_seconds: parseInt((document.getElementById('f_sync_interval')).value || '5'),
                                                            compress_interval_seconds: parseInt((document.getElementById('f_compress_interval')).value || '10'),
                                                            upload_interval_seconds: parseInt((document.getElementById('f_upload_interval')).value || '10'),
                                                        };
                                                        await api('/settings/test', { method: 'POST', body: JSON.stringify(payload) });
                                                        statusMsg.style.color = '#4ade80';
                                                        statusMsg.textContent = '✓ 接続テスト成功';
                                                    } catch (e) {
                                                        statusMsg.style.color = '#f472b6';
                                                        statusMsg.textContent = `✗ ${e.message}`;
                                                    } finally {
                                                        saveBtn.disabled = false;
                                                        testBtn.disabled = false;
                                                    }
                                                };
                                        } catch (e) {
                                                panel.innerHTML = `<p style=\"color:#f472b6\">${e.message}</p>`;
                                        }
                                }

                async function showLogs() {
                    try {
                        const data = await api('/logs?limit=1000');
                        const levelColors = {
                            'INFO': '#4ade80',
                            'WARN': '#fbbf24',
                            'ERROR': '#f87171'
                        };
                        const logsHtml = data.map(log => {
                            const color = levelColors[log.level] || '#94a3b8';
                            const detail = log.detail ? `<div style="margin-left:20px; color:#94a3b8; font-size:11px; margin-top:4px;">詳細: ${log.detail}</div>` : '';
                            return `
                                <div style="margin-bottom:12px; padding:10px; background:#0b1220; border-radius:8px; border-left:3px solid ${color};">
                                    <div style="display:flex; align-items:center; gap:10px;">
                                        <span style="color:${color}; font-weight:700; font-size:11px; min-width:50px;">${log.level}</span>
                                        <span style="color:#64748b; font-size:11px;">${log.created_at}</span>
                                    </div>
                                    <div style="color:#e2e8f0; margin-top:6px;">${log.message}</div>
                                    ${detail}
                                </div>
                            `;
                        }).join('');
                        panel.innerHTML = `
                            <div class="tag">ログ (最新1000件)</div>
                            <div style="max-height:500px; overflow-y:auto; margin-top:10px;">
                                ${logsHtml || '<p style="color:#94a3b8;">ログがありません</p>'}
                            </div>
                        `;
                    } catch (e) {
                        panel.innerHTML = `<p style="color:#f472b6">${e.message}</p>`;
                    }
                }

                function showMenu() {
                    panel.innerHTML = '';
                }

                async function manageUsers() {
                    try {
                        const current = await api('/users');
                        panel.innerHTML = `
                            <div class="tag">ユーザー管理</div>
                            <div class="section">
                                <label>ユーザーID (username)</label>
                                <input id="f_username" value="${current.username || 'admin'}" />
                                <label>パスワード (password)</label>
                                <input id="f_user_password" type="password" placeholder="新しいパスワードを入力" />
                                <p style="color:#94a3b8; font-size:12px; margin:4px 0 10px">※次回ログイン時から新しい認証情報が有効になります</p>
                                <div class="row" style="margin-top:10px">
                                    <button id="saveUsers">保存</button>
                                </div>
                            </div>
                        `;
                        
                        document.getElementById('saveUsers').onclick = async () => {
                            const saveBtn = document.getElementById('saveUsers');
                            try {
                                const username = (document.getElementById('f_username')).value.trim();
                                const password = (document.getElementById('f_user_password')).value.trim();
                                
                                if (!username) {
                                    panel.innerHTML += `<p style="color:#f472b6">ユーザーIDは必須です</p>`;
                                    return;
                                }
                                if (!password) {
                                    panel.innerHTML += `<p style="color:#f472b6">パスワードは必須です</p>`;
                                    return;
                                }
                                
                                saveBtn.disabled = true;
                                panel.innerHTML += `<p style="color:#94a3b8">保存中...</p>`;
                                
                                const payload = { username, password };
                                await api('/users', { method: 'POST', body: JSON.stringify(payload) });
                                panel.innerHTML = `<p style="color:#4ade80">ユーザー情報を保存しました。次回ログイン時から新しい認証情報が有効になります。</p>`;
                                
                                // メインメニューに戻る
                                setTimeout(() => {
                                    panel.innerHTML = '';
                                    showMenu();
                                }, 1500);
                            } catch (e) {
                                panel.innerHTML += `<p style="color:#f472b6">${e.message}</p>`;
                                saveBtn.disabled = false;
                            }
                        };
                    } catch (e) {
                        panel.innerHTML = `<p style="color:#f472b6">${e.message}</p>`;
                    }
                }

                async function clearLogs() {
                    const btnClearLogs = document.getElementById('btnClearLogs');
                    if (!confirm('全てのログを削除してもよろしいですか?')) {
                        return;
                    }
                    try {
                        btnClearLogs.disabled = true;
                        await api('/logs', { method: 'DELETE' });
                        panel.innerHTML = `<p style="color:#4ade80">ログをクリアしました。</p>`;
                        // Auto-refresh logs to show empty state
                        setTimeout(showLogs, 1000);
                    } catch (e) {
                        panel.innerHTML = `<p style="color:#f472b6">${e.message}</p>`;
                    } finally {
                        btnClearLogs.disabled = false;
                    }
                }

                function updateSyncToggleButton(running) {
                    const btn = document.getElementById('btnSyncToggle');
                    if (running) {
                        btn.textContent = 'SFTPから取込 停止';
                        btn.style.background = 'linear-gradient(90deg,#ef4444,#dc2626)';
                    } else {
                        btn.textContent = 'SFTPから取込 開始';
                        btn.style.background = 'linear-gradient(90deg,#3b82f6,#2563eb)';
                    }
                }

                async function toggleSync() {
                    const status = await api('/status');
                    if (status.running) {
                        // Currently running, stop it
                        await api('/sync/stop', { method: 'POST' });
                        panel.innerHTML = '<p style="color:#fbbf24">監視停止を要求しました。現在の処理が完了するまでお待ちください。</p>';
                    } else {
                        // Currently stopped, start it
                        const settings = await api('/settings');
                        const interval = (settings?.sync_interval_seconds !== undefined && settings?.sync_interval_seconds !== null) ? settings.sync_interval_seconds : 5;
                        await api('/sync/run', { method: 'POST', body: JSON.stringify({ force: true }) });
                        if (interval === 0) {
                            panel.innerHTML = '<p style="color:#4ade80">1回のみ同期を実行します。処理完了後は自動的に停止します。</p>';
                        } else {
                            panel.innerHTML = `
                                <p style="color:#4ade80">継続的監視モードを開始しました</p>
                                <p style="color:#94a3b8; margin-top:8px;">両フォルダを${interval}秒毎に監視し、差異があれば自動的に同期します。</p>
                            `;
                        }
                    }
                    setTimeout(checkSyncStatus, 1000);
                }

                async function checkSyncStatus() {
                    try {
                        const status = await api('/status');
                        updateSyncToggleButton(status.running);
                        document.getElementById('btnSyncToggle').disabled = false;
                        if (status.running) {
                            setTimeout(checkSyncStatus, 2000);
                        }
                    } catch (e) {
                        console.error('Status check failed:', e);
                        updateSyncToggleButton(false);
                        document.getElementById('btnSyncToggle').disabled = false;
                    }
                }

                function updateCompressToggleButton(isRunning) {
                    const btn = document.getElementById('btnCompressToggle');
                    if (isRunning) {
                        btn.textContent = '画像圧縮 停止';
                        btn.style.background = 'linear-gradient(90deg,#ef4444,#dc2626)';
                    } else {
                        btn.textContent = '画像圧縮 開始';
                        btn.style.background = 'linear-gradient(90deg,#3b82f6,#2563eb)';
                    }
                }

                async function toggleCompress() {
                    const btn = document.getElementById('btnCompressToggle');
                    try {
                        const settings = await api('/settings');
                        const interval = (settings?.compress_interval_seconds !== undefined && settings?.compress_interval_seconds !== null) ? settings.compress_interval_seconds : 10;
                        const status = await api('/compress/status');
                        if (status.running) {
                            // Currently running, stop it
                            btn.disabled = true;
                            await api('/compress/stop', { method: 'POST' });
                            panel.innerHTML = `<p style="color:#fbbf24">画像圧縮監視を停止中...</p>`;
                            setTimeout(checkCompressStatus, 500);
                        } else {
                            // Not running, start it
                            btn.disabled = true;
                            await api('/compress/run', { method: 'POST' });
                            if (interval === 0) {
                                panel.innerHTML = `
                                    <p style="color:#4ade80">画像圧縮を1回実行します</p>
                                    <p style="color:#94a3b8; margin-top:8px;">処理完了後は自動的に停止します。</p>
                                `;
                            } else {
                                panel.innerHTML = `
                                    <p style="color:#4ade80">画像圧縮監視を開始しました</p>
                                    <p style="color:#94a3b8; margin-top:8px;">コピー先フォルダーを${interval}秒毎に監視し、新規・更新された画像を自動圧縮します。</p>
                                    <p style="color:#94a3b8;">停止するには再度ボタンをクリックしてください。</p>
                                `;
                            }
                            setTimeout(checkCompressStatus, 500);
                        }
                    } catch (e) {
                        panel.innerHTML = `<p style="color:#f472b6">${e.message}</p>`;
                        btn.disabled = false;
                        checkCompressStatus();
                    }
                }

                async function checkCompressStatus() {
                    try {
                        const status = await api('/compress/status');
                        updateCompressToggleButton(status.running);
                        document.getElementById('btnCompressToggle').disabled = false;
                        if (status.running) {
                            setTimeout(checkCompressStatus, 2000);
                        }
                    } catch (e) {
                        console.error('Compress status check failed:', e);
                        updateCompressToggleButton(false);
                        document.getElementById('btnCompressToggle').disabled = false;
                    }
                }

                async function toggleUpload() {
                    const status = await api('/upload/status');
                    const settings = await api('/settings');
                    const interval = (settings?.upload_interval_seconds !== undefined && settings?.upload_interval_seconds !== null) ? settings.upload_interval_seconds : 10;
                    
                    if (status.running) {
                        await api('/upload/stop', { method: 'POST' });
                        panel.innerHTML = '<p style="color:#fbbf24">SFTPアップロード監視を停止中...</p>';
                        setTimeout(checkUploadStatus, 1000);
                    } else {
                        await api('/upload/run', { method: 'POST' });
                        if (interval === 0) {
                            panel.innerHTML = '<p style="color:#4ade80">SFTPアップロードを1回実行します。処理完了後は自動的に停止します。</p>';
                        } else {
                            panel.innerHTML = '<p style="color:#4ade80">SFTPアップロード監視を開始しました。ログを確認してください。</p>';
                        }
                        setTimeout(checkUploadStatus, 1000);
                    }
                }

                function updateUploadToggleButton(running) {
                    const btn = document.getElementById('btnUploadToggle');
                    if (running) {
                        btn.textContent = 'SFTPアップ 停止';
                        btn.style.background = 'linear-gradient(90deg,#ef4444,#dc2626)';
                    } else {
                        btn.textContent = 'SFTPアップ 開始';
                        btn.style.background = 'linear-gradient(90deg,#3b82f6,#2563eb)';
                    }
                }

                async function checkUploadStatus() {
                    try {
                        const status = await api('/upload/status');
                        updateUploadToggleButton(status.running);
                        document.getElementById('btnUploadToggle').disabled = false;
                        if (status.running) {
                            setTimeout(checkUploadStatus, 2000);
                        }
                    } catch (e) {
                        console.error('Upload status check failed:', e);
                        updateUploadToggleButton(false);
                        document.getElementById('btnUploadToggle').disabled = false;
                    }
                }

                document.getElementById('loginBtn').onclick = login;
                document.getElementById('logoutBtn').onclick = logout;
                document.getElementById('btnEditSettings').onclick = editSettings;
                document.getElementById('btnManageUsers').onclick = manageUsers;
                document.getElementById('btnLogs').onclick = showLogs;
                document.getElementById('btnClearLogs').onclick = clearLogs;
                document.getElementById('btnSyncToggle').onclick = toggleSync;
                document.getElementById('btnCompressToggle').onclick = toggleCompress;
                document.getElementById('btnUploadToggle').onclick = toggleUpload;

                checkAuth();
            </script>
        </body>
        </html>
        """


@app.post("/auth/login")
def login(payload: dict, response: Response):
    username = payload.get("username")
    password = payload.get("password")
    
    # Load user credentials from database (or use defaults if not set)
    user_data = db.load_user()
    if user_data:
        valid_user = user_data["username"]
        valid_password = user_data["password"]
    else:
        # Use default credentials if no user data is set in database
        valid_user = APP_USER
        valid_password = APP_PASSWORD
    
    if username != valid_user or password != valid_password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = _sign_token(username, int(time.time()))
    response.set_cookie(
        key=SESSION_NAME,
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 60 * 12,
    )
    return {"user": username}


@app.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_NAME)
    return {"status": "ok"}


@app.get("/auth/me")
def auth_me(user=Depends(require_auth)):
    return {"user": user}


@app.get("/users")
def get_users(user=Depends(require_auth)):
    """Get current login user (without password for security)."""
    user_data = db.load_user()
    if not user_data:
        # Return default if not set
        return {"username": "admin", "password": "********"}
    # Return masked password for security
    return {"username": user_data["username"], "password": "********"}


@app.post("/users")
def update_users(payload: dict, user=Depends(require_auth)):
    """Update login user credentials."""
    username = payload.get("username", "").strip()
    password = payload.get("password", "").strip()
    
    if not username:
        raise HTTPException(status_code=400, detail="ユーザー名は必須です")
    if not password:
        raise HTTPException(status_code=400, detail="パスワードは必須です")
    
    try:
        db.save_user(username, password)
        _log("INFO", f"ログインユーザーを更新しました: {username}")
        return {"status": "ok", "message": "ユーザー情報が更新されました"}
    except Exception as e:  # noqa: BLE001
        _log("ERROR", "ユーザー情報の更新に失敗しました", detail=str(e))
        raise HTTPException(status_code=500, detail="ユーザー情報の更新に失敗しました")


@app.on_event("shutdown")
def on_shutdown() -> None:
    pass


@app.get("/settings", response_model=Optional[SftpSettings])
def get_settings(user=Depends(require_auth)):
    data = db.load_settings()
    if not data:
        return None
    # Mask password in API responses - use fixed mask string if password exists
    if data.get("password"):
        data["password"] = "********"
    else:
        data["password"] = None
    return SftpSettings(**data)


@app.post("/settings", response_model=SftpSettings)
def set_settings(settings: SftpSettings, user=Depends(require_auth)):
    # Basic validations
    if not settings.host or not settings.username:
        raise HTTPException(status_code=422, detail="host/username は必須です")
    # remote_dir must look absolute on SFTP
    if not settings.remote_dir or not settings.remote_dir.startswith("/"):
        raise HTTPException(status_code=422, detail="remote_dir は絶対パスで指定してください（例: /path/to/dir）")
    # local_dir must be absolute path
    from pathlib import Path

    p_local = Path(settings.local_dir)
    if not p_local.is_absolute():
        raise HTTPException(status_code=422, detail="local_dir は絶対パスで指定してください")

    db.save_settings(settings.model_dump())
    _log("INFO", f"設定を更新しました: ホスト={settings.host}:{settings.port}, リモート={settings.remote_dir}, ローカル={settings.local_dir}")
    # Mask password in response
    masked = settings.model_dump()
    masked["password"] = None
    return SftpSettings(**masked)


@app.post("/settings/test")
def test_settings(payload: dict, user=Depends(require_auth)):
    # Merge posted settings with stored ones to allow blank password retaining
    stored = db.load_settings() or {}
    merged = {**stored, **payload}
    # If password blank, keep stored one
    if not merged.get("password"):
        merged["password"] = stored.get("password")
    try:
        settings = SftpSettings(**merged)
        _log("INFO", f"接続テスト開始: {settings.host}:{settings.port}")
        test_connection(settings)
        _log("INFO", f"接続テスト成功: {settings.host}:{settings.port}, リモートディレクトリ={settings.remote_dir}")
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        _log("ERROR", f"接続テスト失敗: {merged.get('host')}:{merged.get('port')}", detail=str(exc))
        raise HTTPException(status_code=400, detail=f"接続テスト失敗: {exc}")


@app.post("/sync/run")
def run_sync(req: SyncRequest, user=Depends(require_auth)):
    global _running
    if _running:
        raise HTTPException(status_code=409, detail="同期処理が既に実行中です")

    _log("INFO", "手動同期実行: ユーザーによる同期開始要求")

    # Run sync in a separate thread to avoid blocking the request
    import threading
    sync_thread = threading.Thread(target=_run_sync, daemon=True)
    sync_thread.start()

    return {"status": "started", "message": "同期処理を開始しました"}


@app.post("/sync/stop")
def stop_sync(user=Depends(require_auth)):
    global _stop_requested
    if not _running:
        raise HTTPException(status_code=400, detail="同期処理が実行されていません")
    _stop_requested = True
    _log("WARN", "同期停止要求: ユーザーによる停止要求")
    return {"status": "stop_requested"}


@app.post("/sync/reset")
def reset_sync_lock(user=Depends(require_auth)):
    """Reset sync lock in case it's stuck"""
    global _running, _stop_requested
    if lock.locked():
        try:
            lock.release()
            _log("WARN", "同期ロック強制解除: 管理者による操作")
        except Exception:
            pass
    _running = False
    _stop_requested = False
    return {"status": "reset", "message": "ロックをリセットしました"}


@app.get("/logs", response_model=list[LogEntry])
def get_logs(limit: int = 200, user=Depends(require_auth)):
    rows = db.list_logs(limit)
    return [LogEntry(**row) for row in rows]


@app.delete("/logs")
def delete_logs(user=Depends(require_auth)):
    """Clear all log entries."""
    db.clear_logs()
    _log("INFO", "ログクリア: 管理者による操作")
    return {"status": "ok", "message": "ログをクリアしました"}


@app.get("/status", response_model=Status)
def get_status(user=Depends(require_auth)):
    return Status(last_run=_last_run, running=_running)


# =====================
# Image Compression API
# =====================

def _run_compress() -> None:
    """Run image compression watch in background."""
    global _compress_running, _compress_stop_requested

    settings_data = db.load_settings()
    if not settings_data:
        _log("WARN", "画像圧縮スキップ: 設定が未構成です")
        return

    settings = SftpSettings(**settings_data)

    if not settings.local_dir:
        _log("ERROR", "画像圧縮エラー: コピー先フォルダー(local_dir)が未設定です")
        return

    if not settings.compress_output_dir:
        _log("ERROR", "画像圧縮エラー: 画像圧縮出力先フォルダー(compress_output_dir)が未設定です")
        return

    if not compress_lock.acquire(blocking=False):
        _log("INFO", "画像圧縮スキップ: 既に実行中です")
        return

    _compress_running = True
    _compress_stop_requested = False

    try:
        quality = settings.compress_quality if settings.compress_quality else 85
        resize_width = settings.resize_width_dpi

        # 秒数が0の場合は1回のみ実行
        if settings.compress_interval_seconds == 0:
            _log("INFO", "画像圧縮: 1回のみ実行モード")
            try:
                from image_compress import compress_images_in_folder as compress_once_func
                compress_once_func(settings.local_dir, settings.compress_output_dir, quality, _log, None, resize_width)
                _log("INFO", "画像圧縮監視終了: 1回実行完了")
            except Exception as exc:  # noqa: BLE001
                _log("ERROR", "画像圧縮エラー", detail=str(exc))
        else:
            stats = watch_and_compress(
                input_dir=settings.local_dir,
                output_dir=settings.compress_output_dir,
                quality=quality,
                log_callback=_log,
                stop_check=_check_compress_stop_requested,
                interval=float(settings.compress_interval_seconds),
                resize_width=resize_width,
            )

            saved_mb = stats['total_saved_bytes'] / (1024 * 1024)
            _log("INFO", f"画像圧縮監視終了: 処理={stats['compressed_files']}件, スキップ={stats['skipped_files']}件, エラー={stats['error_files']}件, 削減={saved_mb:.2f}MB")

    except Exception as exc:
        _log("ERROR", "画像圧縮監視失敗", detail=str(exc))
    finally:
        _compress_running = False
        _compress_stop_requested = False
        compress_lock.release()


@app.post("/compress/run")
def run_compress(user=Depends(require_auth)):
    """Start image compression watch."""
    global _compress_running
    if _compress_running:
        raise HTTPException(status_code=409, detail="画像圧縮監視が既に実行中です")

    _log("INFO", "画像圧縮監視開始: ユーザーによる監視開始要求")

    compress_thread = threading.Thread(target=_run_compress, daemon=True)
    compress_thread.start()

    return {"status": "started", "message": "画像圧縮監視を開始しました"}


@app.post("/compress/stop")
def stop_compress(user=Depends(require_auth)):
    """Stop image compression watch."""
    global _compress_stop_requested
    if not _compress_running:
        raise HTTPException(status_code=400, detail="画像圧縮監視が実行されていません")
    _compress_stop_requested = True
    _log("INFO", "画像圧縮監視停止: ユーザーによる停止要求")
    return {"status": "stop_requested"}


@app.get("/compress/status")
def get_compress_status(user=Depends(require_auth)):
    """Get compression status."""
    return {"running": _compress_running}


# =====================
# SFTP Upload API
# =====================

def _run_upload() -> None:
    """Run SFTP upload watch in background."""
    global _upload_running, _upload_stop_requested

    settings_data = db.load_settings()
    if not settings_data:
        _log("WARN", "SFTPアップロードスキップ: 設定が未構成です")
        return

    settings = SftpSettings(**settings_data)

    if not settings.compress_output_dir:
        _log("ERROR", "SFTPアップロードエラー: 画像圧縮出力先フォルダー(compress_output_dir)が未設定です")
        return

    if not settings.remote_output_dir:
        _log("ERROR", "SFTPアップロードエラー: 接続先出力フォルダー(remote_output_dir)が未設定です")
        return

    if not upload_lock.acquire(blocking=False):
        _log("INFO", "SFTPアップロードスキップ: 既に実行中です")
        return

    _upload_running = True
    _upload_stop_requested = False

    try:
        # 秒数が0の場合は1回のみ実行
        if settings.upload_interval_seconds == 0:
            _log("INFO", "SFTPアップロード: 1回のみ実行モード")
            try:
                from sftp_upload import upload_folder
                stats = upload_folder(
                    local_dir=settings.compress_output_dir,
                    remote_dir=settings.remote_output_dir,
                    settings=settings,
                    log=_log,
                    delete_after_upload=False,
                )
                uploaded_mb = stats['uploaded_bytes'] / (1024 * 1024)
                _log("INFO", f"SFTPアップロード監視終了: アップロード={stats['uploaded_files']}件 ({uploaded_mb:.2f}MB), 削除={stats['deleted_files']}件, エラー={stats['error_files']}件")
            except Exception as exc:  # noqa: BLE001
                _log("ERROR", "SFTPアップロードエラー", detail=str(exc))
        else:
            stats = watch_and_upload(
                local_dir=settings.compress_output_dir,
                remote_dir=settings.remote_output_dir,
                settings=settings,
                log=_log,
                stop_check=_check_upload_stop_requested,
                interval=float(settings.upload_interval_seconds),
                delete_after_upload=False,
            )

            uploaded_mb = stats['uploaded_bytes'] / (1024 * 1024)
            _log("INFO", f"SFTPアップロード監視終了: アップロード={stats['uploaded_files']}件 ({uploaded_mb:.2f}MB), 削除={stats['deleted_files']}件, エラー={stats['error_files']}件")

    except Exception as exc:
        _log("ERROR", "SFTPアップロード監視失敗", detail=str(exc))
    finally:
        _upload_running = False
        _upload_stop_requested = False
        upload_lock.release()


@app.post("/upload/run")
def run_upload(user=Depends(require_auth)):
    """Start SFTP upload watch."""
    global _upload_running
    if _upload_running:
        raise HTTPException(status_code=409, detail="SFTPアップロード監視が既に実行中です")

    _log("INFO", "SFTPアップロード監視開始: ユーザーによる監視開始要求")

    upload_thread = threading.Thread(target=_run_upload, daemon=True)
    upload_thread.start()

    return {"status": "started", "message": "SFTPアップロード監視を開始しました"}


@app.post("/upload/stop")
def stop_upload(user=Depends(require_auth)):
    """Stop SFTP upload watch."""
    global _upload_stop_requested
    if not _upload_running:
        raise HTTPException(status_code=400, detail="SFTPアップロード監視が実行されていません")
    _upload_stop_requested = True
    _log("INFO", "SFTPアップロード監視停止: ユーザーによる停止要求")
    return {"status": "stop_requested"}


@app.get("/upload/status")
def get_upload_status(user=Depends(require_auth)):
    """Get upload status."""
    return {"running": _upload_running}
