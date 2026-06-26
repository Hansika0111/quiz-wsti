#!/usr/bin/env python3
"""
WSTI Quiz - a free, self-hosted Kahoot-style live quiz.
Runs on your laptop using only Python's standard library (no installs).
Players join from their phones on the SAME Wi-Fi by scanning a QR code.

Big screen  ->  http://<this-laptop-ip>:8000/host
Phones      ->  http://<this-laptop-ip>:8000/play
"""

import json
import os
import queue
import socket
import threading
import time
import http.server
import socketserver
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("QUIZ_PORT", "8000"))

# ----------------------------------------------------------------------------
# Questions (colors: 0=red/triangle, 1=blue/diamond, 2=yellow/circle, 3=green/square)
# ----------------------------------------------------------------------------
QUESTIONS = [
    {"type": "mc", "time": 30, "text": "What does AI stand for?",
     "options": ["Artificial Intelligence", "Absolutely Incredible", "Almost Instagram", "Auntie Indu"],
     "correct": 0},
    {"type": "mc", "time": 30, "text": "How does AI learn best?",
     "options": ["Asking Google", "Data", "Magic", "Dreaming"],
     "correct": 1},
    {"type": "mc", "time": 30, "text": "What does Cowork with Claude mean?",
     "options": ["Steals your job", "Works with you", "Free snacks", "Office gossip"],
     "correct": 1},
    {"type": "mc", "time": 30, "text": "Which app can Claude NOT connect?",
     "options": ["Gmail", "Calendar", "Your toaster", "Drive"],
     "correct": 2},
    {"type": "mc", "time": 30, "text": "“Hallucination” in AI means…?",
     "options": ["Saw a ghost", "It is dreaming", "It is tired", "It made something up"],
     "correct": 3},
    {"type": "mc", "time": 30, "text": "AI without context is like what?",
     "options": ["A toaster", "A superhero", "A newborn", "A calculator"],
     "correct": 2},
    {"type": "mc", "time": 30, "text": "What describes a Claude Skill?",
     "options": ["Reusable recipe", "Secret handshake", "Talent show", "New emoji"],
     "correct": 0},
    {"type": "mc", "time": 30, "text": "Agents in Claude are like what?",
     "options": ["Parking inspectors", "Detective Agents", "Secret spies", "Little helpers with specific jobs"],
     "correct": 3},
    {"type": "mc", "time": 30, "text": "Connectors are like what?",
     "options": ["Bluetooth speakers", "Bridges to apps", "Cup noodles", "Headphones"],
     "correct": 1},
    {"type": "mc", "time": 30, "text": "How should you learn about agents?",
     "options": ["Copy neighbour", "Play and experiment", "Wait for magic", "Stare at screen"],
     "correct": 1},
    {"type": "open", "time": 90, "text": "What’s the weirdest or funniest thing we can’t do with AI (yet)?",
     "options": [], "correct": None},
]

OPEN_POINTS = 500  # flat points awarded for submitting an open-ended answer

