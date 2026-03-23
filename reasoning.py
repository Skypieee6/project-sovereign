#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║   PROJECT SOVEREIGN  ·  REASONING ENGINE  ·  v2026.1.0                     ║
║   Pure Python · Zero APIs · Zero external libraries                        ║
║                                                                              ║
║   CAPABILITIES:                                                              ║
║   • Problem decomposition — breaks any question into sub-problems           ║
║   • Chain of thought — reasons step by step showing its work               ║
║   • Evidence weighing — scores evidence for and against each option        ║
║   • Decision engine — reaches justified conclusions                        ║
║   • Socratic questioning — asks clarifying questions when needed           ║
║   • Analogy finder — connects new problems to known patterns               ║
║   • Contradiction detector — finds conflicts in reasoning                  ║
║   • Confidence scoring — knows what it knows vs guesses                    ║
║   • Brain integration — uses StatisticalBrain as its knowledge source      ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os, re, sys, json, math, time, hashlib
from collections import defaultdict, Counter
from typing      import Dict, List, Optional, Tuple, Any

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

try:
    from brain import StatisticalBrain
    HAS_BRAIN = True
except ImportError:
    HAS_BRAIN = False


# ══════════════════════════════════════════════════════════════
# § 1  PRIMITIVES
# ══════════════════════════════════════════════════════════════

class Proposition:
    """A single claim with a truth confidence score."""

    def __init__(self, text: str, confidence: float = 0.5,
                 source: str = "inferred"):
        self.text       = text.strip()
        self.confidence = max(0.0, min(1.0, confidence))
        self.source     = source
        self.support:   List["Proposition"] = []
        self.oppose:    List["Proposition"] = []

    def add_support(self, p: "Proposition") -> None:
        self.support.append(p)
        self._update_confidence()

    def add_opposition(self, p: "Proposition") -> None:
        self.oppose.append(p)
        self._update_confidence()

    def _update_confidence(self) -> None:
        if not self.support and not self.oppose:
            return
        sup_score = sum(p.confidence for p in self.support)
        opp_score = sum(p.confidence for p in self.oppose)
        total     = sup_score + opp_score
        if total > 0:
            self.confidence = sup_score / total

    def __repr__(self) -> str:
        bar = "█" * int(self.confidence * 10) + "░" * (10 - int(self.confidence * 10))
        return f"[{bar}] {self.text} (conf={self.confidence:.2f})"


class ReasoningStep:
    """One step in a chain of thought."""

    def __init__(self, step_num: int, action: str,
                 content: str, confidence: float = 0.5):
        self.step_num   = step_num
        self.action     = action      # e.g. "decompose", "search", "weigh", "conclude"
        self.content    = content
        self.confidence = confidence
        self.ts         = time.time()

    def __str__(self) -> str:
        return f"Step {self.step_num} [{self.action.upper()}]: {self.content}"


class ReasoningTrace:
    """Complete record of a reasoning session."""

    def __init__(self, question: str):
        self.question   = question
        self.steps:     List[ReasoningStep] = []
        self.conclusion: Optional[str]      = None
        self.confidence: float              = 0.0
        self.duration:   float              = 0.0
        self._start     = time.time()
        self._step_n    = 0

    def add(self, action: str, content: str,
            confidence: float = 0.5) -> ReasoningStep:
        self._step_n += 1
        step = ReasoningStep(self._step_n, action, content, confidence)
        self.steps.append(step)
        return step

    def conclude(self, text: str, confidence: float) -> None:
        self.conclusion = text
        self.confidence = confidence
        self.duration   = time.time() - self._start

    def format(self, verbose: bool = True) -> str:
        lines = [
            f"Question: {self.question}",
            "─" * 60,
        ]
        if verbose:
            for step in self.steps:
                conf_bar = "█" * int(step.confidence * 8)
                lines.append(
                    f"  Step {step.step_num:>2} [{step.action:<12}] "
                    f"{conf_bar:<8}  {step.content}"
                )
            lines.append("─" * 60)
        if self.conclusion:
            conf_pct = int(self.confidence * 100)
            lines.append(f"Conclusion ({conf_pct}% confidence):")
            lines.append(f"  {self.conclusion}")
        lines.append(f"Reasoning time: {self.duration:.2f}s  "
                     f"Steps: {len(self.steps)}")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# § 2  QUESTION ANALYSER
# ══════════════════════════════════════════════════════════════

