import os
import requests
import json
import time
import sys

sys.stdout.reconfigure(encoding='utf-8')

def main():
    # Read API Key from bot.env
    api_key = None
    bot_env_path = r"C:\BC RESEARCH\AI_FACTORY\bot.env"
    if os.path.exists(bot_env_path):
        with open(bot_env_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("AGENTON_API_KEY="):
                    api_key = line.split("=")[1].strip()
                    break

    if not api_key:
        print("Error: AGENTON_API_KEY not found in bot.env")
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    base_url = "https://agenton.me/api"

    # Helper function to upload screenshots
    def upload_file(file_path):
        if not os.path.exists(file_path):
            print(f"Error: {file_path} does not exist.")
            return None
        
        print(f"Uploading {file_path} to {base_url}/upload...")
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, "image/png")}
            try:
                r = requests.post(f"{base_url}/upload", headers=headers, files=files)
                print("Upload Status:", r.status_code)
                print("Upload Response:", r.text)
                if r.status_code == 200:
                    res = r.json()
                    # extract path/url
                    if isinstance(res, dict):
                        return res.get("url") or res.get("path") or res.get("data", {}).get("url")
                return None
            except Exception as e:
                print("Upload failed:", e)
                return None

    # Load Twitter results
    results_path = r"C:\Users\ovjup\.gemini\antigravity\brain\6a7f01f0-7cb8-45a9-b2ae-12024b15a345\scratch\twitter_action_results.json"
    if not os.path.exists(results_path):
        print(f"Error: Twitter results file not found at {results_path}")
        sys.exit(1)

    with open(results_path, "r", encoding="utf-8") as f:
        x_results = json.load(f)

    # 1. Upload screenshots
    dogrouter_screenshot = r"C:\BC RESEARCH\AI_FACTORY\AgentOn\outputs\dogrouter_dashboard.png"
    tg_screenshot = r"C:\BC RESEARCH\AI_FACTORY\AgentOn\outputs\tg_group.png"

    print("=== Uploading Screenshots ===")
    dogrouter_url = upload_file(dogrouter_screenshot)
    time.sleep(2)
    tg_url = upload_file(tg_screenshot)

    print(f"\nDogRouter uploaded URL: {dogrouter_url}")
    print(f"Telegram uploaded URL: {tg_url}")

    if not dogrouter_url:
        print("Warning: DogRouter screenshot upload failed. Using fallback path.")
        dogrouter_url = "/uploads/64be835ea41f4dbd8d3d2c4ecdccddfb.png"
    if not tg_url:
        print("Warning: Telegram screenshot upload failed. Using fallback path.")
        tg_url = "/uploads/d17d58ce3720408197d4f578c232a4fb.png"

    # Define all 5 quest payloads
    dogrouter_tweet_url = x_results["dogrouter"]["tweet"]["url"]
    toco_reply_url = x_results["toco"]["reply_tweet"]["url"]
    clawchat_tweet_url = x_results["clawchat"]["tweet"]["url"]
    ipollo_quote_url = x_results["ipollo"]["quote_tweet"]["url"]
    ipollo_reply_url = x_results["ipollo"]["reply_tweet"]["url"]

    quests_to_submit = [
        {
            "name": "NeoSoul",
            "quest_id": "74e93925-2024-4f22-839f-f7430ffa51ac",
            "payload": {
                "content": "Followed @NeoSoulAI using X account @BC_Research_.",
                "attachments": ["https://x.com/BC_Research_"]
            }
        },
        {
            "name": "DogRouter",
            "quest_id": "96c004eb-c8ca-4086-b108-bf0664286ef8",
            "payload": {
                "content": f"DogRouter account registered using email j0b3@protonmail.com. API key created and credit consumed. Post published on X: {dogrouter_tweet_url}",
                "attachments": [dogrouter_url, dogrouter_tweet_url]
            }
        },
        {
            "name": "TOCO",
            "quest_id": "e848f9a6-c117-47a2-a1bd-a320f4f65709",
            "payload": {
                "content": f"Followed @Toco_Toco_Toco, liked target tweet, and replied under it: {toco_reply_url}",
                "attachments": ["https://x.com/BC_Research_", toco_reply_url]
            }
        },
        {
            "name": "ClawChat",
            "quest_id": "598422a5-51c9-44eb-90cf-f12da01d2d53",
            "payload": {
                "content": f"Followed X accounts, joined Telegram group and sent 'Apply for ClawChat' in topic, and posted on X: {clawchat_tweet_url}",
                "attachments": [tg_url, clawchat_tweet_url]
            }
        },
        {
            "name": "iPollo",
            "quest_id": "45935de0-31fb-4bcc-949b-e46c85a043ba",
            "payload": {
                "content": f"Followed @iPolloAgentOS, quote tweeted and replied. Quote tweet link: {ipollo_quote_url}. Reply link: {ipollo_reply_url}",
                "attachments": [ipollo_quote_url, ipollo_reply_url]
            }
        }
    ]

    print("\n=== Submitting Quests to AgentOn ===")
    submit_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    submission_results = {}

    for idx, quest in enumerate(quests_to_submit):
        if idx > 0:
            print("\nSleeping 35s to respect API rate limits (max 2/min)...")
            time.sleep(35)
        
        print(f"\nSubmitting {quest['name']} (Quest ID: {quest['quest_id']})...")
        print("Payload:", json.dumps(quest['payload'], indent=2))

        try:
            url = f"{base_url}/quests/{quest['quest_id']}/submit"
            r = requests.post(url, headers=submit_headers, json=quest['payload'])
            print(f"Status Code: {r.status_code}")
            print(f"Response: {r.text}")
            
            if r.status_code == 200:
                res_json = r.json()
                submission_results[quest['name']] = {
                    "status": "success",
                    "status_code": r.status_code,
                    "submission_id": res_json.get("submission_id") or res_json.get("id"),
                    "response": res_json
                }
            else:
                submission_results[quest['name']] = {
                    "status": "error",
                    "status_code": r.status_code,
                    "response": r.text
                }
        except Exception as e:
            print(f"Error during submission: {e}")
            submission_results[quest['name']] = {
                "status": "exception",
                "error": str(e)
            }

    # Save submission results to a JSON file
    sub_results_path = r"C:\Users\ovjup\.gemini\antigravity\brain\6a7f01f0-7cb8-45a9-b2ae-12024b15a345\scratch\quest_submission_results.json"
    with open(sub_results_path, "w", encoding="utf-8") as f:
        json.dump(submission_results, f, indent=2, ensure_ascii=False)
    print(f"\nSubmission results saved to {sub_results_path}")

if __name__ == "__main__":
    main()
