# WeCom Customer Service Bridge

## Goal

Turn `wechat_clawbot` from a generic webhook placeholder into an official WeCom customer-service bridge for external contacts.

The bridge now focuses on one flow:

1. WeCom customer-service callback reaches DGTEAM.
2. DGTEAM verifies signature and decrypts the callback body.
3. DGTEAM pulls customer messages through `kf/sync_msg`.
4. DGTEAM runs the existing market query engine.
5. DGTEAM sends a text reply through `kf/send_msg`.

## Runtime surface

- `src/dgteam/integrations/wechat_clawbot/app.py`
  - FastAPI app with health endpoint and WeCom callback endpoint
- `src/dgteam/integrations/wechat_clawbot/service.py`
  - callback orchestration, sync loop, dedupe, query, reply
- `src/dgteam/integrations/wechat_clawbot/wecom_crypto.py`
  - signature verification and AES decrypt/encrypt helpers
- `src/dgteam/integrations/wechat_clawbot/wecom_client.py`
  - access token, `kf/sync_msg`, `kf/send_msg`
- `src/dgteam/integrations/wechat_clawbot/formatter.py`
  - market reply text formatting
- `src/dgteam/integrations/wechat_clawbot/storage.py`
  - inbox storage, callback archive, processed-message dedupe, token cache

## Main endpoints

- `GET /health`
- `GET /wecom/kf/callback`
  - WeCom URL verification
- `POST /wecom/kf/callback`
  - WeCom callback receiver
- `POST /webhook/event`
  - legacy debug ingest
- `POST /webhook/command`
  - legacy debug command route

## Required environment variables

- `DGTEAM_WECHAT_CLAWBOT_ENABLED=true`
- `DGTEAM_WECHAT_CLAWBOT_MODE=wecom_customer_service`
- `DGTEAM_WECHAT_CLAWBOT_HOST=127.0.0.1`
- `DGTEAM_WECHAT_CLAWBOT_PORT=8965`
- `DGTEAM_WECHAT_CLAWBOT_CALLBACK_PATH=/wecom/kf/callback`
- `DGTEAM_WECHAT_CLAWBOT_CORP_ID=...`
- `DGTEAM_WECHAT_CLAWBOT_CORP_SECRET=...`
- `DGTEAM_WECHAT_CLAWBOT_TOKEN=...`
- `DGTEAM_WECHAT_CLAWBOT_ENCODING_AES_KEY=...`
- `DGTEAM_WECHAT_CLAWBOT_OPEN_KFID=...`

Optional:

- `DGTEAM_WECHAT_CLAWBOT_API_BASE_URL=https://qyapi.weixin.qq.com`
- `DGTEAM_WECHAT_CLAWBOT_SECRET=...`
  - only used by the two legacy debug endpoints

## What the user still needs to finish outside code

1. Enable WeCom customer service in the admin console.
2. Create at least one customer-service account.
3. Copy these values:
   - `CorpID`
   - `Secret`
   - `Token`
   - `EncodingAESKey`
   - `OpenKfId`
4. Point the callback URL to the deployed bridge endpoint.
5. Generate a customer-service entry link for real external-contact testing.

## Current behavior

- Text queries are supported.
- Ambiguous queries return a short list of likely models.
- Exact or near-exact queries return:
  - model title
  - market range
  - mainstream reference price
  - major capacities

## Safe next steps

1. Fill the real WeCom values in deployment env.
2. Expose `/wecom/kf/callback` on the public server.
3. Complete WeCom URL verification in the admin console.
4. Send a first external-contact test message.