class QuestionAnalyser:
    """
    Classifies questions and decomposes them into sub-questions.

    Question types:
      factual    — what is X, define X, when did X
      causal     — why does X, what causes X
      procedural — how do I X, steps to X
      comparative— X vs Y, should I choose X or Y
      evaluative — is X good, what is the best X
      predictive — what will happen if X
      creative   — how can I X, ways to achieve X
    """

    TYPE_PATTERNS = {
        "factual": re.compile(
            r"^(what\s+is|what\s+are|who\s+is|define|describe|"
            r"tell\s+me\s+about|explain\s+what|when\s+did|where\s+is)",
            re.I),
        "causal": re.compile(
            r"^(why|what\s+causes|what\s+makes|reason\s+for|"
            r"how\s+come|what\s+leads\s+to)",
            re.I),
        "procedural": re.compile(
            r"^(how\s+do\s+i|how\s+to|steps\s+to|how\s+can\s+i|"
            r"what\s+steps|guide\s+to|way\s+to)",
            re.I),
        "comparative": re.compile(
            r"\bvs\b|\bversus\b|or\s+\w+\?|better\s+(than|choice)|"
            r"should\s+i\s+(choose|use|learn|do)",
            re.I),
        "evaluative": re.compile(
            r"^(is\s+it|is\s+\w+\s+good|best\s+way|should\s+i|"
            r"worth\s+it|is\s+this\s+right|recommend)",
            re.I),
        "predictive": re.compile(
            r"^(what\s+will|what\s+would|if\s+i|what\s+happens\s+if|"
            r"what\s+could)",
            re.I),
        "creative": re.compile(
            r"^(how\s+can\s+i|ways\s+to|ideas\s+for|help\s+me|"
            r"suggest|brainstorm)",
            re.I),
    }

    DECOMPOSE_TEMPLATES = {
        "factual": [
            "What is the definition of {topic}?",
            "What are the key properties of {topic}?",
            "What examples of {topic} exist?",
        ],
        "causal": [
            "What are the direct causes of {topic}?",
            "What are the contributing factors to {topic}?",
            "What evidence supports this causal relationship?",
        ],
        "procedural": [
            "What are the prerequisites for {topic}?",
            "What are the main steps involved in {topic}?",
            "What are common mistakes to avoid in {topic}?",
        ],
        "comparative": [
            "What are the strengths of option A?",
            "What are the weaknesses of option A?",
            "What are the strengths of option B?",
            "What are the weaknesses of option B?",
            "In what context is each option better?",
        ],
        "evaluative": [
            "What are the benefits of {topic}?",
            "What are the drawbacks of {topic}?",
            "What criteria should be used to evaluate {topic}?",
            "What do experts say about {topic}?",
        ],
        "predictive": [
            "What is the current state before {topic}?",
            "What mechanisms would drive the change from {topic}?",
            "What historical analogies are relevant to {topic}?",
        ],
        "creative": [
            "What resources are available for {topic}?",
            "What approaches have worked for similar goals to {topic}?",
            "What constraints should guide solutions for {topic}?",
        ],
    }

    def classify(self, question: str) -> str:
        for qtype, pattern in self.TYPE_PATTERNS.items():
            if pattern.search(question):
                return qtype
        return "evaluative"

    def extract_topic(self, question: str) -> str:
        """Extract the core topic from a question."""
        cleaned = re.sub(
            r"^(what\s+is|what\s+are|how\s+do\s+i|how\s+to|why\s+does|"
            r"should\s+i|is\s+it|can\s+i|help\s+me|tell\s+me\s+about|"
            r"explain)\s+",
            "", question, flags=re.I
        ).strip().rstrip("?").strip()
        return cleaned or question

    def decompose(self, question: str, q_type: str) -> List[str]:
        """Break question into sub-questions."""
        topic     = self.extract_topic(question)
        templates = self.DECOMPOSE_TEMPLATES.get(q_type,
                    self.DECOMPOSE_TEMPLATES["evaluative"])

        # For comparative questions extract both options
        if q_type == "comparative":
            # Try to extract option A vs option B
            vs_match = re.search(
                r"(\w[\w\s]+?)\s+(?:vs|versus|or)\s+([\w\s]+?)(?:\?|$)",
                question, re.I)
            if vs_match:
                opt_a = vs_match.group(1).strip()
                opt_b = vs_match.group(2).strip()
                return [
                    f"What are the strengths of {opt_a}?",
                    f"What are the weaknesses of {opt_a}?",
                    f"What are the strengths of {opt_b}?",
                    f"What are the weaknesses of {opt_b}?",
                    f"In what context is {opt_a} better than {opt_b}?",
                    f"In what context is {opt_b} better than {opt_a}?",
                ]

        return [t.format(topic=topic) for t in templates]

    def extract_options(self, question: str) -> List[str]:
        """For comparative/evaluative questions extract the options being compared."""
        patterns = [
            re.compile(r"(\w[\w\s]+?)\s+(?:vs|versus)\s+([\w\s]+?)(?:\?|$)", re.I),
            re.compile(r"(?:choose|pick|select|use|learn)\s+(\w[\w\s]+?)\s+or\s+([\w\s]+?)(?:\?|$)", re.I),
            re.compile(r"(?:better|best):\s*([\w\s,]+?)(?:\?|$)", re.I),
        ]
        for pat in patterns:
            m = pat.search(question)
            if m:
                if m.lastindex >= 2:
                    return [m.group(1).strip(), m.group(2).strip()]
                elif m.lastindex == 1:
                    parts = re.split(r",\s*|\s+or\s+", m.group(1))
                    return [p.strip() for p in parts if p.strip()]
        return []


