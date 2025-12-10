import os
import json
import sys
import subprocess
import time

# --- 0. AUTO-INSTALL DEPENDENCIES (Safety First) ---
def install(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

try:
    import requests
    import google.generativeai as genai
except ImportError:
    print("📦 Installing AI dependencies...")
    install("requests")
    install("google-generativeai")
    import requests
    import google.generativeai as genai

# --- 1. LOAD CONFIGURATION ---
payload_json = os.environ.get("GITHUB_PAYLOAD")
if not payload_json: sys.exit(1)
try: payload = json.loads(payload_json)
except json.JSONDecodeError: sys.exit(1)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

if not GITHUB_TOKEN or not GOOGLE_API_KEY:
    print("❌ Keys missing. Cannot connect to services.")
    sys.exit(1)

# FINAL MODEL FIX: Using the stable 2.5 Flash model
genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash') 

# --- 2. HELPER FUNCTIONS ---

def post_comment(comments_url, body):
    """Posts the AI's final review to the GitHub Pull Request."""
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    final_body = body + "\n\n_— Reviewed by OS-Maintainer Bot 🤖_"
    requests.post(comments_url, json={"body": final_body}, headers=headers)

def update_pr_status(status_url, state, description):
    """Sends a 'Status Check' signal to GitHub to block/allow merging."""
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    data = {
        "state": state,  # 'success', 'failure', 'error', or 'pending'
        "target_url": os.environ.get("KESTRA_EXECUTION_URL"), # Link back to Kestra logs
        "description": description,
        "context": "OS-Maintainer / AI-Reviewer" # The name that appears on GitHub
    }
    requests.post(status_url, json=data, headers=headers)

def get_pr_diff(diff_url):
    """Downloads the raw code changes (Diff) from GitHub."""
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3.diff"} 
    response = requests.get(diff_url, headers=headers)
    return response.text if response.status_code == 200 else None

def analyze_code_with_gemini(diff_text, pr_title, user):
    """Sends the code to Google Gemini for analysis."""
    if len(diff_text) > 30000:
        diff_text = diff_text[:30000] + "\n... (Diff truncated)"

    prompt = f"""
    You are 'OS-Maintainer', an expert Senior Software Engineer.
    Review PR from @{user}. Title: {pr_title}. Code: ```diff {diff_text}```
    
    Instructions: 1. Check for **Security Leaks** and **Logic Bugs**. 2. Be helpful. 3. Verdict: End with either '✅ **APPROVE**' or '⚠️ **REQUEST CHANGES**'.
    """
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"⚠️ **AI Error:** Could not analyze code. The model failed to respond. ({str(e)})"

# --- 3. MAIN EXECUTION FLOW ---
def run():
    print("--- 🧠 AI BRAIN STARTING ---")

    # 1. Validation
    if "pull_request" not in payload: return
    action = payload.get("action")
    if action not in ["opened", "reopened", "synchronize"]: return

    # 2. Extract Details
    pr = payload["pull_request"]
    diff_url = pr["diff_url"]
    comments_url = pr["comments_url"]
    statuses_url = pr["statuses_url"] # URL for the status check API

    # 3. Get Code
    code_diff = get_pr_diff(diff_url)
    if not code_diff or not code_diff.strip(): return

    # 4. Analyze
    print("🤔 Thinking (Querying Gemini)...")
    review = analyze_code_with_gemini(code_diff, pr["title"], pr["user"]["login"])

    # 5. Post Result
    print("🗣️ Posting Review to GitHub...")
    post_comment(comments_url, review)

    # 6. Check Verdict and Block Merge if necessary! (ENFORCEMENT)
    if "⚠️ **REQUEST CHANGES**" in review:
        print("❌ Verdict: REQUEST CHANGES. Blocking Merge.")
        update_pr_status(statuses_url, "failure", "AI Review failed: Changes Requested.")
    elif "✅ **APPROVE**" in review:
        print("✅ Verdict: APPROVED. Allowing Merge.")
        update_pr_status(statuses_url, "success", "AI Review passed successfully.")
    else:
        print("⚠️ Verdict was inconclusive. Setting status to error.")
        update_pr_status(statuses_url, "error", "AI Review was inconclusive.")

if __name__ == "__main__":
    run()
