import { Hono } from 'hono';
import { cors } from 'hono/cors';

export interface Env {
  DB: D1Database;
  VECTORIZE: VectorizeIndex;
  AI: any;
  TELEGRAM_BOT_TOKEN?: string;
  TELEGRAM_CHAT_ID?: string;
  TELEGRAM_SECRET_TOKEN?: string;
  API_KEY?: string;
  DEFAULT_USER?: string;
  ENVIRONMENT?: string;
}

const app = new Hono<{ Bindings: Env }>();

app.use('*', cors());

// --- Auth Guard Middleware for API Endpoints ---
app.use('/api/v1/*', async (c, next) => {
  const configuredKey = c.env.API_KEY;
  if (!configuredKey) {
    // If API_KEY secret is not set, allow requests (open dev mode)
    return await next();
  }

  const authHeader = c.req.header('Authorization');
  const apiKeyHeader = c.req.header('X-API-Key');
  const token = authHeader?.startsWith('Bearer ') ? authHeader.substring(7) : apiKeyHeader;

  if (!token || token !== configuredKey) {
    return c.json({ error: 'Unauthorized: Missing or invalid API key' }, 401);
  }

  await next();
});

// --- Helper Functions ---

async function sendTelegramMessage(token: string, chatId: string, text: string, parseMode: string = 'Markdown') {
  const url = `https://api.telegram.org/bot${token}/sendMessage`;
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: chatId,
        text: text,
        parse_mode: parseMode,
        disable_web_page_preview: true,
      }),
    });
    return await res.json();
  } catch (err) {
    console.error('Telegram dispatch error:', err);
    return { ok: false, error: String(err) };
  }
}

function formatEventsForDigest(events: any[], targetDateStr: string, username: string): string {
  if (!events || events.length === 0) {
    return `🌱 *LifeTrace AI Daily Agenda*\n📅 *${targetDateStr}* (User: \`${username}\`)\n\n🎉 _No events or meetings scheduled for today._`;
  }

  const icons: Record<string, string> = {
    MEETING: '👥',
    TRAVEL: '✈️',
    HEALTH: '🏥',
    DINING: '🍽️',
    MILESTONE: '🏆',
    REMINDER: '⏰',
    DAILY_EVENT: '📌',
  };

  const blocks = events.map((ev, i) => {
    const icon = icons[ev.category?.toUpperCase()] || '📌';
    let line = `*${i + 1}.* ${icon} *[${ev.category}]* *${ev.title}*`;
    if (ev.location) line += `\n   📍 Location: ${ev.location}`;
    if (ev.entity_person) line += `\n   👤 Person: ${ev.entity_person}`;
    if (ev.details) line += `\n   📝 Details: _${ev.details}_`;
    return line;
  });

  return (
    `🌱 *LifeTrace AI Daily Agenda*\n` +
    `📅 *${targetDateStr}* (User: \`${username}\`)\n` +
    `🔔 *${events.length} event(s) scheduled:*\n\n` +
    blocks.join('\n\n') +
    `\n\n_Reply to this chat to ask questions or log new activities!_`
  );
}

// --- API Routes ---

// Health & System Info
app.get('/', (c) => {
  return c.json({
    status: 'healthy',
    system: 'LifeTrace AI Serverless Edge',
    runtime: 'Cloudflare Workers (V8 Edge)',
    database: 'Cloudflare D1 (SQLite)',
    vector_store: 'Cloudflare Vectorize',
    ai_engine: 'Cloudflare Workers AI (Llama 3.3 70B & BGE-Small)',
    telegram_configured: Boolean(c.env.TELEGRAM_BOT_TOKEN && c.env.TELEGRAM_CHAT_ID),
  });
});

