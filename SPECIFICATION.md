# SFTP同期サービス - プログラム仕様書

**バージョン**: 1.0  
**作成日**: 2026年1月19日  
**更新日**: 2026年1月19日

---

## 1. システム概要

### 目的
SFTP サーバー上のファイルを定期的にローカルフォルダへ自動同期するサービス。ユーザーが Web UI からログインして設定を管理し、実行状況・ログを確認できます。

### 主な特徴
- **定期同期**: 1時間（デフォルト）ごとにスケジュール実行
- **差分同期**: ローカルに存在しない、または古い/差異があるファイルのみコピー
- **Web UI**: ブラウザからのログイン・設定・ステータス確認
- **ログ記録**: 全ての同期操作と設定変更を SQLite に記録
- **暗号化保存**: パスワードは `SESSION_SECRET` で暗号化

---

## 2. システム構成

### 2.1 アーキテクチャ

```
┌─────────────┐
│   Web UI    │  HTML/JS（ブラウザで実行）
│ (TS stub)   │
└──────┬──────┘
       │ HTTPS (credentials: include)
       ▼
┌──────────────────────────────────────┐
│      FastAPI Backend (Python)        │
│  - Authentication (Session cookie)   │
│  - SFTP sync scheduler (APScheduler) │
│  - SQLite DB (settings + logs)       │
└──────────────────┬───────────────────┘
       ┌───────────┼────────────┐
       ▼           ▼            ▼
    SQLite      SFTP Srv    Local FS
    (DB)        (source)    (target)
```

### 2.2 技術スタック

| 層 | 技術 | 役割 |
|---|---|---|
| **フロントエンド** | HTML5 + vanilla JS | ログイン、設定編集、ステータス表示 |
| **バックエンド** | FastAPI (Python 3.13) | REST API、認証、スケジューラー |
| **スケジューラー** | APScheduler | 定期同期ジョブ管理 |
| **SFTP** | Paramiko | リモートファイル接続・転送 |
| **DB** | SQLite | 設定・ログ永続化 |
| **暗号化** | cryptography (Fernet) | パスワード暗号化 |

---

## 3. 機能一覧

### 3.1 認証機能
- **ログイン**: ユーザー名・パスワード入力 → HTTP-only クッキーに SESSION 保存
- **セッション有効期限**: 12 時間
- **ログアウト**: クッキー削除
- **デフォルト認証情報**: `admin` / `password`（環境変数で上書き可）

### 3.2 設定管理
ユーザーが以下を Web UI で設定：
| 項目 | 形式 | 説明 |
|---|---|---|
| `host` | 文字列 | SFTP サーバーのドメイン・IP |
| `port` | 整数 (デフォルト: 22) | SFTP ポート |
| `username` | 文字列 | SFTP ユーザー名 |
| `password` | 文字列 (暗号化保存) | SFTP パスワード（未入力で既存値保持） |
| `private_key_path` | 文字列 (optional) | 秘密鍵ファイルパス（鍵認証時） |
| `remote_dir` | 絶対パス (例: /data) | SFTP サーバーの同期元フォルダ |
| `local_dir` | 絶対パス (例: C:/sftp-client) | ローカル同期先フォルダ |
| `interval_minutes` | 整数 (デフォルト: 60) | 同期実行間隔（分） |

### 3.3 同期機能
- **実行トリガー**:
  1. スケジュール実行（`interval_minutes` ごと）
  2. Web UI からの手動実行（「今すぐ同期」ボタン）
- **同期アルゴリズム**:
  - リモートの全ファイルを列挙
  - ローカルの全ファイルを列挙
  - 各ファイルについて:
    - ローカルに存在しない → コピー
    - タイムスタンプ・サイズが異なる → 上書きコピー
    - 同じ → スキップ
  - 削除処理なし（ローカルにのみあるファイルは保持）

### 3.4 ステータス確認
- **最後の実行時刻**: `last_run`
- **次の実行予定時刻**: `next_run_time`
- **実行中フラグ**: `running` (true: 同期処理中、false: アイドル)

### 3.5 ログ・履歴管理
- **記録対象**:
  - 各ファイルのコピー操作
  - 設定の変更
  - 同期完了（件数サマリー）
  - エラー情報
- **ログレベル**: `INFO`, `WARN`, `ERROR`
- **保持期間**: 無制限（ユーザーが削除を明示的に行わない限り）

### 3.6 接続テスト機能
- Web UI の「接続テスト」ボタン
- `POST /settings/test` を呼び出し
- SFTP サーバーへの接続と `remote_dir` のアクセス可否を確認
- 結果を UI に表示（成功 / エラー詳細）

---

## 4. データベーススキーマ

### SQLite: `app.db`