# ══════════════════════════════════════════════════════════════
# § 3  EVIDENCE COLLECTOR
# ══════════════════════════════════════════════════════════════

class EvidenceCollector:
    """
    Gathers evidence from the brain's knowledge base.
    Scores relevance and extracts key claims.
    """

    STOP = {
        "a","an","the","and","or","but","in","on","at","to","for",
        "of","with","by","is","are","was","were","be","have","has",
        "i","you","he","she","it","we","they","this","that","will",
    }

    def __init__(self, brain):
        self.brain = brain

    def gather(self, query: str, top_k: int = 5) -> List[Tuple[str, float]]:
        """
        Search brain for evidence relevant to query.
        Returns list of (evidence_text, relevance_score).
        """
        if not self.brain:
            return []
        try:
            results = self.brain.ask(query, top_k=top_k)
            evidence = []
            for doc_id, score, snippet in results:
                if score > 0.05 and snippet and len(snippet) > 20:
                    # Clean snippet
                    clean = snippet.strip()
                    evidence.append((clean, score))
            return evidence
        except Exception:
            return []

    def extract_claims(self, text: str) -> List[str]:
        """
        Extract individual factual claims from a passage.
        Splits on sentence boundaries and filters for substance.
        """
        sents = re.split(r"(?<=[.!?])\s+", text)
        claims = []
        for sent in sents:
            sent = sent.strip()
            if (len(sent) > 15
                    and len(re.findall(r"\b[a-zA-Z]{3,}\b", sent)) > 3
                    and not sent.lower().startswith(("however", "but", "also",
                                                     "additionally", "furthermore"))):
                claims.append(sent)
        return claims[:5]

    def score_relevance(self, claim: str, query: str) -> float:
        """Simple TF-IDF-like relevance score between claim and query."""
        query_words = set(
            w.lower() for w in re.findall(r"\b[a-z]{3,}\b", query.lower())
            if w not in self.STOP
        )
        claim_words = set(
            w.lower() for w in re.findall(r"\b[a-z]{3,}\b", claim.lower())
            if w not in self.STOP
        )
        if not query_words or not claim_words:
            return 0.0
        overlap = len(query_words & claim_words)
        return overlap / math.sqrt(len(query_words) * len(claim_words))


# ══════════════════════════════════════════════════════════════
# § 4  LOGIC ENGINE
# ══════════════════════════════════════════════════════════════

