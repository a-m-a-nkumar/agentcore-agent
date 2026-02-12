# Langfuse observability (RAG + backend)

Langfuse is **optional**. If you do not set the credentials below, the app runs exactly as before with no tracing. No code paths are broken when Langfuse is disabled.

## Credentials needed

Set these in your environment (e.g. `.env` or your deployment config):

| Variable | Required | Description |
|----------|----------|-------------|
| `LANGFUSE_PUBLIC_KEY` | Yes (when using Langfuse) | Project public key from Langfuse |
| `LANGFUSE_SECRET_KEY` | Yes (when using Langfuse) | Project secret key from Langfuse |
| `LANGFUSE_BASE_URL`   | No (has default) | Langfuse server URL. Default: `https://cloud.langfuse.com` |
| `LANGFUSE_HOST`       | No | Alternative to `LANGFUSE_BASE_URL` (same meaning) |

If **both** `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set, the app will send RAG traces (embedding, vector search, LLM generation) to Langfuse. If either is missing or empty, tracing is disabled and the app behaves as before.

---

## Option 1: Free tier (Langfuse Cloud)

1. Sign up at [cloud.langfuse.com](https://cloud.langfuse.com) (free tier available).
2. Create a project (or use the default).
3. In the project settings, open **API Keys** and create a new key (or copy the existing one).
4. Copy the **Public Key** and **Secret Key**.
5. Set in your environment:
   - `LANGFUSE_PUBLIC_KEY=<your-public-key>`
   - `LANGFUSE_SECRET_KEY=<your-secret-key>`
   - `LANGFUSE_BASE_URL=https://cloud.langfuse.com` (optional; this is the default)

For EU region use: `LANGFUSE_BASE_URL=https://eu.cloud.langfuse.com` (or the URL shown in your Langfuse Cloud project).

---

## Option 2: Self-hosted

1. Deploy Langfuse using the [self-hosting guide](https://langfuse.com/docs/deployment/self-host) (e.g. Docker Compose with Postgres, Redis).
2. Create a project and API keys in your self-hosted instance.
3. Set in your environment:
   - `LANGFUSE_PUBLIC_KEY=<your-public-key>`
   - `LANGFUSE_SECRET_KEY=<your-secret-key>`
   - `LANGFUSE_BASE_URL=https://your-langfuse-host` (your instance URL, e.g. `https://langfuse.yourcompany.com`)

Use the same variable names; the app does not care whether the server is Cloud or self-hosted.

---

## What gets traced

When Langfuse is configured:

- **Root span**: `rag.query` — one per RAG request (with project id, user id, query preview).
- **Child spans**:  
  - `rag.embedding` — query embedding call  
  - `rag.vector_search` — vector DB search  
  - `rag.llm` — LLM generation (model, prompt, full streamed output)

Streaming behavior is unchanged; the LLM response is still sent to the client as SSE while the full output is recorded in Langfuse after the stream completes.
