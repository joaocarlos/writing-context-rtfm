#!/usr/bin/env python3
"""
Generate Prompts for CLI Agent A/B Test

This script generates two text files:
1. prompt_a_full.txt - Contains the full repository context
2. prompt_b_pack.txt - Contains the surgical MCP context pack

You can then pass these files to your preferred CLI agent (e.g., `llm`, `aider`, `claude`).
"""

import os
import sys
import json
import subprocess
from pathlib import Path

# --- Configuration ---
PROJECT_ROOT = "tests/fixtures/mini_latex_project"
TASK = "Write the methodology section detailing dataset and quantization"
TARGET = "section_approach"

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
        "--target", TARGET
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error generating pack: {result.stderr}")
        sys.exit(1)
        
    return json.loads(result.stdout)

def build_prompt(context: str, instruction: str, constraints: list = None) -> str:
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
    print("Gathering contexts...")
    
    # 1. Full Context
    full_context_text = get_full_context()
    prompt_a = build_prompt(full_context_text, TASK)
    
    # 2. Pack Context
    pack_data = get_pack_context()
    pack_spans = []
    for span in pack_data["source_spans"]:
        snippet = span.get("metadata", {}).get("snippet", "")
        priority_tag = f" [Priority: {span.get('priority', 'background')}]"
        pack_spans.append(f"--- {span['path']}{priority_tag} ---\n{snippet}")
        
    pack_context_text = "\n\n".join(pack_spans)
    prompt_b = build_prompt(pack_context_text, TASK, constraints=pack_data.get("constraints", []))
    
    # --- Write to files ---
    out_a = "prompt_a_full.txt"
    out_b = "prompt_b_pack.txt"
    
    with open(out_a, "w", encoding="utf-8") as f:
        f.write(prompt_a)
        
    with open(out_b, "w", encoding="utf-8") as f:
        f.write(prompt_b)
        
    print("\nPrompts generated successfully:")
    print(f"  Agent A (Full Context): {out_a} (approx {len(prompt_a)//4} tokens)")
    print(f"  Agent B (Pack Context): {out_b} (approx {len(prompt_b)//4} tokens)")
    
    print("\nTo test with a CLI agent, you can run something like:")
    print(f"  cat {out_a} | llm > result_a.txt")
    print(f"  cat {out_b} | llm > result_b.txt")
    print("or")
    print(f"  aider --message-file {out_b}")

if __name__ == "__main__":
    main()