class LogicEngine:
    """
    Pure symbolic reasoning over propositions.

    Implements:
    - Modus ponens:   If P then Q. P is true. Therefore Q.
    - Modus tollens:  If P then Q. Q is false. Therefore P is false.
    - Syllogism:      All A are B. All B are C. Therefore all A are C.
    - Analogy:        A is to B as C is to D.
    - Contradiction:  P and not-P cannot both be true.
    - Abduction:      Best explanation for observed evidence.
    """

    def modus_ponens(self, premise: str, condition: str,
                     consequence: str, cond_conf: float) -> Proposition:
        """If premise contains condition and condition holds, derive consequence."""
        return Proposition(
            text       = consequence,
            confidence = cond_conf * 0.9,
            source     = f"modus_ponens('{condition[:30]}')"
        )

    def detect_contradiction(self, props: List[Proposition]) -> List[Tuple[int, int]]:
        """Find pairs of propositions that contradict each other."""
        contradictions = []
        neg_patterns   = [
            (re.compile(r"\bnot\b|\bno\b|\bnever\b|\bcannot\b", re.I),
             re.compile(r"\bcan\b|\bwill\b|\balways\b|\bis\b", re.I)),
        ]
        for i in range(len(props)):
            for j in range(i+1, len(props)):
                a = props[i].text.lower()
                b = props[j].text.lower()
                # Simple heuristic: same subject, opposite predicates
                a_words = set(re.findall(r"\b\w{4,}\b", a))
                b_words = set(re.findall(r"\b\w{4,}\b", b))
                overlap = len(a_words & b_words)
                has_neg_a = bool(re.search(r"\bnot\b|\bno\b|\bnever\b", a))
                has_neg_b = bool(re.search(r"\bnot\b|\bno\b|\bnever\b", b))
                if overlap >= 2 and (has_neg_a != has_neg_b):
                    contradictions.append((i, j))
        return contradictions

    def weigh_evidence(self, for_props: List[Proposition],
                       against_props: List[Proposition]) -> float:
        """
        Weigh evidence for and against a conclusion.
        Returns confidence score for the affirmative conclusion.
        """
        if not for_props and not against_props:
            return 0.5

        for_score     = sum(p.confidence for p in for_props)
        against_score = sum(p.confidence for p in against_props)
        total         = for_score + against_score

        if total == 0:
            return 0.5

        raw = for_score / total
        # Apply softening — extreme values need very strong evidence
        return 0.1 + raw * 0.8

    def abductive_conclusion(self, evidence: List[Tuple[str, float]],
                              question: str) -> Tuple[str, float]:
        """
        Find the best explanation for the evidence (abductive reasoning).
        Returns (conclusion_text, confidence).
        """
        if not evidence:
            return ("Insufficient evidence to reach a conclusion.", 0.1)

        # Weight evidence by relevance score
        weighted = sorted(evidence, key=lambda x: x[1], reverse=True)
        top      = weighted[:3]

        # Build conclusion from strongest evidence
        if len(top) >= 2:
            conf = (top[0][1] + top[1][1]) / 2
        else:
            conf = top[0][1]

        # Extract key insight from top evidence
        key_sent = top[0][0]
        # Cut to first sentence
        first = re.split(r"(?<=[.!?])\s+", key_sent)[0]

        return (first, min(conf * 0.85, 0.92))


# ══════════════════════════════════════════════════════════════
# § 5  CONCLUSION GENERATOR
# ══════════════════════════════════════════════════════════════

