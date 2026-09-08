# Pushes the current code to the VM, restarts the service and re-scores.
# Safe to run any time - the database migrates itself, so this never wipes
# your swipe history, your blocked models or your settings.
$key  = "C:\Users\joenam_tangi0\Documents\camera-flipper\keys\vm-ssh.key"
$vm   = "opc@145.241.226.235"
$here = "C:\Users\joenam_tangi0\Documents\camera-flipper"

Write-Host "1/5  Copying code to the VM..." -ForegroundColor Cyan
scp -i $key `
  "$here\webapp.py" "$here\db.py" "$here\schema.sql" "$here\signals.py" `
  "$here\cli.py" "$here\scoring.py" "$here\camera_knowledge.py" `
  "$here\ebay_client.py" "$here\requirements.txt" "$here\update_env.py" `
  "$here\rescore.py" `
  "${vm}:~/camera-flipper/"
if ($LASTEXITCODE -ne 0) { Write-Host "Copy failed - is the VM reachable?" -ForegroundColor Red; exit 1 }

scp -i $key -r "$here\static" "${vm}:~/camera-flipper/"

Write-Host "2/5  Updating settings (your credentials are left alone)..." -ForegroundColor Cyan
ssh -i $key $vm "cd ~/camera-flipper && python3 update_env.py"

Write-Host "3/5  Installing dependencies..." -ForegroundColor Cyan
ssh -i $key $vm "cd ~/camera-flipper && source venv/bin/activate && pip install -q -r requirements.txt"

Write-Host "4/5  Restarting the app..." -ForegroundColor Cyan
ssh -i $key $vm "sudo systemctl restart camera-flipper; sleep 4; systemctl is-active camera-flipper"

Write-Host "5/5  Re-scoring your queue with the current values..." -ForegroundColor Cyan
ssh -i $key $vm "cd ~/camera-flipper && source venv/bin/activate && python rescore.py"

Write-Host ""
Write-Host "Done  ->  http://145.241.226.235:8000" -ForegroundColor Green
Write-Host ""
Write-Host "If anything looks wrong, watch the live log with:" -ForegroundColor DarkGray
Write-Host "  ssh -i `"$key`" $vm 'journalctl -u camera-flipper -f'" -ForegroundColor DarkGray