#### テーブル 1: `settings`
```sql
CREATE TABLE settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL
);
```
- **用途**: SFTP 設定の永続化（JSON形式）
- **制約**: レコード 1 つのみ（`id=1` 固定）
- **保存形式**: 
  ```json
  {
    "host": "sftp.example.com",
    "port": 22,
    "username": "user",
    "password": "<Fernet encrypted>",
    "private_key_path": null,
    "remote_dir": "/data",
    "local_dir": "C:/sftp-client",
    "interval_minutes": 60
  }
  ```

#### テーブル 2: `logs`
```sql
CREATE TABLE logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    detail TEXT
);
```
- **用途**: 全ての操作ログ
- **カラム**:
  - `id`: 自動採番
  - `created_at`: SQLite の `datetime('now')` (UTC ISO 8601)
  - `level`: `INFO`, `WARN`, `ERROR`
  - `message`: ログメッセージ
  - `detail`: 詳細情報（スタックトレース等、optional）

---

## 5. API 仕様

### ベース URL
```
http://localhost:8000
```

### 5.1 認証エンドポイント

#### `POST /auth/login`
ログイン処理
- **リクエスト**:
  ```json
  {
    "username": "admin",
    "password": "password"
  }
  ```
- **レスポンス** (200 OK):
  ```json
  {
    "user": "admin"
  }
  ```
- **レスポンス** (401 Unauthorized):
  ```
  Invalid credentials
  ```
- **Cookie 設定**: `session` (HTTP-only, 12h 有効)

#### `POST /auth/logout`
ログアウト処理
- **リクエスト**: なし
- **レスポンス** (200 OK):
  ```json
  {
    "status": "ok"
  }
  ```
- **Cookie 削除**: `session`

#### `GET /auth/me`
現在のセッションユーザー確認
- **リクエスト**: Cookie に `session` 必須
- **レスポンス** (200 OK):
  ```json
  {
    "user": "admin"
  }
  ```
- **レスポンス** (401 Unauthorized): セッション無効

---

### 5.2 設定エンドポイント

#### `GET /settings`
現在の SFTP 設定を取得
- **リクエスト**: Cookie に `session` 必須
- **レスポンス** (200 OK):
  ```json
  {
    "host": "sftp.example.com",
    "port": 22,
    "username": "user",
    "password": null,
    "private_key_path": null,
    "remote_dir": "/data",
    "local_dir": "C:/sftp-client",
    "interval_minutes": 60
  }
  ```
  **注**: `password` は API では常に `null` でマスクされます

#### `POST /settings`
SFTP 設定を更新・保存
- **リクエスト**: Cookie に `session` 必須
  ```json
  {
    "host": "sftp.example.com",
    "port": 22,
    "username": "user",
    "password": "newpassword",
    "private_key_path": null,
    "remote_dir": "/data",
    "local_dir": "C:/sftp-client",
    "interval_minutes": 60
  }
  ```
- **バリデーション**:
  - `host`, `username` 必須
  - `remote_dir` は絶対パス形式（`/` で始まる）
  - `local_dir` は絶対パス
  - `port` >= 1 かつ <= 65535
  - `interval_minutes` >= 1
- **レスポンス** (200 OK): 保存した設定（password はマスク）
- **レスポンス** (422 Unprocessable Entity): バリデーション失敗

#### `POST /settings/test`
SFTP 接続テスト
- **リクエスト**: Cookie に `session` 必須。テスト対象の設定（JSON）:
  ```json
  {
    "host": "sftp.example.com",
    "port": 22,
    "username": "user",
    "password": "password",
    "remote_dir": "/data",
    "local_dir": "C:/sftp-client",
    "interval_minutes": 60
  }
  ```
- **処理**:
  1. 入力値と既存設定をマージ（password 空欄なら既存値を使用）
  2. SFTP 接続を試みる
  3. `remote_dir` をリスト（読み取り権限確認）
  4. 接続を切断
- **レスポンス** (200 OK):
  ```json
  {
    "ok": true
  }
  ```
- **レスポンス** (400 Bad Request): 接続失敗
  ```
  接続テスト失敗: [エラー詳細]
  ```

---

### 5.3 同期エンドポイント

#### `POST /sync/run`
同期処理を即座に実行
- **リクエスト**: Cookie に `session` 必須
  ```json
  {
    "force": true
  }
  ```
  - `force=true`: 実行中でも強制実行
  - `force=false`: 実行中ならエラー
- **レスポンス** (200 OK):
  ```json
  {
    "status": "ok"
  }
  ```
- **レスポンス** (409 Conflict): 既に実行中（force=false）

---

### 5.4 ステータスエンドポイント

