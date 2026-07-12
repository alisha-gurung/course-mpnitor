import os
import sys
import json
import argparse
import requests
from bs4 import BeautifulSoup

# Helper to load simple .env file if it exists locally
def load_env():
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    key, val = line.strip().split("=", 1)
                    os.environ[key.strip()] = val.strip()

load_env()

# Force stdout/stderr to use UTF-8 encoding to avoid Windows UnicodeEncodeErrors
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# Define course page and resolve ntfy topic name
COURSE_URL = os.environ.get("COURSE_URL", "").strip()
DEFAULT_NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
STATE_FILE = "state/state.json"

def load_state():
    """Loads monitor state from state.json"""
    state_dir = os.path.dirname(STATE_FILE)
    if state_dir and not os.path.exists(state_dir):
        os.makedirs(state_dir, exist_ok=True)
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"classes": {}, "last_error": None}

def save_state(state):
    """Saves monitor state to state.json"""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Error saving state: {e}")

def parse_availability(html_content):
    """
    Parses the course page HTML and returns a dictionary of class details,
    or None if no session wrappers exist (website markup changed).
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    sessions = soup.find_all(class_='cmp-course-accordion--container-session')
    
    if not sessions:
        return None
        
    results = {}
    for session in sessions:
        cards = session.find_all(class_='cmp-course-accordion--card')
        class_number = None
        available = None
        section = None
        size = None
        
        for card in cards:
            text = card.get_text(separator=' ', strip=True)
            if 'Class number' in text:
                class_number = text.replace('Class number', '').strip()
            elif 'Available' in text:
                available = text.replace('Available', '').strip()
            elif 'Section' in text:
                section = text.replace('Section', '').strip()
            elif 'Size' in text:
                size = text.replace('Size', '').strip()
                
        if class_number:
            try:
                available_count = int(available)
            except (ValueError, TypeError):
                available_count = 0
                
            results[class_number] = {
                'available': available_count,
                'section': section or "Unknown",
                'size': size or "Unknown"
            }
            
    return results

def send_notification(topic, title, message, priority="default", tags="bell"):
    """
    Publishes a notification to ntfy.sh
    """
    url = f"https://ntfy.sh/{topic}"
    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": tags
    }
    try:
        response = requests.post(url, data=message.encode('utf-8'), headers=headers, timeout=10)
        if response.status_code == 200:
            print(f"Successfully sent notification to topic: {topic}")
        else:
            print(f"Failed to send notification. HTTP Status: {response.status_code}, Body: {response.text}")
    except Exception as e:
        print(f"Error sending notification: {e}")

def monitor_classes(topic, target_classes):
    """
    Fetches the course page, checks the targeted classes, and sends alerts if available.
    Tracks state to deduplicate notifications and handle network/parse errors cleanly.
    """
    state = load_state()
    print(f"Fetching course page: {COURSE_URL}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    # 1. Fetch Course Page
    try:
        response = requests.get(COURSE_URL, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        error_msg = str(e)
        print(f"Failed to fetch course page: {error_msg}")
        
        # Notify once for this specific fetch error, then suppress further pings
        if state.get("last_error") != error_msg:
            send_notification(
                topic,
                title="Monitor Fetch Error",
                message=f"Failed to check course page: {error_msg}",
                priority="low",
                tags="warning"
            )
            state["last_error"] = error_msg
            save_state(state)
        else:
            print("Fetch error is identical to previous run. Notification suppressed.")
        return

    # 2. Parse Course Page
    classes = parse_availability(response.text)
    
    # 3. Sanity check: Ensure page structure has not changed
    if classes is None:
        print("Error: No class sessions found on the page. Website structure might have changed!")
        parse_err = "parse_error_no_sessions"
        if state.get("last_error") != parse_err:
            send_notification(
                topic,
                title="Monitor Parsing Error",
                message="No class sessions found on the course page. The website structure may have changed!",
                priority="high",
                tags="warning,exclamation"
            )
            state["last_error"] = parse_err
            save_state(state)
        else:
            print("Parse error is identical to previous run. Notification suppressed.")
        return
        
    # Clear any previous error status since fetch & parse succeeded
    state["last_error"] = None
    
    # 4. Check target classes and compare with previous availability state
    available_alerts = []
    state_changed = False
    
    for class_num in target_classes:
        if class_num in classes:
            details = classes[class_num]
            current_available = details['available']
            size = details['size']
            section = details['section']
            
            print(f"Class {class_num} ({section}): {current_available}/{size} seats available.")
            
            # Retrieve previous availability from state
            prev_available = state["classes"].get(class_num)
            
            # Notify only if seats are available AND it represents a change in count
            if current_available > 0 and current_available != prev_available:
                available_alerts.append(
                    f"🟢 Class {class_num} ({section}) has {current_available} seat(s) available! (Size: {size})"
                )
                
            # Update state if availability has changed
            if prev_available != current_available:
                state["classes"][class_num] = current_available
                state_changed = True
        else:
            print(f"Warning: Target class {class_num} not found on the page.")
            
    if available_alerts:
        alert_title = "CLASS AVAILABLE!"
        alert_message = "\n".join(available_alerts) + f"\n\nLink: {COURSE_URL}"
        print(f"Alerting! {alert_message}")
        send_notification(
            topic=topic,
            title=alert_title,
            message=alert_message,
            priority="high",
            tags="bell,warning,loud_sound"
        )
        
    if state_changed or state.get("last_error") is not None:
        save_state(state)
    else:
        print("No state changes detected.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Monitor university course class availability.")
    parser.add_argument("--test-notify", action="store_true", help="Send a test notification to verify ntfy setup.")
    parser.add_argument("--topic", default=DEFAULT_NTFY_TOPIC, help="The ntfy.sh topic to publish to.")
    args = parser.parse_args()
    
    if not args.topic:
        print("Error: No ntfy topic specified. Please set the 'NTFY_TOPIC' environment variable, create a '.env' file containing 'NTFY_TOPIC=...', or pass the --topic argument.", file=sys.stderr)
        sys.exit(1)

    if not COURSE_URL:
        print("Error: No course URL specified. Please set the 'COURSE_URL' environment variable or create a '.env' file containing 'COURSE_URL=...'.", file=sys.stderr)
        sys.exit(1)
        
    env_classes = os.environ.get("TARGET_CLASSES")
    if not env_classes or not env_classes.strip():
        print("Error: No target classes specified. Please set the 'TARGET_CLASSES' environment variable (comma-separated) or create a '.env' file containing 'TARGET_CLASSES=...'.", file=sys.stderr)
        sys.exit(1)
        
    target_classes = [c.strip() for c in env_classes.split(",") if c.strip()]
    
    if args.test_notify:
        print(f"Sending test notification to topic: {args.topic}")
        send_notification(
            topic=args.topic,
            title="Course Monitor Test",
            message="Test notification from your course availability monitor. It works! 🎉",
            priority="default",
            tags="tada,bell"
        )
    else:
        monitor_classes(args.topic, target_classes)
