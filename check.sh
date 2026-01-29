# 1. Kill the crashing Web Pod (Clean slate)
Write-Host "1. Removing stuck Web Pods..." -ForegroundColor Cyan
kubectl delete pods -l app=web --force --grace-period=0

# 2. Wait for Database to be 100% Ready (Future Proofing)
Write-Host "2. Waiting for Database to be ready..." -ForegroundColor Cyan
kubectl wait --for=condition=ready pod -l app=postgres --timeout=90s

# 3. Restart the Web Deployment (To pick up the running DB)
Write-Host "3. Starting Web Server..." -ForegroundColor Cyan
kubectl rollout restart deployment web

# 4. Wait for Web Server to be Ready (Prevents 'Connection Refused')
Write-Host "4. Waiting for Web App to initialize..." -ForegroundColor Cyan
kubectl wait --for=condition=ready pod -l app=web --timeout=90s

# 5. Get the New Pod Name
$WEB_POD = kubectl get pods -l app=web -o jsonpath="{.items[0].metadata.name}"

# 6. Run Migrations (Required since DB volume is ephemeral)
Write-Host "5. Running Database Migrations..." -ForegroundColor Cyan
kubectl exec $WEB_POD -- python manage.py migrate

# 7. Start the Connection
Write-Host "6. ESTABLISHING CONNECTION. Keep this window open!" -ForegroundColor Green
Write-Host "   Open Browser: http://127.0.0.1:8000/files/dashboard/" -ForegroundColor Yellow
kubectl port-forward $WEB_POD 8000:8000