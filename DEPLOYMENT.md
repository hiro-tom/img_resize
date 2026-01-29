# EC2デプロイ手順

## インスタンス情報
- **インスタンスID**: i-021b05b6ced172fd9
- **パブリックIP**: 13.231.201.247
- **セキュリティグループ**: sg-05924255374718421
- **キーペア**: img-resize-final
- **デプロイ方法**: User Data スクリプトによる自動デプロイ（高速版）

## SSH接続方法

```bash
ssh -i C:\Users\富岡博\.ssh\img-resize-final.pem ubuntu@13.231.201.247
```

## デプロイ手順

1. SSH接続後、以下のコマンドを実行してください：

```bash
# システムアップデート
sudo apt-get update
sudo apt-get upgrade -y

# Python 3.11とgitのインストール
sudo apt-get install -y python3.11 python3.11-venv python3-pip git

# Pillow用の依存関係
sudo apt-get install -y libjpeg-dev zlib1g-dev

# リポジトリのクローン
cd /home/ubuntu
git clone https://github.com/hiro-tom/img_resize.git
cd img_resize/backend

# Python仮想環境のセットアップ
python3.11 -m venv .venv
source .venv/bin/activate

# 依存関係のインストール
pip install --upgrade pip
pip install -r requirements.txt

# systemdサービスの作成
sudo tee /etc/systemd/system/img-resize.service > /dev/null <<'EOF'
[Unit]
Description=Image Resize FastAPI Application
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/img_resize/backend
Environment="PATH=/home/ubuntu/img_resize/backend/.venv/bin"
ExecStart=/home/ubuntu/img_resize/backend/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# サービスの起動
sudo systemctl daemon-reload
sudo systemctl enable img-resize
sudo systemctl start img-resize

# ステータス確認
sudo systemctl status img-resize
```

## アプリケーションへのアクセス

デプロイ完了後、以下のURLでアクセスできます：
- http://13.231.201.247:8000

デフォルト認証情報:
- ユーザー名: admin
- パスワード: password

## 役立つコマンド

```bash
# サービス状態確認
sudo systemctl status img-resize

# ログ確認
sudo journalctl -u img-resize -f

# サービス再起動
sudo systemctl restart img-resize

# サービス停止
sudo systemctl stop img-resize
```
