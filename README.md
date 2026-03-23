# Project Sovereign

I needed an AI agent that could actually reason and remember things locally without melting my phone or requiring a massive GPU. So, I built Sovereign.

This is a lightweight, pure-Python autonomous agent. It doesn't rely on heavy machine learning libraries like PyTorch or Transformers. Instead, it uses symbolic logic, a custom inverted-index search engine for memory, and a smart router that knows when to think locally and when to hand things off to an external API.

## How it works

The project is split into three main pieces:

* **`agent.py` (The Router):** This is the main interface. It handles your profile, runs shell commands safely, and looks at your prompt to decide the best way to handle it. If it's a simple chat or a massive coding task, it pings a fast API (like Cerebras). If it's a complex logic question, it intercepts it and processes it locally.
* **`brain.py` (The Memory):** A custom inverted-index engine. You can feed it massive text files, and it will deduplicate and store them. When the agent needs facts, it searches this local brain instantly instead of relying on a heavy vector database.
* **`reasoning.py` (The Logic Core):** The actual brainpower. It uses pure Python to break down complex questions, extract claims from the local memory, weigh the evidence, look for contradictions, and print out a step-by-step chain of thought. 

## Why this matters

Most AI agents are just thin wrappers around external APIs. Sovereign is designed to run natively on heavily constrained hardware (like Linux, macOS, or Termux on Android) by doing the heavy lifting of memory retrieval and symbolic logic *before* touching an API. 

## Setup

Just drop the files in a folder and run the agent.

```bash
# Optional: Add an API key for the generative fallback
echo "CEREBRAS_API_KEY=your_key_here" >> ~/.env

# Boot the agent
python3 agent.py