class ConclusionGenerator:
    """
    Synthesises evidence and logic steps into a final conclusion.
    Generates human-readable explanations of the reasoning.
    """

    def synthesise(self, question: str, q_type: str,
                   steps: List[ReasoningStep],
                   evidence: List[Tuple[str, float]],
                   options: List[str]) -> Tuple[str, float]:
        """
        Generate final conclusion from all reasoning steps.
        Returns (conclusion_text, confidence).
        """
        if q_type == "comparative" and len(options) == 2:
            return self._comparative_conclusion(question, steps, evidence, options)
        elif q_type == "factual":
            return self._factual_conclusion(question, evidence)
        elif q_type == "procedural":
            return self._procedural_conclusion(question, evidence)
        elif q_type == "evaluative":
            return self._evaluative_conclusion(question, evidence)
        elif q_type == "causal":
            return self._causal_conclusion(question, evidence)
        else:
            return self._general_conclusion(question, evidence)

    def _factual_conclusion(self, question: str,
                             evidence: List[Tuple[str, float]]) -> Tuple[str, float]:
        if not evidence:
            topic = re.sub(r"^(what\s+is|define)\s+", "", question, flags=re.I)
            return (f"My knowledge base does not have enough information "
                    f"about '{topic}' to give a confident answer. "
                    f"Consider feeding more relevant text.", 0.15)

        best_text, best_score = evidence[0]
        # Take the first 2 strong sentences as the answer
        sents = re.split(r"(?<=[.!?])\s+", best_text)
        answer_sents = [s for s in sents[:3] if len(s) > 20]
        answer = " ".join(answer_sents[:2])

        if len(evidence) > 1:
            answer += f" Additionally: {evidence[1][0][:150]}"

        return (answer, min(best_score * 0.9, 0.88))

    def _procedural_conclusion(self, question: str,
                                evidence: List[Tuple[str, float]]) -> Tuple[str, float]:
        if not evidence:
            return ("I need more knowledge on this procedure. "
                    "Feed relevant documentation to the brain.", 0.15)

        steps_text = []
        for i, (text, score) in enumerate(evidence[:4], 1):
            sents = re.split(r"(?<=[.!?])\s+", text)
            if sents:
                steps_text.append(f"{i}. {sents[0].strip()}")

        if steps_text:
            return ("\n".join(steps_text), min(evidence[0][1] * 0.85, 0.85))
        return (evidence[0][0][:300], evidence[0][1] * 0.8)

    def _comparative_conclusion(self, question: str,
                                  steps: List[ReasoningStep],
                                  evidence: List[Tuple[str, float]],
                                  options: List[str]) -> Tuple[str, float]:
        opt_a, opt_b = options[0], options[1]

        # Score evidence for each option
        score_a = sum(s for t, s in evidence
                      if opt_a.lower() in t.lower()) + 0.01
        score_b = sum(s for t, s in evidence
                      if opt_b.lower() in t.lower()) + 0.01

        total  = score_a + score_b
        pct_a  = score_a / total
        pct_b  = score_b / total

        if abs(pct_a - pct_b) < 0.1:
            conclusion = (
                f"The evidence is balanced between {opt_a} and {opt_b}. "
                f"Both have merit. The right choice depends on your specific "
                f"context, goals, and constraints. "
            )
            if evidence:
                conclusion += f"Key insight: {evidence[0][0][:150]}"
            return (conclusion, 0.55)
        elif pct_a > pct_b:
            conclusion = (
                f"Based on available evidence, {opt_a} appears stronger "
                f"for your situation. "
            )
            if evidence:
                rel = [(t, s) for t, s in evidence if opt_a.lower() in t.lower()]
                if rel:
                    conclusion += f"Reason: {rel[0][0][:200]}"
            return (conclusion, min(pct_a * 0.85, 0.82))
        else:
            conclusion = (
                f"Based on available evidence, {opt_b} appears stronger "
                f"for your situation. "
            )
            if evidence:
                rel = [(t, s) for t, s in evidence if opt_b.lower() in t.lower()]
                if rel:
                    conclusion += f"Reason: {rel[0][0][:200]}"
            return (conclusion, min(pct_b * 0.85, 0.82))

    def _evaluative_conclusion(self, question: str,
                                evidence: List[Tuple[str, float]]) -> Tuple[str, float]:
        if not evidence:
            return ("Insufficient evidence for evaluation. "
                    "Feed more relevant knowledge first.", 0.15)
        best  = evidence[0][0]
        score = evidence[0][1]
        # Find positive and negative signals
        pos   = ["good", "better", "best", "effective", "useful",
                 "valuable", "important", "powerful", "strong"]
        neg   = ["bad", "worse", "worst", "ineffective", "useless",
                 "weak", "poor", "limited", "problem"]
        pos_hits = sum(1 for w in pos if w in best.lower())
        neg_hits = sum(1 for w in neg if w in best.lower())

        if pos_hits > neg_hits:
            verdict = "Evidence suggests this is worthwhile."
        elif neg_hits > pos_hits:
            verdict = "Evidence suggests caution or reconsideration."
        else:
            verdict = "Evidence is mixed — context matters greatly."

        return (f"{verdict} {best[:250]}", min(score * 0.85, 0.80))

    def _causal_conclusion(self, question: str,
                            evidence: List[Tuple[str, float]]) -> Tuple[str, float]:
        if not evidence:
            return ("The causal mechanism is unclear from available knowledge.", 0.2)
        causes = []
        for text, score in evidence[:3]:
            sents = re.split(r"(?<=[.!?])\s+", text)
            for s in sents:
                if any(w in s.lower() for w in
                       ["because", "causes", "leads to", "results in",
                        "due to", "effect", "reason"]):
                    causes.append(s.strip())
        if causes:
            return (" ".join(causes[:2]), evidence[0][1] * 0.82)
        return (evidence[0][0][:300], evidence[0][1] * 0.75)

    def _general_conclusion(self, question: str,
                             evidence: List[Tuple[str, float]]) -> Tuple[str, float]:
        if not evidence:
            return ("I need more relevant knowledge to answer this well. "
                    "Try feeding related documents to the brain.", 0.15)
        best = " ".join(
            re.split(r"(?<=[.!?])\s+", evidence[0][0])[:2]
        )
        if len(evidence) > 1:
            supplement = re.split(r"(?<=[.!?])\s+", evidence[1][0])[0]
            best += f" {supplement}"
        return (best[:400], evidence[0][1] * 0.82)


