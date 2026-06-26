# Deploying WSTI Quiz to a permanent URL (optional)

Running it from a laptop already works and lets phones join from any network
(via the auto-created public link). **Deploying is optional** — it just gives you
a permanent web address that's always on, with nothing to run on a laptop.

Deploying does **not** change the laptop workflow. The same code runs both ways:
- **Laptop:** uses port 8000 and creates a Cloudflare quick link automatically.
- **Cloud:** uses the platform's `$PORT`, skips the tunnel (the platform's own
  HTTPS URL is the public link), and the big-screen page points the QR at itself.

## Easiest: Render (free)

1. Push this folder to GitHub (already done if you're reading this in the repo).
2. Go to https://render.com → sign in with GitHub.
3. **New → Web Service →** pick this repo.
4. Render reads `render.yaml` automatically. If asked, confirm:
   - Runtime: **Python**
   - Start command: `python WSTI_Quiz.py`
   - Env var: `QUIZ_NO_TUNNEL = 1`
   - Plan: **Free**, Instances: **1** (keep it 1 — the game state lives in memory)
5. Deploy. You'll get a URL like `https://wsti-quiz.onrender.com`.
6. On the big screen open `https://wsti-quiz.onrender.com/host` and go full screen.
   Players scan the QR (it points at that URL) from any network.

**Free-tier note:** the service sleeps after ~15 min idle, so the first visit
after a quiet spell takes ~30–60s to wake. Open the `/host` page a minute before
you start so it's warm. Keep it to **1 instance** (the leaderboard is in memory).

## Alternatives
- **Railway** (https://railway.app): New Project → Deploy from repo → it uses the
  `Procfile`. Add env var `QUIZ_NO_TUNNEL=1`. One instance.
- **Fly.io / any host** that can run `python WSTI_Quiz.py` and sets `$PORT` works
  the same way. Always set `QUIZ_NO_TUNNEL=1` and run a single instance.