#### `GET /status`
スケジューラーの状態を取得
- **リクエスト**: Cookie に `session` 必須
- **レスポンス** (200 OK):
  ```json
  {
    "last_run": "2026-01-19T14:30:00.123456",
    "next_run": "2026-01-19T15:30:00.123456",
    "running": false
  }
  ```
  - `last_run`: 最後の実行完了時刻（ISO 8601）。未実行の場合は `null`
  - `next_run`: 次の実行予定時刻（ISO 8601）。未設定の場合は `null`
  - `running`: 現在同期処理中ならtrue

---

### 5.5 ログエンドポイント

#### `GET /logs?limit=200`
ログエントリを取得
- **リクエスト**: Cookie に `session` 必須
  - **クエリパラメータ**:
    - `limit`: 取得件数の上限（デフォルト: 200）
- **レスポンス** (200 OK):
  ```json
  [
    {
      "id": 42,
      "created_at": "2026-01-19T14:30:15.654321",
      "level": "INFO",
      "message": "Sync complete: {\"copied\": 5, \"skipped\": 10}",
      "detail": null
    },
    {
      "id": 41,
      "created_at": "2026-01-19T14:30:10.123456",
      "level": "INFO",
      "message": "Copied data/file1.txt",
      "detail": null
    }
  ]
  ```
  最新のログから順（`id` DESC）で取得

---

### 5.6 UI ルート

#### `GET /`
Web UI ページ（ログイン + メニュー画面）
- **レスポンス**: HTML ドキュメント（コンテンツタイプ: `text/html`）
- **機能**:
  - ログインフォーム（username/password）
  - ログイン後: 設定編集、ステータス表示、ログ表示、同期実行
  - セッション自動チェック

---

## 6. 認証・セキュリティ

### 6.1 セッション管理
- **方式**: HTTP-only クッキー + HMAC-SHA256 署名
- **トークン形式**: 
  ```
  Base64(username:timestamp | HMAC-SHA256(username:timestamp, SESSION_SECRET))
  ```
- **有効期限**: 12 時間（タイムスタンプベース）
- **署名鍵**: 環境変数 `SESSION_SECRET` (デフォルト: "change-me")

### 6.2 パスワード保存
- **暗号化方式**: Fernet (symmetric encryption, cryptography ライブラリ)
- **暗号鍵**: `SESSION_SECRET` から SHA-256 ハッシュ → Base64 エンコード
- **保存先**: SQLite `settings.data` (JSON フィールド)
- **マスキング**: API `GET /settings` ではパスワードを `null` で返す

### 6.3 CORS ポリシー
- **許可オリジン**: `http://localhost:8000`, `http://127.0.0.1:8000`
- **Credentials**: `true` (クッキー送信)

### 6.4 推奨事項（本番運用）
1. `SESSION_SECRET` を十分な強度の値に設定
   ```bash
   export SESSION_SECRET="$(openssl rand -hex 32)"
   ```
2. HTTPS を使用
3. SFTP ユーザーのパーミッション最小化
4. ローカルフォルダのアクセス制限（OS レベル）
5. 定期的なログバックアップ

---

## 7. 同期処理の詳細フロー

### 7.1 スケジュール実行フロー

```
APScheduler Job Trigger (interval: interval_minutes)
        ↓
    _run_sync() 関数
        ↓
    [設定存在確認]
        ├─ NO → ログ "Sync skipped: no settings configured" → 終了
        └─ YES
            ↓
        [既実行中?]
            ├─ YES → ログ "Sync skipped: job already running" → 終了
            └─ NO
                ↓
            [_running = True]
                ↓
            sync_once() 関数 → SFTP 同期実行
                ├─ コピー件数
                └─ スキップ件数
                ↓
            [ログ記録: "Sync complete: {...}"]
            [_last_run = 現在時刻]
                ↓
            [_running = False] → 完了
```

### 7.2 sync_once() の詳細

```python
sync_once(settings: SftpSettings, log_callback) -> {"copied": int, "skipped": int}
```

1. **初期化**:
   - ローカルフォルダを作成（存在しない場合）
   - SFTP 接続

2. **リモートファイル列挙**:
   - `_list_remote(remote_dir)` で再帰的に全ファイル・フォルダを取得
   - 各エントリのメタデータ（タイムスタンプ、サイズ）を記録

3. **ローカルファイル索引**:
   - `_list_local(local_dir)` で全ファイルをリスト
   - 相対パスをキーにメタデータを記録

4. **同期ロジック**:
   ```
   for each remote_file:
       if remote is dir:
           create local dir
       else:
           rel_path = remote_file相対パス
           local_file = local_dir + rel_path
           
           if local_file not exists:
               [コピー] copied++
           elif local mtime >= remote mtime AND local size == remote size:
               [スキップ] skipped++
           else:
               [更新コピー] copied++
               
           [ローカルのタイムスタンプを remote に合わせる]
           log "Copied rel_path"
   ```

5. **接続終了**
   - SFTP セッション切断

---

## 8. エラーハンドリング