// 1. Events Endpoints
app.get('/api/v1/events', async (c) => {
  const username = c.req.query('username') || c.env.DEFAULT_USER || 'default_user';
  const category = c.req.query('category');
  const eventDate = c.req.query('event_date');
  const query = c.req.query('query');
  const limit = Number(c.req.query('limit')) || 50;

  let sql = 'SELECT * FROM personal_events WHERE username = ?';
  const params: any[] = [username];

  if (category && category !== 'ALL') {
    sql += ' AND category = ?';
    params.push(category.toUpperCase());
  }
  if (eventDate) {
    sql += ' AND event_date = ?';
    params.push(eventDate);
  }
  if (query && query.trim()) {
    const q = `%${query.trim().toLowerCase()}%`;
    sql += ' AND (LOWER(title) LIKE ? OR LOWER(details) LIKE ? OR LOWER(location) LIKE ? OR LOWER(entity_person) LIKE ?)';
    params.push(q, q, q, q);
  }

  sql += ' ORDER BY event_date DESC, id DESC LIMIT ?';
  params.push(limit);

  const { results } = await c.env.DB.prepare(sql).bind(...params).all();
  return c.json(results);
});

app.post('/api/v1/events', async (c) => {
  const body = await c.req.json();
  const username = body.username || c.env.DEFAULT_USER || 'default_user';
  const title = body.title;
  const category = (body.category || 'DAILY_EVENT').toUpperCase();
  const eventDate = body.event_date || new Date().toISOString().split('T')[0];
  const location = body.location || null;
  const entityPerson = body.entity_person || null;
  const details = body.details || null;
  const isSecure = body.is_secure ? 1 : 0;

  if (!title) {
    return c.json({ error: 'Title is required' }, 400);
  }

  // 1. Insert into D1 SQLite
  const stmt = c.env.DB.prepare(`
    INSERT INTO personal_events (title, category, event_date, location, entity_person, details, username, is_secure)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `);
  const res = await stmt.bind(title, category, eventDate, location, entityPerson, details, username, isSecure).run();
  const eventId = res.meta.last_row_id;

  // 2. Generate Dense Embedding & Index into Cloudflare Vectorize
  try {
    const textToEmbed = `Personal Event (${eventDate}) [${category}]: ${title}${location ? ` at ${location}` : ''}${entityPerson ? ` with ${entityPerson}` : ''}${details ? `. Details: ${details}` : ''}`;
    const embeddingRes = await c.env.AI.run('@cf/baai/bge-small-en-v1.5', {
      text: [textToEmbed],
    });
    const vector = embeddingRes.data[0];

    await c.env.VECTORIZE.upsert([
      {
        id: `event-${eventId}`,
        values: vector,
        metadata: {
          type: 'event',
          id: eventId,
          title,
          category,
          event_date: eventDate,
          username,
          text: textToEmbed,
        },
      },
    ]);
  } catch (err) {
    console.warn('Vectorize embedding index warning:', err);
  }

  // 3. Optional Telegram Notification
  if (c.env.TELEGRAM_BOT_TOKEN && c.env.TELEGRAM_CHAT_ID) {
    const notifyText = `🌱 *New Event Logged:*\n*${title}* (${category})\n📅 Date: \`${eventDate}\``;
    c.executionCtx.waitUntil(
      sendTelegramMessage(c.env.TELEGRAM_BOT_TOKEN, c.env.TELEGRAM_CHAT_ID, notifyText)
    );
  }

  return c.json({ id: eventId, message: 'Event created and indexed at edge.' }, 201);
});

// 2. Transactions Endpoints
app.get('/api/v1/transactions', async (c) => {
  const username = c.req.query('username') || c.env.DEFAULT_USER || 'default_user';
  const entity = c.req.query('entity_person');
  const limit = Number(c.req.query('limit')) || 50;

  let sql = 'SELECT * FROM transactions WHERE username = ?';
  const params: any[] = [username];

  if (entity) {
    sql += ' AND LOWER(entity_person) LIKE ?';
    params.push(`%${entity.toLowerCase()}%`);
  }

  sql += ' ORDER BY transaction_date DESC, id DESC LIMIT ?';
  params.push(limit);

  const { results } = await c.env.DB.prepare(sql).bind(...params).all();
  return c.json(results);
});

