# 📧 Enterprise Email Verification Platform (SaaS)

> **A high-performance, multi-tenant SaaS solution for cleaning email lists at scale.**
> Designed to handle millions of records with millisecond-latency filtering, credit management, and real-time analytics.

---

## 📑 Table of Contents

1. [📖 Project Overview](https://www.google.com/search?q=%23-project-overview)
2. [✨ Key Features](https://www.google.com/search?q=%23-key-features)
3. [🏗 System Architecture](https://www.google.com/search?q=%23-system-architecture)
* [Logical Multi-Tenancy](https://www.google.com/search?q=%23logical-multi-tenancy)
* [Redis Consistent Hashing](https://www.google.com/search?q=%23redis-consistent-hashing)


4. [⚙️ The Verification Pipeline](https://www.google.com/search?q=%23%25EF%25B8%258F-the-verification-pipeline)
5. [🛠 Tech Stack](https://www.google.com/search?q=%23-tech-stack)
6. [🚀 Installation & Setup](https://www.google.com/search?q=%23-installation--setup)
7. [📡 API Documentation](https://www.google.com/search?q=%23-api-documentation)
8. [💻 Operational Guide (Scaling)](https://www.google.com/search?q=%23-operational-guide-scaling)
9. [🔧 Configuration](https://www.google.com/search?q=%23-configuration)
10. [❓ Troubleshooting](https://www.google.com/search?q=%23-troubleshooting)

---

## 📖 Project Overview

This platform is a complete backend and frontend solution for **Email Verification**. It allows organizations to upload massive CSV files, which are processed asynchronously to validate email deliverability.

Unlike simple scripts, this system is built for **Commercial SaaS Operations**:

* **It creates isolated environments** for every registered user (Multi-Tenancy).
* **It scales horizontally** using Celery workers and Redis sharding.
* **It manages costs** via a built-in Credit System.
* **It learns** from past data by caching Bounces and Unsubscribes globally.

---

## ✨ Key Features

### 🛡️ Enterprise Security & Tenancy

* **Logical Isolation**: Every user gets a unique "Database Alias" (e.g., `db_x9z1`), ensuring their file history and results are logically separated from others, even though they share the same physical infrastructure.
* **JWT Authentication**: Stateless, secure API access using JSON Web Tokens.

### ⚡ High-Performance Processing

* **Smart Column Detection**: Automatically scans CSV headers to find email columns (supports `email`, `e-mail`, `mail`, `Contact Email`, etc.).
* **Chunked Processing**: Processes files in batches of 5,000 rows, allowing the system to handle 1GB+ files with minimal RAM usage.
* **Global Suppression Cache**: Bounced emails are stored in a **Shared Global Database** & **Redis Cache**. If User A finds a bad email, User B is protected from it instantly without spending credits.

### 📊 Real-Time Dashboard

* **Live Progress**: WebSocket-like polling updates progress circles (0% → 100%) in real-time.
* **Visual Analytics**: Charts for Valid, Risky, Bounced, and Unsubscribed counts.
* **Credit Management**: Auto-deducts credits (default 1,000,000) and blocks processing if funds are low.

---

## 🏗 System Architecture

The system uses a **microservices-like** architecture within a monorepo, orchestrated by Docker Compose.

```mermaid
graph TD
    User[User / Frontend] -->|REST API| Web[Django Web Server]
    Web -->|Auth & Metadata| DB[(PostgreSQL Main DB)]
    Web -->|Enqueue Task| Redis[(Redis Broker)]
    
    subgraph "Async Worker Cluster"
        Worker[Celery Worker]
    end
    
    Redis -->|Pop Task| Worker
    
    subgraph "Data Layer"
        Worker -->|Read/Write| DB
        Worker -->|O(1) Lookup| RedisCache[(Redis Cache Shards)]
    end

```

### Logical Multi-Tenancy

We use a **Shared Database, Isolated Schema** approach.

* **Router (`db_routers.py`)**: Intercepts every database query.
* **Logic**: Checks the `account_id` of the user. If the user is `Account_A`, the query is aliased to `db_account_a`.
* **Physical Storage**: All aliases point to the `default` PostgreSQL connection, but this architecture allows for future physical separation (sharding) without changing application code.

### Redis Consistent Hashing

To handle millions of suppression records (bounces), a single Redis instance might fill up. We implemented **Consistent Hashing** (`core/consistent_hash.py`).

* **How it works**: We define multiple "virtual" Redis nodes (shards).
* **Distribution**: When an email is added to the blocklist, we hash the email string to determine exactly which Redis shard holds that data.
* **Scalability**: This allows us to distribute memory load across multiple Redis databases (or physical servers in the future).

---

## ⚙️ The Verification Pipeline

When a file is uploaded, it goes through this rigorous 6-step pipeline:

1. **Format Validation**: Checks syntax (RFC 5322). `user@.com` is rejected immediately.
2. **Global Suppression Check**:
* Checks Redis Cache (O(1) speed).
* If found in Global Bounced/Unsub list -> Marked as **FILTERED** (Cost: 0 Credits).


3. **Domain/MX Check**: DNS lookup to ensure the domain exists and has valid Mail Exchanger (MX) records.
4. **Disposable Check**: Checks against a list of known temporary email providers (e.g., Yopmail).
5. **Role-Based Check**: Flags emails like `admin@`, `support@` as "Risky".
6. **SMTP Handshake (Simulation)**: Attempts to connect to the mail server without actually sending mail, verifying if the user exists.

---

## 🛠 Tech Stack

| Component | Technology | Description |
| --- | --- | --- |
| **Backend** | Python 3.11 + Django 5 | Robust web framework with DRF for APIs. |
| **Task Queue** | Celery 5.3 | Distributed task queue for async processing. |
| **Broker/Cache** | Redis 6-alpine | Message broker and in-memory cache. |
| **Database** | PostgreSQL 14 | Relational database for persistent storage. |
| **Data Science** | Pandas | High-performance CSV parsing and manipulation. |
| **Container** | Docker | Containerization for consistent environments. |

---

## 🚀 Installation & Setup

### Prerequisites

* Docker Desktop & Docker Compose
* Git

### 1. Clone & Config

```bash
git clone https://github.com/your-username/email-verifier.git
cd email-verifier

# Create .env file (See Configuration section below for contents)
touch .env

```

### 2. Build Infrastructure

```bash
# Build and start services in detached mode
docker-compose up -d --build

```

### 3. Initialize Database

Since the database is fresh, apply migrations to create the schema.

```bash
docker-compose exec web python manage.py migrate

```

### 4. Create Super Admin (Optional)

To access the Django Admin panel:

```bash
docker-compose exec web python manage.py createsuperuser
# Follow prompts. NOTE: Run the account linking script (in docs) to give admin credits.

```

### 5. Access

* **Dashboard**: `http://localhost:8000/login/`
* **API Root**: `http://localhost:8000/api/`

---

## 📡 API Documentation

### Authentication

**Login**

* `POST /api/v2/auth/login/`
* **Body**: `{"email": "user@test.com", "password": "password"}`
* **Response**: `{"access_token": "...", "user": {...}}`

**Register**

* `POST /api/v2/auth/register/`
* **Body**: `{"email": "new@test.com", "password": "password", "organization_name": "My Corp"}`

### File Operations

**Upload File**

* `POST /api/v1/upload/`
* **Header**: `Authorization: Bearer <token>`
* **Body (Form-Data)**: `file` = `list.csv`

**Get Progress**

* `GET /api/v1/history/`
* **Returns**: List of files with `unique_record_count` (Valid), `filtered_bounce_count` (Bad), and status.

### Management

**Get Credits**

* `GET /api/v1/credits/`
* **Returns**: `{"credits": 1000000.0}`

---

## 💻 Operational Guide: Scaling

### Adding Redis Shards

If your dataset grows too large for one Redis DB, you can add shards in `core/redis_utils.py`.

1. **Update Config**: Add `'shard_04'` to `REDIS_NODES_CONFIG`.
2. **Restart Containers**: `docker-compose restart`.
3. **Rebalance Data**: Run the custom management command to re-distribute data to the new shard structure.

```bash
docker-compose exec web python manage.py refresh_redis

```

### Handling Port Conflicts

If port `5432` is blocked on your host machine (Windows/Mac), the `docker-compose.yml` is configured to use **54321** for external access.

* **App Internal**: Connects via `main_db:5432`.
* **External Tool (pgAdmin/DBeaver)**: Connect via `localhost:54321`.

---

## 🔧 Configuration

**Recommended `.env` file:**

```ini
# --- CORE SETTINGS ---
SECRET_KEY=change-this-in-production-random-string
DEBUG=True
ALLOWED_HOSTS=*

# --- DATABASE (Internal Docker Network) ---
SQL_ENGINE=django.db.backends.postgresql
SQL_DATABASE=postgres
SQL_USER=postgres
SQL_PASSWORD=postgres
SQL_HOST=main_db
# NOTE: This must remain 5432 (Internal Port)
SQL_PORT=5432  

# --- REDIS & CELERY ---
REDIS_HOST=redis
REDIS_PORT=6379
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1

# --- SYSTEM CONSTANTS ---
MAIN_DB_LABEL=default

```

---

## ❓ Troubleshooting

### 🛑 "Relation 'accounts_account' does not exist"

* **Cause**: The database container was rebuilt, but tables weren't created.
* **Fix**: Run `docker-compose exec web python manage.py migrate`.

### 🛑 "Network Error" on Frontend

* **Cause**: The API is unreachable.
* **Fix**: Check if the backend crashed (`docker-compose logs -f web`). Ensure your frontend is calling `http://localhost:8000`.

### 🛑 Superuser has no credits / not working

* **Cause**: `createsuperuser` bypasses the Account creation logic.
* **Fix**: You must manually link an Account object to the superuser via `python manage.py shell`. (See `fix_db.py` or project documentation for the script).

### 🛑 Port 5432 already allocated

* **Cause**: Local Postgres or Hyper-V is using the port.
* **Fix**: In `docker-compose.yml`, change the `main_db` ports to `"54321:5432"`. The internal app will still work fine.

---

### 📜 License

This project is licensed under the **MIT License**.

* **Author**: VenuG
* **Year**: 2026