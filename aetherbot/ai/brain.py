"""AI brain — LOCAL LLM advisory layer via Ollama. No API keys.

This is AetherBot's AI reasoning layer. It works out of the box once
Ollama is installed and the model pulled (`ollama pull llama3.2`) — the
user asked for exactly that: "the AI works after being installed".

DESIGN CONTRACT (same proven design as the RegimeDesk project):
- The LLM REASONS about proposed trades; it never creates, modifies,
  sizes, or approves them. The strategy + risk engine is the only trigger.
- Output is a validated annotation in its own namespace. Any field the
  LLM hallucinates (direction, size, mode, api keys) is IGNORED by
  construction — the engine reads only whitelisted annotation fields.
- Its ONLY power is negative: agreement == "against" + advisory_veto
  enabled makes the engine SKIP that signal. It can reduce trading,
  never increase it.
- If Ollama is unreachable, a deterministic local reasoner annotates from
  the proposal's own facts. Nothing ever depends on a cloud API or key.
"""
from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger("aetherbot.ai")

ALLOWED_AGREEMENT = ("supports", "questions", "against")

ADVISORY_NOTE = ("LLM output is advisory only and never a trade trigger; "
                 "orders come solely from the strategy engine through the "
                 "risk manager gates.")

_SYSTEM_PROMPT = (
    "You are the advisory reasoning layer of a rule-based crypto trading "
    "bot. You CANNOT place, modify, size, or approve any trade. You only "
    "analyze a rule-generated trade proposal and reply with a strict JSON "
    "object: "
    '{"summary": string (2-4 factual sentences), '
    '"agreement": "supports" | "questions" | "against", '
    '"notes": [string, ...]}. Reason only from the supplied facts. Never '
    "promise or imply gains. If the evidence is contradictory, say so "
    "plainly."
)


class AIBrain:
    def __init__(self, ollama_url: str = "http://localhost:11434",
                 ollama_model: str = "llama3.2", timeout: int = 25):
        self.url = ollama_url.rstrip("/")
        self.model = ollama_model
        self.timeout = timeout
        self._probe: bool | None = None

    def _available(self) -> bool:
        if self._probe is None:
            try:
                urllib.request.urlopen(self.url + "/api/tags", timeout=2)
                self._probe = True
            except Exception:  # noqa: BLE001 — offline is a normal state
                self._probe = False
        return self._probe

    def annotate(self, proposal: dict) -> dict:
        """Return {"summary", "agreement", "notes", "source", "advisory"}.
        Never mutates the proposal. Never raises."""
        try:
            if self._available():
                return self._validate(self._call_ollama(proposal),
                                      source="ollama:%s" % self.model)
            return self._validate(self._local(proposal),
                                  source="local_deterministic")
        except Exception as exc:  # noqa: BLE001 — degrade, never crash
            ann = self._validate(self._local(proposal),
                                 source="local_deterministic")
            ann["notes"] = ann["notes"] + [
                "Ollama call failed (%s); using local reasoning" % exc]
            return ann

    def _facts(self, proposal: dict) -> dict:
        """The exact fact set the LLM may reason over — nothing else."""
        return {
            "pair": proposal.get("pair"),
            "side": proposal.get("side"),
            "entry_price": proposal.get("entry_price"),
            "stake": proposal.get("stake"),
            "strategy": proposal.get("strategy"),
            "timeframe": proposal.get("timeframe"),
            "indicators": proposal.get("indicators"),
            "reasons": proposal.get("reasons"),
        }

    def _call_ollama(self, proposal: dict) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(self._facts(proposal))},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2},
        }
        req = urllib.request.Request(
            self.url + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.load(resp)
        return json.loads(body["message"]["content"])

    def _local(self, proposal: dict) -> dict:
        """Deterministic offline reasoner — conservative posture: ambiguous
        cases read as "questions", never silent agreement."""
        ind = proposal.get("indicators") or {}
        notes: list[str] = []
        agreement = "supports"
        if ind.get("rsi") is not None:
            if proposal.get("side") == "long" and ind["rsi"] > 70:
                agreement = "questions"
                notes.append("RSI %.1f is overbought while the proposal is "
                             "long — entry context is stretched" % ind["rsi"])
            if proposal.get("side") == "short" and ind["rsi"] < 30:
                agreement = "questions"
                notes.append("RSI %.1f is oversold while the proposal is "
                             "short — entry context is stretched" % ind["rsi"])
        if not ind:
            agreement = "questions"
            notes.append("no indicator facts supplied")
        notes.append("deterministic signal from strategy %s"
                     % proposal.get("strategy"))
        summary = ("Local deterministic read: %s proposal on %s at %s "
                   "(strategy %s). %s"
                   % (proposal.get("side"), proposal.get("pair"),
                      proposal.get("entry_price"),
                      proposal.get("strategy"),
                      "; ".join(notes[:2]) or "no contradictions found"))
        return {"summary": summary, "agreement": agreement, "notes": notes}

    def _validate(self, ann: dict, source: str) -> dict:
        """Whitelist, always. Whatever else an LLM returns is dropped."""
        if not isinstance(ann, dict):
            ann = {}
        return {
            "summary": str(ann.get("summary", ""))[:2000],
            "agreement": ann.get("agreement") if ann.get("agreement")
            in ALLOWED_AGREEMENT else "questions",
            "notes": [str(n)[:500] for n in ann.get("notes", [])][:10],
            "source": source,
            "advisory": ADVISORY_NOTE,
        }
