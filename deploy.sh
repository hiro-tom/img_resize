#!/bin/bash
# Deployment script for img_resize app

set -e

echo "Starting deployment..."

# Update system
sudo apt-get update
sudo apt-get upgrade -y

# Install Python 3.11 and dependencies
sudo apt-get install -y python3.11 python3.11-venv python3-pip git

# Install system dependencies for Pillow
sudo apt-get install -y libjpeg-dev zlib1g-dev

# Clone repository
cd /home/ubuntu
if [ -d "img_resize" ]; then
    echo "Repository already exists, pulling latest changes..."
    cd img_resize
    git pull
else
    echo "Cloning repository..."
    git clone https://github.com/hiro-tom/img_resize.git
    cd img_resize
fi

# Set up backend
cd backend
echo "Setting up Python virtual environment..."
python3.11 -m venv .venv
source .venv/bin/activate

echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Create systemd service file
echo "Creating systemd service..."
sudo tee /etc/systemd/system/img-resize.service > /dev/null <<EOF
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

# Reload systemd and start service
echo "Starting service..."
sudo systemctl daemon-reload
sudo systemctl enable img-resize
sudo systemctl restart img-resize

echo "Deployment complete!"
echo "Application is running on port 8000"
echo "Check status: sudo systemctl status img-resize"
