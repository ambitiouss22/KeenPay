# KeenPay Docker Quickstart Guide

## Prerequisites

Before running Docker, ensure you have:

1. **Docker Desktop for Windows**
   - Download: https://www.docker.com/products/docker-desktop
   - Make sure WSL 2 (Windows Subsystem for Linux 2) is installed
   - Version requirement: Docker 20.10+

2. **Verify Installation**
   ```bash
   docker --version
   docker-compose --version
   ```

---

## Quick Start (Recommended)

### Option 1: Run Everything with Docker Compose (Easiest)

This will start PostgreSQL, Redis, and the KeenPay API all at once.

#### Step 1: Navigate to Project
```bash
cd C:\Users\Asus Vivobook\OneDrive\Desktop\KeenPay
```

#### Step 2: Build the Docker Image
```bash
docker-compose build
```

**Output:** You'll see:
```
Building keenpay_api
...
Successfully built [image_id]
```

#### Step 3: Start All Services
```bash
docker-compose up -d
```

**Output:** You'll see:
```
Creating keenpay_postgres ... done
Creating keenpay_redis ... done
Creating keenpay_api ... done
```

#### Step 4: Verify Services are Running
```bash
docker-compose ps
```

**Expected output:**
```
NAME              COMMAND                  SERVICE      STATUS
keenpay_api       "uvicorn main:app..."    keenpay_api  Up 10 seconds (healthy)
keenpay_postgres  "postgres"               postgres     Up 15 seconds (healthy)
keenpay_redis     "redis-server"           redis        Up 12 seconds (healthy)
keenpay_adminer   "docker-php-entrypoint" adminer      Up 8 seconds
```

#### Step 5: Test the API
```bash
# In PowerShell or Command Prompt
curl http://localhost:8000/health

# Or visit in browser:
# http://localhost:8000/docs (Swagger UI)
# http://localhost:8000/health (Health check)
```

**Expected response:**
```json
{"status": "ok"}
```

#### Step 6: Access Database (Optional)
- **Adminer Web UI:** http://localhost:8080
  - Server: `postgres`
  - Username: `keenpay`
  - Password: `keenpay_secure_password`
  - Database: `keenpay_db`

- **Or use psql:**
  ```bash
  psql -h localhost -U keenpay -d keenpay_db -p 5432
  # Password: keenpay_secure_password
  ```

---

## Option 2: Build and Run Just the API

If you already have PostgreSQL and Redis running locally:

### Step 1: Build Image Only
```bash
docker build -t keenpay:latest .
```

### Step 2: Run the Container
```bash
docker run -d \
  --name keenpay_api \
  -p 8000:8000 \
  -e DATABASE_URL=postgresql+asyncpg://keenpay:keenpay_secure_password@localhost:5432/keenpay_db \
  -e REDIS_URL=redis://localhost:6379/0 \
  -e RAZORPAY_KEY_ID=your_key \
  -e RAZORPAY_KEY_SECRET=your_secret \
  -e OPENAI_API_KEY=your_key \
  keenpay:latest
```

### Step 3: Check Logs
```bash
docker logs -f keenpay_api
```

---

## Common Commands

### View Logs
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f keenpay_api

# Last 100 lines
docker-compose logs --tail=100
```

### Stop Services
```bash
# Stop all
docker-compose down

# Stop specific service
docker-compose stop keenpay_api
```

### Restart Services
```bash
docker-compose restart

# Or specific service
docker-compose restart keenpay_api
```

### Access Container Shell
```bash
# Access API container
docker exec -it keenpay_api bash

# Access PostgreSQL
docker-compose exec postgres psql -U keenpay -d keenpay_db
```

### Rebuild After Code Changes
```bash
# Rebuild image
docker-compose build --no-cache

# Start fresh
docker-compose up -d
```

### Clean Up (Remove Everything)
```bash
# Stop and remove all containers
docker-compose down

# Remove volumes (database data)
docker-compose down -v

# Remove images
docker rmi keenpay_api:latest
```

---

## Troubleshooting

### Port Already in Use
If you get "port 8000 already in use":
```bash
# Change port in docker-compose.yml or use:
docker-compose up -d -e APP_PORT=8001
```

### Database Connection Failed
```bash
# Check if postgres is healthy
docker-compose ps

# View postgres logs
docker-compose logs postgres

# Reset database
docker-compose down -v
docker-compose up -d
```

### Out of Disk Space
```bash
# Clean up unused images/volumes
docker system prune -a

# Remove specific volume
docker volume rm keenpay_postgres_data
```

### Permission Denied on Windows
If you get permission errors:
1. Right-click Docker Desktop icon
2. Run as Administrator
3. Ensure WSL 2 backend is enabled (Docker Settings → Resources → WSL 2)

---

## Environment Variables

Edit `.env` file to configure:

```bash
# Database
DB_USER=keenpay
DB_PASSWORD=your_password
DB_NAME=keenpay_db
DB_PORT=5432

# Redis
REDIS_PORT=6379

# App
APP_PORT=8000
ENVIRONMENT=production
DEBUG=false

# API Keys (Add your actual keys)
RAZORPAY_KEY_ID=your_key
RAZORPAY_KEY_SECRET=your_secret
OPENAI_API_KEY=your_key
JWT_SECRET=your_secret_change_in_production
```

Then restart:
```bash
docker-compose up -d
```

---

## Development Workflow

### 1. Make Changes to Code
Edit files in `api/` folder

### 2. Rebuild Container
```bash
docker-compose build
```

### 3. Restart Service
```bash
docker-compose up -d keenpay_api
```

### 4. Check Logs
```bash
docker-compose logs -f keenpay_api
```

### 5. Test Changes
```bash
curl http://localhost:8000/health
```

---

## Production Deployment

For production, consider:

1. **Use a .env file (not in git)**
   ```bash
   cp .env.example .env
   # Edit .env with real secrets
   ```

2. **Set `ENVIRONMENT=production`** in .env

3. **Use strong passwords** for DB and JWT

4. **Enable HTTPS** (use nginx reverse proxy)

5. **Use managed database** (not container)

6. **Add monitoring** (Prometheus/Grafana)

7. **Set resource limits** in docker-compose.yml:
   ```yaml
   keenpay_api:
     deploy:
       resources:
         limits:
           cpus: '1'
           memory: 512M
   ```

---

## Getting Help

### Check Logs
```bash
docker-compose logs keenpay_api | tail -50
```

### Verify Network
```bash
# Test API container can reach postgres
docker-compose exec keenpay_api curl postgres:5432

# Test API container can reach redis
docker-compose exec keenpay_api redis-cli -h redis ping
```

### Run Tests in Container
```bash
docker-compose exec keenpay_api pytest -v
```

---

## Next Steps

1. ✅ Build Docker image: `docker-compose build`
2. ✅ Start services: `docker-compose up -d`
3. ✅ Access API: http://localhost:8000/docs
4. ✅ Test endpoint: `curl http://localhost:8000/health`
5. ✅ Run tests: `docker-compose exec keenpay_api pytest -v`
6. ✅ View logs: `docker-compose logs -f`

**You're ready to go! 🚀**