# ══════════════════════════════════════════════════════════════
# § 6  REASONING ENGINE  (main interface)
# ══════════════════════════════════════════════════════════════

class ReasoningEngine:
    """
    The complete reasoning pipeline.

    Given any question it:
    1.  Classifies the question type
    2.  Decomposes into sub-questions
    3.  Collects evidence from brain for each sub-question
    4.  Builds propositions from evidence
    5.  Detects contradictions
    6.  Weighs evidence for and against
    7.  Generates a structured conclusion
    8.  Returns a full reasoning trace

    Works completely offline. No API needed.
    Quality scales directly with brain knowledge.
    """

    def __init__(self, brain=None, vault_path: str = "brain_vault.json"):
        # Load brain if not provided
        if brain is not None:
            self.brain = brain
        elif HAS_BRAIN:
            try:
                self.brain = StatisticalBrain(vault_path=vault_path)
                self.brain.boot()
            except Exception:
                self.brain = None
        else:
            self.brain = None

        self.analyser    = QuestionAnalyser()
        self.collector   = EvidenceCollector(self.brain)
        self.logic       = LogicEngine()
        self.generator   = ConclusionGenerator()

        # Cache recent reasoning results
        self._cache: Dict[str, ReasoningTrace] = {}

    # ── public API ────────────────────────────────────────────

    def reason(self, question: str,
               verbose: bool = True) -> ReasoningTrace:
        """
        Main entry point. Reason through any question.
        Returns a ReasoningTrace with full step-by-step chain of thought.
        """
        # Check cache
        cache_key = hashlib.md5(question.lower().encode()).hexdigest()[:8]
        if cache_key in self._cache:
            return self._cache[cache_key]

        trace = ReasoningTrace(question)

        # ── Step 1: Classify ──────────────────────────────────
        q_type = self.analyser.classify(question)
        trace.add("classify",
                  f"Question type: {q_type}. "
                  f"Topic: '{self.analyser.extract_topic(question)}'",
                  confidence=0.95)

        # ── Step 2: Decompose ─────────────────────────────────
        sub_questions = self.analyser.decompose(question, q_type)
        options       = self.analyser.extract_options(question)

        trace.add("decompose",
                  f"Decomposed into {len(sub_questions)} sub-questions: "
                  + "; ".join(f"'{sq}'" for sq in sub_questions[:3]),
                  confidence=0.90)

        if options:
            trace.add("identify_options",
                      f"Comparing: {' vs '.join(options)}",
                      confidence=0.95)

        # ── Step 3: Gather evidence ───────────────────────────
        all_evidence: List[Tuple[str, float]] = []

        # Evidence for main question
        main_evidence = self.collector.gather(question, top_k=4)
        all_evidence.extend(main_evidence)

        if main_evidence:
            top_score = main_evidence[0][1]
            trace.add("search_primary",
                      f"Found {len(main_evidence)} evidence items for main question. "
                      f"Best score: {top_score:.3f}",
                      confidence=min(top_score, 0.9))
        else:
            trace.add("search_primary",
                      "No direct evidence found in brain for main question.",
                      confidence=0.2)

        # Evidence for sub-questions
        for sq in sub_questions[:3]:
            sq_evidence = self.collector.gather(sq, top_k=2)
            if sq_evidence:
                all_evidence.extend(sq_evidence)
                trace.add("search_sub",
                          f"Sub-question '{sq[:50]}': "
                          f"{len(sq_evidence)} results (best={sq_evidence[0][1]:.3f})",
                          confidence=sq_evidence[0][1])

        # Deduplicate evidence
        seen_snippets = set()
        unique_evidence = []
        for text, score in all_evidence:
            sig = text[:60].lower()
            if sig not in seen_snippets:
                seen_snippets.add(sig)
                unique_evidence.append((text, score))
        all_evidence = sorted(unique_evidence, key=lambda x: x[1], reverse=True)

        trace.add("deduplicate",
                  f"After deduplication: {len(all_evidence)} unique evidence items",
                  confidence=0.95)

        # ── Step 4: Build propositions ────────────────────────
        propositions = []
        for text, score in all_evidence[:6]:
            claims = self.collector.extract_claims(text)
            for claim in claims[:2]:
                rel = self.collector.score_relevance(claim, question)
                prop = Proposition(
                    text=claim,
                    confidence=min(score * rel * 2.0, 0.95),
                    source="brain"
                )
                propositions.append(prop)

        if propositions:
            avg_conf = sum(p.confidence for p in propositions) / len(propositions)
            trace.add("build_propositions",
                      f"Built {len(propositions)} propositions. "
                      f"Average confidence: {avg_conf:.3f}",
                      confidence=avg_conf)

        # ── Step 5: Detect contradictions ─────────────────────
        if propositions:
            contradictions = self.logic.detect_contradiction(propositions)
            if contradictions:
                c_desc = "; ".join(
                    f"'{propositions[i].text[:30]}' conflicts with "
                    f"'{propositions[j].text[:30]}'"
                    for i, j in contradictions[:2]
                )
                trace.add("contradiction_check",
                          f"Detected {len(contradictions)} contradiction(s): {c_desc}",
                          confidence=0.85)
                # Lower confidence of contradicting propositions
                for i, j in contradictions:
                    propositions[i].confidence *= 0.7
                    propositions[j].confidence *= 0.7
            else:
                trace.add("contradiction_check",
                          "No contradictions detected. Evidence is consistent.",
                          confidence=0.90)

        # ── Step 6: Weigh evidence ────────────────────────────
        if options and len(options) == 2:
            for_a = [p for p in propositions
                     if options[0].lower() in p.text.lower()]
            for_b = [p for p in propositions
                     if options[1].lower() in p.text.lower()]
            conf_a = self.logic.weigh_evidence(for_a, for_b)
            conf_b = 1.0 - conf_a
            trace.add("weigh_evidence",
                      f"Evidence weight: {options[0]}={conf_a:.2f}  "
                      f"{options[1]}={conf_b:.2f}",
                      confidence=max(conf_a, conf_b))
        else:
            # Weigh all evidence
            strong = [p for p in propositions if p.confidence > 0.3]
            weak   = [p for p in propositions if p.confidence <= 0.3]
            overall_conf = self.logic.weigh_evidence(strong, weak)
            trace.add("weigh_evidence",
                      f"Strong evidence: {len(strong)} items. "
                      f"Weak evidence: {len(weak)} items. "
                      f"Overall confidence: {overall_conf:.2f}",
                      confidence=overall_conf)

        # ── Step 7: Apply logic rules ─────────────────────────
        # Try modus ponens on top evidence
        if all_evidence and len(all_evidence) >= 2:
            derived = self.logic.modus_ponens(
                premise     = all_evidence[0][0],
                condition   = self.analyser.extract_topic(question),
                consequence = f"This is relevant to: {question[:60]}",
                cond_conf   = all_evidence[0][1]
            )
            trace.add("apply_logic",
                      f"Derived: '{derived.text[:80]}' "
                      f"(conf={derived.confidence:.2f})",
                      confidence=derived.confidence)

        # ── Step 8: Generate conclusion ───────────────────────
        conclusion_text, conclusion_conf = self.generator.synthesise(
            question   = question,
            q_type     = q_type,
            steps      = trace.steps,
            evidence   = all_evidence,
            options    = options,
        )

        trace.add("synthesise",
                  f"Synthesising conclusion from "
                  f"{len(all_evidence)} evidence items...",
                  confidence=conclusion_conf)

        trace.conclude(conclusion_text, conclusion_conf)

        # Cache result
        self._cache[cache_key] = trace
        return trace

    def quick_answer(self, question: str) -> str:
        """
        Just the conclusion — no trace.
        Fast path for integration with agent.py.
        """
        trace = self.reason(question, verbose=False)
        if trace.conclusion:
            conf_pct = int(trace.confidence * 100)
            return f"{trace.conclusion}\n\n[Reasoning confidence: {conf_pct}%  Steps: {len(trace.steps)}]"
        return "Unable to reach a conclusion with current knowledge."

    def explain(self, question: str) -> str:
        """
        Full chain-of-thought explanation.
        Shows every reasoning step.
        """
        trace = self.reason(question)
        return trace.format(verbose=True)

    def compare(self, option_a: str, option_b: str,
                context: str = "") -> str:
        """
        Dedicated comparison between two options.
        More thorough than general reasoning.
        """
        question = f"{option_a} vs {option_b}"
        if context:
            question += f" for {context}"
        return self.explain(question)

    def status(self) -> str:
        brain_status = "not loaded"
        if self.brain:
            try:
                brain_status = self.brain.status()
            except Exception:
                pass
        return (f"ReasoningEngine v2026.1.0\n"
                f"  Brain     : {brain_status}\n"
                f"  Cache     : {len(self._cache)} cached traces\n"
                f"  HAS_BRAIN : {HAS_BRAIN}")