app.post('/api/v1/transactions', async (c) => {
  const body = await c.req.json();
  const username = body.username || c.env.DEFAULT_USER || 'default_user';
  const entity = body.entity_person;
  const amount = Number(body.amount);
  const currency = (body.currency || 'USD').toUpperCase();
  const txDate = body.transaction_date || new Date().toISOString().split('T')[0];
  const notes = body.notes || null;
  const isSecure = body.is_secure ? 1 : 0;

  if (!entity || isNaN(amount)) {
    return c.json({ error: 'entity_person and amount are required' }, 400);
  }

  const stmt = c.env.DB.prepare(`
    INSERT INTO transactions (entity_person, amount, currency, transaction_date, notes, username, is_secure)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `);
  const res = await stmt.bind(entity, amount, currency, txDate, notes, username, isSecure).run();
  const txId = res.meta.last_row_id;

  // Dense Embedding & Index into Vectorize
  try {
    const textToEmbed = `Financial Transaction: Paid/received $${amount} ${currency} with ${entity} on ${txDate}.${notes ? ` Notes: ${notes}` : ''}`;
    const embeddingRes = await c.env.AI.run('@cf/baai/bge-small-en-v1.5', { text: [textToEmbed] });
    const vector = embeddingRes.data[0];
    await c.env.VECTORIZE.upsert([
      {
        id: `tx-${txId}`,
        values: vector,
        metadata: {
          type: 'transaction',
          id: txId,
          entity_person: entity,
          amount,
          currency,
          transaction_date: txDate,
          username,
          text: textToEmbed,
        },
      },
    ]);
  } catch (err) {
    console.warn('Vectorize transaction index warning:', err);
  }

  return c.json({ id: txId, message: 'Transaction saved and indexed.' }, 201);
});

// 3. Document / Notes Ingestion Endpoint
app.post('/api/v1/documents', async (c) => {
  const body = await c.req.json();
  const username = body.username || c.env.DEFAULT_USER || 'default_user';
  const title = body.title || 'Untitled Note';
  const content = body.content;
  if (!content) {
    return c.json({ error: 'content is required' }, 400);
  }

  const stmt = c.env.DB.prepare(`
    INSERT INTO documents (file_path, file_type, file_size_bytes, username, source_name)
    VALUES (?, ?, ?, ?, ?)
  `);
  const res = await stmt.bind(`notes/${title}`, 'text/plain', content.length, username, title).run();
  const docId = res.meta.last_row_id;

  // Embed and index document into Vectorize
  try {
    const embeddingRes = await c.env.AI.run('@cf/baai/bge-small-en-v1.5', { text: [content] });
    const vector = embeddingRes.data[0];
    await c.env.VECTORIZE.upsert([
      {
        id: `doc-${docId}`,
        values: vector,
        metadata: {
          type: 'document',
          id: docId,
          title,
          username,
          text: content.slice(0, 1000),
        },
      },
    ]);
  } catch (err) {
    console.warn('Vectorize document index warning:', err);
  }

  return c.json({ id: docId, message: 'Document saved and indexed.' }, 201);
});

