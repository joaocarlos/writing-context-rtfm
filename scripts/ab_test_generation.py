#!/usr/bin/env python3
"""
A/B Test Script: Full Repository Context vs. Context Pack Context

This script evaluates the generation capability of an LLM by feeding it:
Agent A: The entire manuscript (Full Context)
Agent B: The targeted context pack (MCP Context)

Usage:
  export GEMINI_API_KEY="your-api-key"
  python scripts/ab_test_generation.py
"""

import os
import sys
import json
import urllib.request
import subprocess
from pathlib import Path

# --- Configuration ---
PROJECT_ROOT = "tests/fixtures/mini_latex_project"
TASK = "Write the methodology section detailing dataset and quantization"
TARGET = "section_approach"
MODEL = "gemini-2.5-flash"  # Flash is fast and cheap for testing

def call_gemini(prompt: str, api_key: str) -> str:
    """Calls the Gemini REST API without external dependencies."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={api_key}"
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 800
        }
    }
    
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"API Error: {e}")
        if hasattr(e, 'read'):
            print(e.read().decode())
        sys.exit(1)

def get_full_context() -> str:
    """Reads all .tex files in the fixture sections directory to simulate 'all context'."""
    sections_dir = Path(PROJECT_ROOT) / "sections"
    context_parts = []
    
    for tex_file in sorted(sections_dir.glob("*.tex")):
        with open(tex_file, "r", encoding="utf-8") as f:
            context_parts.append(f"--- File: {tex_file.name} ---\n{f.read()}")
            
    return "\n\n".join(context_parts)

def get_pack_context() -> dict:
    """Calls the writing-context-rtfm CLI to get the surgical context pack."""
    cmd = [
        "python3", "src/writing_context_rtfm/cli.py", "pack",
        "--project-root", PROJECT_ROOT,
        "--corpus", "manuscript",
        "--task", TASK,
        "--target", TARGET,
        "--format", "json"
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error generating pack: {result.stderr}")
        sys.exit(1)
        
    return json.loads(result.stdout)

def build_prompt(context: str, instruction: str, constraints: list = None, avoid: list = None) -> str:
    """Builds the prompt for the writing agent."""
    prompt = f"You are an academic writing assistant. Your task is to write a manuscript section based on the provided context.\n\n"
    prompt += f"TASK: {instruction}\n\n"
    
    if constraints:
        prompt += "CONSTRAINTS MUST PRESERVE:\n"
        for c in constraints:
            prompt += f"- {c}\n"
        prompt += "\n"
        
    prompt += "CONTEXT:\n"
    prompt += context + "\n\n"
    prompt += "Write ONLY the manuscript section text. Do not include introductory conversational filler."
    return prompt

def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: Please set the GEMINI_API_KEY environment variable.")
        print("Example: export GEMINI_API_KEY='your-key-here'")
        sys.exit(1)

    print("Gathering contexts...")
    
    # 1. Full Context
    full_context_text = get_full_context()
    print(f"Full Context Size: ~{len(full_context_text) // 4} tokens")
    
    # 2. Pack Context
    pack_data = get_pack_context()
    pack_spans = []
    for span in pack_data["source_spans"]:
        snippet = span.get("metadata", {}).get("snippet", "")
        # Emphasize essential context
        priority_tag = f" [Priority: {span.get('priority', 'background')}]"
        pack_spans.append(f"--- {span['path']}{priority_tag} ---\n{snippet}")
        
    pack_context_text = "\n\n".join(pack_spans)
    print(f"Pack Context Size: ~{len(pack_context_text) // 4} tokens")
    
    # --- Generate Agent A (Full Context) ---
    print("\n--- GENERATING AGENT A (Full Context) ---")
    prompt_a = build_prompt(full_context_text, TASK)
    result_a = call_gemini(prompt_a, api_key)
    
    # --- Generate Agent B (Pack Context) ---
    print("\n--- GENERATING AGENT B (Pack Context) ---")
    prompt_b = build_prompt(pack_context_text, TASK, constraints=pack_data.get("constraints", []))
    result_b = call_gemini(prompt_b, api_key)
    
    # --- Print Results ---
    print("\n" + "="*60)
    print("RESULTS COMPARISON")
    print("="*60)
    
    print("\n>>> AGENT A (FULL CONTEXT) <<<")
    print(f"(Input Tokens: ~{len(prompt_a)//4} | Output length: {len(result_a)} chars)\n")
    print(result_a)
    
    print("\n" + "-"*60)
    
    print("\n>>> AGENT B (MCP PACK CONTEXT) <<<")
    print(f"(Input Tokens: ~{len(prompt_b)//4} | Output length: {len(result_b)} chars)\n")
    print(result_b)

if __name__ == "__main__":
    main()