# ----------------------------------------------------------------------------
# Game state
# ----------------------------------------------------------------------------
class Game:
    def __init__(self):
        self.lock = threading.RLock()
        self.players = {}      # id -> {name, score, answered, lastGain, answerIdx}
        self.subscribers = []  # list of {q: Queue, role, id}
        self.phase = "lobby"   # lobby | question | reveal | score | podium
        self.qIndex = -1
        self.startTime = 0     # ms when current question began
        self.answers = {}      # id -> {idx/text, t (ms used), correct, points}
        self.open_responses = []

    # -- subscriber management --
    def subscribe(self, role, pid):
        q = queue.Queue()
        sub = {"q": q, "role": role, "id": pid}
        with self.lock:
            self.subscribers.append(sub)
        return sub

    def unsubscribe(self, sub):
        with self.lock:
            if sub in self.subscribers:
                self.subscribers.remove(sub)

    def broadcast(self):
        with self.lock:
            subs = list(self.subscribers)
        for sub in subs:
            try:
                sub["q"].put_nowait(self.state_for(sub["id"]))
            except Exception:
                pass

    # -- public state sent to a given client --
    def state_for(self, pid):
        with self.lock:
            now = int(time.time() * 1000)
            board = sorted(
                ({"name": p["name"], "score": p["score"]} for p in self.players.values()),
                key=lambda x: -x["score"],
            )
            st = {
                "phase": self.phase,
                "qIndex": self.qIndex,
                "total": len(QUESTIONS),
                "now": now,
                "startTime": self.startTime,
                "playerCount": len(self.players),
                "answeredCount": len(self.answers),
                "board": board,
            }
            q = None
            if 0 <= self.qIndex < len(QUESTIONS):
                Q = QUESTIONS[self.qIndex]
                q = {
                    "type": Q["type"],
                    "text": Q["text"],
                    "options": Q["options"],
                    "time": Q["time"],
                    "optionCount": len(Q["options"]),
                }
                if self.phase in ("reveal", "score", "podium"):
                    q["correct"] = Q["correct"]
            st["question"] = q

            if self.phase in ("reveal", "score") and q is not None:
                if QUESTIONS[self.qIndex]["type"] == "mc":
                    counts = [0, 0, 0, 0]
                    fastest_name, fastest_t = None, None
                    for aid, a in self.answers.items():
                        if isinstance(a.get("idx"), int) and 0 <= a["idx"] < 4:
                            counts[a["idx"]] += 1
                        if a.get("correct"):
                            if fastest_t is None or a["t"] < fastest_t:
                                fastest_t = a["t"]
                                fastest_name = self.players.get(aid, {}).get("name")
                    st["counts"] = counts
                    st["correctCount"] = sum(1 for a in self.answers.values() if a.get("correct"))
                    st["fastest"] = fastest_name
                else:
                    st["openResponses"] = list(self.open_responses)

            # per-player info
            you = None
            if pid in self.players:
                p = self.players[pid]
                you = {
                    "name": p["name"],
                    "score": p["score"],
                    "answered": pid in self.answers,
                    "lastGain": p.get("lastGain", 0),
                    "correct": self.answers.get(pid, {}).get("correct") if pid in self.answers else None,
                }
                # rank
                names = [b["name"] for b in board]
                if p["name"] in names:
                    you["rank"] = names.index(p["name"]) + 1
            st["you"] = you
            return st

    # -- player actions --
    def join(self, name):
        name = (name or "").strip()[:20] or "Player"
        pid = "p" + str(int(time.time() * 1000)) + str(len(self.players))
        with self.lock:
            # de-duplicate display names lightly
            existing = {p["name"] for p in self.players.values()}
            base, n = name, 2
            while name in existing:
                name = f"{base} {n}"; n += 1
            self.players[pid] = {"name": name, "score": 0, "answered": False, "lastGain": 0}
        self.broadcast()
        return pid, name

    def answer(self, pid, idx=None, text=None):
        with self.lock:
            if self.phase != "question" or pid not in self.players:
                return
            if pid in self.answers:
                return  # already answered
            Q = QUESTIONS[self.qIndex]
            now = int(time.time() * 1000)
            used = max(0, now - self.startTime)
            limit = Q["time"] * 1000
            if Q["type"] == "open":
                t = (text or "").strip()[:250]
                if not t:
                    return
                self.answers[pid] = {"text": t, "t": used, "correct": None, "points": OPEN_POINTS}
                self.open_responses.append({"name": self.players[pid]["name"], "text": t})
                self.players[pid]["score"] += OPEN_POINTS
                self.players[pid]["lastGain"] = OPEN_POINTS
            else:
                if idx is None or not (0 <= idx < len(Q["options"])):
                    return
                correct = (idx == Q["correct"])
                if correct:
                    frac = min(1.0, used / limit) if limit else 1.0
                    points = int(round(1000 * (1 - frac / 2)))
                else:
                    points = 0
                self.answers[pid] = {"idx": idx, "t": used, "correct": correct, "points": points}
                self.players[pid]["score"] += points
                self.players[pid]["lastGain"] = points
        self.broadcast()

    # -- host actions --
    def start_question(self, i):
        with self.lock:
            self.qIndex = i
            self.phase = "question"
            self.startTime = int(time.time() * 1000)
            self.answers = {}
            self.open_responses = []
            for p in self.players.values():
                p["lastGain"] = 0
        self.broadcast()

    def host_next(self):
        with self.lock:
            phase, i = self.phase, self.qIndex
        if phase == "lobby":
            self.start_question(0)
        elif phase == "question":
            with self.lock:
                self.phase = "reveal"
            self.broadcast()
        elif phase == "reveal":
            with self.lock:
                self.phase = "score"
            self.broadcast()
        elif phase == "score":
            if i + 1 < len(QUESTIONS):
                self.start_question(i + 1)
            else:
                with self.lock:
                    self.phase = "podium"
                self.broadcast()
        elif phase == "podium":
            pass

    def host_reveal(self):
        with self.lock:
            if self.phase == "question":
                self.phase = "reveal"
        self.broadcast()

    def host_back(self):
        # go back one question (host correction)
        with self.lock:
            i = self.qIndex
        if i > 0:
            self.start_question(i - 1)
        else:
            with self.lock:
                self.phase = "lobby"; self.qIndex = -1
            self.broadcast()

    def reset(self):
        with self.lock:
            self.phase = "lobby"; self.qIndex = -1; self.answers = {}
            self.open_responses = []
            for p in self.players.values():
                p["score"] = 0; p["lastGain"] = 0
        self.broadcast()

    def auto_tick(self):
        # auto-reveal when time runs out or everyone has answered
        while True:
            time.sleep(0.5)
            with self.lock:
                if self.phase == "question" and 0 <= self.qIndex < len(QUESTIONS):
                    Q = QUESTIONS[self.qIndex]
                    now = int(time.time() * 1000)
                    over = (now - self.startTime) >= Q["time"] * 1000
                    everyone = len(self.players) > 0 and len(self.answers) >= len(self.players)
                    do_reveal = over or everyone
                else:
                    do_reveal = False
            if do_reveal:
                self.host_reveal()


