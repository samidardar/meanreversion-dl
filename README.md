# AI Call Center System

A production-grade AI call center with human-like voice agents that handle both **outbound campaigns** and **inbound calls** in **English and French**.

## Architecture

```
CSV/Excel Upload → Campaign → Outbound Dialer → Twilio → Media Streams WS
                                                              ↕
                                                    VoiceSession Manager
                                                     ├─ Deepgram STT (EN/FR)
                                                     ├─ LangGraph Agent
                                                     │   ├─ Greeting
                                                     │   ├─ Intent Classifier
                                                     │   ├─ Responder (Claude)
                                                     │   ├─ Completion Checker
                                                     │   └─ Reporter
                                                     └─ Cartesia TTS (EN/FR)
```

## Tech Stack

| Component | Technology | Why |
|---|---|---|
| Telephony | Twilio | Industry standard |
| STT | Deepgram Nova-2 | <200ms, $0.0043/min |
| TTS | Cartesia Sonic | <100ms, ultra-natural EN+FR |
| LLM | Claude Haiku (fast) + Sonnet (complex) | Cost + quality |
| Orchestration | LangGraph + LangChain | State machine conversations |
| Backend | FastAPI | Async, WebSocket, fast |
| DB | SQLite/PostgreSQL | Call records & reports |
| Queue | Celery + Redis | Bulk outbound dialing |

## Quick Start

### 1. Setup

```bash
cp .env.example .env
# Fill in your API keys in .env
```

Required API keys:
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`
- `DEEPGRAM_API_KEY`
- `CARTESIA_API_KEY`
- `ANTHROPIC_API_KEY`

### 2. Run with Docker

```bash
docker-compose up
```

### 3. Run locally

```bash
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
```

For outbound dialing (background tasks):
```bash
celery -A worker.tasks.celery_app worker --loglevel=info
```

### 4. Expose for Twilio (local dev)

```bash
ngrok http 8000
# Set BASE_URL=https://your-ngrok-url.ngrok.io in .env
```

## Outbound Campaigns

### 1. Upload contacts

```bash
curl -X POST http://localhost:8000/upload \
  -F "file=@tests/sample_contacts.csv" \
  -F "campaign_name=April Delivery Confirmations" \
  -F "task_type=delivery"
```

CSV format:
```csv
first_name,last_name,phone,reason_for_call,language,order_ref,address
John,Smith,+15141234567,Package delivery,en,ORD-001,123 Main St
Marie,Tremblay,+15149876543,Livraison colis,fr,ORD-002,456 Rue Principale
```

### 2. Start dialing

```bash
curl -X POST http://localhost:8000/campaigns/{campaign_id}/start
```

### 3. Check reports

```bash
curl http://localhost:8000/reports
curl http://localhost:8000/reports/{call_id}
```

## Inbound Mode

### Create inbound configuration

```bash
curl -X POST http://localhost:8000/inbound-configs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Le Bon Bistro Reception",
    "task_type": "restaurant",
    "business_name": "Le Bon Bistro",
    "agent_name": "Sophie",
    "language": "fr",
    "knowledge_base": {
      "hours": "Mon-Sun 11am-10pm",
      "cuisine": "French-Canadian",
      "max_party": "20",
      "address": "123 Rue Saint-Denis, Montreal"
    }
  }'
```

Supported inbound task types: `restaurant`, `hotel`, `support`, `custom`

## Supported Task Types

**Outbound:**
- `delivery` — Delivery window confirmation
- `survey` — Customer satisfaction survey
- `reminder` — Appointment reminders

**Inbound:**
- `restaurant` — Reservations, menu questions, hours
- `hotel` — Room bookings, concierge
- `support` — Customer service, troubleshooting

## Human-Like Quality Features

1. **<800ms latency**: Streaming STT → LLM → streaming TTS pipeline
2. **Barge-in / interruption**: Caller can interrupt the agent mid-sentence
3. **Language detection**: Auto-detects EN/FR from first 2 seconds of audio
4. **Mid-call language switch**: Seamlessly switches language if caller changes
5. **Natural fillers**: "Sure!", "Of course!", "Bien sûr!", "Tout à fait!"
6. **Context memory**: Full conversation history maintained across all turns
7. **Model routing**: Haiku for simple turns, Sonnet for complex reasoning

## Cost Estimate

Per 2-minute call:
- STT (Deepgram Nova-2): ~$0.009
- TTS (Cartesia Sonic): ~$0.015
- LLM (Claude Haiku): ~$0.005-0.02
- Twilio: ~$0.013 + per-minute
- **Total: ~$0.04-0.08 per call**

## API Reference

Interactive docs available at: `http://localhost:8000/docs`