### 8.1 SFTP 接続エラー
- **原因**: ホスト到達不可、ユーザー認証失敗、ネットワークタイムアウト
- **処理**: 例外をキャッチ → ログ記録 (ERROR) → 同期スキップ

### 8.2 ファイル転送エラー
- **原因**: 権限不足、ディスク満杯、ネットワーク断
- **処理**: 該当ファイルをスキップ → ログ記録 (WARN)

### 8.3 ローカルパス書き込みエラー
- **原因**: ディスク満杯、パーミッション不足
- **処理**: 例外をキャッチ → ログ記録 (ERROR)

### 8.4 API バリデーションエラー
- **リクエスト不正**: 422 Unprocessable Entity
  ```json
  {
    "detail": "host/username は必須です"
  }
  ```
- **認証失敗**: 401 Unauthorized
- **既に実行中**: 409 Conflict

---

## 9. ログフォーマット

### 9.1 ログメッセージ例

| 操作 | レベル | メッセージ |
|---|---|---|
| 設定更新 | INFO | `Settings updated` |
| 同期開始 | INFO | `Sync complete: {"copied": 5, "skipped": 3}` |
| ファイルコピー | INFO | `Copied data/file1.txt` |
| 設定なし | WARN | `Sync skipped: no settings configured` |
| 接続エラー | ERROR | `Sync failed` (detail に詳細) |

### 9.2 ログ閲覧
- Web UI メニューの「ログ取得」 → 最新 50 件表示
- API `GET /logs?limit=200` で任意件数取得

---

## 10. 環境変数一覧

| 変数名 | デフォルト | 説明 |
|---|---|---|
| `APP_USER` | `admin` | ログインユーザー名 |
| `APP_PASSWORD` | `password` | ログインパスワード |
| `SESSION_SECRET` | `change-me` | セッション署名・パスワード暗号化の鍵 |

**使用例**:
```bash
export APP_USER="myuser"
export APP_PASSWORD="mypass123"
export SESSION_SECRET="$(openssl rand -hex 32)"
uvicorn main:app --port 8000
```

---

## 11. 制限事項・今後の改善案

### 11.1 現在の制限
- ✓ 同期は一方向のみ（リモート → ローカル）
- ✓ ローカルの削除済みファイルは複製されない
- ✓ 複数ユーザー管理なし（単一認証）
- ✓ 並列同期なし（1 ジョブのみ実行）

### 11.2 改善案
- [ ] 複数 SFTP 接続設定の管理（プロファイル管理）
- [ ] ローカルフォルダ削除・クリーンアップ機能
- [ ] 差分バックアップ機能
- [ ] Webhook / メール通知機能
- [ ] ロール・多ユーザー管理
- [ ] リトライ・タイムアウト設定の細分化
- [ ] パフォーマンス分析（同期時間、データ量統計）

---

## 12. 開発・テスト

### 12.1 開発環境セットアップ
```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # または .venv\Scripts\activate (Windows)
pip install -r requirements.txt
```

### 12.2 API テスト例（curl）
```bash
# ログイン
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"password"}' \
  -c cookies.txt

# 設定取得
curl -X GET http://localhost:8000/settings \
  -b cookies.txt

# 接続テスト
curl -X POST http://localhost:8000/settings/test \
  -H "Content-Type: application/json" \
  -b cookies.txt \
  -d '{...}'

# ログアウト
curl -X POST http://localhost:8000/auth/logout \
  -b cookies.txt
```

---

## 付録 A: ディレクトリ構成

```
idbmake/
├── backend/
│   ├── main.py           # FastAPI アプリ本体
│   ├── db.py             # SQLite DB ヘルパー
│   ├── schemas.py        # Pydantic モデル
│   ├── sftp_sync.py      # SFTP 同期ロジック
│   ├── requirements.txt   # Python 依存
│   └── data/
│       └── app.db        # SQLite DB ファイル
├── frontend/
│   └── src/
│       ├── api.ts        # TypeScript API クライアント
│       └── main.ts       # TS フロントエンド例
├── README.md             # 簡潔な使い方
└── SPECIFICATION.md      # このドキュメント
```

---

## 付録 B: トラブルシューティング

### 問題: SFTP 接続テスト失敗
**原因**: ホスト名解決不可、認証失敗、ネットワーク遮断  
**対処**: ホスト・ユーザー・パスワードを確認、`remote_dir` は絶対パス形式で指定

### 問題: ファイルが同期されない
**原因**: スケジューラーが起動していない、設定が未保存  
**対処**: Web UI で設定を保存、ステータスで `next_run` が表示されているか確認

### 問題: ローカルフォルダへのアクセス拒否
**原因**: パスが不正、フォルダ作成権限なし  
**対処**: 絶対パスを指定、フォルダの親ディレクトリへの書き込み権限を確認

---

**仕様書作成完了**