GAME = Game()


# ----------------------------------------------------------------------------
# HTTP handler
# ----------------------------------------------------------------------------
def get_lan_ip():
    ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            pass
    return ip


def read_file(name):
    with open(os.path.join(BASE_DIR, name), "rb") as f:
        return f.read()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # quiet

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/", "/host", "/host.html"):
            return self._send(200, read_file("host.html"), "text/html; charset=utf-8")
        if path in ("/play", "/player", "/player.html"):
            return self._send(200, read_file("player.html"), "text/html; charset=utf-8")
        if path == "/config":
            ip = get_lan_ip()
            cfg = {"ip": ip, "port": PORT, "joinUrl": f"http://{ip}:{PORT}/play", "total": len(QUESTIONS)}
            return self._send(200, json.dumps(cfg))
        if path == "/state":
            pid = qs.get("id", [""])[0]
            return self._send(200, json.dumps(GAME.state_for(pid)))
        if path == "/events":
            return self.handle_sse(qs)
        return self._send(404, json.dumps({"error": "not found"}))

    def handle_sse(self, qs):
        role = qs.get("role", ["player"])[0]
        pid = qs.get("id", [""])[0]
        sub = GAME.subscribe(role, pid)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            # initial state
            self.wfile.write(b": connected\n\n")
            self.wfile.write(("data: " + json.dumps(GAME.state_for(pid)) + "\n\n").encode())
            self.wfile.flush()
            while True:
                try:
                    msg = sub["q"].get(timeout=15)
                    self.wfile.write(("data: " + json.dumps(msg) + "\n\n").encode())
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except Exception:
            pass
        finally:
            GAME.unsubscribe(sub)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            data = {}

        if path == "/join":
            pid, name = GAME.join(data.get("name", ""))
            return self._send(200, json.dumps({"id": pid, "name": name}))
        if path == "/answer":
            pid = data.get("id", "")
            if "idx" in data and data["idx"] is not None:
                GAME.answer(pid, idx=int(data["idx"]))
            else:
                GAME.answer(pid, text=data.get("text", ""))
            return self._send(200, json.dumps({"ok": True}))
        if path == "/host":
            action = data.get("action", "")
            if action == "next":
                GAME.host_next()
            elif action == "reveal":
                GAME.host_reveal()
            elif action == "back":
                GAME.host_back()
            elif action == "reset":
                GAME.reset()
            elif action == "start":
                GAME.start_question(0)
            return self._send(200, json.dumps({"ok": True}))
        return self._send(404, json.dumps({"error": "not found"}))


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    global PORT
    threading.Thread(target=GAME.auto_tick, daemon=True).start()
    ip = get_lan_ip()
    # try to bind, walking up ports if busy
    server = None
    for p in range(PORT, PORT + 20):
        try:
            server = ThreadingHTTPServer(("0.0.0.0", p), Handler)
            PORT = p
            break
        except OSError:
            continue
    if server is None:
        print("Could not find a free port. Close other apps and try again.")
        return
    print("\n" + "=" * 56)
    print("  WSTI QUIZ is running!  (press Ctrl+C to stop)")
    print("=" * 56)
    print(f"\n  BIG SCREEN (this laptop):  http://localhost:{PORT}/host")
    print(f"\n  PLAYERS join on phones  :  http://{ip}:{PORT}/play")
    print("\n  (Phones must be on the SAME Wi-Fi as this laptop.)")
    print("=" * 56 + "\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Thanks for playing!")


if __name__ == "__main__":
    main()