// 4. Multimodal RAG Query Engine (Workers AI + D1 + Vectorize)
app.post('/api/v1/query', async (c) => {
  const body = await c.req.json();
  const query = body.query;
  const username = body.username || c.env.DEFAULT_USER || 'default_user';

  if (!query) {
    return c.json({ error: 'Query is required' }, 400);
  }

  // A. Semantic Search in Vectorize (Dense Vector Similarity)
  let vectorHits: any[] = [];
  try {
    const embedRes = await c.env.AI.run('@cf/baai/bge-small-en-v1.5', { text: [query] });
    const queryVector = embedRes.data[0];
    const vecResults = await c.env.VECTORIZE.query(queryVector, { topK: 5, returnMetadata: 'all' });
    vectorHits = vecResults.matches || [];
  } catch (err) {
    console.warn('Vector search warning:', err);
  }

  // B. Relational Matching in D1
  const todayStr = new Date().toISOString().split('T')[0];
  const qLower = query.toLowerCase();

  // Date check
  let targetDate = '';
  if (qLower.includes('today')) {
    targetDate = todayStr;
  } else {
    const dateMatch = query.match(/\b\d{4}-\d{2}-\d{2}\b/);
    if (dateMatch) {
      targetDate = dateMatch[0];
    }
  }

  // Search personal events via keyword / date
  let matchingEvents: any[] = [];
  try {
    let eventSql = 'SELECT * FROM personal_events WHERE username = ?';
    const eventParams: any[] = [username];

    if (targetDate) {
      eventSql += ' AND event_date = ?';
      eventParams.push(targetDate);
    } else {
      eventSql += ' AND (LOWER(title) LIKE ? OR LOWER(details) LIKE ? OR LOWER(entity_person) LIKE ? OR LOWER(location) LIKE ?)';
      const term = `%${qLower}%`;
      eventParams.push(term, term, term, term);
    }
    eventSql += ' ORDER BY event_date DESC LIMIT 5';
    const { results } = await c.env.DB.prepare(eventSql).bind(...eventParams).all();
    matchingEvents = results || [];
  } catch (e) {
    console.warn('D1 events query error:', e);
  }

  // Search transactions via entity or general money query
  let matchingTx: any[] = [];
  const isFinancial = qLower.includes('pay') || qLower.includes('spent') || qLower.includes('cost') || qLower.includes('$') || qLower.includes('money') || qLower.includes('transaction');
  try {
    if (isFinancial) {
      const { results } = await c.env.DB.prepare(
        'SELECT * FROM transactions WHERE username = ? ORDER BY transaction_date DESC LIMIT 5'
      ).bind(username).all();
      matchingTx = results || [];
    } else {
      const { results } = await c.env.DB.prepare(
        'SELECT * FROM transactions WHERE username = ? AND (LOWER(entity_person) LIKE ? OR LOWER(notes) LIKE ?) ORDER BY transaction_date DESC LIMIT 5'
      ).bind(username, `%${qLower}%`, `%${qLower}%`).all();
      matchingTx = results || [];
    }
  } catch (e) {
    console.warn('D1 transactions query error:', e);
  }

  // C. Assemble Grounded Context for LLM Synthesis
  const contextParts: string[] = [];

  // 1. Vector Semantic Snippets
  if (vectorHits.length > 0) {
    const vectorTexts = vectorHits
      .filter((hit: any) => hit.metadata?.text)
      .map((hit: any) => `- [Score ${(hit.score || 0).toFixed(2)}] ${hit.metadata.text}`);
    if (vectorTexts.length > 0) {
      contextParts.push(`Relevant Semantic Records:\n${vectorTexts.join('\n')}`);
    }
  }

  // 2. Structured D1 Events
  if (matchingEvents.length > 0) {
    contextParts.push(`Events in Ledger:\n${matchingEvents.map(e => `- [${e.category}] ${e.title} on ${e.event_date} at ${e.location || 'N/A'} with ${e.entity_person || 'N/A'}${e.details ? `. Note: ${e.details}` : ''}`).join('\n')}`);
  }

  // 3. Structured D1 Transactions
  if (matchingTx.length > 0) {
    contextParts.push(`Financial Records:\n${matchingTx.map(t => `- Paid/received $${t.amount} ${t.currency} with ${t.entity_person} on ${t.transaction_date}${t.notes ? ` (${t.notes})` : ''}`).join('\n')}`);
  }

  let finalAnswer = '';
  if (contextParts.length > 0) {
    try {
      const aiRes = await c.env.AI.run('@cf/meta/llama-3.3-70b-instruct', {
        messages: [
          { role: 'system', content: 'You are an accurate, grounded personal assistant. Answer questions concisely using ONLY the provided context. If the answer is found in the context, be clear and direct. Do NOT hallucinate.' },
          { role: 'user', content: `Context:\n${contextParts.join('\n\n')}\n\nUser Question: ${query}\nAnswer:` }
        ],
        max_tokens: 500,
        temperature: 0.1,
      });
      finalAnswer = (aiRes as any)?.response || '';
    } catch (err) {
      console.warn('Workers AI answer generation warning:', err);
    }
  }

  // Fallback if LLM gives empty output or is offline
  if (!finalAnswer && (matchingEvents.length > 0 || vectorHits.length > 0 || matchingTx.length > 0)) {
    const fallbackLines: string[] = [];
    if (matchingEvents.length > 0) {
      fallbackLines.push(`📅 **Events:**\n` + matchingEvents.map(e => `- **[${e.category}] ${e.title}** on ${e.event_date}${e.location ? ` at ${e.location}` : ''}`).join('\n'));
    }
    if (matchingTx.length > 0) {
      fallbackLines.push(`💰 **Transactions:**\n` + matchingTx.map(t => `- **${t.entity_person}:** $${t.amount} ${t.currency} on ${t.transaction_date}`).join('\n'));
    }
    if (fallbackLines.length === 0 && vectorHits.length > 0) {
      const validSnippets = vectorHits.filter((h: any) => h.metadata?.text).map((h: any) => `• ${h.metadata.text}`);
      if (validSnippets.length > 0) fallbackLines.push(`🔍 **Relevant Notes:**\n` + validSnippets.join('\n'));
    }
    finalAnswer = fallbackLines.join('\n\n');
  } else if (!finalAnswer) {
    finalAnswer = `No records found matching '${query}' in your ledger or notes.`;
  }

  return c.json({
    query,
    answer: finalAnswer,
    events: matchingEvents,
    transactions: matchingTx,
    vector_matches: vectorHits.length,
  });
});

