# SFTP Sync Sample

Python backend (FastAPI) + TypeScript frontend to sync files from an SFTP server to a local client folder, log operations in SQLite, and expose settings/status over HTTP. A simple login UI is served at the root page.

## Backend

1. Create venv and install deps:
   ```bash
   cd backend
   python -m venv .venv
   .venv/Scripts/activate
   pip install -r requirements.txt
   ```
2. Run API:
   ```bash
   uvicorn main:app --reload --port 8000
   ```
3. Open `http://localhost:8000` for the built-in login/menu UI.

### Auth
- Default credentials: `admin` / `password` (override with env `APP_USER`, `APP_PASSWORD`).
- Session cookie is HTTP-only and expires after 12h; change `SESSION_SECRET` for a new signing key.

### API endpoints
- `GET /settings` / `POST /settings` — read/update SFTP settings (saved in SQLite).
- `POST /sync/run` — trigger sync immediately (`{"force": true}` to ignore running guard).
- `GET /status` — current scheduler state.
- `GET /logs?limit=200` — recent log entries.

Scheduler runs every `interval_minutes` (default 60) using APScheduler; jobs run only if settings exist.

## Frontend (TypeScript stub)
- Basic fetch wrapper in `frontend/src/api.ts` and usage example in `frontend/src/main.ts`.
- You can wire these into any TS/React/Vite UI and add authentication + forms for settings/logs.

## 設定項目
- ドメイン: SFTP `host` と任意の `port`
- ユーザー・パスワード: `username` / `password`（鍵認証を使う場合は `private_key_path` を利用）
- 接続先フォルダー: `remote_dir`
- コピー先フォルダー: `local_dir`
- 同期サイクル: `interval_minutes`（分）

トップページの「設定を編集」から保存するとスケジューラーが再設定されます。

### 保存前テスト
- 「接続テスト」ボタンで `/settings/test` を呼び出し、SFTP接続と `remote_dir` のアクセス可否をチェックします。

### パスワードの保存方式
- パスワードは `SESSION_SECRET` 由来の鍵で暗号化してSQLiteに保存されます。APIで`GET /settings`した場合はパスワードはマスクされます。
- パスワードを未入力のまま保存すると既存の保存済みパスワードを保持します（変更しない）。
- 推奨: 可能なら鍵認証（`private_key_path`）を使用し、`SESSION_SECRET` を十分強度の高い値に設定してください。

## Notes
- Files copy one-way (remote → local). Missing or outdated local files are overwritten. Directories are created as needed.
- Authentication is not implemented; add auth middleware (session/JWT) before production.
- Ensure the local path is writable and the SFTP account has read access to the remote path.

## ドキュメント
- [プログラム仕様書](./SPECIFICATION.md) - 完全な仕様・API・DB スキーマ
- このファイル - クイックスタート＆環境設定
