# デプロイ監視スクリプト

# インスタンス情報
$INSTANCE_ID = "i-021b05b6ced172fd9"
$PUBLIC_IP = "13.231.201.247"

Write-Host "=== EC2 Instance Deployment Monitor ===" -ForegroundColor Cyan
Write-Host "Instance ID: $INSTANCE_ID" -ForegroundColor Yellow
Write-Host "Public IP: $PUBLIC_IP" -ForegroundColor Yellow
Write-Host ""

# インスタンスの状態確認
Write-Host "Checking instance state..." -ForegroundColor Green
aws ec2 describe-instances --instance-ids $INSTANCE_ID --query 'Reservations[0].Instances[0].[State.Name,PublicIpAddress]' --output table

# インスタンスステータス確認
Write-Host "`nChecking instance status..." -ForegroundColor Green
aws ec2 describe-instance-status --instance-ids $INSTANCE_ID --query 'InstanceStatuses[0].[InstanceStatus.Status,SystemStatus.Status]' --output table

# アプリケーション起動確認
Write-Host "`nChecking application..." -ForegroundColor Green
try {
    $response = Invoke-WebRequest -Uri "http://$PUBLIC_IP:8000" -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
    Write-Host "✓ Application is running!" -ForegroundColor Green
    Write-Host "Status Code: $($response.StatusCode)" -ForegroundColor Green
    Write-Host "URL: http://$PUBLIC_IP:8000" -ForegroundColor Cyan
} catch {
    Write-Host "✗ Application not responding yet" -ForegroundColor Red
    Write-Host "This is normal during initial deployment (takes 3-5 minutes)" -ForegroundColor Yellow
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor DarkGray
}

Write-Host "`n=== Useful Commands ===" -ForegroundColor Cyan
Write-Host "View console output:" -ForegroundColor White
Write-Host "  aws ec2 get-console-output --instance-id $INSTANCE_ID --query 'Output' --output text" -ForegroundColor Gray
Write-Host "`nTest connection:" -ForegroundColor White
Write-Host "  curl http://$PUBLIC_IP:8000" -ForegroundColor Gray
Write-Host "`nSSH connection:" -ForegroundColor White
Write-Host "  ssh -i path/to/key.pem ubuntu@$PUBLIC_IP" -ForegroundColor Gray
