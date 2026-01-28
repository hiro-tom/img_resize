#!/bin/bash
set -e

# ログ出力
exec > >(tee /var/log/user-data.log)
exec 2>&1

echo "=== img_resize デプロイ開始 ===" 

# システムアップデート
echo "システムパッケージ更新中..."
apt-get update -qq
apt-get install -y -qq python3-pip python3-venv git

# アプリケーション準備
echo "アプリケーション準備中..."
mkdir -p /opt/img_resize
cd /opt/img_resize

# リポジトリクローン
if [ -d '.git' ]; then
    git pull origin master
else
    git clone https://github.com/hiro-tom/img_resize.git .
fi

# Python環境構築
cd backend
python3 -m venv venv
. venv/bin/activate
pip install --quiet -r requirements.txt

# サービスファイル作成
cat > /etc/systemd/system/img-resize.service << 'EOF'
[Unit]
Description=img_resize FastAPI Application
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/img_resize/backend
Environment="PATH=/opt/img_resize/backend/venv/bin"
ExecStart=/opt/img_resize/backend/venv/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# サービス有効化・起動
systemctl daemon-reload
systemctl enable img-resize
systemctl start img-resize

echo "=== デプロイ完了 ===" 
echo "アプリケーション起動: http://$(ec2-metadata --public-ipv4 | cut -d ' ' -f 2):8000"