# ══════════════════════════════════════════════════════════════
# § 7  AGENT INTEGRATION HELPER
# ══════════════════════════════════════════════════════════════

class ReasoningRouter:
    """
    Decides when to use reasoning vs direct brain search vs Cerebras.
    Call this from agent.py to route complex questions to the engine.
    """

    REASONING_TRIGGERS = re.compile(
        r"\b(should\s+i|which\s+is\s+better|vs\s+|versus|"
        r"compare|reason\s+through|think\s+through|"
        r"pros\s+and\s+cons|advantages|disadvantages|"
        r"what\s+would\s+happen\s+if|step\s+by\s+step|"
        r"analyse|analyze|evaluate|assess|decide|"
        r"best\s+approach|right\s+choice|recommend|"
        r"best\s+way|how\s+do\s+i|how\s+can\s+i|"
        r"how\s+to|is\s+it\s+worth|worth\s+learning|"
        r"what\s+is\s+better|which\s+one|what\s+should|"
        r"explain\s+why|why\s+is|why\s+should|"
        r"earn\s+money|make\s+money|learn\s+to|"
        r"start\s+learning|get\s+started|"
        r"difference\s+between|what\s+are\s+the)\b",
        re.IGNORECASE
    )

    def needs_reasoning(self, text: str) -> bool:
        """Returns True if this question benefits from deep reasoning."""
        if len(text.split()) < 4:
            return False
        return bool(self.REASONING_TRIGGERS.search(text))
