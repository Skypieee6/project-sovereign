#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║   PROJECT SOVEREIGN  ·  UNIFIED AGENT  ·  v2026.4.0                        ║
║   Brain  ·  Cerebras AI  ·  Web Search  ·  Self-Learning                   ║
║                                                                              ║
║   FIXES IN v2026.4.0:                                                       ║
║   • No duplicate responses                                                  ║
║   • file/feed command works correctly                                       ║
║   • Clean markdown stripping                                                ║
║   • Streaming works without double print                                    ║
║   • Code saved and run once only                                            ║
║   • Short casual replies go to Cerebras not brain search                   ║
║   • Brain search threshold raised to avoid random matches                  ║
╚══════════════════════════════════════════════════════════════════════════════╝

SETUP:
    echo "CEREBRAS_API_KEY=your-key" >> ~/.env
    python3 agent.py
"""

import os, re, sys, json, math, time, hashlib, random, heapq
import subprocess, threading, itertools, readline
from collections import defaultdict, Counter, deque
from typing      import Dict, List, Optional, Tuple, Any

try:
    from urllib.request import urlopen, Request
    from urllib.parse   import urlencode
    from urllib.error   import URLError, HTTPError
    HAS_NET = True
except ImportError:
    HAS_NET = False

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

try:
    from brain import StatisticalBrain
    HAS_BRAIN = True
except ImportError:
    HAS_BRAIN = False

try:
    from chitchat import IntentClassifier
    HAS_CHITCHAT = True
except ImportError:
    HAS_CHITCHAT = False


# ══════════════════════════════════════════════════════════════
# COLOURS
# ══════════════════════════════════════════════════════════════
class C:
    R="\033[0m"; B="\033[1m"; D="\033[2m"
    CY="\033[96m"; BL="\033[94m"; GR="\033[92m"
    YL="\033[93m"; RD="\033[91m"; MG="\033[95m"
    WH="\033[97m"; GY="\033[90m"


# ══════════════════════════════════════════════════════════════
# MARKDOWN STRIPPER
# ══════════════════════════════════════════════════════════════
def clean(text: str) -> str:
    """Strip all markdown formatting for clean terminal output."""
    # Remove code fences but keep the code content
    text = re.sub(r"```\w*\n?", "", text)
    text = re.sub(r"```", "", text)
    # Remove bold and italic
    text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_\n]+)_{1,3}", r"\1", text)
    # Remove headers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove bullet points
    text = re.sub(r"^[ \t]*[-*+]\s+", "", text, flags=re.MULTILINE)
    # Remove numbered lists
    text = re.sub(r"^[ \t]*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Remove inline code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Remove horizontal rules
    text = re.sub(r"^[-*_]{3,}$", "", text, flags=re.MULTILINE)
    # Clean up extra blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Clean trailing whitespace per line
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    return text.strip()


# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════
class Config:
    BRAIN_VAULT  = os.path.expanduser("~/brain_vault.json")
    PROFILE_PATH = os.path.expanduser("~/profile.json")
    LOG_PATH     = os.path.expanduser("~/agent_log.txt")
    WORKSPACE    = os.path.expanduser("~/workspace")
    ENV_PATH     = os.path.expanduser("~/.env")

    CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
    MODEL_FAST   = "llama3.1-8b"
    MODEL_POWER  = "qwen-3-235b-a22b-instruct-2507"
    MODEL_BACKUP = "llama3.1-8b"

    TOKEN_BUDGETS = {
        "chat":    600,
        "explain": 1500,
        "code":    2500,
        "website": 3000,
        "reason":  3000,
    }

    DAILY_LIMIT    = 1_000_000
    WARN_REMAINING = 100_000

    # Brain search — higher threshold = fewer random matches
    BRAIN_SCORE_QUESTION = 0.15
    BRAIN_SCORE_CHAT     = 0.25
    BRAIN_TOP_K          = 3

    COMPRESS_MAX_SENTS = 60
    DEDUP_THRESHOLD    = 0.80

    SHELL_BLACKLIST = re.compile(
        r"\b(rm\s+-rf|mkfs|dd\s+if|:(){ :|:& };:|"
        r"shutdown|reboot|halt|poweroff|format|fdisk|wipefs)\b",
        re.IGNORECASE
    )


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════
def load_env(path: str = Config.ENV_PATH) -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
    except OSError:
        pass


class Logger:
    def __init__(self, path: str):
        self.path = path

    def log(self, tag: str, msg: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"[{ts}][{tag}] {msg}\n")
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════
# AUTO COMPRESSOR
# ══════════════════════════════════════════════════════════════
class AutoCompressor:
    STOP = {
        "a","an","the","and","or","but","in","on","at","to","for",
        "of","with","by","is","are","was","were","be","have","has",
        "i","you","he","she","it","we","they","this","that","will",
        "would","could","should","can","may","just","also","so","very",
    }

    def _tok(self, text: str) -> List[str]:
        return [w for w in re.findall(r"\b[a-z]{3,}\b", text.lower())
                if w not in self.STOP]

    def compress(self, text: str) -> str:
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text)
                 if len(s.strip()) > 30
                 and len(re.findall(r"\b[a-zA-Z]{3,}\b", s)) > 5]
        if len(sents) <= Config.COMPRESS_MAX_SENTS:
            return text
        all_tok = Counter(self._tok(text))
        total   = max(sum(all_tok.values()), 1)
        scored  = []
        for i, s in enumerate(sents):
            toks = self._tok(s)
            score = sum(all_tok.get(t, 0) / total for t in toks) / max(len(toks), 1)
            scored.append((score, i, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = sorted(scored[:Config.COMPRESS_MAX_SENTS], key=lambda x: x[1])
        return " ".join(s for _, _, s in top)

    def is_duplicate(self, text: str, brain) -> bool:
        if not brain:
            return False
        try:
            r = brain.ask(text[:200], top_k=1)
            return bool(r and r[0][1] > Config.DEDUP_THRESHOLD)
        except Exception:
            return False


# ══════════════════════════════════════════════════════════════
# CEREBRAS CLIENT
# ══════════════════════════════════════════════════════════════
class CerebrasClient:
    def __init__(self, api_key: str, logger: Logger):
        self.api_key          = api_key
        self.logger           = logger
        self.tokens_remaining = Config.DAILY_LIMIT
        self.tokens_used      = 0
        self.requests         = 0
        self._ok              = bool(api_key and HAS_NET)

    @property
    def available(self) -> bool:
        return self._ok and bool(self.api_key)

    def _model(self, task: str) -> str:
        if self.tokens_remaining < Config.WARN_REMAINING:
            return Config.MODEL_BACKUP
        if task in ("code", "website", "reason"):
            return Config.MODEL_POWER
        return Config.MODEL_FAST

    def _system(self, context: str = "", task: str = "chat") -> str:
        s = (
            "You are Sovereign, an advanced personal AI agent. "
            "You are direct, intelligent, and highly capable. "
            "You think step by step for complex problems. "
            "You give complete, accurate answers. "
        )
        if task in ("code", "website"):
            s += (
                "When writing code: produce complete working code. "
                "Include all imports. Make it production quality. "
                "Put all code inside a single code block. "
            )
        if context:
            s += f"\n\nRelevant knowledge:\n{context}"
        return s

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "User-Agent":    "SovereignAgent/2026.4.0",
        }

    def call(self, prompt: str, task: str = "chat",
             context: str = "",
             history: List[dict] = None) -> Tuple[bool, str, dict]:
        if not self.available:
            return False, "", {"error": "unavailable"}

        model  = self._model(task)
        tokens = Config.TOKEN_BUDGETS.get(task, 600)
        msgs   = [{"role": "system", "content": self._system(context, task)}]
        if history:
            msgs.extend(history[-6:])
        msgs.append({"role": "user", "content": prompt})

        data = json.dumps({
            "model":                 model,
            "messages":              msgs,
            "max_completion_tokens": tokens,
            "temperature":           0.7,
        }).encode()

        req = Request(Config.CEREBRAS_URL, data=data, headers=self._headers())

        try:
            with urlopen(req, timeout=60) as resp:
                hdrs    = dict(resp.headers)
                result  = json.loads(resp.read())
                text    = result["choices"][0]["message"]["content"].strip()
                usage   = result.get("usage", {})
                tk      = usage.get("total_tokens", 0)
                rem     = hdrs.get("x-ratelimit-remaining-tokens-day", "")
                if rem and str(rem).isdigit():
                    self.tokens_remaining = int(rem)
                self.tokens_used += tk
                self.requests    += 1
                self.logger.log("API", f"model={model} tokens={tk} task={task}")
                return True, text, {"model": model, "tokens": tk}

        except HTTPError as e:
            body = ""
            try: body = e.read().decode()[:300]
            except: pass
            if e.code == 429:
                return False, "Rate limit — wait a moment and try again.", {}
            if e.code == 401:
                self._ok = False
                return False, "Invalid API key.", {}
            return False, f"API error {e.code}: {body}", {}
        except Exception as e:
            return False, f"Connection error: {e}", {}

    def stream(self, prompt: str, task: str = "chat",
               context: str = "",
               history: List[dict] = None) -> str:
        """Stream response to terminal token by token. Returns full text."""
        if not self.available:
            return ""

        model  = self._model(task)
        tokens = Config.TOKEN_BUDGETS.get(task, 600)
        msgs   = [{"role": "system", "content": self._system(context, task)}]
        if history:
            msgs.extend(history[-6:])
        msgs.append({"role": "user", "content": prompt})

        data = json.dumps({
            "model":                 model,
            "messages":              msgs,
            "max_completion_tokens": tokens,
            "temperature":           0.7,
            "stream":                True,
        }).encode()

        req = Request(Config.CEREBRAS_URL, data=data,
                      headers={**self._headers(), "Accept": "text/event-stream"})

        collected = []
        try:
            with urlopen(req, timeout=60) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                        token = chunk["choices"][0].get("delta", {}).get("content", "")
                        if token:
                            print(token, end="", flush=True)
                            collected.append(token)
                    except Exception:
                        continue
            print()  # newline after streaming
            self.requests += 1
            return "".join(collected)
        except Exception as e:
            self.logger.log("STREAM", f"error: {e}")
            return ""

    def quota(self) -> str:
        pct = (self.tokens_remaining / Config.DAILY_LIMIT) * 100
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        return (f"Quota: {bar} {pct:.0f}%  "
                f"remaining={self.tokens_remaining:,}  "
                f"used={self.tokens_used:,}  "
                f"requests={self.requests}")


# ══════════════════════════════════════════════════════════════
# WEB SEARCHER
# ══════════════════════════════════════════════════════════════
class WebSearcher:
    DDG = "https://api.duckduckgo.com/"

    def __init__(self, logger: Logger):
        self.logger = logger

    def search(self, query: str) -> Tuple[bool, str]:
        if not HAS_NET or len(query.split()) < 2:
            return False, ""
        try:
            params = urlencode({
                "q": query, "format": "json",
                "no_html": "1", "no_redirect": "1",
            })
            req = Request(f"{self.DDG}?{params}",
                          headers={"User-Agent": "Sovereign/2026"})
            with urlopen(req, timeout=10) as r:
                data = json.loads(r.read())

            parts = []
            if data.get("Answer"):
                parts.append(data["Answer"])
            if data.get("AbstractText"):
                parts.append(data["AbstractText"])
            for t in data.get("RelatedTopics", [])[:2]:
                if isinstance(t, dict) and t.get("Text"):
                    parts.append(t["Text"])

            if parts:
                return True, " ".join(parts[:2])[:600]
        except Exception as e:
            self.logger.log("SEARCH", f"error: {e}")
        return False, ""


# ══════════════════════════════════════════════════════════════
# SHELL EXECUTOR
# ══════════════════════════════════════════════════════════════
class Shell:
    def __init__(self, logger: Logger):
        self.logger  = logger
        self.history = []

    def run(self, cmd: str, confirm: bool = True) -> dict:
        r = {"cmd": cmd, "stdout": "", "stderr": "",
             "rc": None, "blocked": False, "skipped": False}

        if Config.SHELL_BLACKLIST.search(cmd):
            r["blocked"] = True
            r["stderr"]  = "BLOCKED — dangerous command"
            return r

        if confirm:
            print(f"\n  {C.YL}Run:{C.R} {cmd}")
            if input(f"  {C.YL}Confirm [y/N]:{C.R} ").strip().lower() != "y":
                r["skipped"] = True
                return r

        try:
            p = subprocess.run(cmd, shell=True, capture_output=True,
                               text=True, timeout=15)
            r["stdout"] = p.stdout.strip()
            r["stderr"] = p.stderr.strip()
            r["rc"]     = p.returncode
        except subprocess.TimeoutExpired:
            r["stderr"] = "Timed out after 15s"
            r["rc"]     = -1
        except Exception as e:
            r["stderr"] = str(e)
            r["rc"]     = -1

        self.history.append(r)
        self.logger.log("SHELL", f"cmd='{cmd}' rc={r['rc']}")
        return r


# ══════════════════════════════════════════════════════════════
# CODE MANAGER
# ══════════════════════════════════════════════════════════════
class CodeManager:
    EXTS = {
        "python": ".py", "html": ".html", "javascript": ".js",
        "css": ".css", "bash": ".sh", "text": ".txt",
    }

    def __init__(self, workspace: str, shell: Shell, logger: Logger):
        self.ws     = workspace
        self.shell  = shell
        self.logger = logger
        os.makedirs(workspace, exist_ok=True)

    def detect_lang(self, code: str, request: str) -> str:
        rl = request.lower()
        if any(w in rl for w in ("website", "html", "web page", "webpage")): return "html"
        if any(w in rl for w in ("javascript", "js", "node")):               return "javascript"
        if any(w in rl for w in ("bash", "shell")):                           return "bash"
        if "<html" in code.lower() or "<!doctype" in code.lower():            return "html"
        if "def " in code or "import " in code or "python" in rl:            return "python"
        return "python"

    def extract_blocks(self, text: str) -> List[Tuple[str, str]]:
        blocks  = []
        pattern = re.compile(r"```(\w*)\n?([\s\S]*?)```", re.MULTILINE)
        for m in pattern.finditer(text):
            lang = m.group(1) or "python"
            code = m.group(2).strip()
            if len(code) > 5:
                blocks.append((lang, code))
        # Fallback: if no blocks but looks like code
        if not blocks and len(text) > 20:
            lines = text.strip().splitlines()
            indicators = ("import ", "def ", "class ", "print(", "if __name__",
                          "#!/", "<html", "function ", "const ", "var ")
            if any(any(l.strip().startswith(i) for i in indicators) for l in lines[:10]):
                blocks.append(("python", text))
        return blocks

    def save(self, code: str, lang: str = "python") -> str:
        ext  = self.EXTS.get(lang, ".txt")
        ts   = time.strftime("%Y%m%d_%H%M%S")
        name = f"sovereign_{lang}_{ts}{ext}"
        path = os.path.join(self.ws, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        self.logger.log("CODE", f"saved={path}")
        return path


# ══════════════════════════════════════════════════════════════
# PROFILE
# ══════════════════════════════════════════════════════════════
class Profile:
    DEFAULT = {
        "user_name": "Operator", "agent_name": "Sovereign",
        "goals": [], "skills": [], "preferences": {},
        "learned_facts": [], "interaction_count": 0,
    }

    def __init__(self, path: str):
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                d = json.load(open(self.path))
                for k, v in self.DEFAULT.items():
                    d.setdefault(k, v)
                return d
            except Exception:
                pass
        return dict(self.DEFAULT)

    def save(self) -> None:
        try:
            json.dump(self.data, open(self.path, "w"), indent=2)
        except OSError:
            pass

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value, append=False) -> None:
        if append and isinstance(self.data.get(key), list):
            if value not in self.data[key]:
                self.data[key].append(value)
        else:
            self.data[key] = value
        self.save()

    def bump(self) -> None:
        self.data["interaction_count"] = self.data.get("interaction_count", 0) + 1
        self.save()

    def summary(self) -> str:
        parts = [f"Name: {self.data.get('user_name','Operator')}"]
        if self.data.get("goals"):
            parts.append(f"Goals: {', '.join(self.data['goals'][:3])}")
        if self.data.get("skills"):
            parts.append(f"Skills: {', '.join(self.data['skills'][:3])}")
        if self.data.get("learned_facts"):
            parts.append(f"Facts: {'; '.join(self.data['learned_facts'][-3:])}")
        return " | ".join(parts)


# ══════════════════════════════════════════════════════════════
# SELF LEARNER
# ══════════════════════════════════════════════════════════════
class SelfLearner:
    def __init__(self, brain, comp: AutoCompressor, logger: Logger):
        self.brain  = brain
        self.comp   = comp
        self.logger = logger
        self._queue: List[Tuple[str, str]] = []

    def queue(self, text: str, label: str) -> None:
        if text and len(text) > 50:
            self._queue.append((text, label))

    def flush(self) -> int:
        if not self.brain or not self._queue:
            return 0
        items, self._queue = list(self._queue), []
        done = 0
        for text, label in items:
            try:
                compressed = self.comp.compress(text)
                if len(compressed) < 30:
                    continue
                if self.comp.is_duplicate(compressed, self.brain):
                    continue
                self.brain.learn(compressed, label=label)
                done += 1
            except Exception as e:
                self.logger.log("LEARN", f"error: {e}")
        if done:
            try: self.brain.save()
            except: pass
        return done


# ══════════════════════════════════════════════════════════════
# ROUTER
# ══════════════════════════════════════════════════════════════
class Router:
    META = {
        "status", "stats", "quota", "help", "?", "commands",
        "save", "files", "history", "profile", "clear",
    }

    SHELL_RE = re.compile(
        r"^(ls|pwd|cat|mkdir|chmod|grep|find|ps|kill|df|du|"
        r"wget|curl|apt|pip|python3?|bash|sh|echo|cp|mv|tar|"
        r"nano|vim|run|execute)\s",
        re.IGNORECASE
    )

    CODE_RE = re.compile(
        r"\b(build|create|make|write|generate|code|script|program|"
        r"website|webpage|web\s+page|app|application|automat|tool|"
        r"function|class|implement|develop)\b",
        re.IGNORECASE
    )

    LEARN_RE = re.compile(
        r"(my\s+name\s+is|i'?m\s+called|call\s+me|remember\s+that|"
        r"my\s+goal\s+is|i\s+want\s+to|my\s+skill\s+is|i\s+know\s+"
        r"|add\s+goal|add\s+skill|i\s+like|i\s+love|i\s+prefer)",
        re.IGNORECASE
    )

    PERSONAL_RE = re.compile(
        r"(what\s+are\s+my|what\s+is\s+my|who\s+am\s+i|my\s+goals|"
        r"my\s+skills|my\s+profile|what\s+do\s+you\s+know\s+about\s+me)",
        re.IGNORECASE
    )

    GREET = {
        "hey", "hi", "hello", "howdy", "yo", "sup", "hru",
        "good morning", "good afternoon", "good evening",
        "how are you", "what's up", "whats up",
    }

    BYE = {"bye", "goodbye", "later", "cya", "see you", "see ya"}

    EMO_RE = re.compile(
        r"i\s+(am|feel|'m)\s+(tired|sad|happy|frustrated|angry|"
        r"excited|anxious|stressed|overwhelmed|motivated|confused|"
        r"bored|great|good|bad|terrible|amazing|depressed|lost|lonely)",
        re.IGNORECASE
    )

    Q_RE = re.compile(
        r"^(what|who|where|when|why|how|explain|define|"
        r"tell\s+me|describe|what'?s|who'?s|is\s+there|"
        r"can\s+you\s+explain|do\s+you\s+know)",
        re.IGNORECASE
    )

    def route(self, text: str) -> str:
        tl = text.lower().strip()

        # Meta commands — exact match or prefix
        if tl in self.META:
            return "meta"
        if re.match(r"^(feed|file)\s+\S", tl):
            return "meta"

        # Shell commands
        if self.SHELL_RE.match(text):
            return "shell"

        # Learning
        if self.LEARN_RE.search(text):
            return "learn"

        # Personal questions
        if self.PERSONAL_RE.search(text):
            return "personal"

        # Greetings — exact or starts with
        if tl in self.GREET or any(tl.startswith(g) for g in self.GREET):
            return "greeting"

        # Farewell
        if tl in self.BYE:
            return "farewell"

        # Emotional
        if self.EMO_RE.search(text):
            return "emotional"

        # Code — needs 3+ words to avoid false positives
        if self.CODE_RE.search(text) and len(text.split()) >= 3:
            return "code"

        # Questions
        if self.Q_RE.match(text) or text.rstrip().endswith("?"):
            return "question"

        return "chat"


# ══════════════════════════════════════════════════════════════
# RESPONSE FORMATTER
# ══════════════════════════════════════════════════════════════
class Formatter:
    GREET_TMPLS = [
        "Hey {n}. Sovereign ready. What are we working on?",
        "{n}. Online and focused. What do you need?",
        "Good to have you back, {n}. Talk to me.",
        "Sovereign active, {n}. What's the plan?",
    ]
    BYE_TMPLS = [
        "Later, {n}. Everything's saved.",
        "Signing off. Come back when you're ready, {n}.",
        "Vault saved. Take care, {n}.",
    ]
    EMO_POS = [
        "That energy is solid, {n}. Let's put it to work. What are we tackling?",
        "Good state to be in. Channel it — what's the next target?",
    ]
    EMO_NEG = [
        "Understood, {n}. That's real. What's one thing that would help right now?",
        "I hear you. Sometimes stepping back and attacking again works. What's going on?",
    ]

    def __init__(self, profile: Profile):
        self.profile = profile
        self._g = itertools.cycle(self.GREET_TMPLS)
        self._b = itertools.cycle(self.BYE_TMPLS)
        self._ep = itertools.cycle(self.EMO_POS)
        self._en = itertools.cycle(self.EMO_NEG)

    def _n(self) -> str:
        return self.profile.get("user_name", "Operator")

    def greeting(self) -> str:
        return next(self._g).format(n=self._n())

    def farewell(self) -> str:
        return next(self._b).format(n=self._n())

    def emotional(self, positive: bool) -> str:
        tpl = next(self._ep) if positive else next(self._en)
        return tpl.format(n=self._n())

    def from_brain(self, results: list, query: str) -> str:
        if not results or results[0][1] < Config.BRAIN_SCORE_QUESTION:
            return ""
        snippet = results[0][2] if len(results[0]) > 2 else ""
        if not snippet or len(snippet.strip()) < 20:
            return ""
        s = snippet.strip()
        # Cut at last complete sentence
        for p in [". ", "! ", "? "]:
            idx = s.rfind(p, 30)
            if idx > 50:
                s = s[:idx + 1]
                break
        return s[:800].strip()

    def from_web(self, result: str) -> str:
        return f"From the web: {result[:400]}"


# ══════════════════════════════════════════════════════════════
# SOVEREIGN AGENT
# ══════════════════════════════════════════════════════════════
class Sovereign:

    def __init__(self):
        load_env()
        self.logger  = Logger(Config.LOG_PATH)
        self.profile = Profile(Config.PROFILE_PATH)
        self.router  = Router()
        self.shell   = Shell(self.logger)
        self.code    = CodeManager(Config.WORKSPACE, self.shell, self.logger)
        self.searcher= WebSearcher(self.logger)
        self.comp    = AutoCompressor()

        # Brain
        self.brain = None
        if HAS_BRAIN:
            try:
                self.brain = StatisticalBrain(vault_path=Config.BRAIN_VAULT)
            except Exception as e:
                self.logger.log("INIT", f"brain error: {e}")

        self.learner   = SelfLearner(self.brain, self.comp, self.logger)

        # Reasoning engine
        try:
            from reasoning import ReasoningEngine as _RE, ReasoningRouter as _RR
            self.reasoner  = _RE(brain=self.brain)
            self.re_router = _RR()
        except Exception as _re_err:
            self.reasoner  = None
            self.re_router = None

        self.formatter = Formatter(self.profile)

        # Cerebras
        key = os.environ.get("CEREBRAS_API_KEY", "")
        self.cerebras = CerebrasClient(key, self.logger)

        # Conversation history for multi-turn context
        self.history: List[dict] = []
        self._start = time.time()
        self._count = 0

        os.makedirs(Config.WORKSPACE, exist_ok=True)

    # ── boot ──────────────────────────────────────────────────
    def boot(self) -> None:
        print(f"\n{C.CY}{C.B}")
        print("╔══════════════════════════════════════════════════════════════╗")
        print("║   PROJECT SOVEREIGN  ·  UNIFIED AGENT  ·  v2026.4.0        ║")
        print("║   Brain  ·  Cerebras AI  ·  Web Search  ·  Self-Learning   ║")
        print("╚══════════════════════════════════════════════════════════════╝")
        print(C.R)

        if self.brain:
            try:
                msg = self.brain.boot()
                print(f"  {C.GR}✓{C.R} Brain  — {msg}")
            except Exception as e:
                print(f"  {C.RD}✗{C.R} Brain  — {e}")

        if self.cerebras.available:
            print(f"  {C.GR}✓{C.R} Cerebras — fast={Config.MODEL_FAST}"
                  f"  power={Config.MODEL_POWER}")
        else:
            key = os.environ.get("CEREBRAS_API_KEY", "")
            if not key:
                print(f"  {C.YL}○{C.R} Cerebras — no key  (add to ~/.env)")
            else:
                print(f"  {C.YL}○{C.R} Cerebras — network unavailable")

        name  = self.profile.get("user_name", "Operator")
        goals = self.profile.get("goals", [])
        print(f"  {C.GR}✓{C.R} Profile — {name}  goals={len(goals)}"
              f"  interactions={self.profile.get('interaction_count', 0)}")

        print(f"\n{C.GY}Type 'help' for commands or speak naturally.{C.R}\n")

    # ── main entry ────────────────────────────────────────────
    def process(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""

        self._count += 1
        self.profile.bump()
        self.logger.log("IN", text[:120])

        intent = self.router.route(text)
        self.logger.log("ROUTE", intent)

        # Reasoning engine — fires before routing for complex questions
        _SKIP_RE = {"meta","shell","learn","personal",
                    "greeting","farewell","emotional","code"}
        if intent not in _SKIP_RE and self.reasoner and self.re_router:
            try:
                if self.re_router.needs_reasoning(text):
                    self.logger.log("ROUTE", "reasoning_engine")
                    return self.reasoner.quick_answer(text)
            except Exception as _e:
                self.logger.log("ERR", f"reasoner failed: {_e}")

        dispatch = {
            "meta":      self._meta,
            "shell":     self._shell,
            "learn":     self._learn,
            "personal":  self._personal,
            "greeting":  lambda t: self.formatter.greeting(),
            "farewell":  lambda t: self.formatter.farewell(),
            "emotional": self._emotional,
            "code":      self._code,
            "question":  self._question,
            "chat":      self._chat,
        }

        handler = dispatch.get(intent, self._chat)
        return handler(text)

    # ── handlers ──────────────────────────────────────────────
    def _meta(self, text: str) -> str:
        tl = text.lower().strip()

        if tl in ("help", "?", "commands"):
            return self._help()

        if tl in ("status", "stats"):
            return self._status()

        if tl == "quota":
            return self.cerebras.quota()

        if tl == "profile":
            return self.profile.summary()

        if tl == "save":
            if self.brain:
                ok = self.brain.save()
                return "Brain saved." if ok else "Save failed."
            return "No brain loaded."

        if tl == "files":
            if self.brain:
                files = self.brain.learned_files()
                if not files:
                    return "Nothing learned yet."
                lines = [f"  {f['label']:<35} {f.get('tokens',0):,} tokens"
                         for f in files[-10:]]
                return "Last 10 learned files:\n" + "\n".join(lines)
            return "No brain loaded."

        if tl == "history":
            h = self.shell.history[-5:]
            if not h:
                return "No shell commands this session."
            return "\n".join(f"  [{r['rc']}] {r['cmd']}" for r in h)

        # feed or file command
        if re.match(r"^(feed|file)\s+", tl):
            path = re.sub(r"^(feed|file)\s+", "", text).strip()
            return self._feed(path)

        return f"Unknown command: {text}"

    def _feed(self, path: str) -> str:
        """Feed a file to brain with auto-compression."""
        if not self.brain:
            return "Brain not loaded."
        path = os.path.expanduser(path.strip().strip("\"'"))
        if not os.path.exists(path):
            return f"File not found: {path}"
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
            compressed = self.comp.compress(raw)
            label      = os.path.basename(path)
            result     = self.brain.learn(compressed, label=label)
            self.brain.save()
            orig_kb = len(raw) // 1024
            comp_kb = len(compressed) // 1024
            return (f"Learned '{label}'\n"
                    f"  Original : {orig_kb} KB\n"
                    f"  Compressed: {comp_kb} KB\n"
                    f"  Tokens   : {result.get('tokens', 0):,}")
        except Exception as e:
            return f"Error: {e}"

    def _shell(self, text: str) -> str:
        r = self.shell.run(text, confirm=True)
        if r["blocked"]: return f"BLOCKED — {r['stderr']}"
        if r["skipped"]: return "Cancelled."
        out = r["stdout"]
        if r["stderr"] and r["rc"] != 0:
            out += f"\nError: {r['stderr']}"
        return out or f"Done (exit code {r['rc']})"

    def _learn(self, text: str) -> str:
        patterns = [
            (re.compile(r"(?:my\s+name\s+is|call\s+me|i'?m\s+called)\s+(\w+)", re.I),
             "user_name", False),
            (re.compile(r"(?:my\s+goal\s+is\s+(?:to\s+)?|i\s+want\s+to\s+)(.+)", re.I),
             "goals", True),
            (re.compile(r"(?:i\s+know|i'?m\s+good\s+at|my\s+skill\s+is)\s+(.+)", re.I),
             "skills", True),
            (re.compile(r"remember\s+that\s+(.+)", re.I),
             "learned_facts", True),
            (re.compile(r"i\s+(?:like|love|prefer)\s+(.+)", re.I),
             "learned_facts", True),
        ]
        for pat, key, append in patterns:
            m = pat.search(text)
            if m:
                val = m.group(1).strip().rstrip(".!,;")
                if key == "user_name":
                    val = val.title()
                self.profile.set(key, val, append=append)
                self.learner.queue(f"{key}: {val}", label=f"profile_{key}")
                self.learner.flush()
                if key == "user_name":
                    return f"Got it — I'll call you {val}."
                return f"Saved: {val}"
        return "Noted."

    def _personal(self, text: str) -> str:
        tl   = text.lower()
        name = self.profile.get("user_name", "Operator")
        if re.search(r"goal|objective|aim", tl):
            g = self.profile.get("goals", [])
            return f"Your goals: {', '.join(g)}" if g else "No goals saved yet."
        if re.search(r"skill|good\s+at|strength", tl):
            s = self.profile.get("skills", [])
            return f"Skills: {', '.join(s)}" if s else "No skills logged yet."
        if re.search(r"name|who\s+am", tl):
            return f"You're {name} according to my records."
        if re.search(r"remember|taught|told|fact", tl):
            f = self.profile.get("learned_facts", [])
            return f"What you've taught me: {'; '.join(f[-5:])}" if f else "Nothing noted yet."
        return self.profile.summary()

    def _emotional(self, text: str) -> str:
        m   = re.search(r"i\s+(?:am|'m|feel)\s+(\w+)", text, re.I)
        emo = m.group(1).lower() if m else ""
        pos = {"happy","great","good","excited","amazing","motivated",
               "fantastic","energized","pumped","thrilled"}
        return self.formatter.emotional(emo in pos)

    def _question(self, text: str) -> str:
        # 1. Brain search
        if self.brain:
            try:
                results = self.brain.ask(text, top_k=Config.BRAIN_TOP_K)
                answer  = self.formatter.from_brain(results, text)
                if answer:
                    self.logger.log("ROUTE", f"brain hit score={results[0][1]:.3f}")
                    return answer
            except Exception as e:
                self.logger.log("ERR", f"brain: {e}")

        # 2. Web search
        ok, web = self.searcher.search(text)
        if ok and web:
            self.learner.queue(web, f"web_{text[:30]}")
            self.learner.flush()
            return self.formatter.from_web(web)

        # 3. Cerebras
        return self._cerebras(text, task="explain")

    def _chat(self, text: str) -> str:
        # Only search brain for substantial queries
        if len(text.split()) >= 5 and self.brain:
            try:
                results = self.brain.ask(text, top_k=2)
                if results and results[0][1] >= Config.BRAIN_SCORE_CHAT:
                    answer = self.formatter.from_brain(results, text)
                    if answer:
                        return answer
            except Exception:
                pass
        return self._cerebras(text, task="chat")

    def _code(self, text: str) -> str:
        # Get brain context
        ctx = ""
        if self.brain:
            try:
                r = self.brain.ask(text, top_k=1)
                if r and r[0][1] > 0.08:
                    ctx = r[0][2][:300]
            except Exception:
                pass

        task = "website" if any(w in text.lower()
                                for w in ("website", "webpage", "html")) else "code"

        print(f"\n{C.GY}Generating...{C.R}\n")

        # Use streaming for real-time output
        streamed = self.cerebras.stream(text, task=task, context=ctx,
                                        history=self.history)

        if streamed:
            # Auto-learn the response
            self.learner.queue(streamed, f"code_{text[:40]}")
            self.learner.flush()
            self._hist(text, streamed)

            # Extract and save code blocks
            raw_blocks = self.code.extract_blocks(streamed)
            if raw_blocks:
                print(f"\n{C.GR}Code saved:{C.R}")
                saved = []
                seen  = set()
                for lang, block in raw_blocks:
                    if lang == "auto":
                        lang = self.code.detect_lang(block, text)
                    sig = hashlib.md5(block.encode()).hexdigest()[:8]
                    if sig not in seen:
                        seen.add(sig)
                        path = self.code.save(block, lang)
                        saved.append(path)
                        print(f"  {path}")

                py_files = [p for p in saved if p.endswith(".py")]
                if py_files:
                    if input(f"\n  Run it? [y/N]: ").strip().lower() == "y":
                        r = self.shell.run(f"python3 {py_files[0]}", confirm=False)
                        if r["stdout"]:
                            print(f"\n{C.GR}Output:{C.R}\n{r['stdout']}")
                        if r["stderr"] and r["rc"] != 0:
                            print(f"\n{C.RD}Error:{C.R}\n{r['stderr']}")
            # Return empty string — already printed via streaming
            return ""

        # Fallback to non-streaming if stream fails
        ok, response, _ = self.cerebras.call(text, task=task, context=ctx)
        if not ok:
            if not self.cerebras.available:
                return "Set CEREBRAS_API_KEY in ~/.env to enable code generation."
            return f"Failed: {response}"
        return clean(response)

    def _cerebras(self, text: str, task: str = "chat") -> str:
        """Call Cerebras with streaming. Returns empty string if streamed."""
        ctx = ""
        if self.brain:
            try:
                r = self.brain.ask(text, top_k=1)
                if r and r[0][1] > 0.08:
                    ctx = r[0][2][:300]
            except Exception:
                pass

        # Stream directly to terminal
        streamed = self.cerebras.stream(text, task=task, context=ctx,
                                        history=self.history)
        if streamed:
            self.learner.queue(streamed, f"cerebras_{task}_{text[:20]}")
            self.learner.flush()
            self._hist(text, streamed)
            return ""  # already printed

        # Fallback
        ok, response, _ = self.cerebras.call(text, task=task, context=ctx,
                                              history=self.history)
        if not ok:
            if not self.cerebras.available:
                return self._local_fallback(text)
            return f"Issue: {response}"
        self._hist(text, response)
        self.learner.queue(response, f"cerebras_{task}")
        self.learner.flush()
        return clean(response)

    def _local_fallback(self, text: str) -> str:
        if self.brain:
            try:
                r = self.brain.ask(text, top_k=1)
                answer = self.formatter.from_brain(r, text)
                if answer:
                    return answer
            except Exception:
                pass
        return "Set CEREBRAS_API_KEY in ~/.env for AI responses."

    def _hist(self, user: str, assistant: str) -> None:
        self.history.extend([
            {"role": "user",      "content": user},
            {"role": "assistant", "content": assistant},
        ])
        if len(self.history) > 20:
            self.history = self.history[-20:]

    # ── info ──────────────────────────────────────────────────
    def _status(self) -> str:
        lines = [f"\n{C.CY}{C.B}Sovereign Status{C.R}"]
        if self.brain:
            lines.append(f"  Brain     : {self.brain.status()}")
        else:
            lines.append(f"  Brain     : not loaded")
        if self.cerebras.available:
            lines.append(f"  Cerebras  : {self.cerebras.quota()}")
        else:
            lines.append(f"  Cerebras  : offline")
        lines.append(f"  Profile   : {self.profile.summary()}")
        lines.append(f"  Session   : {self._count} queries  "
                     f"uptime={int(time.time()-self._start)}s")
        lines.append(f"  Workspace : {Config.WORKSPACE}")
        return "\n".join(lines)

    def _help(self) -> str:
        return f"""
{C.CY}{C.B}Sovereign Commands{C.R}

  Natural language        speak normally — routes automatically
  ──────────────────────────────────────────────────────────
  status                  system status
  quota                   Cerebras daily quota
  profile                 your profile
  feed <path>             feed a file to brain
  file <path>             same as feed
  save                    save brain vault
  files                   list learned files
  history                 recent shell commands
  help / ?                this screen
  exit / quit             save and exit
  ──────────────────────────────────────────────────────────
  Any shell command       ls, pwd, cat, python3, etc.
  build / create / write  code generation via Cerebras
  what / how / explain    knowledge search
  my name is / my goal is save to profile

  API key: echo "CEREBRAS_API_KEY=your-key" >> ~/.env
{C.R}"""

    def shutdown(self) -> None:
        print(f"\n{C.GY}Saving...{C.R}", end="", flush=True)
        if self.brain:
            try: self.brain.save()
            except: pass
        self.profile.save()
        n = self.learner.flush()
        print(f" done")
        if n:
            print(f"{C.GY}Learned {n} new items.{C.R}")
        print(f"\n{C.CY}Sovereign offline. "
              f"{self._count} queries. Vault saved.{C.R}\n")


# ══════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════
def main() -> None:
    agent = Sovereign()
    agent.boot()

    while True:
        try:
            user_input = input(f"{C.MG}{C.B}sovereign ▶{C.R} ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{C.YL}Shutting down...{C.R}")
            user_input = "exit"

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "q"):
            agent.shutdown()
            sys.exit(0)

        response = agent.process(user_input)

        # Only print if there is a response AND it was not already streamed
        if response:
            print(f"\n{C.WH}{response}{C.R}\n")


if __name__ == "__main__":
    main()
