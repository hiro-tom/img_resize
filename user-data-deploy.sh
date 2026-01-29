#!/bin/bash
exec > /var/log/user-data.log 2>&1

echo "Starting deployment at $(date)"

# Update package list
apt-get update -y

# Install Python 3.11 and dependencies
apt-get install -y python3.11 python3.11-venv python3-pip git libjpeg-dev zlib1g-dev

# Clone repository
cd /home/ubuntu
git clone https://github.com/hiro-tom/img_resize.git || true
cd img_resize
chown -R ubuntu:ubuntu /home/ubuntu/img_resize

# Set up backend
cd backend
sudo -u ubuntu python3.11 -m venv .venv
sudo -u ubuntu /home/ubuntu/img_resize/backend/.venv/bin/pip install --upgrade pip
sudo -u ubuntu /home/ubuntu/img_resize/backend/.venv/bin/pip install -r requirements.txt

# Create systemd service file
cat > /etc/systemd/system/img-resize.service <<'EOF'
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

# Start service
systemctl daemon-reload
systemctl enable img-resize
systemctl start img-resize

echo "Deployment complete at $(date)"