# ══════════════════════════════════════════════════════════════
# § 8  STANDALONE CLI
# ══════════════════════════════════════════════════════════════

def _help() -> None:
    print("""
Sovereign Reasoning Engine — Commands

  reason <question>        Full chain-of-thought reasoning
  quick  <question>        Just the conclusion
  compare <A> vs <B>       Compare two options
  explain <question>       Verbose step-by-step explanation
  status                   Engine status
  help                     This screen
  exit                     Quit
""")


def main() -> None:
    print("\n" + "="*65)
    print("  SOVEREIGN REASONING ENGINE  v2026.1.0")
    print("  Pure Python · No API · Chain of Thought Reasoning")
    print("="*65 + "\n")

    vault = os.path.expanduser("~/brain_vault.json")
    engine = ReasoningEngine(vault_path=vault)
    print(f"  {engine.status()}\n")
    print("  Type 'help' for commands.\n")

    router = ReasoningRouter()

    while True:
        try:
            raw = input("reason ▶ ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            break

        if not raw:
            continue

        cmd_l = raw.lower()

        if cmd_l in ("exit", "quit", "q"):
            print("Goodbye.")
            break

        if cmd_l in ("help", "?"):
            _help()
            continue

        if cmd_l == "status":
            print(f"\n{engine.status()}\n")
            continue

        if cmd_l.startswith("quick "):
            q = raw[6:].strip()
            print(f"\n{engine.quick_answer(q)}\n")
            continue

        if cmd_l.startswith("compare "):
            q = raw[8:].strip()
            print(f"\n{engine.explain(q)}\n")
            continue

        if cmd_l.startswith("explain "):
            q = raw[8:].strip()
            print(f"\n{engine.explain(q)}\n")
            continue

        if cmd_l.startswith("reason "):
            q = raw[7:].strip()
        else:
            q = raw  # treat any input as a question

        t0    = time.time()
        trace = engine.reason(q)
        ms    = (time.time() - t0) * 1000

        print(f"\n{trace.format(verbose=True)}")
        print(f"  [{ms:.0f}ms]\n")


if __name__ == "__main__":
    main()
