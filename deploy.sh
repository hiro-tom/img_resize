#!/bin/bash

# EC2へのデプロイスクリプト
set -e

echo "=== img_resize デプロイスクリプト ===" 
echo "本番環境への更新を開始します"

# 1. 既存のアプリケーション停止
echo "既存のアプリケーションを停止..."
if [ -f "/home/ubuntu/img_resize/app.pid" ]; then
    kill $(cat /home/ubuntu/img_resize/app.pid) || true
    sleep 2
fi

# 2. リポジトリの最新コードを取得
echo "最新コードをプル..."
cd /home/ubuntu/img_resize || mkdir -p /home/ubuntu/img_resize && cd /home/ubuntu/img_resize

if [ -d ".git" ]; then
    git pull origin master
else
    git clone https://github.com/hiro-tom/img_resize.git .
fi

# 3. 依存パッケージをインストール
echo "依存パッケージをインストール..."
cd backend
pip install -r requirements.txt

# 4. アプリケーションを起動
echo "アプリケーションを起動..."
nohup python main.py > /tmp/img_resize.log 2>&1 &
echo $! > /home/ubuntu/img_resize/app.pid

echo "=== デプロイ完了 ==="
echo "アプリケーションは http://43.206.220.127:8000 で実行しています"
echo "ログ: tail -f /tmp/img_resize.log"
