# KeenPay — Deployment Summary

## Quick Start

### Prerequisites
```bash
- Python 3.10+
- PostgreSQL 14+
- Node.js 18+
- Docker (optional, for containerized deployment)
```

### Local Development Setup

1. **Clone & Install Backend**
   ```bash
   cd backend
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Set Up Environment Variables**
   ```bash
   cp .env.example .env
   # Edit .env with your values:
   # DATABASE_URL=postgresql://user:password@localhost:5432/keenpay
   # RAZORPAY_KEY_ID=your_key
   # RAZORPAY_SECRET=your_secret
   # ANTHROPIC_API_KEY=your_key
   ```

3. **Initialize Database**
   ```bash
   python scripts/init_db.py
   ```

4. **Start Backend**
   ```bash
   python api/main.py
   # Runs on http://localhost:8000
   ```

5. **Start Frontend** (in separate terminal)
   ```bash
   cd frontend
   npm install
   npm run dev
   # Runs on http://localhost:3000
   ```

---

## Production Deployment

### Option 1: Railway.app (Recommended for Demo)

1. **Connect GitHub Repository**
   - Push code to GitHub
   - Sign up at Railway.app
   - Create new project from GitHub repo

2. **Configure Environment Variables**
   - Add DATABASE_URL, RAZORPAY credentials, API keys in Railway dashboard

3. **Deploy**
   ```bash
   railway up
   ```

### Option 2: Docker Deployment

**Build Images**
```bash
docker-compose build
```

**Run Containers**
```bash
docker-compose up -d
```

**Access Application**
- API: http://localhost:8000
- Frontend: http://localhost:3000
- Database: PostgreSQL on localhost:5432

### Option 3: Traditional Server (VPS/EC2)

1. **SSH into Server**
   ```bash
   ssh user@server-ip
   ```

2. **Clone Repository**
   ```bash
   git clone https://github.com/yourusername/KeenPay.git
   cd KeenPay
   ```

3. **Install Dependencies & Configure**
   ```bash
   cd backend && pip install -r requirements.txt
   cd ../frontend && npm install
   ```

4. **Run with Systemd**
   - Backend: Create `/etc/systemd/system/keenpay-backend.service`
   - Frontend: Create `/etc/systemd/system/keenpay-frontend.service`
   - Enable and start services

5. **Set Up Reverse Proxy (Nginx)**
   ```nginx
   server {
       listen 80;
       server_name your-domain.com;

       location /api/ {
           proxy_pass http://localhost:8000;
       }

       location / {
           proxy_pass http://localhost:3000;
       }
   }
   ```

---

## Monitoring & Logs

### Backend Logs
```bash
# Local: Check console output
# Production: tail -f /var/log/keenpay/backend.log
```

### Database Logs
```bash
# Check PostgreSQL logs
tail -f /var/log/postgresql/postgresql.log
```

### API Health Check
```bash
curl http://localhost:8000/health
# Response: {"status": "healthy"}
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Database connection refused | Check DATABASE_URL in .env, ensure PostgreSQL is running |
| API port 8000 already in use | `lsof -i :8000` then kill the process or change PORT in .env |
| Frontend won't connect to API | Check CORS settings in `api/main.py`, ensure API_URL in frontend .env is correct |
| Razorpay payments failing | Verify test mode credentials, check webhook URL is reachable |

---

## Database Backups

```bash
# Backup PostgreSQL
pg_dump keenpay > backup_$(date +%Y%m%d).sql

# Restore
psql keenpay < backup_20260901.sql
```

---

## Performance Optimization

- **Database**: Add indexes on `users.email`, `orders.user_id`, `audit_logs.created_at`
- **Frontend**: Enable code splitting and lazy loading (already configured in Vite)
- **API**: Use connection pooling (pgbouncer) for PostgreSQL
- **Caching**: Redis for session storage (optional, improves performance 10-50x)

---

## Security Checklist

- [ ] All API keys in environment variables (never in code)
- [ ] HTTPS enabled in production
- [ ] CORS configured for specific domains only
- [ ] Database backups automated daily
- [ ] API rate limiting configured
- [ ] Prompt injection detection enabled
- [ ] HMAC verification for Razorpay webhooks enabled

---

## Support & Questions

For issues during deployment:
1. Check logs: `docker logs keenpay-backend`
2. Test API: `curl http://localhost:8000/docs` (Swagger UI)
3. Review `.env` configuration
4. Check GitHub issues: https://github.com/yourusername/KeenPay/issues