// 4. Telegram Webhook Receiver (Listens to DM & Channel Posts)
app.post('/telegram/webhook', async (c) => {
  const update = await c.req.json();
  const token = c.env.TELEGRAM_BOT_TOKEN;
  if (!token) {
    return c.json({ ok: false, error: 'TELEGRAM_BOT_TOKEN not configured in Worker' });
  }

  const message = update.message || update.edited_message || update.channel_post || update.edited_channel_post;
  if (!message || !message.text) {
    return c.json({ ok: true, note: 'No text message' });
  }

  // 1. Optional Secret Token check from Telegram
  if (c.env.TELEGRAM_SECRET_TOKEN) {
    const incomingSecret = c.req.header('X-Telegram-Bot-Api-Secret-Token');
    if (incomingSecret !== c.env.TELEGRAM_SECRET_TOKEN) {
      return c.json({ ok: false, error: 'Unauthorized webhook source' }, 401);
    }
  }

  const chatId = String(message.chat.id);
  const text = message.text.trim();
  const username = c.env.DEFAULT_USER || 'default_user';

  // 2. Chat ID Guard: Only permit interaction from the configured owner chat or channel
  const authorizedChatId = c.env.TELEGRAM_CHAT_ID;
  if (authorizedChatId && chatId !== authorizedChatId) {
    console.warn(`Blocked unauthorized access attempt from Telegram Chat ID: ${chatId}`);
    await sendTelegramMessage(token, chatId, '⛔ *Access Denied:* You are not authorized to interact with this Personal RAG.');
    return c.json({ ok: false, error: 'Unauthorized chat ID' }, 403);
  }

  // Commands
  if (text.startsWith('/start') || text.startsWith('/help')) {
    const helpMsg = 
      `🌱 *LifeTrace AI — Cloudflare Edge Assistant*\n\n` +
      `Commands:\n` +
      `• \`/today\` — Today's scheduled events\n` +
      `• \`/events\` — Recent personal events\n` +
      `• \`/expenses\` — Recent financial records\n` +
      `• \`/digest\` — Send full daily agenda\n\n` +
      `Or ask me any question directly!`;
    await sendTelegramMessage(token, chatId, helpMsg);
    return c.json({ ok: true });
  }

  if (text.startsWith('/today') || text.startsWith('/digest')) {
    const todayStr = new Date().toISOString().split('T')[0];
    const { results } = await c.env.DB.prepare(
      'SELECT * FROM personal_events WHERE username = ? AND event_date = ? ORDER BY id DESC'
    ).bind(username, todayStr).all();
    const digestText = formatEventsForDigest(results || [], todayStr, username);
    await sendTelegramMessage(token, chatId, digestText);
    return c.json({ ok: true });
  }

  if (text.startsWith('/events')) {
    const { results } = await c.env.DB.prepare(
      'SELECT * FROM personal_events WHERE username = ? ORDER BY event_date DESC LIMIT 5'
    ).bind(username).all();
    const lines = (results || []).map((e: any) => `• *[${e.category}]* ${e.title} (${e.event_date})`);
    const reply = lines.length ? `📅 *Recent Events:*\n${lines.join('\n')}` : 'No events found.';
    await sendTelegramMessage(token, chatId, reply);
    return c.json({ ok: true });
  }

  if (text.startsWith('/expenses')) {
    const { results } = await c.env.DB.prepare(
      'SELECT * FROM transactions WHERE username = ? ORDER BY transaction_date DESC LIMIT 5'
    ).bind(username).all();
    const lines = (results || []).map((t: any) => `• *${t.entity_person}:* $${t.amount} ${t.currency} on ${t.transaction_date}`);
    const reply = lines.length ? `💰 *Recent Transactions:*\n${lines.join('\n')}` : 'No transactions found.';
    await sendTelegramMessage(token, chatId, reply);
    return c.json({ ok: true });
  }

  // Natural Language Question via Edge RAG
  try {
    const queryRes = await app.request('/api/v1/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: text, username }),
    }, c.env);
    const data: any = await queryRes.json();
    await sendTelegramMessage(token, chatId, `🌱 *LifeTrace AI:*\n\n${data.answer}`);
  } catch (err) {
    await sendTelegramMessage(token, chatId, `⚠️ Error processing request: ${err}`);
  }

  return c.json({ ok: true });
});

