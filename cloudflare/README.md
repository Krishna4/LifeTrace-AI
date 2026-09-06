# 🚀 Deploying LifeTrace AI on 100% Native Cloudflare Serverless Stack

This folder contains the **100% native Cloudflare Worker deployment** for LifeTrace AI.  
It runs on Cloudflare's global edge network with **zero external API dependencies** (no OpenRouter, OpenAI, or third-party keys required) and operates entirely within Cloudflare's **100% free tier ($0.00 / month)**.

---

## 🏗️ Architecture

* **Edge Compute & Routing**: Cloudflare Workers + [Hono](https://hono.dev/) framework
* **Relational Storage**: Cloudflare D1 (Serverless SQLite)
* **Vector Database**: Cloudflare Vectorize (384-dimensional cosine index)
* **AI Embeddings**: Cloudflare Workers AI (`@cf/baai/bge-small-en-v1.5`)
* **AI Extraction & RAG Synthesis**: Cloudflare Workers AI (`@cf/meta/llama-3.1-8b-instruct-fp8` & `@cf/meta/llama-3.3-70b-instruct-fp8-fast`)
* **Telegram Interface**: Native Webhook (`/telegram/webhook`) + Automated Morning Agenda Cron

---

## ⚡ Quick Start: 1-Command Automated Setup Wizard

If you just cloned this repository, you can deploy your own instance in under 2 minutes:

```bash
cd cloudflare
npm install
npm run setup
```

The setup script (`setup.sh`) will automatically:
1. Verify Cloudflare authentication (`wrangler whoami`).
2. Create your D1 Database (`personal-rag-db`) and inject its ID into `wrangler.toml`.
3. Apply the SQL schema to remote D1.
4. Create the Vectorize index (`personal-rag-vectors`).
5. Prompt for optional Telegram secrets (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `API_KEY`).
6. Deploy globally to Cloudflare Edge!

---

## 🛠️ Manual Step-by-Step Setup

If you prefer configuring resources manually:

### Step 1: Copy Configuration Template
```bash
cp wrangler.toml.example wrangler.toml
```

### Step 2: Create D1 Database & Apply Schema
```bash
npx wrangler d1 create personal-rag-db
# Copy the printed database_id into wrangler.toml under [[d1_databases]] database_id

npx wrangler d1 execute personal-rag-db --file=schema.sql --remote
```

### Step 3: Create Vectorize Index
```bash
npx wrangler vectorize create personal-rag-vectors --dimensions=384 --metric=cosine
```

### Step 4: Configure Secrets & Auth Guards
```bash
# Telegram credentials (from @BotFather and your chat ID)
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put TELEGRAM_CHAT_ID

# Optional Auth Guard API Key for /api/v1/*
npx wrangler secret put API_KEY
```

> **🛡️ Auth Guards in Place:**
> - **API Auth Guard:** When `API_KEY` is set, all calls to `/api/v1/*` require `Authorization: Bearer <API_KEY>` or `X-API-Key: <API_KEY>` (`401 Unauthorized` otherwise).
> - **Telegram Chat Guard:** Only messages from your configured `TELEGRAM_CHAT_ID` can access or alter your personal data (`403 Forbidden` for other users).

### Step 5: Deploy to Cloudflare Edge
```bash
npx wrangler deploy
```

Once deployed, Wrangler will output your live URL:
`https://lifetrace-ai-edge.<your-subdomain>.workers.dev`

---

## 📲 Connecting Your Telegram Bot

Link your Telegram Bot to your Cloudflare Worker with a single request:
```bash
curl -F "url=https://<your-worker-url>.workers.dev/telegram/webhook" \
  https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook
```

### What You Can Do in Telegram:
* **View Agenda:** `/today` or `/digest`
* **Log Life Events:** 
  * `/log attended AI workshop in office today`
  * Or simply type naturally: `"Visited dentist today for cleaning"`
* **Log Financial Transactions:**
  * `/spend 50 groceries at Walmart`
* **View Records:** `/events` or `/expenses`
* **Delete an Entry:** `/delete <id>`
* **Ask Grounded RAG Questions:** *"Did I visit the dentist this week?"*, *"What meetings do I have?"*
* **Morning Briefing:** Automated agenda delivered to your chat every morning via Cloudflare Cron!

