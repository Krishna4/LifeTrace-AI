# 🚀 Deploying LifeTrace AI on 100% Native Cloudflare Serverless Stack

This folder contains the **100% native Cloudflare Worker deployment** for LifeTrace AI.  
It runs on Cloudflare's global edge network with **zero external API dependencies** (no OpenRouter, OpenAI, or third-party keys required) and is **100% free-tier eligible**.

---

## 🏗️ Architecture

* **Edge Compute & Routing**: Cloudflare Workers + [Hono](https://hono.dev/) framework
* **Relational Storage**: Cloudflare D1 (Serverless SQLite)
* **Vector Database**: Cloudflare Vectorize (384-dimensional cosine index)
* **AI Embeddings**: Cloudflare Workers AI (`@cf/baai/bge-small-en-v1.5`)
* **AI LLM & Synthesis**: Cloudflare Workers AI (`@cf/meta/llama-3.3-70b-instruct`)
* **Telegram Interface**: Native Webhook (`/telegram/webhook`) + 8:00 AM Daily Morning Agenda Cron

---

## 📋 Prerequisites

1. Install [Node.js](https://nodejs.org/) (v18+)
2. Install Cloudflare Wrangler CLI:
   ```bash
   npm install -g wrangler
   ```
3. Login to your Cloudflare account:
   ```bash
   wrangler login
   ```

---

## ⚡ Deployment in 5 Steps

### Step 1: Create the Cloudflare D1 Database
Run this in the `cloudflare/` directory:
```bash
wrangler d1 create personal-rag-db
```
This prints your unique `database_id`. Open `wrangler.toml` and paste it under `database_id`:
```toml
[[d1_databases]]
binding = "DB"
database_name = "personal-rag-db"
database_id = "PASTE_YOUR_DATABASE_ID_HERE"
```

### Step 2: Initialize Database Tables
Execute the migration schema against your remote D1 database:
```bash
wrangler d1 execute personal-rag-db --file=schema.sql --remote
```

### Step 3: Create the Vectorize Vector Database
Create the 384-dimensional vector index matching BGE-Small embeddings:
```bash
wrangler vectorize create personal-rag-vectors --dimensions=384 --metric=cosine
```

### Step 4: Configure Secrets & Auth Guards
Set your Telegram credentials and API Key as encrypted Cloudflare secrets:
```bash
# 1. Telegram Credentials (for mobile ledger & briefings)
wrangler secret put TELEGRAM_BOT_TOKEN
# Enter your bot token from @BotFather

wrangler secret put TELEGRAM_CHAT_ID
# Enter your chat ID or channel ID (e.g. 123456789 or @MyChannel)

# 2. Auth Guard API Key (Protects /api/v1/* from unauthorized access)
wrangler secret put API_KEY
# Enter a secure passphrase/key of your choice
```

> **Auth Guards in Place:**
> - **API Auth Guard:** When `API_KEY` is set, all calls to `/api/v1/*` require `Authorization: Bearer <API_KEY>` or `X-API-Key: <API_KEY>` (returns `401 Unauthorized` otherwise).
> - **Telegram Chat Guard:** Only messages from your configured `TELEGRAM_CHAT_ID` can query your personal records or trigger actions (unauthorized users get blocked with `403 Forbidden`).

### Step 5: Deploy to Cloudflare Edge
Deploy your serverless application globally:
```bash
wrangler deploy
```


Once deployed, Wrangler will output your live URL:
`https://lifetrace-ai-edge.<your-subdomain>.workers.dev`

---

## 📲 Connecting Your Telegram Bot

Link your Telegram Bot to your Cloudflare Worker with a single request:
```bash
curl -F "url=https://lifetrace-ai-edge.<your-subdomain>.workers.dev/telegram/webhook" \
  https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook
```

### What You Can Do in Telegram:
* `/today` — Get today's scheduled life events
* `/events` — View recent events & meetings
* `/expenses` — View recent financial transactions
* `/digest` — Broadcast full daily agenda
* Send any question: *"Did I visit the dentist?"*, *"What meetings do I have?"*
* Automated 8:00 AM briefing sent to your chat every morning via Cloudflare Cron!