// Manual Telegram Digest Trigger
app.post('/api/v1/telegram/publish-digest', async (c) => {
  const token = c.env.TELEGRAM_BOT_TOKEN;
  const chatId = c.env.TELEGRAM_CHAT_ID;
  if (!token || !chatId) {
    return c.json({ error: 'TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be configured' }, 400);
  }

  const username = c.req.query('username') || c.env.DEFAULT_USER || 'default_user';
  const todayStr = new Date().toISOString().split('T')[0];

  const { results } = await c.env.DB.prepare(
    'SELECT * FROM personal_events WHERE username = ? AND event_date = ? ORDER BY id DESC'
  ).bind(username, todayStr).all();

  const msg = formatEventsForDigest(results || [], todayStr, username);
  const delivery = await sendTelegramMessage(token, chatId, msg);

  return c.json({
    events_count: results?.length || 0,
    delivery,
  });
});

// --- Scheduled Cron Handler (Automated Daily Morning Telegram Briefing) ---
export default {
  fetch: app.fetch,

  async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext) {
    console.log('⏰ Cloudflare Scheduled Cron triggered at:', new Date().toISOString());
    const token = env.TELEGRAM_BOT_TOKEN;
    const chatId = env.TELEGRAM_CHAT_ID;
    if (!token || !chatId) {
      console.warn('Skipping scheduled briefing: Telegram credentials not bound.');
      return;
    }

    const username = env.DEFAULT_USER || 'default_user';
    const todayStr = new Date().toISOString().split('T')[0];

    const { results } = await env.DB.prepare(
      'SELECT * FROM personal_events WHERE username = ? AND event_date = ? ORDER BY id DESC'
    ).bind(username, todayStr).all();

    const msg = formatEventsForDigest(results || [], todayStr, username);
    ctx.waitUntil(sendTelegramMessage(token, chatId, msg));
  },
};
