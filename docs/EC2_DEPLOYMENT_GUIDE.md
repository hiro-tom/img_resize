# AWS EC2 デプロイ手順書

## 1. 概要

本手順書は、SFTP画像圧縮処理システムをAWS EC2にデプロイするための手順を説明します。

### 1.1 前提条件

- AWS CLIがインストール・設定済み
- AWS IAMユーザーにEC2操作権限あり
- GitHubリポジトリへのアクセス権限あり

### 1.2 構成情報

| 項目 | 値 |
|------|-----|
| AMI | Ubuntu 22.04 LTS (ami-0d5239ebe558a73be) |
| インスタンスタイプ | t2.micro |
| リージョン | ap-northeast-1 (東京) |
| ポート | 22 (SSH), 8000 (アプリケーション) |

---

## 2. AWS CLI セットアップ確認

### 2.1 AWS CLI設定確認

```powershell
# 設定確認
aws configure list

# アカウント確認
aws sts get-caller-identity
```

出力例:
```json
{
    "UserId": "AIDAXXXXXXXXXXXXXXXXX",
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/your-username"
}
```

---

## 3. キーペア作成

### 3.1 新規キーペア作成

```powershell
# キーペア作成
aws ec2 create-key-pair --key-name img-resize-key --query 'KeyMaterial' --output text > ~/.ssh/img-resize-key.pem

# 権限設定 (Linux/Mac)
chmod 400 ~/.ssh/img-resize-key.pem
```

### 3.2 既存キーペア確認

```powershell
aws ec2 describe-key-pairs --query 'KeyPairs[*].KeyName' --output table
```

---

## 4. セキュリティグループ作成

### 4.1 セキュリティグループ作成

```powershell
# デフォルトVPC ID取得
$VPC_ID = aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query 'Vpcs[0].VpcId' --output text

# セキュリティグループ作成
aws ec2 create-security-group `
    --group-name img-resize-sg `
    --description "Security group for Image Resize App" `
    --vpc-id $VPC_ID
```

### 4.2 インバウンドルール追加

```powershell
# セキュリティグループID取得
$SG_ID = aws ec2 describe-security-groups --group-names img-resize-sg --query 'SecurityGroups[0].GroupId' --output text

# SSH (ポート22) 許可
aws ec2 authorize-security-group-ingress `
    --group-id $SG_ID `
    --protocol tcp `
    --port 22 `
    --cidr 0.0.0.0/0

# アプリケーション (ポート8000) 許可
aws ec2 authorize-security-group-ingress `
    --group-id $SG_ID `
    --protocol tcp `
    --port 8000 `
    --cidr 0.0.0.0/0
```

---

## 5. EC2インスタンス作成

### 5.1 user-dataスクリプト準備

以下の内容で `user-data-deploy.sh` を作成:

```bash
#!/bin/bash
exec > /var/log/user-data.log 2>&1

echo "=== Starting deployment ==="
date

# System update
apt-get update
apt-get install -y python3.11 python3.11-venv python3-pip git

# Clone repository
cd /home/ubuntu
git clone https://github.com/hiro-tom/img_resize.git || true
cd img_resize/backend

# Setup Python environment
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Create systemd service
cat > /etc/systemd/system/img-resize.service << 'SERVICEEOF'
[Unit]
Description=Image Resize FastAPI Application
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/img_resize/backend
Environment="PATH=/home/ubuntu/img_resize/backend/.venv/bin"
ExecStart=/home/ubuntu/img_resize/backend/.venv/bin/python3.11 /home/ubuntu/img_resize/backend/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
SERVICEEOF

# Set permissions
chown -R ubuntu:ubuntu /home/ubuntu/img_resize

# Enable and start service
systemctl daemon-reload
systemctl enable img-resize
systemctl start img-resize

echo "=== Deployment completed ==="
date
```

### 5.2 EC2インスタンス起動

```powershell
# Base64エンコード (Windows PowerShell)
$USER_DATA = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((Get-Content -Raw user-data-deploy.sh)))

# EC2インスタンス作成
aws ec2 run-instances `
    --image-id ami-0d5239ebe558a73be `
    --instance-type t2.micro `
    --key-name img-resize-key `
    --security-group-ids $SG_ID `
    --user-data $USER_DATA `
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=img-resize-server}]' `
    --query 'Instances[0].InstanceId' `
    --output text
```

