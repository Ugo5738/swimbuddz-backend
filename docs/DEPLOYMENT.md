# SwimBuddz Backend - Deployment Guide

Comprehensive deployment guide for the SwimBuddz microservices backend.

---

## Overview

The SwimBuddz backend consists of:

- **11 microservices** (ports 8000-8010)
- **1 PostgreSQL database**
- **Docker containerization**
- **API Gateway** (single entry point on port 8000)

---

## Prerequisites

1. ✅ Server with Docker and Docker Compose installed
2. ✅ PostgreSQL database (or use Docker)
3. ✅ Supabase project for authentication
4. ✅ Domain name (optional but recommended)
5. ✅ SSL certificate (Let's Encrypt via Nginx)

---

## Deployment Options

### Option 1: Docker Compose (Recommended for Small-Medium Scale)

Best for:

- Initial launch
- Small to medium traffic
- Single-server deployment
- Development/staging environments

### Option 2: Kubernetes (For Large Scale)

Best for:

- High traffic
- Multi-server deployment
- Auto-scaling requirements
- Production at scale

**This guide focuses on Option 1 (Docker Compose).**

---

## Quick Deployment Steps

### 1. Server Setup

**Requirements:**

- Ubuntu 20.04+ or similar Linux distribution
- 4+ GB RAM (8 GB recommended)
- 50+ GB disk space
- Docker 20.10+
- Docker Compose 2.0+

**Install Docker:**

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Install Docker Compose
sudo apt install docker-compose-plugin -y

# Verify installation
docker --version
docker compose version
```

### 2. Clone Repository

```bash
# Clone repo
git clone https://github.com/your-org/swimbuddz.git
cd swimbuddz/swimbuddz-backend
```

### 3. Environment Configuration

**Create `.env` file:**

```bash
cp .env.example .env
```

**Edit `.env` with production values:**

```bash
# Database
DATABASE_URL=postgresql+psycopg://user:password@db:5432/swimbuddz
POSTGRES_USER=swimbuddz_user
POSTGRES_PASSWORD=<strong-password>
POSTGRES_DB=swimbuddz

# Supabase Auth
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SUPABASE_JWT_SECRET=your-jwt-secret

# API URLs (for service-to-service communication)
GATEWAY_URL=http://gateway-service:8000
MEMBERS_SERVICE_URL=http://members-service:8001
SESSIONS_SERVICE_URL=http://sessions-service:8002
ATTENDANCE_SERVICE_URL=http://attendance-service:8003
COMMUNICATIONS_SERVICE_URL=http://communications-service:8004
PAYMENTS_SERVICE_URL=http://payments-service:8005
ACADEMY_SERVICE_URL=http://academy-service:8006
EVENTS_SERVICE_URL=http://events-service:8007
MEDIA_SERVICE_URL=http://media-service:8008
TRANSPORT_SERVICE_URL=http://transport-service:8009
STORE_SERVICE_URL=http://store-service:8010

# Paystack (for payments)
PAYSTACK_SECRET_KEY=sk_live_...
PAYSTACK_PUBLIC_KEY=pk_live_...

# Email (optional - for communications service)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password

# Environment
ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=INFO

# CORS (frontend URLs)
ALLOWED_ORIGINS=https://swimbuddz.com,https://www.swimbuddz.com
```

**⚠️ Security:**

- Never commit `.env` to version control
- Use strong passwords
- Rotate secrets regularly
- Use encrypted secret management (see [DEPLOY_ENV_GPG.md](./DEPLOY_ENV_GPG.md))

### 4. Database Migrations

**Run migrations before starting services:**

```bash
# Start only the database
docker compose up -d db

# Wait for database to be ready
sleep 10

# Run migrations
docker compose run --rm gateway-service alembic upgrade head
```

### 5. Build and Deploy

```bash
# Build all services
docker compose build

# Start all services
docker compose up -d

# Check status
docker compose ps

# View logs
docker compose logs -f
```

**Services should now be running:**

- Gateway: http://localhost:8000
- Individual services: http://localhost:8001-8010
- Database: localhost:5432

### 6. Verify Deployment

```bash
# Check gateway health
curl http://localhost:8000/health

# Check individual service
curl http://localhost:8001/health

# View API docs
open http://localhost:8000/docs
```

---

## Production Configuration

### Nginx Reverse Proxy (Recommended)

**Install Nginx:**

```bash
sudo apt install nginx -y
```

**Create Nginx config:**

```nginx
# /etc/nginx/sites-available/swimbuddz-api

upstream backend {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name api.swimbuddz.com;

    # Redirect to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name api.swimbuddz.com;

    # SSL certificates (Let's Encrypt)
    ssl_certificate /etc/letsencrypt/live/api.swimbuddz.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.swimbuddz.com/privkey.pem;

    # SSL settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # Proxy settings
    location / {
        proxy_pass http://backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # Health check endpoint
    location /health {
        proxy_pass http://backend/health;
        access_log off;
    }

    # Increase max body size (for file uploads)
    client_max_body_size 50M;
}
```

**Enable site:**

```bash
sudo ln -s /etc/nginx/sites-available/swimbuddz-api /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### SSL Certificate (Let's Encrypt)

```bash
# Install Certbot
sudo apt install certbot python3-certbot-nginx -y

# Get certificate
sudo certbot --nginx -d api.swimbuddz.com

# Auto-renewal is configured automatically
# Test renewal
sudo certbot renew --dry-run
```

### Firewall Configuration

```bash
# Allow SSH
sudo ufw allow 22/tcp

# Allow HTTP/HTTPS
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Enable firewall
sudo ufw enable
```

---

## Database Management

### Backups

**Automated daily backups:**

```bash
# Create backup script
cat > /usr/local/bin/backup-swimbuddz-db.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/var/backups/swimbuddz"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p $BACKUP_DIR

docker exec swimbuddz-db pg_dump -U swimbuddz_user swimbuddz | gzip > $BACKUP_DIR/backup_$DATE.sql.gz

# Keep only last 30 days
find $BACKUP_DIR -name "backup_*.sql.gz" -mtime +30 -delete
EOF

chmod +x /usr/local/bin/backup-swimbuddz-db.sh
```

**Add to crontab:**

```bash
# Run daily at 2 AM
sudo crontab -e

# Add line:
0 2 * * * /usr/local/bin/backup-swimbuddz-db.sh
```

### Restore from Backup

```bash
# Stop services
docker compose down

# Restore database
gunzip -c /var/backups/swimbuddz/backup_YYYYMMDD_HHMMSS.sql.gz | \
  docker exec -i swimbuddz-db psql -U swimbuddz_user swimbuddz

# Start services
docker compose up -d
```

---

## Monitoring & Logging

### View Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f gateway-service

# Last 100 lines
docker compose logs --tail=100

# Follow with timestamps
docker compose logs -f --timestamps
```

### Log Rotation

Docker automatically rotates logs. Configure in `/etc/docker/daemon.json`:

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
```

Restart Docker:

```bash
sudo systemctl restart docker
```

### Health Monitoring

**Simple health check script:**

```bash
#!/bin/bash
# /usr/local/bin/check-swimbuddz-health.sh

API_URL="https://api.swimbuddz.com/health"
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" $API_URL)

if [ $RESPONSE -eq 200 ]; then
    echo "✅ API is healthy"
    exit 0
else
    echo "❌ API is down (HTTP $RESPONSE)"
    # Send alert (email, Slack, etc.)
    exit 1
fi
```

**Run every 5 minutes:**

```bash
# Add to crontab
*/5 * * * * /usr/local/bin/check-swimbuddz-health.sh
```

---

## Continuous Deployment

### Option 1: GitHub Actions

**Create `.github/workflows/deploy.yml`:**

```yaml
name: Deploy to Production

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to server
        uses: appleboy/ssh-action@master
        with:
          host: ${{ secrets.SERVER_HOST }}
          username: ${{ secrets.SERVER_USER }}
          key: ${{ secrets.SSH_PRIVATE_KEY }}
          script: |
            cd /path/to/swimbuddz/swimbuddz-backend
            git pull origin main
            docker compose build
            docker compose up -d
            docker compose run --rm gateway-service alembic upgrade head
```

**Set GitHub secrets:**

- `SERVER_HOST` - Your server IP/domain
- `SERVER_USER` - SSH username
- `SSH_PRIVATE_KEY` - SSH private key

### Option 2: Manual Deployment

```bash
# SSH into server
ssh user@api.swimbuddz.com

# Navigate to project
cd /path/to/swimbuddz/swimbuddz-backend

# Pull latest code
git pull origin main

# Rebuild and restart
docker compose build
docker compose up -d

# Run migrations
docker compose run --rm gateway-service alembic upgrade head

# Check status
docker compose ps
```

---

## Scaling & Performance

### Horizontal Scaling

**Scale specific services:**

```bash
# Scale gateway service to 3 instances
docker compose up -d --scale gateway-service=3

# Scale academy service to 2 instances
docker compose up -d --scale academy-service=2
```

**Add load balancer (Nginx):**

```nginx
upstream backend {
    least_conn;  # Load balancing method
    server 127.0.0.1:8000;
    server 127.0.0.1:8001;
    server 127.0.0.1:8002;
}
```

### Database Connection Pooling

Already configured in SQLAlchemy. Adjust in `libs/db/database.py`:

```python
engine = create_engine(
    DATABASE_URL,
    pool_size=20,        # Connections per service
    max_overflow=10,     # Additional connections
    pool_pre_ping=True,  # Verify connections
)
```

### Caching (Future Enhancement)

Consider adding Redis for:

- Session caching
- API response caching
- Rate limiting

---

## Security Best Practices

### 1. Environment Variables

- ✅ Never commit `.env` to version control
- ✅ Use strong passwords (16+ characters)
- ✅ Rotate secrets regularly
- ✅ Use encrypted secret management (see [DEPLOY_ENV_GPG.md](./DEPLOY_ENV_GPG.md))

### 2. Database Security

- ✅ Strong database password
- ✅ Database not exposed to public internet
- ✅ Regular backups
- ✅ Use SSL for database connections (if external)

### 3. API Security

- ✅ HTTPS only (enforce with Nginx)
- ✅ CORS configured for specific origins
- ✅ JWT authentication for all protected endpoints
- ✅ Rate limiting (implement with Nginx or FastAPI)

### 4. Server Security

- ✅ Firewall enabled (UFW)
- ✅ SSH key authentication only (disable password auth)
- ✅ Regular security updates
- ✅ Fail2ban for SSH protection

**Configure Fail2ban:**

```bash
sudo apt install fail2ban -y
sudo systemctl enable fail2ban
sudo systemctl start fail2ban
```

---

## Troubleshooting

### Services Won't Start

**Check logs:**

```bash
docker compose logs service-name
```

**Common issues:**

- Database not ready → Add healthcheck in docker-compose.yml
- Port already in use → Check with `sudo netstat -tulpn`
- Missing environment variables → Check `.env` file

### Database Connection Errors

**Check:**

1. Database is running: `docker compose ps db`
2. Correct credentials in `.env`
3. Database URL format: `postgresql+psycopg://user:pass@host:port/db`

### Migration Failures

**Reset migrations (CAUTION - data loss):**

```bash
# Drop all tables
docker compose exec db psql -U swimbuddz_user -d swimbuddz -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

# Rerun migrations
docker compose run --rm gateway-service alembic upgrade head
```

**Create new migration:**

```bash
docker compose run --rm gateway-service alembic revision --autogenerate -m "description"
```

### High Memory Usage

**Check container stats:**

```bash
docker stats
```

**Restart specific service:**

```bash
docker compose restart service-name
```

---

## Rollback Procedure

If deployment fails:

```bash
# View recent commits
git log --oneline -10

# Rollback to previous commit
git reset --hard <commit-hash>

# Rebuild and restart
docker compose build
docker compose up -d

# Rollback migrations if needed
docker compose run --rm gateway-service alembic downgrade -1
```

---

## Cost Estimate

### DigitalOcean Droplet (Recommended for Start)

**$48/month:**

- 4 GB RAM
- 2 vCPUs
- 80 GB SSD
- 4 TB transfer

**Sufficient for:**

- 500-1000 active users
- Moderate traffic
- All 11 microservices

### AWS EC2 (Alternative)

**t3.medium: $30-40/month**

- 4 GB RAM
- 2 vCPUs
- Similar performance

**RDS PostgreSQL: $15-30/month**

- Managed database
- Automated backups
- High availability

---

## Support & Resources

**SwimBuddz Documentation:**

- [ARCHITECTURE.md](./ARCHITECTURE.md) - System architecture
- [DEPLOY_ENV_GPG.md](./DEPLOY_ENV_GPG.md) - Environment secrets management
- [CONVENTIONS.md](./CONVENTIONS.md) - Coding standards

**Docker Documentation:**

- https://docs.docker.com

**FastAPI Deployment:**

- https://fastapi.tiangolo.com/deployment/

---

## Post-Deployment Checklist

- [ ] All services running (`docker compose ps`)
- [ ] Health checks passing (`curl https://api.swimbuddz.com/health`)
- [ ] Database migrations applied
- [ ] SSL certificate installed and valid
- [ ] CORS configured for frontend domain
- [ ] Supabase authentication working
- [ ] Paystack integration tested (if applicable)
- [ ] Backup cron job configured
- [ ] Monitoring/alerting set up
- [ ] DNS configured (api.swimbuddz.com → server IP)
- [ ] Firewall rules configured
- [ ] Error tracking configured (Sentry, optional)

---

## ✅ Production Ready!

Your backend is now deployed with:

- ✅ 11 microservices running
- ✅ PostgreSQL database
- ✅ HTTPS with SSL
- ✅ Nginx reverse proxy
- ✅ Automated backups
- ✅ Health monitoring
- ✅ Security best practices

**Your API is live at https://api.swimbuddz.com! 🚀**