### 5.3 インスタンス状態確認

```powershell
# インスタンスID指定で状態確認
aws ec2 describe-instances `
    --instance-ids i-xxxxxxxxxxxxxxxxx `
    --query 'Reservations[0].Instances[0].[State.Name,PublicIpAddress]' `
    --output text
```

---

## 6. デプロイ確認

### 6.1 SSH接続確認

```powershell
ssh -i ~/.ssh/img-resize-key.pem ubuntu@<PUBLIC_IP>
```

### 6.2 サービス状態確認

```bash
# サービス状態
sudo systemctl status img-resize

# ログ確認
sudo journalctl -u img-resize -f

# user-dataログ確認
sudo cat /var/log/user-data.log
```

### 6.3 アプリケーション動作確認

```powershell
# HTTPレスポンス確認
curl http://<PUBLIC_IP>:8000/
```

---

## 7. 再デプロイ手順

### 7.1 コード更新のみ

```powershell
# SSH経由で更新
ssh -i ~/.ssh/img-resize-key.pem ubuntu@<PUBLIC_IP> "cd /home/ubuntu/img_resize && git pull origin main && sudo systemctl restart img-resize"
```

### 7.2 サービス再起動

```powershell
ssh -i ~/.ssh/img-resize-key.pem ubuntu@<PUBLIC_IP> "sudo systemctl restart img-resize && sudo systemctl status img-resize --no-pager"
```

---

## 8. トラブルシューティング

### 8.1 インスタンスが起動しない

```powershell
# インスタンス状態確認
aws ec2 describe-instance-status --instance-ids i-xxxxxxxxxxxxxxxxx

# システムログ取得
aws ec2 get-console-output --instance-ids i-xxxxxxxxxxxxxxxxx --output text
```

### 8.2 アプリケーションに接続できない

1. セキュリティグループのポート8000が開放されているか確認
2. サービスが起動しているか確認
3. ファイアウォール設定確認

```bash
# ポート確認
sudo netstat -tlnp | grep 8000

# ファイアウォール確認
sudo ufw status
```

### 8.3 サービス起動エラー

```bash
# 詳細ログ確認
sudo journalctl -u img-resize -n 50 --no-pager

# 手動起動テスト
cd /home/ubuntu/img_resize/backend
source .venv/bin/activate
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## 9. インスタンス管理

### 9.1 インスタンス停止

```powershell
aws ec2 stop-instances --instance-ids i-xxxxxxxxxxxxxxxxx
```

### 9.2 インスタンス開始

```powershell
aws ec2 start-instances --instance-ids i-xxxxxxxxxxxxxxxxx
```

### 9.3 インスタンス終了（削除）

```powershell
aws ec2 terminate-instances --instance-ids i-xxxxxxxxxxxxxxxxx
```

---

## 10. 現行環境情報

| 項目 | 値 |
|------|-----|
| インスタンスID | i-07eca3c2a961622bd |
| パブリックIP | 54.64.177.45 |
| キーペア名 | img-resize-final |
| セキュリティグループ | sg-0d5743f0007e2b46e |
| アプリケーションURL | http://54.64.177.45:8000/ |

### 10.1 現行環境への再デプロイコマンド

```powershell
ssh -o StrictHostKeyChecking=no -i ~/.ssh/img-resize-final.pem ubuntu@54.64.177.45 "cd /home/ubuntu/img_resize && git pull origin main && sudo systemctl restart img-resize && sudo systemctl status img-resize --no-pager"
```

---

## 11. セキュリティ推奨事項

1. **本番環境では以下を変更:**
   - `SESSION_SECRET` を強力なランダム文字列に変更
   - `APP_USER` / `APP_PASSWORD` をデフォルトから変更
   - SSHアクセスを特定IPに制限

2. **HTTPS対応:**
   - Let's Encryptでssl証明書取得
   - Nginxをリバースプロキシとして設定

3. **監視設定:**
   - CloudWatch Alarmでインスタンス監視
   - ログをCloudWatch Logsに転送

---

## 12. 変更履歴

| 日付 | 変更内容 |
|------|---------|
| 2026-01-29 | 初版作成 |
